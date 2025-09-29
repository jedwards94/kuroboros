from logging import Logger
from typing import Any, Dict, Generator, List, MutableMapping, Tuple, Type, cast
import time
import threading
import caseconverter
from kubernetes import client, watch

from kuroboros import logger
from kuroboros.config import KuroborosConfig
from kuroboros.extended_api import ExtendedApi, ExtendedClient
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.reconciler import BaseReconciler
from kuroboros.schema import CRDSchema
from kuroboros.utils import NamespaceName, event_aware_sleep
from kuroboros.webhook import BaseMutationWebhook, BaseValidationWebhook


class EventEnum:
    """
    Event types from Kubernetes for the Controller
    """

    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"


class ControllerConfigVersions:
    """
    The version configuration for the ControllerConfig
    """

    name: str
    reconciler: Type[BaseReconciler] | None = None
    crd: Type[CRDSchema] | None = None
    validation_webhook: Type[BaseValidationWebhook] | None = None
    mutation_webhook: Type[BaseMutationWebhook] | None = None

    def has_webhooks(self) -> bool:
        """
        Checks if any webhook is loaded
        """
        return self.validation_webhook is not None or self.mutation_webhook is not None


class ControllerConfig:
    """
    Configuration for the Controller
    """

    name: str
    group_version_info: GroupVersionInfo
    versions: List[ControllerConfigVersions]

    def __init__(self) -> None:
        self.versions = []

    def has_webhooks(self) -> bool:
        """
        Checks if the running version has webhooks loaded
        """
        return self.get_run_version().has_webhooks()

    @property
    def validation_webhook(self) -> Type[BaseValidationWebhook] | None:
        """
        Returns the validation webhook for the current version
        """
        return self.get_run_version().validation_webhook

    @property
    def mutation_webhook(self) -> Type[BaseMutationWebhook] | None:
        """
        Returns the mutation webhook for the current version
        """
        return self.get_run_version().mutation_webhook

    def get_run_version(self):
        """
        Gets the version that match the GroupVersionInfo
        """
        for version in self.versions:
            if self.group_version_info.api_version == version.name:
                return version
        raise RuntimeError(f"no version match {self.group_version_info.api_version}")


