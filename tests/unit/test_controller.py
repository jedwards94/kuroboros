from threading import Thread
import time
import unittest
from unittest.mock import MagicMock, patch
from kuroboros import controller
from kuroboros.controller import Controller, EventEnum
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.reconciler import BaseReconciler
from kuroboros.webhook import BaseValidationWebhook
from kuroboros.schema import BaseCRD
from kubernetes import client

from tests.unit.test_webhook import DummyMutationWebhook


def group_version_info():
    return GroupVersionInfo(
        group="testgroup", api_version="v1", plural="dummies", kind="Dummy"
    )


class FailDummyCRD(BaseCRD):
    pass


class DummyCRD(BaseCRD):
    @property
    def namespace_name(self):
        return self._namespace_name

    @property
    def finalizers(self):
        return self._finalizers

    @property
    def name(self):
        return self._namespace_name[1]

    @property
    def namemespace(self):
        return self._namespace_name[0]

    @property
    def resource_version(self):
        return "123"

    def __init__(self, api=None, group_version=None):
        self._namespace_name = ("default", "dummy")
        self._finalizers = []
        self._data = {}

    def load_data(self, data):
        self._data = data
        self._namespace_name = (
            data.get("metadata", {}).get("namespace", "default"),
            data.get("metadata", {}).get("name", "dummy"),
        )
        self._finalizers = data.get("metadata", {}).get("finalizers", [])


class DummyReconciler(BaseReconciler[DummyCRD]):
    def __init__(self, namespace_name):
        super().__init__(namespace_name)

    def reconcilation_loop(self):
        time.sleep(4)


class DummyWebhookValidation(BaseValidationWebhook[DummyCRD]):
    pass


class FailDummyWebhookValidation(BaseValidationWebhook[FailDummyCRD]):
    pass


DummyCRD.set_gvi(group_version_info())
DummyReconciler.set_gvi(group_version_info())
DummyWebhookValidation.set_gvi(group_version_info())
DummyMutationWebhook.set_gvi(group_version_info())
FailDummyCRD.set_gvi(group_version_info())
FailDummyWebhookValidation.set_gvi(group_version_info())


def make_controller():
    with patch("kuroboros.controller.Controller._check_permissions"):
        return Controller("DummyController", group_version_info(), DummyReconciler)


