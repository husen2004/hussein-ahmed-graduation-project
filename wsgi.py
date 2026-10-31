"""WSGI adapter for PythonAnywhere.

This keeps the existing BaseHTTPRequestHandler-based application unchanged and
adapts each WSGI request into the request format expected by server.APIHandler.
"""
import io
import os
import sys
import traceback
from http import HTTPStatus

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Safe defaults for the hosted WSGI deployment. The PythonAnywhere WSGI file
# may override these values before importing this module.
os.environ.setdefault('AFAQ_CLOUD', '1')
os.environ.setdefault('AFAQ_SQLITE_WAL', '1')
os.environ.setdefault('AFAQ_DB_PATH', os.path.join(BASE_DIR, 'database', 'students.db'))
os.environ.setdefault('AFAQ_BACKUP_DIR', os.path.join(BASE_DIR, 'database', 'backups'))

from server import APIHandler  # noqa: E402
from database.init_db import init_database  # noqa: E402

init_database()


class _KeepOpenBytesIO(io.BytesIO):
    def close(self):
        # BaseHTTPRequestHandler closes wfile at the end; keep bytes readable.
        self.flush()


class _FakeSocket:
    def __init__(self, request_bytes):
        self.rfile = io.BytesIO(request_bytes)
        self.wfile = _KeepOpenBytesIO()

    def makefile(self, mode, buffering=None):
        return self.rfile if 'r' in mode else self.wfile

    def sendall(self, data):
        self.wfile.write(data)

    def close(self):
        pass


def _build_raw_request(environ):
    method = environ.get('REQUEST_METHOD', 'GET')
    path = environ.get('PATH_INFO', '/') or '/'
    query = environ.get('QUERY_STRING', '')
    target = path + (('?' + query) if query else '')

    body = environ.get('wsgi.input', io.BytesIO()).read()
    headers = []
    host = environ.get('HTTP_HOST') or environ.get('SERVER_NAME', 'localhost')
    headers.append(('Host', host))

    for key, value in environ.items():
        if key.startswith('HTTP_'):
            name = key[5:].replace('_', '-').title()
            if name.lower() == 'host':
                continue
            headers.append((name, str(value)))

    if environ.get('CONTENT_TYPE'):
        headers.append(('Content-Type', environ['CONTENT_TYPE']))
    if environ.get('CONTENT_LENGTH'):
        headers.append(('Content-Length', environ['CONTENT_LENGTH']))
    elif body:
        headers.append(('Content-Length', str(len(body))))

    head = f"{method} {target} HTTP/1.1\r\n".encode('latin-1')
    for name, value in headers:
        head += f"{name}: {value}\r\n".encode('latin-1')
    return head + b"\r\n" + body


def _run_handler(raw_request, client_address=('127.0.0.1', 0)):
    sock = _FakeSocket(raw_request)
    APIHandler(sock, client_address, None)
    return sock.wfile.getvalue()


def _parse_response(raw):
    header_blob, sep, body = raw.partition(b'\r\n\r\n')
    if not sep:
        return '500 Internal Server Error', [('Content-Type', 'text/plain; charset=utf-8')], b'Invalid response from application'

    lines = header_blob.split(b'\r\n')
    status_line = lines[0].decode('latin-1', 'replace')
    parts = status_line.split(' ', 2)
    code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 500
    phrase = parts[2] if len(parts) > 2 else HTTPStatus(code).phrase
    status = f"{code} {phrase}"

    headers = []
    for line in lines[1:]:
        if b':' not in line:
            continue
        name, value = line.split(b':', 1)
        name = name.decode('latin-1').strip()
        value = value.decode('latin-1').strip()
        # WSGI/server will manage connection framing.
        if name.lower() in ('connection', 'transfer-encoding'):
            continue
        headers.append((name, value))
    return status, headers, body


def application(environ, start_response):
    try:
        raw_request = _build_raw_request(environ)
        client = (environ.get('REMOTE_ADDR', '127.0.0.1'), 0)
        raw_response = _run_handler(raw_request, client)
        status, headers, body = _parse_response(raw_response)
        start_response(status, headers)
        return [body]
    except Exception as exc:
        traceback.print_exc()
        body = b"Internal Server Error"
        start_response('500 Internal Server Error', [
            ('Content-Type', 'text/plain; charset=utf-8'),
            ('Content-Length', str(len(body))),
        ])
        return [body]
