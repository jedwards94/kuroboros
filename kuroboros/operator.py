import threading
import time
from typing import Dict, List, cast
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


class Operator:
    __METRICS_INTERVAL = float(
        kuroboros_config.getfloat(
            "operator", "metrics_update_interval_seconds", fallback=5.0
        )
    )
    __METRICS_PORT = int(
        kuroboros_config.getint("operator", "metrics_port", fallback=8080)
    )
    __running = False
    __uid = str(uuid.uuid4())
    __logger = logger.root_logger.getChild(__name__)
    __is_leader = threading.Event()
    __threads_by_reconciler: Dict[BaseReconciler, Gauge] = {}

    __namespace = OPERATOR_NAMESPACE
    name = get_operator_name()
    
    __controllers: List[Controller] = []

    def __init__(self) -> None:
        self.__logger = self.__logger.getChild(self.name)
        try:
            config.load_kube_config()
        except Exception:
            config.load_incluster_config()
        pass

    def add_controller(self, name:str, group_version: GroupVersionInfo, reconciler: BaseReconciler):
        if self.__running:
            raise RuntimeError("cannot add reconciler while operator is running")
        
        controller = Controller(
            name=name,
            group_version_info=group_version,
            reconciler=reconciler,
        )
        if controller in self.__controllers:
            raise RuntimeError("cannot add an already added reconciller")

        self.__threads_by_reconciler[controller.reconciler] = Gauge(
            "kuroboros_python_threads_by_reconciler",
            "The number of threads running by the CRD controller",
            labelnames=["namespace", "reconciler"],
        )
        
        self.__controllers.append(controller)



    def __acquire_leader_lease(self):
        api = client.CoordinationV1Api()
        lease_name = f"{self.name}-leader"
        lease_duration = 10
        self.__logger.info(f"trying to acquire leadership with uid: {self.__uid}")
        while True:
            try:
                lease = api.read_namespaced_lease(
                    name=lease_name, namespace=self.__namespace
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
                            holder_identity=self.__uid,
                        ),
                    )
                    api.create_namespaced_lease(namespace=self.__namespace, body=lease)
                    if not self.__is_leader.is_set():
                        self.__logger.info(
                            f"leadership acquired under uid {self.__uid}"
                        )
                        self.__is_leader.set()
                    continue

                else:
                    self.__logger.error(
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
            if lease_expired or lease_data.spec.holder_identity == self.__uid:
                lease_data.spec.renew_time = time.strftime(
                    "%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime()
                )
                lease_data.spec.holder_identity = self.__uid
                lease_data.spec.lease_duration_seconds = lease_duration
                api.replace_namespaced_lease(
                    name=lease_name, namespace=self.__namespace, body=lease_data
                )
                if not self.__is_leader.is_set():
                    self.__logger.info(f"leadership acquired under uid {self.__uid}")
                    self.__is_leader.set()

            time.sleep(
                kuroboros_config.getfloat(
                    "operator", "leader_acquire_interval_seconds", fallback=10.0
                )
            )

    def __metrics(self) -> None:
        while True:
            for ctrl in self.__controllers:
                metric = self.__threads_by_reconciler[ctrl.reconciler]
                metric.labels(OPERATOR_NAMESPACE, ctrl.reconciler.__class__.__name__).set(
                    ctrl.threads
                )
            time.sleep(self.__METRICS_INTERVAL)

    def start(self):
        if self.__running:
            raise RuntimeError("cannot start an already started Operator")
        
        if len(self.__controllers) == 0:
            raise RuntimeError("no controllers found to run the operator")

        try:
            start_http_server(self.__METRICS_PORT)
        except Exception:
            pass

        self.__is_leader.clear()
        leader_election = threading.Thread(target=self.__acquire_leader_lease)
        leader_election.start()
        while not self.__is_leader.is_set():
            if not leader_election.is_alive():
                raise RuntimeError("leader election loop died while trying to acquire leadership")
            continue

        metrics_loop = threading.Thread(target=self.__metrics)

        for ctrl in self.__controllers:
            ctrl.run()

        metrics_loop.start()
        self.__running = True
