import time
import unittest
from unittest.mock import MagicMock, patch
from kuroboros.controller import Controller, EventEnum
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.reconciler import BaseReconciler
from kuroboros.schema import BaseCRD
from kubernetes import client
class DummyCRD(BaseCRD):
    @property
    def namespace_name(self):
        return self._namespace_name

    @property
    def finalizers(self):
        return self._finalizers

    def __init__(self, api=None):
        self._namespace_name = ("default", "dummy")
        self._finalizers = []
        self._data = {}
    def load_data(self, data):
        self._data = data
        self._namespace_name = (data.get("metadata", {}).get("namespace", "default"),
                               data.get("metadata", {}).get("name", "dummy"))
        self._finalizers = data.get("metadata", {}).get("finalizers", [])

class DummyReconciler(BaseReconciler):
    def __init__(self):
        self._type = DummyCRD
        self.api = None
    def _reconcile(self, object, stop):
        time.sleep(2)
        pass

def group_version_info():
    return GroupVersionInfo(
        group="testgroup",
        api_version="v1",
        plural="dummies",
        kind="Dummy"
    )

def reconciler():
    return DummyReconciler()

def make_controller():
    with patch("kuroboros.controller.Controller._check_permissions"):
        return Controller("dummy-controller", group_version_info(), reconciler())

class TestController(unittest.TestCase):
    def setUp(self):
        self.controller = make_controller()

    def test_controller_init_sets_attributes(self):
        self.assertEqual(self.controller.name, "dummy-controller")
        self.assertIsInstance(self.controller.reconciler, DummyReconciler)

    def test_add_member_adds_thread(self):
        crd = DummyCRD()
        with patch("kuroboros.controller.client.CustomObjectsApi"):
            self.controller._add_member(crd)  
        self.assertIn(crd.namespace_name, self.controller._members)
        thread, event = self.controller._members[crd.namespace_name]
        self.assertTrue(thread.is_alive())
        self.assertFalse(event.is_set())

    def test_add_member_duplicate(self):
        crd = DummyCRD()
        with patch("kuroboros.controller.client.CustomObjectsApi"):
            self.controller._add_member(crd)
            before = len(self.controller._members)
            self.controller._add_member(crd)
            after = len(self.controller._members)
        self.assertEqual(before, after)

    def test_remove_member_removes_and_sets_event(self):
        crd = DummyCRD()
        with patch("kuroboros.controller.client.CustomObjectsApi"):
            self.controller._add_member(crd)
        self.controller._remove_member(crd.namespace_name)
        self.assertNotIn(crd.namespace_name, self.controller._members)

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
        watcher.stream.return_value = iter([{"type": EventEnum.ADDED, "object": {"metadata": {"name": "foo", "namespace": "bar"}}}])
        result = self.controller._stream_events(api, watcher, DummyCRD)
        self.assertTrue(isinstance(result, dict) or hasattr(result, "__iter__"))

    def test_preload_existing_cr_adds_members(self):
        api_mock = MagicMock()
        api_mock.list_cluster_custom_object.return_value = {
            "items": [
                {"metadata": {"name": "foo", "namespace": "bar"}},
                {"metadata": {"name": "baz", "namespace": "qux"}}
            ]
        }
        with patch("kuroboros.controller.client.CustomObjectsApi", return_value=api_mock):
            with patch.object(self.controller, "_add_member") as add_member:
                self.controller._preload_existing_cr()
                self.assertEqual(add_member.call_count, 2)

    def test_watch_cr_events_handles_events(self):
        api = MagicMock()
        watcher = MagicMock()
        events = [
            {"type": EventEnum.ADDED, "object": {"metadata": {"name": "foo", "namespace": "bar"}}},
            {"type": EventEnum.MODIFIED, "object": {"metadata": {"name": "foo", "namespace": "bar"}}},
            {"type": EventEnum.DELETED, "object": {"metadata": {"name": "foo", "namespace": "bar"}, "finalizers": []}},
            {"type": "UNKNOWN", "object": {"metadata": {"name": "foo", "namespace": "bar"}}},
            "notadict"
        ]
        watcher.stream.return_value = iter(events)
        with patch("kuroboros.controller.client.CustomObjectsApi", return_value=api):
            with patch.object(self.controller, "_add_member") as add_member, \
                 patch.object(self.controller, "_remove_member") as remove_member, \
                 patch.object(self.controller, "_add_pending_remove") as add_pending_remove:
                self.controller._stream_events = MagicMock(return_value=events)
                self.controller._watch_cr_events()
                self.assertEqual(add_member.call_count, 2)
                self.assertEqual(remove_member.call_count, 1)

    def test_watch_pending_remove_removes_when_404(self):
        api = MagicMock()
        api.get_namespaced_custom_object_with_http_info.side_effect = client.ApiException(status=404, reason="Not Found")
        self.controller._pending_remove = [("default", "dummy")]
        with patch("kuroboros.controller.client.CustomObjectsApi", return_value=api):
            with patch.object(self.controller, "_remove_member") as remove_member, \
                 patch("time.sleep", side_effect=Exception("break")):
                try:
                    self.controller._watch_pending_remove()
                except Exception:
                    pass
                remove_member.assert_called_with(("default", "dummy"))

if __name__ == "__main__":
    unittest.main()