import unittest
from unittest.mock import patch, MagicMock
from kuroboros.operator import Operator
from kuroboros.controller import Controller
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.reconciler import BaseReconciler
from kuroboros.schema import BaseCRD, prop

class TestCrd(BaseCRD):
    test_field = prop(str)


class DummyReconciler(BaseReconciler[TestCrd]):
    pass

def make_group_version():
    return GroupVersionInfo(
        group="testgroup",
        api_version="v1",
        plural="dummies",
        kind="Dummy"
    )

class TestOperator(unittest.TestCase):
    def setUp(self):
        patcher1 = patch('kuroboros.operator.config.load_kube_config')
        patcher2 = patch('kuroboros.operator.config.load_incluster_config')
        patcher3 = patch('kuroboros.controller.Controller._check_permissions')
        self.addCleanup(patcher1.stop)
        self.addCleanup(patcher2.stop)
        self.addCleanup(patcher3.stop)
        self.mock_kube = patcher1.start()
        self.mock_incluster = patcher2.start()
        self.mock_check_permissions = patcher3.start()

    def test_operator_initializes(self):
        operator = Operator()
        self.assertIsInstance(operator, Operator)
        self.assertIsInstance(operator.controllers, list)
        self.assertIsInstance(operator.namespace, str)
        self.assertIsInstance(operator.uid, str)

    def test_add_controller_adds_controller(self):
        operator = Operator()
        group_version = make_group_version()
        reconciler = DummyReconciler(group_version)
        with patch('kuroboros.operator.Gauge'):
            operator.add_controller('test', group_version, reconciler)
        self.assertEqual(len(operator.controllers), 1)
        self.assertIsInstance(operator.controllers[0], Controller)

    def test_add_controller_while_running_raises(self):
        operator = Operator()
        operator._running = True
        group_version = make_group_version()
        reconciler = DummyReconciler(group_version)
        with patch('kuroboros.operator.Gauge'):
            with self.assertRaises(RuntimeError):
                operator.add_controller('test', group_version, reconciler)
        operator._running = False

    def test_add_duplicate_controller_raises(self):
        operator = Operator()
        group_version = make_group_version()
        reconciler = DummyReconciler(group_version)
        with patch('kuroboros.operator.Gauge'):
            operator.add_controller('test', group_version, reconciler)
            with self.assertRaises(RuntimeError):
                operator.add_controller('test', group_version, reconciler)

    def test_start_without_controllers_raises(self):
        operator = Operator()
        with self.assertRaises(RuntimeError):
            operator.start()

    def test_start_twice_raises(self):
        operator = Operator()
        group_version = make_group_version()
        reconciler = DummyReconciler(group_version)
        with patch('kuroboros.operator.Gauge'):
            operator.add_controller('test', group_version, reconciler)
        operator._running = True
        with self.assertRaises(RuntimeError):
            operator.start()
        operator._running = False

    def test_metrics_loop_runs(self):
        operator = Operator()
        group_version = make_group_version()
        reconciler = DummyReconciler(group_version)
        with patch('kuroboros.operator.Gauge') as mock_gauge:
            operator.add_controller('test', group_version, reconciler)
            metric = mock_gauge.return_value
            # Patch the class-level private dict
            threads_by_reconciler = operator._threads_by_reconciler
            threads_by_reconciler[operator.controllers[0].reconciler] = metric
            with patch('time.sleep', side_effect=Exception('break')):
                try:
                    operator._metrics()
                except Exception:
                    pass
            metric.labels.assert_called()

