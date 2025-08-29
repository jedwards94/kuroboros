from inspect import isclass
import json
from typing import (
    Any,
    ClassVar,
    Generic,
    Type,
    TypeVar,
    cast,
    get_args,
    get_origin,
)
import threading
from datetime import timedelta
from logging import Logger
import caseconverter
from kubernetes import client, dynamic

from kuroboros.exceptions import RetriableException, UnrecoverableException
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.logger import root_logger, reconciler_logger
from kuroboros.schema import BaseCRD
from kuroboros.utils import NamespaceName, event_aware_sleep, with_timeout

T = TypeVar("T", bound=BaseCRD)
R = TypeVar("R", bound=Any)


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
    _api_client: client.ApiClient

    reconcile_timeout: timedelta | None = None
    timeout_retry: bool = False
    timeout_requeue_time: timedelta | None = timedelta(minutes=5)

    crd_inst: T
    name: str
    dynamic_api: dynamic.DynamicClient

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
        self._api_client = client.ApiClient()
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
            ns, n = self._namespace_name
            return f"{self.name}(Namespace={ns}, Name={n})"
        return f"{self.name}"


    def _deserialize(self, obj, typ):
        if isclass(typ) and issubclass(typ, BaseCRD):
            return typ(data=obj.to_dict())
        return self._api_client.deserialize(
            response=type("obj", (object,), {"data": json.dumps(obj.to_dict())}),
            response_type=typ,
        )

    def _api_info_from_class(self, typ: Type):
        api_version = None
        kind = None
        if isclass(typ) and issubclass(typ, BaseCRD):
            gvi = typ.get_gvi()
            assert gvi is not None
            api_version = f"{gvi.group}/{gvi.api_version}"
            kind = gvi.kind

        self._logger.info(typ)
        self._logger.info((api_version, kind))
        return (api_version, kind)

    def reconcilation_loop(self):
        """
        Runs the reconciliation loop of every object
        while its a member of the `Controller`
        """
        interval = None
        while not self._stop.is_set():
            crd_inst = self.crd_type()(api=client.CustomObjectsApi())
            try:
                latest = self.get(
                    name=self._namespace_name[1],
                    api_version=self.__group_version_info.api_version,
                    kind=self.__group_version_info.kind,
                    namespace=self._namespace_name[0],
                    typ=object,
                )
                crd_inst.load_data(latest)
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
        self._logger.debug("%s reconcile loop stopped", self._namespace_name)

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

    def get(
        self,
        name: str,
        api_version: str | None = None,
        kind: str | None = None,
        namespace: str | None = None,
        typ: Type[R] = object,
    ) -> R:
        """
        Gets the resources in the cluster givcen its name and returns it deserialized.
        `api_version` and `kind` can be obtained from `BaseCRD` subclasses

        :param kind: the kind to get
        :param api_version: the target group/version
        :param namespace: the target namespace
        :param name: the target name to retrieve
        :param typ: the return type of the object

        """
        av, k = self._api_info_from_class(typ) or (api_version, kind)
        return cast(
            R,
            self._deserialize(
                self.dynamic_api.resources.get(api_version=av, kind=k).get(
                    name=name, namespace=namespace
                ),
                typ,
            ),
        )

    def get_list(
        self,
        api_version: str | None = None,
        kind: str | None = None,
        namespace: str | None = None,
        typ: Type[R] = object,
        **kwargs,
    ) -> list[R]:
        """
        List the resources in the cluster givcen the kwargs and returns it deserialized.
        `api_version` and `kind` can be obtained from `BaseCRD` subclasses

        :param kind: the kind to list
        :param api_version: the target group/version
        :param namespace: the target namespace
        :param typ: the return type of the list[]
        :param **kwargs: extra arguments given to DynamicClient.get()

        """
        av, k = (
            self._api_info_from_class(typ)
            if (api_version, kind) == (None, None)
            else (api_version, kind)
        )
        return [
            cast(R, self._deserialize(el, typ))
            for el in self.dynamic_api.resources.get(api_version=av, kind=k)
            .get(namespace=namespace, **kwargs)
            .items
        ]

    def create(
        self,
        body: dict,
        kind: str | None = None,
        api_version: str | None = None,
        namespace: str | None = None,
        typ: Type[R] = object,
    ) -> R:
        """
        Creates the resource in the clsuter and returns it deserialized.
        `api_version` and `kind` can be obtained from `BaseCRD` subclasses

        :param body: The camelCased dictonary to create in the cluster
        :param kind: the kind to create
        :param api_version: the target group/version
        :param namespace: the target namespace
        :param typ: the return type
        """
        av, k = (
            self._api_info_from_class(typ)
            if (api_version, kind) == (None, None)
            else (api_version, kind)
        )
        return cast(
            R,
            self._deserialize(
                self.dynamic_api.resources.get(api_version=av, kind=k).create(
                    namespace=namespace, body=body
                ),
                typ,
            ),
        )

    def patch(
        self,
        patch_body: dict,
        name: str,
        kind: str | None = None,
        api_version: str | None = None,
        namespace: str | None = None,
        typ: Type[R] = object,
        **kwargs,
    ) -> R:
        """
        Patch the resource in the clsuter and returns it deserialized.
        `api_version` and `kind` can be obtained from `BaseCRD` subclasses

        :param patch_body: The camelCased dictonary to create in the cluster
        :param kind: the kind to create
        :param api_version: the target group/version
        :param namespace: the target namespace
        :param typ: the return type
        :param **kwargs: extra arguments given to DynamicClient.patch()

        """
        av, k = (
            self._api_info_from_class(typ)
            if (api_version, kind) == (None, None)
            else (api_version, kind)
        )
        return cast(
            R,
            self._deserialize(
                self.dynamic_api.resources.get(api_version=av, kind=k).patch(
                    namespace=namespace, name=name, body=patch_body, **kwargs
                ),
                typ,
            ),
        )

    def delete(
        self,
        name: str,
        api_version: str | None = None,
        kind: str | None = None,
        namespace: str | None = None,
        typ: Type = type(None),
        **kwargs,
    ):
        """
        Deletes a resource in the clsuter.
        `api_version` and `kind` can be obtained from `BaseCRD` subclasses

        :param kind: the kind to create
        :param api_version: the target group/version
        :param namespace: the target namespace
        :param typ: the return type
        """
        av, k = (
            self._api_info_from_class(typ)
            if (api_version, kind) == (None, None)
            else (api_version, kind)
        )
        self.dynamic_api.resources.get(api_version=av, kind=k).delete(
            namespace=namespace, name=name, **kwargs
        )

    def start(self):
        """
        Starts the reconcilation loop
        """
        if self._running:
            raise RuntimeError(
                "cannot start an already started reconciler",
                f"{self.crd_type().__class__}-{self._namespace_name}",
            )
        self.dynamic_api = dynamic.DynamicClient(self._api_client)
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
