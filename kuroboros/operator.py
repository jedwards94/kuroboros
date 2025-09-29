import logging
import multiprocessing
import signal
import sys
import threading
import time
from typing import Dict, List, Tuple, Type, cast
import uuid

import caseconverter
from kubernetes import client, config
from prometheus_client import Gauge, start_http_server

from kuroboros import logger
from kuroboros.config import OPERATOR_NAMESPACE, KuroborosConfig
from kuroboros.controller import Controller, ControllerConfig
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.reconciler import BaseReconciler
from kuroboros.utils import event_aware_sleep
from kuroboros.webhook import BaseMutationWebhook, BaseValidationWebhook, BaseWebhook
from kuroboros.webhook_server import HTTPSWebhookServer


class Operator:
    """
    The kuroboros Operator, it collect metrics, acquiere leadership,
    start the webhook server and start the controllers reconcilation loops
    """

    _leader_interval: float
    _metrics_interval: float
    _metrics_port: int
    _cert_path: str
    _key_path: str
    _webhook_port: int
    _gunicorn_workers: int
    _running: bool
    _uid: str
    _logger: logging.Logger
    _is_leader: threading.Event
    _threads_by_reconciler: Gauge
    _python_threads: Gauge
    _stop: threading.Event
    _threads: List[threading.Thread]
    _webhook_server_process: multiprocessing.Process | None
    _interrupted = False

    _namespace: str
    _controllers: List[Controller]
    _controller_threads: Dict[Controller, Tuple[threading.Thread, threading.Thread]] = (
        {}
    )

    name: str

    def __init__(self) -> None:
        self.name = KuroborosConfig.get("operator", "name", typ=str)
        self._leader_interval = KuroborosConfig.get(
            "operator", "leader_acquire_interval_seconds", typ=float
        )
        self._metrics_interval = KuroborosConfig.get(
            "operator", "metrics", "interval_seconds", typ=float
        )
        self._metrics_port = KuroborosConfig.get("operator", "metrics", "port", typ=int)
        self._cert_path = KuroborosConfig.get(
            "operator", "webhook_server", "cert_path", typ=str
        )
        self._key_path = KuroborosConfig.get(
            "operator", "webhook_server", "key_path", typ=str
        )
        self._gunicorn_workers = KuroborosConfig.get(
            "operator", "webhook_server", "gunicorn_workers", typ=int
        )
        self._webhook_port = KuroborosConfig.get(
            "operator", "webhook_server", "port", typ=int
        )
        self._threads_by_reconciler = Gauge(
            "kuroboros_python_threads_by_reconciler",
            "The number of threads running by the CRD controller",
            labelnames=["namespace", "reconciler"],
        )
        self._python_threads = Gauge(
            "python_active_threads", "The number of active python threads"
        )
        self._controllers = []
        self._threads = []
        self._webhook_server_process = None
        self._running = False
        self._is_leader = threading.Event()
        self._uid = str(uuid.uuid4())
        self._namespace = OPERATOR_NAMESPACE
        self._logger = logger.root_logger.getChild(__name__).getChild(
            caseconverter.pascalcase(self.name)
        )
        self._stop = threading.Event()
        try:
            config.load_kube_config()
        except Exception:  # pylint: disable=broad-except
            config.load_incluster_config()

    def is_leader(self) -> bool:
        """
        Check if the leader event is set
        """
        return self._is_leader.is_set()

    def is_running(self) -> bool:
        """
        Checks that the operator is running
        """
        return self._running

    @property
    def namespace(self) -> str:
        """
        Get the opeartor namespace (if any)
        """
        return self._namespace

    @property
    def uid(self) -> str:
        """
        Get the random UID generated for the Opeartor
        """
        return self._uid

    @property
    def controllers(self) -> List[Controller]:
        """
        Get a copy of the Controllers in tyhe Operator
        """
        return self._controllers.copy()

    def _add_controller(
        self,
        name: str,
        group_version: GroupVersionInfo,
        reconciler: Type[BaseReconciler],
        validation_webhook: Type[BaseValidationWebhook] | None = None,
        mutation_webhook: Type[BaseMutationWebhook] | None = None,
    ) -> None:
        if self.is_running():
            raise RuntimeError("cannot add controller while operator is running")

        controller = Controller(
            name=name,
            group_version_info=group_version,
            reconciler=reconciler,
            validation_webhook=validation_webhook,
            mutation_webhook=mutation_webhook,
        )
        if controller.name in [ctrl.name for ctrl in self._controllers]:
            raise RuntimeError("cannot add an already added controller")

        self._controllers.append(controller)

    def _acquire_leader_lease(self):
        api = client.CoordinationV1Api()
        lease_name = f"{caseconverter.kebabcase(self.name)}-leader"
        lease_duration = 10
        self._logger.info(f"trying to acquire leadership with uid: {self._uid}")
        while not self._stop.is_set():
            try:
                lease = api.read_namespaced_lease(
                    name=lease_name, namespace=self._namespace
                )
            except client.ApiException as e:
                if e.status == 404:
                    lease = client.V1Lease(
                        metadata=client.V1ObjectMeta(name=lease_name),
                        spec=client.V1LeaseSpec(
                            renew_time=time.strftime(
                                "%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime()
                            ),
                            lease_duration_seconds=lease_duration,
                            holder_identity=self._uid,
                        ),
                    )
                    api.create_namespaced_lease(namespace=self._namespace, body=lease)
                    if not self.is_leader():
                        self._logger.info(f"leadership acquired under uid {self._uid}")
                        self._is_leader.set()

                else:
                    self._logger.error(
                        f"error while trying to acquire leadership lease: {e}",
                        exc_info=True,
                    )
                    raise RuntimeError("Error while acquiring leadership") from e
            lease_data: client.V1Lease = cast(client.V1Lease, lease)
            if lease_data.spec is None:
                raise RuntimeError("Unexpected empty lease.spec")
            current_time = time.time()
            renew_time = lease_data.spec.renew_time.timestamp()
            lease_expired = (
                current_time > renew_time + lease_data.spec.lease_duration_seconds
            )
            if lease_expired or lease_data.spec.holder_identity == self._uid:
                lease_data.spec.renew_time = time.strftime(
                    "%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime()
                )
                lease_data.spec.holder_identity = self._uid
                lease_data.spec.lease_duration_seconds = lease_duration
                api.replace_namespaced_lease(
                    name=lease_name, namespace=self._namespace, body=lease_data
                )
                if not self._is_leader.is_set():
                    self._logger.info(f"leadership acquired under uid {self._uid}")
                    self._is_leader.set()

            time.sleep(self._leader_interval)

    def _metrics(self) -> None:

        while not self._stop.is_set():
            for ctrl in self._controllers:
                self._threads_by_reconciler.labels(
                    OPERATOR_NAMESPACE, ctrl.reconciler.__name__
                ).set(ctrl.threads)
            self._python_threads.set(threading.active_count())
            event_aware_sleep(self._stop, self._metrics_interval)

    def start(
        self,
        controllers: List[ControllerConfig],
        skip_controllers: bool = False,
        skip_webhook_server: bool = False,
    ) -> None:
        """
        Starts the opearator an the services after acquiring leadership of the CRs
        """

        if skip_controllers and skip_webhook_server:
            raise RuntimeError(
                "cannot start operator without running controllers or webhook server"
            )
        if self._running:
            raise RuntimeError("cannot start an already started Operator")

        if len(controllers) == 0:
            raise RuntimeError("no controllers found to run the operator")

        # Add Controllers from controller configs
        for ctrl in controllers:
            self._logger.debug(f"adding {ctrl.name} controller")
            run_version = ctrl.get_run_version()
            if run_version.reconciler is None:
                raise RuntimeError(
                    f"reconciler `None` in {ctrl.name} {run_version.name}"
                )

            try:
                self._add_controller(
                    name=ctrl.name,
                    group_version=ctrl.group_version_info,
                    reconciler=run_version.reconciler,
                    validation_webhook=run_version.validation_webhook,
                    mutation_webhook=run_version.mutation_webhook,
                )

            except RuntimeError as e:
                self._logger.warning(e)
                continue

        # Start the webhook server if needed
        if not skip_webhook_server:
            webhooks: List[BaseWebhook] = []
            for ctrl in self._controllers:
                if ctrl.validation_webhook is not None:
                    webhooks.append(ctrl.validation_webhook)
                if ctrl.mutation_webhook is not None:
                    webhooks.append(ctrl.mutation_webhook)

            if len(webhooks) > 0:
                webhook_server = HTTPSWebhookServer(
                    cert_file=self._cert_path,
                    key_file=self._key_path,
                    endpoints=webhooks,
                    port=self._webhook_port,
                    workers=self._gunicorn_workers
                )
                self._webhook_server_process = multiprocessing.Process(
                    target=webhook_server.start,
                    name=f"{self.name}-webhook-server-process",
                )
                self._webhook_server_process.start()

        # Start controllers
        if not skip_controllers:
            self._is_leader.clear()
            leader_election = threading.Thread(
                target=self._acquire_leader_lease,
                name=f"{self.name}::LeaderElection",
                daemon=True,
            )
            self._threads.append(leader_election)
            leader_election.start()
            while not self.is_leader():
                if not leader_election.is_alive():
                    raise RuntimeError(
                        "leader election loop died while trying to acquire leadership"
                    )
                continue

            for ctrl in self._controllers:
                ctrl_threads = ctrl.run()
                self._controller_threads[ctrl] = ctrl_threads

        # start metrics maybe
        try:
            start_http_server(self._metrics_port)
        except Exception:  # pylint: disable=broad-except
            self._logger.warning("metrics http server could not be started")

        metrics_loop = threading.Thread(
            target=self._metrics, name=f"{self.name}::Metrics", daemon=True
        )
        metrics_loop.start()
        self._threads.append(metrics_loop)
        self._running = True

        # handle stop
        signal.signal(signal.SIGINT, self.signal_stop)
        signal.signal(signal.SIGTERM, self.signal_stop)

        # handle crash
        while self._running:
            for thread in self._threads:
                if not thread.is_alive():
                    self._logger.error(f"Thread {thread.name} died unexpectedly")
                    raise threading.ThreadError("Death thread")

            if (
                self._webhook_server_process is not None
                and not self._webhook_server_process.is_alive()
            ):
                self._logger.error("Webhook Server Process died unexpectedly")
                raise multiprocessing.ProcessError("Death process")

            if not skip_controllers:
                for ctrl in self._controllers:
                    for thread in self._controller_threads[ctrl]:
                        if not thread.is_alive():
                            self._logger.error(
                                f"Controller {ctrl.name} thread {thread.name} died unexpectedly"
                            )
                            raise threading.ThreadError("Death thread")
            event_aware_sleep(self._stop, 1)

        self._logger.info("good bye!")

    def signal_stop(self, sig, _):
        """
        Starts the shutdown process on a SIGINT, sending stop events to every component and
        ppropagating the event to every thread
        """
        self._logger.warning(f"{signal.Signals(sig).name} received")
        if sig == 2:  # SIGINT
            if self._interrupted:
                self._logger.warning("second SIGINT received, killing process")
                sys.exit(1)
            self._interrupted = True
            self._logger.warning("trying to gracefully shutdown...")
            for ctrl in self._controllers:
                ctrl.stop()

            alive_threads = self._threads
            self._logger.debug(
                f"waiting for {len(alive_threads)} threads in the operator to stop..."
            )
            self._stop.set()
            while len(alive_threads) > 0:
                alive_threads = [
                    thread
                    for thread in self._threads
                    if thread.is_alive() and isinstance(thread, threading.Thread)
                ]
                time.sleep(0.5)

        self._running = False
