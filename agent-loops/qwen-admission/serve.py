#!/usr/bin/env python3
"""OpenAI-compatible Qwen gateway with whole-host, render-first admission."""
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time
import urllib.error
import urllib.request
from policy import inference_limit

UPSTREAM = os.environ.get('QWEN_UPSTREAM', 'http://127.0.0.1:8082')
RENDER = os.environ.get('RENDER_HOST_HEALTH_URL', 'http://127.0.0.1:8095/healthz')
MAX_BODY = 16 * 1024 * 1024


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.rejected = 0
        self.completed = 0
        self.failed = 0
        self.resources = {}
        self.limit = 0
        self.reason = 'resource_sample_unavailable'
        self.baseline = None
        self.slow_samples = 0
        self.good_samples = 0
        self.slowdown = False
        self.ttft = deque(maxlen=100)
        self.durations = deque(maxlen=100)

    def observe_latency(self, ttft, duration, rendering):
        with self.lock:
            self.completed += 1
            self.durations.append(duration)
            if ttft is None:
                return
            self.ttft.append(ttft)
            if self.baseline is None:
                self.baseline = max(.05, ttft)
            if ttft > max(2, self.baseline * 2):
                self.slow_samples += 1
                self.good_samples = 0
            else:
                self.slow_samples = 0
                self.good_samples += 1
                if not rendering:
                    self.baseline = .9 * self.baseline + .1 * max(.05, ttft)
            if self.slow_samples >= 3:
                self.slowdown = True
            if self.good_samples >= 5:
                self.slowdown = False

    def snapshot(self):
        with self.lock:
            samples = sorted(self.ttft)
            return dict(active=self.active, limit=self.limit, reason=self.reason, rejected=self.rejected, completed=self.completed, failed=self.failed, slowdown=self.slowdown, ttftBaselineSeconds=self.baseline, ttftP95Seconds=samples[min(len(samples)-1, int(len(samples)*.95))] if samples else None, lastResponseSeconds=self.durations[-1] if self.durations else None)


state = State()


def sample_resources():
    while True:
        try:
            with urllib.request.urlopen(RENDER, timeout=2) as response:
                resources = json.load(response)
        except (OSError, ValueError):
            resources = {}
        with state.lock:
            state.resources = resources
            state.limit, state.reason = inference_limit(resources, slowdown=state.slowdown)
        time.sleep(1)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        # Request bodies and headers can contain private source code or credentials.
        pass

    def reply(self, status, payload, retry=False):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        if retry:
            self.send_header('Retry-After', '5')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/healthz':
            self.reply(200, state.snapshot())
            return
        if self.path not in ['/v1/models', '/health']:
            self.reply(404, {'error': {'message': 'Unknown gateway path'}})
            return
        self.forward()

    def do_POST(self):
        if self.path not in ['/v1/chat/completions', '/v1/completions', '/v1/embeddings']:
            self.reply(404, {'error': {'message': 'Unknown inference path'}})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            size = -1
        if size <= 0 or size > MAX_BODY:
            self.close_connection = True
            self.reply(413, {'error': {'message': 'Invalid or oversized request'}})
            return
        body = self.rfile.read(size)
        with state.lock:
            # Re-evaluate sample freshness at admission, even if the poller stalled.
            limit, reason = inference_limit(state.resources, slowdown=state.slowdown)
            rejected = state.active >= limit
            if rejected:
                state.rejected += 1
            else:
                state.active += 1
            rendering = state.resources.get('activeRenders', 0) > 0
        if rejected:
            self.reply(429 if limit else 503, {'error': {'message': 'Local inference admission paused: ' + reason, 'type': 'local_backpressure'}}, retry=True)
            return
        started = time.monotonic()
        try:
            ttft = self.forward(body, started)
            state.observe_latency(ttft, time.monotonic() - started, rendering)
        except (OSError, ValueError):
            with state.lock:
                state.failed += 1
            self.close_connection = True
        finally:
            with state.lock:
                state.active -= 1

    def forward(self, body=None, started=None):
        headers = {'Content-Type': 'application/json'}
        if self.headers.get('Authorization'):
            headers['Authorization'] = self.headers['Authorization']
        request = urllib.request.Request(UPSTREAM + self.path, data=body, headers=headers, method=self.command)
        try:
            upstream = urllib.request.urlopen(request, timeout=1800)
        except urllib.error.HTTPError as error:
            upstream = error
        with upstream:
            self.send_response(upstream.status)
            self.send_header('Content-Type', upstream.headers.get('Content-Type', 'application/json'))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            pending = b''
            ttft = None
            while True:
                chunk = upstream.read1(16384)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
                if started is not None and ttft is None:
                    pending = (pending + chunk)[-65536:]
                    lines = pending.split(b'\n')
                    pending = lines.pop()
                    for line in lines:
                        if not line.startswith(b'data: '):
                            continue
                        try:
                            choices = json.loads(line[6:]).get('choices', [])
                            if any(any(choice.get('delta', {}).get(k) for k in ['content', 'reasoning_content', 'tool_calls']) or choice.get('text') for choice in choices):
                                ttft = time.monotonic() - started
                                break
                        except (ValueError, TypeError):
                            pass
            return ttft


if __name__ == '__main__':
    threading.Thread(target=sample_resources, daemon=True).start()
    server = ThreadingHTTPServer((os.environ.get('QWEN_GATEWAY_HOST', '127.0.0.1'), int(os.environ.get('QWEN_GATEWAY_PORT', '8081'))), Handler)
    server.daemon_threads = True
    server.serve_forever()
