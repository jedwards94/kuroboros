from typing import Generic, Type, TypeVar, get_args, get_origin
import threading
from logging import Logger
from kubernetes import client

from kuroboros.exceptions import RetriableException, UnrecoverableException
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.logger import root_logger, reconciler_logger
from kuroboros.schema import BaseCRD
from kuroboros.utils import event_aware_sleep, with_timeout
from datetime import timedelta

T = TypeVar("T", bound=BaseCRD)


class BaseReconciler(Generic[T]):

    reconcile_timeout: timedelta | None = None
    timeout_retry: bool = False
    timeout_requeue_time: timedelta | None = timedelta(minutes=5)

    __api: client.CustomObjectsApi

    _type: Type[T]
    _logger = root_logger.getChild(__name__)

    _group_version_info: GroupVersionInfo

    def __init__(self, group_version: GroupVersionInfo):
        self._logger = self._logger.getChild(self.__class__.__name__)
        self._group_version_info = group_version
        t_type = None
        for base in getattr(self.__class__, "__orig_bases__", []):
            origin = get_origin(base)
            if origin is BaseReconciler:
                t_type = get_args(base)[0]
                break

        if t_type is None or BaseCRD not in t_type.__mro__:
            raise RuntimeError(
                "Could not determine generic type T. "
                "Subclass BaseReconciler with a concrete type (e.g., `class MyReconciler(BaseReconciler[MyCRD]): ...`)"
            )

        self._type = t_type

    @property
    def api(self):
        return self.__api

    @api.setter
    def api(self, value):
        self.__api = value

    @property
    def crd_type(self) -> Type[T]:
        return self._type

    def reconcilation_loop(self, object: T, stop: threading.Event):
        """
        Runs the reconciliation loop of every object
        while its a member of the `Controller`
        """
        interval = None
        while not stop.is_set():
            try:
                latest = self.__api.get_namespaced_custom_object(
                    group=self._group_version_info.group,
                    version=self._group_version_info.api_version,
                    name=object.name,
                    namespace=object.namespace,
                    plural=self._group_version_info.plural,
                )
                inst = self._type(
                    api=self.__api, group_version=self._group_version_info
                )
                inst.load_data(latest)
                inst_logger, filt = reconciler_logger(self._group_version_info, inst)
                if self.reconcile_timeout is None:
                    interval = self.reconcile(logger=inst_logger, object=inst, stopped=stop)
                else:
                    interval = with_timeout(
                        stop,
                        self.timeout_retry,
                        self.reconcile_timeout.total_seconds(),
                        self.reconcile,
                        logger=inst_logger,
                        object=inst,
                        stopped=stop
                    )
                inst_logger.removeFilter(filt)

            except client.ApiException as e:
                if e.status == 404:
                    self._logger.info(f"{object} no longer found, killing thread")
                else:
                    self._logger.fatal(
                        f"A `APIException` ocurred while proccessing {object}: {e}",
                        exc_info=True,
                    )
            except UnrecoverableException as e:
                self._logger.fatal(
                    f"A `UnrecoverableException` ocurred while proccessing {object}: {e}",
                    exc_info=True,
                )
            except RetriableException as e:
                self._logger.warning(
                    f"A `RetriableException` ocurred while proccessing {object}: {e}",
                )
                interval = e.backoff
            except TimeoutError as e:
                self._logger.warning(
                    f"A `TimeoutError` ocurred while proccessing {object}: {e}",
                )
                if not self.timeout_retry:
                    self._logger.warning(
                        f"`TimeoutError` will not be retried. To retry, enable `timeout_rety = True` in {self.__class__.__name__}",
                    )
                else:
                    interval = self.timeout_requeue_time
            except Exception as e:
                self._logger.error(
                    f"An `Exception` ocurred while proccessing {object}: {e}",
                    exc_info=True,
                )
            finally:
                if interval is not None:
                    assert isinstance(interval, timedelta)
                    event_aware_sleep(stop, interval.total_seconds())
                else:
                    break
        self._logger.info(f"{object} reconcile loop stopped")
        return

    def reconcile(self, logger: Logger, object: T, stopped: threading.Event) -> None | timedelta:
        """
        The function that reconcile the object to the desired status.
        Returns `None` or `timedelta`. A `timedelta` represent the interval for the next `reconcile` run,  `None` represent the end of the loop.

        :param logger: The python logger with `name`, `namespace_name` and `resource_version` pre-loaded
        :param object: The CRD instance at the run moment
        :param stopped: The reconciliation loop event that signal a stop
        :returns interval (`timedelta`|`None`): The amount of time that the controller waits to run the `reconcile` function again.
        If its `None` it will never run again until further updates or a controller restart
        """
        pass
