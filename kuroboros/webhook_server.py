from http.server import BaseHTTPRequestHandler, HTTPServer
from logging import Logger
from typing import Any, Callable, Dict
from kuroboros import logger


class WebhookHandler(BaseHTTPRequestHandler):
    # endpoints: Dict[str, Callable] will be set on the class
    endpoints: Dict[str, Callable] = {}
    _logger: Logger = logger.root_logger.getChild(__name__)
    
    def log_message(self, format: str, *args: Any) -> None:
        self._logger.info(f"{self.client_address[0]} - - {format % args}")

    def do_POST(self):
        handler = self.endpoints.get(self.path)
        if handler:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length) if content_length > 0 else b''
            response, status, headers = handler(body)
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(response)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Endpoint not found')


class HTTPSWebhookServer:
    cert_file: str
    key_file: str
    port: int
    host: str
    _handler = WebhookHandler
    _server: HTTPServer
    _logger = logger.root_logger.getChild(__name__)

    def __init__(self, cert_file: str, key_file: str, endpoints: Dict[str, Callable], port: int = 443, host: str = "0.0.0.0") -> None:
        self.cert_file = cert_file
        self.key_file = key_file
        self.port = port
        self.host = host
        self._handler.endpoints = endpoints
        self._server = HTTPServer((self.host, self.port), WebhookHandler)

    def start(self) -> None:
        import ssl
        self._logger.info(f"Starting webhook server on {self.host}:{self.port} with cert {self.cert_file} and key {self.key_file}")
        try:
            # Wrap the socket with SSL
            self._server.socket = ssl.wrap_socket(
                self._server.socket,
                server_side=True,
                certfile=self.cert_file,
                keyfile=self.key_file,
                ssl_version=ssl.PROTOCOL_TLS_SERVER
            )
            self._server.serve_forever()
        except Exception as e:
            self._logger.error(f"Error starting webhook server: {e}")
        finally:
            self._server.server_close()
            self._logger.info("Webhook server closed")