class TestController(unittest.TestCase):
    def setUp(self):
        self.controller = make_controller()

    def test_controller_init_sets_attributes(self):
        self.assertEqual(self.controller.name, "DummyControllerV1StableController")
        self.assertIs(self.controller.reconciler, DummyReconciler)

    def test_controller_webhook_reconciler_equals_crd_cls(self):
        with patch("kuroboros.controller.Controller._check_permissions"):
            ctrl = Controller(
                "DummyController",
                group_version_info(),
                DummyReconciler,
                DummyWebhookValidation,
            )
            self.assertIsInstance(ctrl, Controller)

            with self.assertRaises(RuntimeError):
                Controller(
                    "DummyController",
                    group_version_info(),
                    DummyReconciler,
                    FailDummyWebhookValidation,
                )

    @patch("kubernetes.dynamic.DynamicClient")
    def test_add_member_adds_thread(self, _):
        self.controller._add_member(("default", "dummy"))
        self.assertIn(("default", "dummy"), self.controller._members)
        reconciler = self.controller._members[("default", "dummy")]
        self.assertTrue(reconciler.is_running())
        self.assertFalse(reconciler._stop.is_set())

    @patch("kubernetes.dynamic.DynamicClient")
    def test_add_member_duplicate(self, _):

        self.controller._add_member(("default", "dummy"))
        before = len(self.controller._members)
        self.controller._add_member(("default", "dummy"))
        after = len(self.controller._members)
        self.assertEqual(before, after)

    @patch("kubernetes.dynamic.DynamicClient")
    def test_remove_member_stops_reconciler(self, _):
        self.controller._add_member(("default", "dummy"))
        reconciler = self.controller._members[("default", "dummy")]
        self.controller._remove_member(("default", "dummy"))
        self.assertFalse(("default", "dummy") in self.controller._members)
        self.assertFalse(reconciler.is_running())

    def test_add_pending_remove(self):
        ns_name = ("default", "dummy")
        self.controller._add_pending_remove(ns_name)
        self.assertIn(ns_name, self.controller._pending_remove)

    def test_add_pending_remove_duplicate(self):
        ns_name = ("default", "dummy")
        self.controller._add_pending_remove(ns_name)
        self.controller._add_pending_remove(ns_name)
        self.assertEqual(self.controller._pending_remove.count(ns_name), 1)

    def test_get_current_cr_list_returns_items(self):
        api = MagicMock()
        api.list_cluster_custom_object.return_value = {"items": [1, 2, 3]}
        items = self.controller._get_current_cr_list(api)
        self.assertEqual(items, [1, 2, 3])

    def test_stream_events_returns_iterator(self):
        api = MagicMock()
        watcher = MagicMock()
        watcher.stream.return_value = iter(
            [
                {
                    "type": EventEnum.ADDED,
                    "object": {"metadata": {"name": "foo", "namespace": "bar"}},
                }
            ]
        )
        result = self.controller._stream_events(api, watcher)
        self.assertTrue(isinstance(result, dict) or hasattr(result, "__iter__"))

    def test_preload_existing_cr_adds_members(self):
        api_mock = MagicMock()
        api_mock.list_cluster_custom_object.return_value = {
            "items": [
                {"metadata": {"name": "foo", "namespace": "bar"}},
                {"metadata": {"name": "baz", "namespace": "qux"}},
            ]
        }
        with patch(
            "kuroboros.controller.client.CustomObjectsApi", return_value=api_mock
        ):
            with patch.object(self.controller, "_add_member") as add_member:
                self.controller._preload_existing_cr()
                self.assertEqual(add_member.call_count, 2)

    def test_watch_cr_events_handles_events(self):
        api = MagicMock()
        watcher = MagicMock()
        events = [
            {
                "type": EventEnum.ADDED,
                "object": {"metadata": {"name": "foo", "namespace": "bar"}},
            },
            {
                "type": EventEnum.MODIFIED,
                "object": {"metadata": {"name": "foo", "namespace": "bar"}},
            },
            {
                "type": EventEnum.DELETED,
                "object": {
                    "metadata": {"name": "foo", "namespace": "bar"},
                    "finalizers": [],
                },
            },
            {
                "type": "UNKNOWN",
                "object": {"metadata": {"name": "foo", "namespace": "bar"}},
            },
            "notadict",
        ]
        watcher.stream.return_value = iter(events)
        with patch("kuroboros.controller.client.CustomObjectsApi", return_value=api):
            with patch.object(
                self.controller, "_add_member"
            ) as add_member, patch.object(
                self.controller, "_remove_member"
            ) as remove_member, patch.object(
                self.controller, "_add_pending_remove"
            ) as add_pending_remove:
                self.controller._stream_events = MagicMock(return_value=events)
                self.controller._watch_cr_events()
                self.assertEqual(add_member.call_count, 2)
                self.assertEqual(remove_member.call_count, 1)

    def test_watch_pending_remove_removes_when_404(self):
        self.controller._pending_remove = [("default", "dummy")]
        # Patch the _api attribute/property on the instance
        with patch.object(
            self.controller._api,
            "get_namespaced_custom_object_with_http_info",
            side_effect=client.ApiException(status=404, reason="Not Found"),
        ):
            with patch.object(self.controller, "_remove_member") as remove_member:
                thread = Thread(target=self.controller._watch_pending_remove)
                thread.start()
                self.controller._stop.set()
                while thread.is_alive():
                    time.sleep(0.1)
                remove_member.assert_called_with(("default", "dummy"))

    def test_check_permissions_allows(self):
        with patch("kuroboros.controller.client.AuthorizationV1Api") as mock_api:
            mock_instance = mock_api.return_value
            # Simulate allowed for all verbs
            mock_instance.create_self_subject_access_review.return_value.status = (
                MagicMock(allowed=True, denied=False)
            )
            ctrl = Controller("DummyController", group_version_info(), DummyReconciler)
            # Should not raise
            ctrl._check_permissions()

    def test_check_permissions_denied(self):
        with patch("kuroboros.controller.client.AuthorizationV1Api") as mock_api:
            mock_instance = mock_api.return_value

            # Simulate denied for one verb
            def denied_review(*args, **kwargs):
                class Status:
                    allowed = False
                    denied = True

                class Review:
                    status = Status()

                return Review()

            mock_instance.create_self_subject_access_review.side_effect = denied_review
            with self.assertRaises(RuntimeWarning):
                Controller("DummyController", group_version_info(), DummyReconciler)
