from inspect import isclass
import json
from typing import (
    Any,
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
from kubernetes import client

from kuroboros.exceptions import RetriableException
from kuroboros.extended_api import ExtendedApi
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.logger import root_logger
from kuroboros.schema import OpenAPISchema, CRDSchema
from kuroboros.utils import NamespaceName, event_aware_sleep, with_timeout

T = TypeVar("T", bound=CRDSchema)
R = TypeVar("R", bound=Any)


class Result:
    """
    The result of a `reconcile` call.
    It defined if the reconcile should be requeued and in how mucho time.
    """

    requeue: bool
    requeue_after_seconds: float

    def __init__(self, requeue: bool = True, requeue_after_seconds: float = 0) -> None:
        if requeue_after_seconds < 0:
            raise RuntimeError("cannot requeue in time < 0")
        self.requeue = requeue
        self.requeue_after_seconds = requeue_after_seconds


class BaseReconciler(Generic[T]):
    """
    The base Reconciler.
    This class perform the reconcilation logic in `reconcile`
    """

    __group_version_info: ClassVar[GroupVersionInfo]
    _stop: threading.Event
    _running: bool
    _loop_thread: threading.Thread
    _namespace_name: NamespaceName
    _api_client: client.ApiClient

    reconcile_timeout: ClassVar[timedelta | None] = None
    timeout_retry: ClassVar[bool] = False
    timeout_requeue_seconds: ClassVar[float] = timedelta(minutes=5).total_seconds()

    logger: Logger
    crd_inst: T
    name: str
    extended_api: ExtendedApi

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
        if t_type is None or CRDSchema not in t_type.__mro__:
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
        pretty_version = self.__group_version_info.pversion()
        self._namespace_name = namespace_name
        self.name = f"{self.__class__.__name__}{pretty_version}"
        self.logger = root_logger.getChild(__name__).getChild(self.__repr__())

    def __repr__(self) -> str:
        if self._namespace_name is not None:
            ns, n = self._namespace_name
            return f"{self.name}(Namespace={ns}, Name={n})"
        return f"{self.name}"

    def _deserialize(self, obj, typ):
        if isclass(typ) and issubclass(typ, CRDSchema):
            return typ(data=obj.to_dict())
        return self._api_client.deserialize(
            response=type("obj", (object,), {"data": json.dumps(obj.to_dict())}),
            response_type=typ,
        )

    def _deserialize_openapi(self, obj, typ):
        if isclass(typ) and issubclass(typ, OpenAPISchema):
            return typ(**obj)
        return self._api_client.deserialize(
            response=type("obj", (object,), {"data": json.dumps(obj)}),
            response_type=typ,
        )

    def _api_info_from_class(self, typ: Type):
        api_version = None
        kind = None
        if isclass(typ) and issubclass(typ, CRDSchema):
            gvi = typ.get_gvi()
            assert gvi is not None
            api_version = f"{gvi.group}/{gvi.api_version}"
            kind = gvi.kind

        return (api_version, kind)

    def _handle_reconcilation_loop_exception(self, e: Exception) -> Result | None:
        result = None
        if isinstance(e, client.ApiException):
            if e.status == 404:
                self.logger.info(e)
                self.logger.info(
                    "%s no longer found, killing thread", self._namespace_name
                )
            else:
                self.logger.fatal(
                    "A `APIException` ocurred while proccessing %s: %s",
                    self._namespace_name,
                    e,
                    exc_info=True,
                )
        elif isinstance(e, RetriableException):
            self.logger.warning(
                "A `RetriableException` ocurred while proccessing %s: %s",
                self._namespace_name,
                e,
            )
            result = Result(requeue_after_seconds=e.backoff)
        elif isinstance(e, TimeoutError):
            self.logger.warning(
                "A `TimeoutError` ocurred while proccessing %s: %s",
                self._namespace_name,
                e,
            )
            if not self.timeout_retry:
                self.logger.warning(
                    "`TimeoutError` will not be retried. To retry, enable it in %s",
                    self.__class__.__name__,
                )
            else:
                result = Result(requeue_after_seconds=self.timeout_requeue_seconds)
        else:
            self.logger.error(
                f"`{e.__class__.__name__}` ocurred while proccessing %s: %s",
                self._namespace_name,
                e,
                exc_info=True,
            )
        return result

    def reconcilation_loop(self):
        """
        Runs the reconciliation loop of every object
        while its a member of the `Controller`
        """
        while not self._stop.is_set():
            try:
                latest = self.get(
                    namespace=self._namespace_name[0],
                    name=self._namespace_name[1],
                    api_version=self.__group_version_info.api_version,
                    kind=self.__group_version_info.kind,
                    klass=self.crd_type(),
                )
                if self.reconcile_timeout is None:
                    result = self.reconcile(obj=latest, stopped=self._stop)
                else:
                    result = with_timeout(
                        self._stop,
                        self.timeout_retry,
                        self.reconcile_timeout.total_seconds(),
                        self.reconcile,
                        obj=latest,
                        stopped=self._stop,
                    )

            except (  # pylint: disable=broad-exception-caught
                client.ApiException,
                RetriableException,
                TimeoutError,
                Exception,
            ) as e:
                result = self._handle_reconcilation_loop_exception(e)

            should_requeue = result is not None and result.requeue
            requeue_after = result.requeue_after_seconds if result else 0

            result = None
            latest = None
            if should_requeue:
                if requeue_after == 0:
                    continue
                event_aware_sleep(self._stop, requeue_after)
            else:
                break
        self.logger.debug("%s reconcile loop stopped", self._namespace_name)

    def reconcile(
        self,
        obj: T,  # pylint: disable=unused-argument
        stopped: threading.Event,  # pylint: disable=unused-argument
    ) -> Result:  # pylint: disable=unused-argument
        """
        The function that reconcile the object to the desired status.

        :param obj: The CRD instance at the run moment
        :param stopped: The reconciliation loop event that signal a stop
        :returns result (`reconciler.Result`): Reconcilation result.
        If its `Result(requeue=False)` it wait until further updates or a
        controller restart to continue running
        """
        return Result(requeue=False)

    def get(
        self,
        api_version: str | None = None,
        kind: str | None = None,
        namespace: str | None = None,
        klass: Type[R] = object,
        **kwargs,
    ) -> R:
        """
        Gets the resources in the cluster givcen its name and returns it deserialized.
        `api_version` and `kind` can be obtained from `CRDSchema` subclasses

        :param kind: the kind to get
        :param api_version: the target group/version if `None` will use prefered kind version
        :param namespace: the target namespace
        :param name: the target name to retrieve
        :param klass: the return type of the object

        """
        return self.extended_api.get(
            **kwargs,
            namespace=namespace,
            api_version=api_version,
            kind=kind,
            klass=klass,
        )

    def create(
        self,
        body: Any,
        kind: str | None = None,
        api_version: str | None = None,
        namespace: str | None = None,
        klass: Type[R] = object,
    ) -> R:
        """
        Creates the resource in the clsuter and returns it deserialized.
        `api_version` and `kind` can be obtained from `CRDSchema` subclasses

        :param body: The camelCased dictonary to create in the cluster
        :param kind: the kind to create
        :param api_version: the target group/version if `None` will use prefered kind version
        :param namespace: the target namespace
        :param klass: the return type
        """
        return self.extended_api.create(
            body=body,
            kind=kind,
            api_version=api_version,
            namespace=namespace,
            klass=klass,
        )

    def patch(
        self,
        patch_body: Any,
        name: str,
        kind: str | None = None,
        api_version: str | None = None,
        namespace: str | None = None,
        subresources: list[str] | None = None,
        content_type: str = "application/merge-patch+json",
        klass: Type[R] = object,
        **kwargs,
    ) -> R:
        """
        Patch the resource in the clsuter and returns it deserialized.
        `api_version` and `kind` can be obtained from `CRDSchema` subclasses

        :param patch_body: The camelCased dictonary to create in the cluster
        :param kind: the kind to create
        :param api_version: the target group/version if `None` will use prefered kind version
        :param namespace: the target namespace
        :param subresources: list of subresources to update
        :param content_type: the Content-Type header for the Patch api call
        :param typ: the return type
        :param **kwargs: extra arguments given to `kubernetes.dynamic.DynamicClient.patch()`

        """
        return self.extended_api.patch(
            **kwargs,
            name=name,
            api_version=api_version,
            kind=kind,
            namespace=namespace,
            klass=klass,
            subresources=subresources,
            content_type=content_type,
            patch_body=patch_body,
        )

    def delete(
        self,
        name: str,
        api_version: str | None = None,
        kind: str | None = None,
        namespace: str | None = None,
        klass: Type = type(None),
        **kwargs,
    ):
        """
        Deletes a resource in the clsuter.
        `api_version` and `kind` can be obtained from `CRDSchema` subclasses

        :param kind: the kind to create
        :param api_version: the target group/version if `None` will use prefered kind version
        :param namespace: the target namespace
        :param typ: the return type
        :param **kwargs: extra arguments given to `kubernetes.dynamic.DynamicClient.delete()`
        """
        return self.extended_api.delete(
            **kwargs,
            name=name,
            api_version=api_version,
            kind=kind,
            namespace=namespace,
            klass=klass,
        )

    def start(self, extended_api: ExtendedApi):
        """
        Starts the reconcilation loop
        """
        if self._running:
            raise RuntimeError(
                "cannot start an already started reconciler",
                f"{self.crd_type().__class__}-{self._namespace_name}",
            )
        self.extended_api = extended_api
        loop_thread = threading.Thread(
            target=self.reconcilation_loop,
            daemon=True,
            name=f"{self.name}::{self._namespace_name}",
        )
        loop_thread.start()
        self._running = True
        self._loop_thread = loop_thread

    def stop(self):
        """
        Stops the reconciliation loop
        """
        self.logger.debug("stopping %s thread", self._loop_thread.name)
        if not self.is_running():
            return
        self._stop.set()
        self._running = False
        self._api_client.close()
        del self.extended_api

    def is_running(self) -> bool:
        """
        Checks if the reconciler is running
        """
        return self._running and self._loop_thread.is_alive()
