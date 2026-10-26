#!/usr/bin/env python3
"""Private, dependency-free review player for the frozen Smart Cub candidates."""
import argparse
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import urlsplit


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, root, state):
        self.root = Path(root).resolve()
        self.state = Path(state)
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        manifest = json.loads((self.root / 'manifest.json').read_text())
        self.rows = []
        self.media = {}
        for i, row in enumerate(manifest['videos']):
            path = (self.root / row['path']).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                raise ValueError(f'Invalid selected video: {path}')
            key = '|'.join([row['kind'], row['id'], row['aspect'], row['sha256']])
            self.rows.append({k: row.get(k) for k in ['id', 'title', 'kind', 'aspect', 'sha256',
                                                    'path', 'distribution_restrictions']}
                             | {'key': key, 'media': f'/media/{i}.mp4'})
            self.media[f'/media/{i}.mp4'] = path
        self.keys = {r['key'] for r in self.rows}
        super().__init__(address, Handler)

    def reviews(self):
        return json.loads(self.state.read_text()) if self.state.exists() else {'version': 1, 'reviews': {}}

    def save(self, item):
        if item.get('key') not in self.keys or item.get('status') not in ['unreviewed', 'good', 'fix']:
            raise ValueError('Unknown video or review status')
        if not isinstance(item.get('notes'), str) or len(item['notes']) > 20000:
            raise ValueError('Invalid notes')
        row = {'status': item['status'], 'notes': item['notes'],
               'updatedAt': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        with self.lock:
            data = self.reviews()
            data['reviews'][item['key']] = row
            fd, tmp = tempfile.mkstemp(dir=self.state.parent, prefix='.reviews-')
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, indent=2)
                f.write('\n')
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state)
            with self.state.with_suffix('.history.jsonl').open('a') as f:
                f.write(json.dumps({'key': item['key'], **row}) + '\n')
        return row


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def allowed_host(self):
        return self.headers.get('Host', '') in [f'127.0.0.1:{self.server.server_port}',
                                                f'localhost:{self.server.server_port}']

    def respond(self, body, content_type='application/json; charset=utf-8', status=200, head=False):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        if not self.allowed_host():
            return self.respond({'error': 'Invalid host'}, status=403, head=head)
        route = urlsplit(self.path).path
        if route == '/':
            return self.respond(Path(__file__).with_name('review.html').read_bytes(),
                                'text/html; charset=utf-8', head=head)
        if route == '/api/catalog':
            return self.respond({'videos': self.server.rows}, head=head)
        if route in ['/api/reviews', '/api/export']:
            with self.server.lock:
                data = self.server.reviews()
            if route.endswith('export'):
                data = {**data, 'videos': self.server.rows, 'automaticPublication': False}
            return self.respond(data, head=head)
        path = self.server.media.get(route)
        if path:
            return self.video(path, head)
        return self.respond({'error': 'Not found'}, status=404, head=head)

    def video(self, path, head):
        size = path.stat().st_size
        start, end, partial = 0, size - 1, False
        value = self.headers.get('Range')
        if value:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
            if not match or not any(match.groups()):
                return self.range_error(size)
            a, b = match.groups()
            if a:
                start = int(a)
                end = min(int(b), size - 1) if b else size - 1
            else:
                start = max(0, size - int(b))
            if start > end or start >= size:
                return self.range_error(size)
            partial = True
        count = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(count))
        if partial:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if head:
            return
        try:
            with path.open('rb') as f:
                f.seek(start)
                while count:
                    chunk = f.read(min(count, 512 * 1024))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    count -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def range_error(self, size):
        self.send_response(416)
        self.send_header('Content-Range', f'bytes */{size}')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_POST(self):
        if not self.allowed_host() or self.headers.get('Origin') != 'http://' + self.headers.get('Host', ''):
            return self.respond({'error': 'Invalid origin'}, status=403)
        if urlsplit(self.path).path != '/api/reviews':
            return self.respond({'error': 'Not found'}, status=404)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 100000:
                raise ValueError('Invalid request length')
            data = json.loads(self.rfile.read(size))
            result = self.server.save(data)
        except (ValueError, TypeError, AttributeError) as error:
            return self.respond({'error': str(error)}, status=400)
        self.respond(result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--port', default=18762, type=int)
    args = parser.parse_args()
    ReviewServer(('127.0.0.1', args.port), args.root, args.state).serve_forever()
