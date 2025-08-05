# pylint: skip-file
from datetime import timedelta
from logging import Logger
from threading import Event, Thread
import threading
from time import sleep
import unittest
from unittest.mock import patch, MagicMock
from kubernetes import client

from kuroboros.exceptions import RetriableException, UnrecoverableException
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.reconciler import BaseReconciler
from kuroboros.schema import BaseCRD, prop

test_api_group = GroupVersionInfo(api_version="v1", group="test", kind="Test")


class TestCrd(BaseCRD):
    @property
    def namespace_name(self):
        return ("default", "dummy")
    test_filed = prop(str)
    
TestCrd.set_gvi(test_api_group)


class TestReconciler(BaseReconciler[TestCrd]):
    pass

TestReconciler.set_gvi(test_api_group)

class InvalidReconciler(BaseReconciler[str]):  # type: ignore
    pass

InvalidReconciler.set_gvi(test_api_group)

class TestInit(unittest.TestCase):

    def test_valid(self):
        TestReconciler.crd_type()
        reconciler = TestReconciler(("default", "dummy"))
        

        self.assertIsInstance(reconciler, TestReconciler)

    def test_invalid(self):
        with self.assertRaises(RuntimeError):
            InvalidReconciler.crd_type()


class LoopTest(BaseReconciler[TestCrd]):
    max_loops = 2
    loops = 0
    reconcile_call_count = 0
    infinite = False
    retriable_exception = False
    unrecoverable_exception = False

    def reconcile(self, logger: Logger, obj: TestCrd, stopped: threading.Event):
        self.reconcile_call_count = self.reconcile_call_count + 1
        self.loops = self.loops + 1
        if self.loops == self.max_loops and self.infinite is False:
            return

        if self.retriable_exception:
            raise RetriableException(timedelta(seconds=2), Exception("testing"))

        if self.unrecoverable_exception:
            raise UnrecoverableException(Exception("testing"))
        return timedelta(seconds=2)
    
LoopTest.set_gvi(test_api_group)


class TestLoop(unittest.TestCase):

    @patch("kubernetes.client.CustomObjectsApi.get_namespaced_custom_object")
    def test_object_exists(self, mock_get: MagicMock):
        reconciler = LoopTest(("default", "dummy"))
        reconciler.api = client.CustomObjectsApi()

        mock_get.return_value = {
            "metadata": {
                "resourceVersion": "1234",
                "namespace": "test",
                "name": "test",
                "uid": "1",
            }
        }
        reconciler.reconcilation_loop()
        self.assertEqual(reconciler.reconcile_call_count, 2)
        self.assertEqual(mock_get.call_count, 2)

    @patch("kubernetes.client.CustomObjectsApi.get_namespaced_custom_object")
    def test_object_does_not_exists(self, mock_get: MagicMock):
        reconciler = LoopTest(("default", "dummy"))
        mock_get.side_effect = client.ApiException(status=404, reason="Not Found")
        reconciler.reconcilation_loop()
        mock_get.assert_called_once()
        self.assertEqual(reconciler.reconcile_call_count, 0)

    @patch("kubernetes.client.CustomObjectsApi.get_namespaced_custom_object")
    def test_stop_loop_on_event(self, mock_get: MagicMock):
        reconciler = LoopTest(("default", "dummy"))
        reconciler.infinite = True
        mock_get.return_value = {
            "metadata": {
                "resourceVersion": "1234",
                "namespace": "test",
                "name": "test",
                "uid": "1",
            }
        }
        reconciler.start()
        reconciler.stop()
        sleep(0.2)
        self.assertFalse(reconciler.is_running())
        self.assertFalse(reconciler._loop_thread.is_alive())

    @patch("kubernetes.client.CustomObjectsApi.get_namespaced_custom_object")
    def test_retriable_exception(self, mock_get: MagicMock):
        reconciler = LoopTest(("default", "dummy"))
        reconciler.retriable_exception = True
        mock_get.return_value = {
            "metadata": {
                "resourceVersion": "1234",
                "namespace": "test",
                "name": "test",
                "uid": "1",
            }
        }
        reconciler.reconcilation_loop()
        self.assertEqual(reconciler.reconcile_call_count, 2)

    @patch("kubernetes.client.CustomObjectsApi.get_namespaced_custom_object")
    def test_unrecoverable_exception(self, mock_get: MagicMock):
        reconciler = LoopTest(("default", "dummy"))
        reconciler.unrecoverable_exception = True
        mock_get.return_value = {
            "metadata": {
                "resourceVersion": "1234",
                "namespace": "test",
                "name": "test",
                "uid": "1",
            }
        }
        reconciler.reconcilation_loop()
        self.assertEqual(reconciler.reconcile_call_count, 1)
