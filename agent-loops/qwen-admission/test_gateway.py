import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import serve


class Upstream(BaseHTTPRequestHandler):
    calls = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        type(self).calls += 1
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        self.wfile.write(b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')


class GatewayIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        cls.gateway = ThreadingHTTPServer(('127.0.0.1', 0), serve.Handler)
        serve.UPSTREAM = 'http://127.0.0.1:' + str(cls.upstream.server_port)
        for server in [cls.upstream, cls.gateway]:
            threading.Thread(target=server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for server in [cls.upstream, cls.gateway]:
            server.shutdown()
            server.server_close()

    def setUp(self):
        serve.state = serve.State()
        serve.state.resources = dict(pressureSampledAt=datetime.now(timezone.utc).isoformat(), admissionPaused=False, criticalMemoryPressure=False, memoryPressure=False, cpuPressure=False, activeRenders=0)
        self.url = 'http://127.0.0.1:' + str(self.gateway.server_port) + '/v1/chat/completions'

    def request(self):
        return urllib.request.urlopen(urllib.request.Request(self.url, data=b'{"stream":true}', headers={'Content-Type': 'application/json'}), timeout=3)

    def test_sse_preserved_and_first_token_latency_recorded(self):
        with self.request() as response:
            self.assertIn(b'data: [DONE]', response.read())
        self.assertEqual(serve.state.active, 0)
        self.assertEqual(serve.state.completed, 1)
        self.assertGreater(serve.state.snapshot()['ttftP95Seconds'], 0)

    def test_memory_pressure_rejects_without_calling_qwen(self):
        serve.state.resources['memoryPressure'] = True
        before = Upstream.calls
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request()
        self.assertEqual(caught.exception.code, 503)
        caught.exception.close()
        self.assertEqual(caught.exception.headers['Retry-After'], '5')
        self.assertEqual(Upstream.calls, before)
        self.assertEqual(serve.state.active, 0)

    def test_render_limits_qwen_to_one_without_queueing(self):
        serve.state.resources['activeRenders'] = 1
        serve.state.active = 1
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request()
        self.assertEqual(caught.exception.code, 429)
        caught.exception.close()
        self.assertEqual(serve.state.active, 1)
