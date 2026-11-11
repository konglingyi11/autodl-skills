"""Local same-origin transport checks with synthetic credentials only."""
import json
import importlib.util
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from autodl_instance import Client, HTTP_CHROME_MAJOR


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args):
        pass
    def do_GET(self):
        if self.path == '/':
            body = b'<html><body>fixture</body></html>'
            self.send_response(200)
            self.send_header('Set-Cookie', 'fixture=local; Path=/; SameSite=Lax')
            self.send_header('Content-Type', 'text/html')
        else:
            body = json.dumps({'code': 'Success', 'data': {
                'user_agent': self.headers.get('User-Agent'),
                'client_hints': self.headers.get('Sec-CH-UA'),
                'platform': self.headers.get('Sec-CH-UA-Platform'),
                'language': self.headers.get('Accept-Language'),
                'cookie_present': self.headers.get('Cookie') == 'fixture=local',
                'auth_present': self.headers.get('Authorization') == 'synthetic-token',
                'protocol': self.request_version,
            }}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        body = json.dumps({'code': 'Success', 'data': {
            'payload': payload,
            'cookie_present': self.headers.get('Cookie') == 'fixture=local',
            'auth_present': self.headers.get('Authorization') == 'synthetic-token',
        }}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'Playwright is optional for sub-account use')
class LocalTransportTests(unittest.TestCase):
    def test_browser_fetch_and_http_identity(self):
        from playwright.sync_api import sync_playwright
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = 'http://127.0.0.1:%d' % server.server_port
        client = Client()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={'width': 1280, 'height': 900})
                    page.goto(origin)
                    page.evaluate("""() => {
                        localStorage.setItem('token', 'synthetic-token');
                        localStorage.setItem('app_version', JSON.stringify({version:'v-fixture'}));
                    }""")
                    client.page = page
                    with patch('autodl_instance.BASE', origin):
                        observed = client._request('GET', '/api/v1/fixture')
                        posted = client._request('POST', '/api/v1/instance', {'page_index': 1})
                    self.assertTrue(observed['cookie_present'])
                    self.assertTrue(observed['auth_present'])
                    self.assertEqual(posted, {'payload': {'page_index': 1},
                                               'cookie_present': True, 'auth_present': True})
                    self.assertIn('Chrom', observed['user_agent'])
                    client.page = None
                    client.session.get(origin)
                    http = client.session.get(origin + '/api/v1/fixture').json()['data']
                    self.assertTrue(http['cookie_present'])
                    self.assertIn('Chrome/' + HTTP_CHROME_MAJOR + '.', http['user_agent'])
                    self.assertIn('v="' + HTTP_CHROME_MAJOR + '"', http['client_hints'])
                    self.assertEqual(http['platform'], '"Windows"')
                    self.assertEqual(observed['protocol'], 'HTTP/1.1')
                finally:
                    browser.close()
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join()
