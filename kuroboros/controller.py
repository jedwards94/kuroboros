from logging import Logger
from typing import Any, Dict, List, MutableMapping, Tuple, cast
import time
import threading
from kubernetes import client, watch

from kuroboros import logger
from kuroboros.config import config
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.reconciler import BaseReconciler
from kuroboros.schema import BaseCRD
from kuroboros.utils import event_aware_sleep
from kuroboros.webhook import BaseMutationWebhook, BaseValidationWebhook


class EventEnum:
    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"


class ControllerConfigVersions:
    name: str
    reconciler: BaseReconciler | None = None
    crd: BaseCRD | None = None
    validation_webhook: BaseValidationWebhook | None = None
    mutation_webhook: BaseMutationWebhook | None = None

    def has_webhooks(self) -> bool:
        return self.validation_webhook is not None or self.mutation_webhook is not None


class ControllerConfig:
    name: str
    group_version_info: GroupVersionInfo
    versions: List[ControllerConfigVersions] = []

    def has_webhooks(self) -> bool:
        return self.get_run_version().has_webhooks()
    
    @property
    def validation_webhook(self) -> BaseValidationWebhook | None:
        """
        Returns the validation webhook for the current version
        """
        return self.get_run_version().validation_webhook
    
    @property
    def mutation_webhook(self) -> BaseMutationWebhook | None:
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
    __CLEANUP_INTERVAL = float(
        config.getfloat("operator", "pending_remove_interval_seconds", fallback=5.0)
    )
    _logger: Logger = logger.root_logger.getChild(__name__)
    _members: MutableMapping[Tuple[str, str], Tuple[threading.Thread, threading.Event]]
    _pending_remove: List[Tuple[str, str]]
    _group_version_info: GroupVersionInfo
    _stop: threading.Event
    _watcher: watch.Watch
    
    _watcher_loop: threading.Thread
    _cleanup_loop: threading.Thread

    reconciler: BaseReconciler
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
        reconciler: BaseReconciler,
        validation_webhook: BaseValidationWebhook | None = None,
        mutation_webhook: BaseMutationWebhook | None = None,
    ) -> None:

        if (
            validation_webhook is not None
            and reconciler.crd_type != validation_webhook.crd_type
        ):
            raise RuntimeError(
                "The validation webhook type must match the reconciler type"
            )

        self._group_version_info = group_version_info
        self.name = (
            f"{name.capitalize()}{group_version_info.pretty_version_str()}Controller"
        )
        self._logger = self._logger.getChild(self.name)
        self.reconciler = reconciler
        self._check_permissions()
        self._members = {}
        self._pending_remove = []
        self.validation_webhook = validation_webhook
        self.mutation_webhook = mutation_webhook
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
            elif response.status is not None and response.status.denied:
                raise RuntimeWarning(
                    f"operator doesn't have {verb} permission over the CRD {self._group_version_info.crd_name}"
                )

    def _add_member(self, crd: BaseCRD):
        """
        Adds the object to be managed and starts its `_reconcile` function
        in a thread
        """
        if crd.namespace_name in self._members:
            return
        self.reconciler.api = client.CustomObjectsApi()
        event = threading.Event()
        thread_loop = threading.Thread(
            target=self.reconciler.reconcilation_loop,
            args=(crd, event),
            daemon=True,
            name=f"{self.name}-{crd.namespace_name}",
        )
        thread_loop.start()
        self._members[crd.namespace_name] = (thread_loop, event)
        self._logger.info(
            f"{self._group_version_info.pretty_kind_str(crd.namespace_name)} added as member"
        )

    def _add_pending_remove(self, namespace_name: Tuple[str, str]):
        """
        Adds the object to be safely removed from the management list
        """
        if namespace_name in self._pending_remove:
            return
        self._pending_remove.append(namespace_name)
        self._logger.info(
            f"{self._group_version_info.pretty_kind_str(namespace_name)} CR added as pending_remove"
        )

    def _remove_member(self, namespace_name: Tuple[str, str]):
        """
        Sends an stop event to the member thread and stops the loop
        """
        if namespace_name not in self._members:
            return
        self._members.pop(namespace_name)[1].set()

    def _get_current_cr_list(self, api: client.CustomObjectsApi) -> List[Any]:
        """
        Gets the current list of objects in the cluster
        """
        current_cr_resp = api.list_cluster_custom_object(
            group=self._group_version_info.group,
            version=self._group_version_info.api_version,
            plural=self._group_version_info.plural,
        )
        return current_cr_resp["items"]

    def _stream_events(
        self,
        api: client.CustomObjectsApi,
        watcher: watch.Watch,
    ) -> Dict[Any, Any]:
        """
        Wrapper to `kubernetes.watch.Watch().stream()`
        """
        return cast(
            Dict[Any, Any],
            watcher.stream(
                api.list_cluster_custom_object,
                group=self._group_version_info.group,
                version=self._group_version_info.api_version,
                plural=self._group_version_info.plural,
            ),
        )

    def _preload_existing_cr(self):
        self._logger.info(
            f"preloading existing {self._group_version_info.pretty_kind_str()} CRs"
        )
        try:
            api = client.CustomObjectsApi()
            crd_type: type[BaseCRD] = self.reconciler.crd_type
            current_crs = self._get_current_cr_list(api)
            for pending in current_crs:
                crd_inst: BaseCRD = crd_type(api)
                crd_inst.load_data(data=pending)
                self._add_member(crd_inst)
            self._logger.info(
                f"preloaded {len(current_crs)} {self._group_version_info.pretty_kind_str()} CR(s)"
            )
        except Exception as e:
            self._logger.error(
                f"error while preloading {self._group_version_info.pretty_kind_str()} CR",
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
            f"starting to watch {self._group_version_info.pretty_kind_str()} CRs pending to remove"
        )
        api = client.CustomObjectsApi()
        while not self._stop.is_set():
            for namespace, name in self._pending_remove:
                self._logger.info(
                    f"currently {len(self._pending_remove)} {self._group_version_info.pretty_kind_str()} CRs pending to remove"
                )
                try:
                    api.get_namespaced_custom_object_with_http_info(
                        group=self._group_version_info.group,
                        version=self._group_version_info.api_version,
                        plural=self._group_version_info.plural,
                        name=name,
                        namespace=namespace,
                    )
                except client.ApiException as e:
                    if e.status == 404:
                        self._remove_member((namespace, name))
                        self._pending_remove.remove((namespace, name))
                        self._logger.info(
                            f"{self._group_version_info.pretty_kind_str((namespace, name))} CR no longer found, removed"
                        )
                    else:
                        self._logger.error(
                            f"unexpected api error ocurred while watching pending_remove {self._group_version_info.pretty_kind_str((namespace, name))} CR",
                            e,
                            exc_info=True,
                        )
                        raise e

            defunct_members = []
            for namespace_name, (thread, _) in self._members.items():
                if not thread.is_alive():
                    defunct_members.append(namespace_name)
            for m in defunct_members:
                self._remove_member(m)

            event_aware_sleep(self._stop, self.__CLEANUP_INTERVAL)

    def _watch_cr_events(self):
        """
        Watch for the kubernetes events of the object.
        Adds the member if its `ADDED` or `MODIFIED` and removes them when `DELETED`
        """
        self._logger.info(
            f"starting to watch {self._group_version_info.pretty_kind_str()} events"
        )
        self._watcher = watch.Watch()
        api = client.CustomObjectsApi()
        try:
            for event in self._stream_events(api, self._watcher):
                if self._stop.is_set():
                    self._watcher.stop()
                    break
                if type(event) != dict:
                    self._logger.warning("event received is not a dict, skipping")
                    continue
                try:
                    e_type = event["type"]
                    crd_inst: BaseCRD = self.reconciler.crd_type(api)
                    crd_inst.load_data(event["object"])
                    self._logger.debug(
                        f"event: {crd_inst.namespace_name} {event['type']}"
                    )
                    if e_type == EventEnum.ADDED or e_type == EventEnum.MODIFIED:
                        self._add_member(crd_inst)
                    elif e_type == EventEnum.DELETED:
                        if (
                            crd_inst.finalizers is not None
                            and len(crd_inst.finalizers) > 0
                        ):
                            self._add_pending_remove(crd_inst.namespace_name)
                            continue
                        self._remove_member(crd_inst.namespace_name)
                    else:
                        self._logger.warning(f"event type {event['type']} not handled")

                except Exception as e:
                    self._logger.warning(
                        f"an Exception ocurred while streaming {self._group_version_info.pretty_kind_str()} events",
                        e,
                        exc_info=True,
                    )
                    continue
        except Exception as e:
            self._logger.error(
                f"error while watching {self._group_version_info.pretty_kind_str()}",
                e,
                exc_info=True,
            )
            pass
        finally:
            self._logger.info(
                f"no longer watching events from {self._group_version_info.pretty_kind_str()}"
            )
            self._watcher.stop()

    def run(self) -> Tuple[threading.Thread, threading.Thread]:
        self._watcher_loop = threading.Thread(
            target=self._watch_cr_events, name=f"{self.name}-watcher", daemon=True
        )
        self._cleanup_loop = threading.Thread(
            target=self._watch_pending_remove, name=f"{self.name}-cleanup", daemon=True
        )

        self._preload_existing_cr()

        self._watcher_loop.start()
        self._cleanup_loop.start()

        return (self._watcher_loop, self._cleanup_loop)

    def stop(self):
        self._logger.info(f"stopping controller {self.name}")
        if self._watcher is not None:
            self._watcher.stop()
        if not self._stop.is_set():
            self._stop.set()
            
        wait_for_stop: List[threading.Thread] = []
        for member in self._members:
            thread, stop = self._members[member]
            self._logger.info(f"sending stop event to {self._group_version_info.pretty_kind_str(member)}")
            stop.set()
            wait_for_stop.append(thread)
            
        alive_threads = wait_for_stop
        if len(alive_threads) > 0:
            self._logger.info(f"waiting for {len(alive_threads)} reconcilation loop(s) thread(s) to stop...")
            while len(alive_threads) > 0:
                alive_threads = [thread for thread in wait_for_stop if thread.is_alive()]
                time.sleep(0.5)
        
        self._logger.info(f"controller {self.name} succesfully stopped")