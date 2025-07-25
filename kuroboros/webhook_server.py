from typing import Any, Dict, List

import falcon
from gunicorn import glogging
from gunicorn.app.base import BaseApplication

from kuroboros import logger
from kuroboros.webhook import BaseWebhook



class InjectedLogger(glogging.Logger):
    """
    The injected logger to the GunicornApp
    """
    def setup(self, cfg):
        logg = logger.root_logger.getChild(__name__)
        self.error_log = logg.getChild("gunicorn")
        self.access_log = logg.getChild("gunicorn.access")


class GunicornApp(BaseApplication):
    """
    The GunicornApp that runs the Falcon app
    """
    def __init__(self, app, options=None):
        self.application = app
        self.options = options or {}
        super().__init__()

    def init(self, parser, opts, args):
        # No additional initialization required
        return None

    def load_config(self):
        if self.cfg is not None:
            for key, value in self.options.items():
                self.cfg.set(key.lower(), value)

            self.cfg.set("logger_class", InjectedLogger)

    def access(self, res, req, environ, req_time):
        """
        Access logs
        """
        self.application.access_log.info(
            f"{req.method} {req.path} {res.status} {req_time:.3f}s "
            f"{environ.get('REMOTE_ADDR', 'unknown')}"
        )

    def load(self):
        return self.application


class HTTPSWebhookServer:
    """
    A Falcon app that runs in a Gunicorn app with SSL. 
    This HTTP server will register all webhooks defined in the project controllers
    """
    port: int
    host: str
    _endpoints: List[BaseWebhook]
    _falcon: falcon.App
    _server: GunicornApp
    _server_options: Dict[str, Any]
    _logger = logger.root_logger.getChild(__name__)

    def __init__(
        self,
        cert_file: str,
        key_file: str,
        endpoints: List[BaseWebhook],
        port: int = 443,
        host: str = "0.0.0.0",
        workers: int = 4,
    ) -> None:
        self.port = port
        self.host = host

        self._falcon = falcon.App()
        self._endpoints = endpoints
        self._server_options = {
            "bind": f"{self.host}:{self.port}",
            "workers": workers,
            "certfile": cert_file,
            "keyfile": key_file,
            "worker_class": "sync",
        }
        self._server = GunicornApp(self._falcon, self._server_options)

    def start(self) -> None:
        """
        Adds all the routes to the Falcon app and starts it
        """
        self._logger.info(f"starting webhook server on {self.host}:{self.port}")
        self._logger.info(f"using cert file: {self._server_options.get('certfile')}")
        self._logger.info(f"using key file: {self._server_options.get('keyfile')}")
        for webhook in self._endpoints:
            self._logger.info(
                f"registering endpoint: {webhook.name} at {webhook.endpoint}"
            )
            self._falcon.add_route(webhook.endpoint, webhook)

        self._server.run()
