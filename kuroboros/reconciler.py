from typing import (
    ClassVar,
    Generic,
    Type,
    TypeVar,
    get_args,
    get_origin,
)
import threading
from datetime import timedelta
from logging import Logger
import caseconverter
from kubernetes import client

from kuroboros.exceptions import RetriableException, UnrecoverableException
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.logger import root_logger, reconciler_logger
from kuroboros.schema import BaseCRD
from kuroboros.utils import NamespaceName, event_aware_sleep, with_timeout

T = TypeVar("T", bound=BaseCRD)


class BaseReconciler(Generic[T]):
    """
    The base Reconciler.
    This class perform the reconcilation logic in `reconcile`
    """

    __group_version_info: ClassVar[GroupVersionInfo]
    _logger = root_logger.getChild(__name__)
    _stop: threading.Event
    _running: bool
    _loop_thread: threading.Thread
    _namespace_name: NamespaceName

    reconcile_timeout: timedelta | None = None
    timeout_retry: bool = False
    timeout_requeue_time: timedelta | None = timedelta(minutes=5)

    api: client.CustomObjectsApi
    crd_inst: T
    name: str

    @classmethod
    def crd_type(cls) -> Type[T]:
        """
        Return the class of the CRD
        """
        t_type = None
        for base in getattr(cls, "__orig_bases__", []):
            origin = get_origin(base)
            if origin is BaseReconciler:
                t_type = get_args(base)[0]
                break

        if t_type is None or BaseCRD not in t_type.__mro__:
            raise RuntimeError(
                "Could not determine generic type T. "
                "Subclass BaseReconciler with a concrete CRD type"
            )

        return t_type

    @classmethod
    def set_gvi(cls, gvi: GroupVersionInfo) -> None:
        """
        Sets the GroupVersionInfo of the Reconciler
        """
        cls.__group_version_info = gvi

    def __init__(self, namespace_name: NamespaceName):
        self.api = client.CustomObjectsApi()
        self._stop = threading.Event()
        self._running = False
        pretty_version = self.__group_version_info.pretty_version_str()
        self.name = (
            f"{caseconverter.pascalcase(self.__class__.__name__)}{pretty_version}"
        )
        self._logger = self._logger.getChild(self.name)
        self._namespace_name = namespace_name

    def __repr__(self) -> str:
        if self._namespace_name is not None:
            return f"{self.name}(Namespace={self._namespace_name[0]}, Name={self._namespace_name[1]})"
        return f"{self.name}"

    def _load_latest(self, crd: T) -> None:
        namespaced = self.__group_version_info.is_namespaced()
        getter = None
        args = {
            "group": self.__group_version_info.group,
            "version": self.__group_version_info.api_version,
            "name": self._namespace_name[1],
            "plural": self.__group_version_info.plural,
        }

        if namespaced:
            assert self._namespace_name[0] is not None
            args["namespace"] = self._namespace_name[0]
            getter = self.api.get_namespaced_custom_object
        else:
            getter = self.api.get_cluster_custom_object

        latest = getter(**args)
        crd.load_data(latest)

    def reconcilation_loop(self):
        """
        Runs the reconciliation loop of every object
        while its a member of the `Controller`
        """
        interval = None
        crd_inst = self.crd_type()(api=self.api)
        while not self._stop.is_set():
            try:
                self._load_latest(crd_inst)
                inst_logger, filt = reconciler_logger(
                    self.__group_version_info, crd_inst
                )
                if self.reconcile_timeout is None:
                    interval = self.reconcile(
                        logger=inst_logger, obj=crd_inst, stopped=self._stop
                    )
                else:
                    interval = with_timeout(
                        self._stop,
                        self.timeout_retry,
                        self.reconcile_timeout.total_seconds(),
                        self.reconcile,
                        logger=inst_logger,
                        obj=crd_inst,
                        stopped=self._stop,
                    )
                inst_logger.removeFilter(filt)

            except client.ApiException as e:
                if e.status == 404:
                    self._logger.info(e)
                    self._logger.info("%s no longer found, killing thread", crd_inst)
                else:
                    self._logger.fatal(
                        "A `APIException` ocurred while proccessing %s: %s",
                        crd_inst,
                        e,
                        exc_info=True,
                    )
            except UnrecoverableException as e:
                self._logger.fatal(
                    "A `UnrecoverableException` ocurred while proccessing %s: %s",
                    crd_inst,
                    e,
                    exc_info=True,
                )
            except RetriableException as e:
                self._logger.warning(
                    "A `RetriableException` ocurred while proccessing %s: %s",
                    crd_inst,
                    e,
                )
                interval = e.backoff
            except TimeoutError as e:
                self._logger.warning(
                    "A `TimeoutError` ocurred while proccessing %s: %s",
                    crd_inst,
                    e,
                )
                if not self.timeout_retry:
                    self._logger.warning(
                        "`TimeoutError` will not be retried. To retry, enable it in %s",
                        self.__class__.__name__,
                    )
                else:
                    interval = self.timeout_requeue_time
            except Exception as e:  # pylint: disable=broad-exception-caught
                self._logger.error(
                    "An `Exception` ocurred while proccessing %s: %s",
                    crd_inst,
                    e,
                    exc_info=True,
                )

            if interval is not None:
                assert isinstance(interval, timedelta)
                event_aware_sleep(self._stop, interval.total_seconds())
            else:
                break
        self._logger.debug("%s reconcile loop stopped", crd_inst)

    def reconcile(
        self,
        logger: Logger,  # pylint: disable=unused-argument
        obj: T,  # pylint: disable=unused-argument
        stopped: threading.Event,  # pylint: disable=unused-argument
    ) -> None | timedelta:  # pylint: disable=unused-argument
        """
        The function that reconcile the object to the desired status.

        :param logger: The python logger with `name`, `namespace` and `resource_version` pre-loaded
        :param obj: The CRD instance at the run moment
        :param stopped: The reconciliation loop event that signal a stop
        :returns interval (`timedelta`|`None`): Reconcilation interval.
        If its `None` it will never run again until further updates or a controller restart
        """
        return None

    def start(self):
        """
        Starts the reconcilation loop
        """
        if self._running:
            raise RuntimeError(
                "cannot start an already started reconciler",
                f"{self.crd_type().__class__}-{self._namespace_name}",
            )
        loop_thread = threading.Thread(
            target=self.reconcilation_loop,
            daemon=True,
            name=f"{self.name}-{self._namespace_name}",
        )
        loop_thread.start()
        self._running = True
        self._loop_thread = loop_thread

    def stop(self):
        """
        Stops the reconciliation loop
        """
        self._logger.debug("stopping %s thread", self._loop_thread.name)
        if not self.is_running():
            return
        self._stop.set()
        self._running = False

    def is_running(self) -> bool:
        """
        Checks if the reconciler is running
        """
        return self._running and self._loop_thread.is_alive()
