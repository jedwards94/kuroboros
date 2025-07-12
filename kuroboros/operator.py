import multiprocessing
import threading
import time
from typing import Dict, List, Tuple, cast
import uuid

from kubernetes import client, config
from prometheus_client import Gauge, start_http_server

from kuroboros import logger
from kuroboros.config import (
    get_operator_name,
    OPERATOR_NAMESPACE,
    config as kuroboros_config,
)
from kuroboros.controller import Controller
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.reconciler import BaseReconciler 
from kuroboros.webhook_server import HTTPSWebhookServer


class Operator:
    __METRICS_INTERVAL = float(
        kuroboros_config.getfloat(
            "operator", "metrics_update_interval_seconds", fallback=5.0
        )
    )
    __METRICS_PORT = int(
        kuroboros_config.getint("operator", "metrics_port", fallback=8080)
    )
    __CERT_PATH = kuroboros_config.get(
        "operator", "cert_path", fallback="/etc/tls/tls.crt"
    )
    __KEY_PATH = kuroboros_config.get("operator", "key_path", fallback="/etc/tls/tls.key")
    __WEBHOOK_PORT = int(
        kuroboros_config.getint("operator", "webhook_port", fallback=443)
    )
    _running: bool
    _uid: str
    _logger = logger.root_logger.getChild(__name__)
    _is_leader: threading.Event
    _threads_by_reconciler: Dict[BaseReconciler, Gauge]

    _namespace: str
    name = get_operator_name()

    _controllers: List[Controller]
    _controller_threads: Dict[Controller, Tuple[threading.Thread, threading.Thread]] = (
        {}
    )

    def __init__(self) -> None:
        self._threads_by_reconciler = {}
        self._is_leader = threading.Event()
        self._running = False
        self._uid = str(uuid.uuid4())
        self._namespace = OPERATOR_NAMESPACE
        self._logger = self._logger.getChild(self.name)
        self._controllers = []
        try:
            config.load_kube_config()
        except Exception:
            config.load_incluster_config()
        pass

    def is_leader(self) -> bool:
        return self._is_leader.is_set()

    def is_running(self) -> bool:
        return self._running

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def uid(self) -> str:
        return self._uid

    @property
    def controllers(self) -> List[Controller]:
        return self._controllers.copy()

    def add_controller(
        self,
        name: str,
        group_version: GroupVersionInfo,
        reconciler: BaseReconciler,
        **kwargs,
    ) -> None:
        if self.is_running():
            raise RuntimeError("cannot add controller while operator is running")

        controller = Controller(
            name=name,
            group_version_info=group_version,
            reconciler=reconciler,
            validation_webhook=kwargs.get("validation_webhook", None),
        )
        if controller.name in [ctrl.name for ctrl in self._controllers]:
            raise RuntimeError("cannot add an already added controller")

        self._threads_by_reconciler[controller.reconciler] = Gauge(
            "kuroboros_python_threads_by_reconciler",
            "The number of threads running by the CRD controller",
            labelnames=["namespace", "reconciler"],
        )

        self._controllers.append(controller)

    def _acquire_leader_lease(self):
        api = client.CoordinationV1Api()
        lease_name = f"{self.name}-leader"
        lease_duration = 10
        self._logger.info(f"trying to acquire leadership with uid: {self._uid}")
        while True:
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
                    continue

                else:
                    self._logger.error(
                        "error while trying to acquire leadership lease",
                        e,
                        exc_info=True,
                    )
                    raise RuntimeError("Error while acquiring leadership")
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

            time.sleep(
                kuroboros_config.getfloat(
                    "operator", "leader_acquire_interval_seconds", fallback=10.0
                )
            )

    def _metrics(self) -> None:
        while True:
            for ctrl in self._controllers:
                metric = self._threads_by_reconciler[ctrl.reconciler]
                metric.labels(
                    OPERATOR_NAMESPACE, ctrl.reconciler.__class__.__name__
                ).set(ctrl.threads)
            time.sleep(self.__METRICS_INTERVAL)

    def start(
        self, skip_controllers: bool = False, skip_webhook_server: bool = False
    ) -> None:
        if skip_controllers and skip_webhook_server:
            raise RuntimeError(
                "cannot start operator without running controllers or webhook server"
            )
        if self._running:
            raise RuntimeError("cannot start an already started Operator")

        if len(self._controllers) == 0:
            raise RuntimeError("no controllers found to run the operator")

        op_threads = []

        if not skip_controllers:
            self._is_leader.clear()
            leader_election = threading.Thread(
                target=self._acquire_leader_lease,
                name=f"{self.name}-leader-election",
                daemon=True,
            )
            op_threads.append(leader_election)
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


        # Start the webhook server if needed
        if not skip_webhook_server:
            webhooks = []
            for ctrl in self._controllers:
                if ctrl.validation_webhook is not None:
                    webhooks.append(ctrl.validation_webhook)
                    

            if len(webhooks) > 0:
                webhook_server = HTTPSWebhookServer(
                    cert_file=self.__CERT_PATH,
                    key_file=self.__KEY_PATH,
                    endpoints=webhooks,
                    port=self.__WEBHOOK_PORT,
                )
                webhook_server_process = multiprocessing.Process(
                    target=webhook_server.start,
                    name=f"{self.name}-webhook-server-process",
                )
                webhook_server_process.start()
                op_threads.append(webhook_server_process)

        try:
            start_http_server(self.__METRICS_PORT)
        except Exception:
            pass
        metrics_loop = threading.Thread(
            target=self._metrics, name=f"{self.name}-metrics", daemon=True
        )
        metrics_loop.start()
        op_threads.append(metrics_loop)

        self._running = True

        while self._running:
            for thread in op_threads:
                if not thread.is_alive():
                    self._logger.error(f"Thread {thread.name} died unexpectedly")
                    raise RuntimeError(f"Thread {thread.name} died unexpectedly")

            if not skip_controllers:
                for ctrl in self._controllers:
                    for thread in self._controller_threads[ctrl]:
                        if not thread.is_alive():
                            self._logger.error(
                                f"Controller {ctrl.name} thread {thread.name} died unexpectedly"
                            )
                            raise RuntimeError(
                                f"Controller {ctrl.name} thread {thread.name} died unexpectedly"
                            )
            time.sleep(1.0)