class Controller:
    """
    Kuroboros Controller class.
    Triggers the reconciliation_loop of CR and manage the Threads
    """

    _cleanup_interval: float
    _logger: Logger
    _members: MutableMapping[NamespaceName, BaseReconciler]
    _pending_remove: List[NamespaceName]
    _group_version_info: GroupVersionInfo
    _stop: threading.Event
    _watcher: watch.Watch
    _watcher_loop: threading.Thread
    _cleanup_loop: threading.Thread
    _api_client: ExtendedClient
    _extended_api: ExtendedApi

    reconciler: Type[BaseReconciler]
    validation_webhook: BaseValidationWebhook | None
    mutation_webhook: BaseMutationWebhook | None
    name: str

    @property
    def threads(self) -> int:
        """
        Returns the number of currently watched `Threads` of the controller
        """
        return len(self._members)

    def __init__(
        self,
        name: str,
        group_version_info: GroupVersionInfo,
        reconciler: Type[BaseReconciler],
        validation_webhook: Type[BaseValidationWebhook] | None = None,
        mutation_webhook: Type[BaseMutationWebhook] | None = None,
    ) -> None:

        if (
            validation_webhook is not None
            and reconciler.crd_type() != validation_webhook.crd_type()
        ):
            raise RuntimeError(
                "The validation webhook type must match the reconciler type",
                f"{reconciler.crd_type()} != {validation_webhook.crd_type()}",
            )

        self._cleanup_interval = KuroborosConfig.get(
            "operator", "cleanup_interval_seconds", typ=float
        )
        self._group_version_info = group_version_info
        pascal_name = caseconverter.pascalcase(name)
        self.name = f"{pascal_name}{group_version_info.pversion()}Controller"
        self._logger = logger.root_logger.getChild(__name__).getChild(self.name)
        self._check_permissions()
        self.reconciler = reconciler
        self._api_client = ExtendedClient()
        self._members = {}
        self._pending_remove = []
        self.validation_webhook = (
            validation_webhook() if validation_webhook is not None else None
        )
        self.mutation_webhook = (
            mutation_webhook() if mutation_webhook is not None else None
        )
        self._stop = threading.Event()

    def _check_permissions(self):
        api = client.AuthorizationV1Api()
        for verb in ["create", "list", "watch", "delete", "get", "patch", "update"]:
            resource_attributes = client.V1ResourceAttributes(
                group=self._group_version_info.group,
                resource=self._group_version_info.plural,
                verb=verb,
            )

            access_review = client.V1SelfSubjectAccessReview(
                spec=client.V1SelfSubjectAccessReviewSpec(
                    resource_attributes=resource_attributes
                )
            )

            res = api.create_self_subject_access_review(access_review)
            response = cast(client.V1SelfSubjectAccessReview, res)

            if response.status is not None and response.status.allowed:
                continue

            if response.status is not None and response.status.denied:
                crd_name = self._group_version_info.crd_name
                raise RuntimeWarning(
                    f"operator doesn't have {verb} permission over the CRD {crd_name}"
                )

    def _add_member(self, namespace_name: NamespaceName):
        """
        Adds the object to be managed and starts its `_reconcile` function
        in a thread
        """
        if namespace_name not in self._members:
            reconciler = self.reconciler(namespace_name)
            reconciler.start(extended_api=self._extended_api)
            self._members[namespace_name] = reconciler
            self._logger.info(
                "%s added as member",
                reconciler,
            )
        else:
            if self._members[namespace_name].is_running():
                return
            self._members[namespace_name].start(extended_api=self._extended_api)

    def _add_pending_remove(self, namespace_name: NamespaceName):
        """
        Adds the object to be safely removed from the management list
        """
        if namespace_name in self._pending_remove:
            return
        self._pending_remove.append(namespace_name)
        self._logger.info(
            "%s CR added as pending_remove",
            self._group_version_info.pkind(namespace_name),
        )

    def _remove_member(self, namespace_name: NamespaceName):
        """
        Sends an stop event to the member thread and stops the loop
        """
        if namespace_name not in self._members:
            return
        self._members[namespace_name].stop()
        self._members.pop(namespace_name)
        self._logger.info(
            "%s CR removed",
            self._group_version_info.pkind(namespace_name),
        )

    def _get_current_cr_list(self) -> List[Any]:
        """
        Gets the current list of objects in the cluster
        """
        return self._extended_api.get(
            kind=self._group_version_info.kind,
            api_version=f"{self._group_version_info.group}/{self._group_version_info.api_version}",
            klass=list[object],
        )

    def _stream_events(
        self,
        watcher: watch.Watch,
    ) -> Generator[Any | dict | str, str, None]:
        """
        Wrapper to `kubernetes.watch.Watch().stream()`
        """
        cr_api = client.CustomObjectsApi()
        return watcher.stream(
            cr_api.list_cluster_custom_object,
            group=self._group_version_info.group,
            version=self._group_version_info.api_version,
            plural=self._group_version_info.plural,
        )

    def _preload_existing_cr(self):
        self._logger.info(
            "preloading existing %s CRs", self._group_version_info.pkind()
        )
        try:
            current_crs = self._get_current_cr_list()
            for pending in current_crs:
                crd_namespace_name = (
                    pending["metadata"].get("namespace", None),
                    pending["metadata"]["name"],
                )
                self._add_member(crd_namespace_name)
            self._logger.info(
                "preloaded %d %s CR(s)",
                len(current_crs),
                self._group_version_info.pkind(),
            )
        except Exception as e:
            self._logger.error(
                "error while preloading %s CR: %s",
                self._group_version_info.pkind(),
                e,
                exc_info=True,
            )
            raise e

    def _watch_pending_remove(self):
        """
        Looks for the objects with `finalizers` pending to be removed
        every 5 seconds and removes them once they no longer exists
        """
        self._logger.info(
            "watching %s CRs pending to remove",
            self._group_version_info.pkind(),
        )
        while not self._stop.is_set():
            for namespace, name in self._pending_remove:
                self._logger.info(
                    "%d %s CRs pending to remove",
                    len(self._pending_remove),
                    self._group_version_info.pkind(),
                )

                try:
                    if not self._cr_exists(name, namespace):
                        self._remove_member((namespace, name))
                        self._pending_remove.remove((namespace, name))
                        self._logger.info(
                            "%s CR no longer found, removed",
                            self._group_version_info.pkind((namespace, name)),
                        )
                except client.ApiException as e:
                    self._logger.error(
                        "unexpected api error ocurred while watching pending_remove %s CR: %s",
                        self._group_version_info.pkind((namespace, name)),
                        e,
                        exc_info=True,
                    )
            event_aware_sleep(self._stop, self._cleanup_interval)

    def _watch_cr_events(self):
        """
        Watch for the kubernetes events of the object.
        Adds the member if its `ADDED` or `MODIFIED` and removes them when `DELETED`
        """
        self._logger.info(
            "starting to watch %s events", self._group_version_info.pkind()
        )
        self._watcher = watch.Watch()
        try:
            for event in self._stream_events(self._watcher):
                if self._stop.is_set():
                    self._watcher.stop()
                    break
                if not isinstance(event, dict):
                    self._logger.warning("event received is not a dict, skipping")
                    continue
                try:
                    e_type = event["type"]
                    raw_cr = event["object"]
                    metadata: Dict[str, Any] = raw_cr["metadata"]
                    namespace_name = (
                        metadata.get("namespace", None),
                        metadata["name"],
                    )
                    finalizers = metadata.get("finalizers")
                    self._logger.debug(f"event: {namespace_name} {event['type']}")
                    if e_type in (EventEnum.ADDED, EventEnum.MODIFIED):
                        self._add_member(namespace_name)
                    elif e_type == EventEnum.DELETED:
                        if finalizers is not None and len(finalizers) > 0:
                            self._add_pending_remove(namespace_name)
                            continue
                        self._remove_member(namespace_name)
                    else:
                        self._logger.warning(f"event type {event['type']} not handled")

                except Exception as e:  # pylint: disable=broad-exception-caught
                    self._logger.warning(
                        "an Exception ocurred while streaming %s events: %s",
                        self._group_version_info.pkind(),
                        e,
                        exc_info=True,
                    )
                    continue
        except Exception as e:  # pylint: disable=broad-exception-caught
            self._logger.error(
                "error while watching %s: %s",
                self._group_version_info.pkind(),
                e,
                exc_info=True,
            )
        finally:
            self._logger.info(
                "no longer watching events from %s",
                self._group_version_info.pkind(),
            )
            self._watcher.stop()

    def _cr_exists(self, name: str, namespace: str | None = None) -> bool:
        try:
            group = self._group_version_info.group
            api_version = self._group_version_info.api_version
            self._extended_api.get(
                kind=self._group_version_info.kind,
                api_version=f"{group}/{api_version}",
                name=name,
                namespace=namespace,
            )
        except client.ApiException as e:
            if e.status == 404:
                return False
            raise e
        return True

    def run(self) -> Tuple[threading.Thread, threading.Thread]:
        """
        Starts the controller.
        Pre-load the current CRs in the cluster ands start both watcher and cleanup threads.
        """
        self._extended_api = ExtendedApi(api_client=self._api_client)
        self._watcher_loop = threading.Thread(
            target=self._watch_cr_events, name=f"{self.name}::Watcher", daemon=True
        )
        self._cleanup_loop = threading.Thread(
            target=self._watch_pending_remove, name=f"{self.name}::Cleanup", daemon=True
        )

        self._preload_existing_cr()

        self._watcher_loop.start()
        self._cleanup_loop.start()

        return (self._watcher_loop, self._cleanup_loop)

    def stop(self):
        """
        Send a stop event to every thread under this controller and waits for every CR to react
        to the event
        """
        self._logger.info("stopping controller %s", self.name)
        if self._watcher is not None:
            self._watcher.stop()
        if not self._stop.is_set():
            self._stop.set()

        wait_for_stop: List[BaseReconciler] = []
        for namespace_name, member in self._members.items():
            self._logger.debug(
                "sending stop event to %s",
                self._group_version_info.pkind(namespace_name),
            )
            member.stop()
            wait_for_stop.append(member)

        alive_threads = wait_for_stop
        if len(alive_threads) > 0:
            self._logger.debug(
                "waiting for %d reconcilation loop(s) thread(s) to stop...",
                len(alive_threads),
            )
            while len(alive_threads) > 0:
                alive_threads = [
                    member for member in wait_for_stop if member.is_running()
                ]
                time.sleep(0.5)

        self._logger.info("controller %s succesfully stopped", self.name)
