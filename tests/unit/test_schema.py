# pylint: skip-file
from typing import cast
import unittest
from unittest.mock import MagicMock, patch
from kubernetes import client

from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.schema import BaseCRD, BaseCRDProp, CRDProp, prop


test_api_group = GroupVersionInfo(api_version="v1", group="test", kind="Test")


class TestCrdProp(BaseCRDProp):
    test_sub_field = prop(str)


class NestedTestCrdProp(BaseCRDProp):
    test_sub_field = prop(TestCrdProp)


class TestCrdWithSubProp(BaseCRD):
    test_field = prop(TestCrdProp)
    nested_test_field = prop(NestedTestCrdProp)


class TestCrd(BaseCRD):
    test_field = prop(str)
    not_in_data = prop(int)


class TestInit(unittest.TestCase):

    def test_subprop(self):

        data = {
            "metadata": {"namespace": "test", "name": "name", "uid": "1234"},
            "spec": {
                "test_field": {"test_sub_field": "testing string"},
                "nested_test_field": {
                    "test_sub_field": {"test_sub_field": "testing string"}
                },
            },
            "status": {"some": "thing"},
        }

        test = TestCrdWithSubProp()
        test.load_data(data)
        self.assertEqual(test.test_field.test_sub_field, "testing string")
        self.assertEqual(
            test.nested_test_field.test_sub_field.test_sub_field, "testing string"
        )

        test.test_field.test_sub_field = "new value"
        test.nested_test_field.test_sub_field.test_sub_field = "new value"
        self.assertEqual(test.test_field.test_sub_field, "new value")
        self.assertEqual(
            test.nested_test_field.test_sub_field.test_sub_field, "new value"
        )

    def test_prop_types(self):
        supported_types = {
            str: "string",
            int: "integer",
            float: "number",
            dict: "object",
            bool: "boolean",
            list[str]: "array",
            list[int]: "array",
            list[float]: "array",
            list[bool]: "array",
        }
        for supported_type in supported_types:
            typed_prop = cast(CRDProp, prop(supported_type))
            self.assertEqual(typed_prop.typ, supported_types[supported_type])

        with self.assertRaises(TypeError):
            cast(CRDProp, prop(object))

    def test_load_data(self):
        inst = TestCrd()
        data = {
            "metadata": {"namespace": "test", "name": "name", "uid": "1234"},
            "spec": {"test_field": "testing string"},
            "status": {"some": "thing"},
        }

        inst.load_data(data)
        self.assertEqual(inst.test_field, "testing string")
        self.assertEqual(inst.namespace, "test")
        self.assertEqual(inst.name, "name")
        self.assertIsNotNone(inst.status)
        self.assertIsNotNone(inst.metadata)
        self.assertDictEqual(inst.status, {"some": "thing"})
        self.assertIsNone(inst.not_in_data)

    def test_load_data_by_value(self):
        data_1 = {"metadata": {"name": "test"}, "spec": {"test_field": "test"}}
        inst_1 = TestCrd(data=data_1)
        inst_2 = TestCrd(data=data_1)

        inst_1.test_field = "test2"

        self.assertNotEqual(inst_1.get_data(), inst_2.get_data())

    def test_sub_prop_set_data(self):
        data = {
            "metadata": {"namespace": "test", "name": "name", "uid": "1234"},
            "spec": {
                "test_field": {"test_sub_field": "testing string"},
                "nested_test_field": {
                    "test_sub_field": {"test_sub_field": "testing string"}
                },
            },
            "status": {"some": "thing"},
        }
        inst_1 = TestCrdWithSubProp(data=data)
        inst_2 = TestCrdWithSubProp(data=data)

        inst_1.test_field.test_sub_field = "test2"
        inst_2.test_field.test_sub_field = "test3"
        self.assertNotEqual(
            inst_1.get_data()["spec"]["test_field"]["test_sub_field"],
            inst_2.get_data()["spec"]["test_field"]["test_sub_field"],
        )

        inst_1.nested_test_field.test_sub_field.test_sub_field = "test4"
        inst_2.nested_test_field.test_sub_field.test_sub_field = "test5"
        self.assertNotEqual(
            inst_1.get_data()["spec"]["nested_test_field"]["test_sub_field"][
                "test_sub_field"
            ],
            inst_2.get_data()["spec"]["nested_test_field"]["test_sub_field"][
                "test_sub_field"
            ],
        )
        
        inst_1.nested_test_field.test_sub_field.test_sub_field = "test"
        inst_2.nested_test_field.test_sub_field.test_sub_field = "test"
        self.assertEquals(
            inst_1.get_data()["spec"]["nested_test_field"]["test_sub_field"][
                "test_sub_field"
            ],
            inst_2.get_data()["spec"]["nested_test_field"]["test_sub_field"][
                "test_sub_field"
            ],
        )
        
        


class TestInstance(unittest.TestCase):

    @patch("kubernetes.client.CustomObjectsApi.patch_namespaced_custom_object_status")
    @patch("kubernetes.client.CustomObjectsApi.patch_namespaced_custom_object")
    def test_patch_cr(self, patch_cr_mock: MagicMock, patch_cr_status_mock: MagicMock):

        data = {
            "metadata": {"namespace": "test", "name": "name", "uid": "1234"},
            "spec": {"test_field": "testing string"},
            "status": {"some": "thing"},
        }

        def mock_patch_cr_status(
            group, version, namespace, plural, name, body, **kwargs
        ):
            return {**data, **body}

        def mock_patch_cr(group, version, namespace, plural, name, body, **kwargs):
            return {**body}

        patch_cr_status_mock.side_effect = mock_patch_cr_status
        patch_cr_mock.side_effect = mock_patch_cr

        inst = TestCrd(api=client.CustomObjectsApi(), group_version=test_api_group)
        inst.load_data(data)

        inst.status = {"another": "one"}
        inst.test_field = "string_test"
        inst.patch()
        self.assertDictEqual(inst.status, {"another": "one"})
        self.assertEqual(inst.test_field, "string_test")
        patch_cr_status_mock.assert_called_once()
        patch_cr_mock.assert_called_once()

        inst.test_field = "string_test_2"
        inst.status = {"not": "updated"}
        inst.patch(patch_status=False)
        patch_cr_status_mock.assert_called_once()
        self.assertEqual(patch_cr_mock.call_count, 2)

    def test_wrong_patch_time(self):

        data = {
            "metadata": {"namespace": "test", "name": "name", "uid": "1234"},
            "spec": {"test_field": "testing string"},
            "status": {"some": "thing"},
        }

        inst = TestCrd(group_version=test_api_group)
        inst.load_data(data)

        with self.assertRaises(RuntimeError):
            inst.patch()

        inst = TestCrd(api=client.CustomObjectsApi())
        inst.load_data(data)

        with self.assertRaises(RuntimeError):
            inst.patch()

    def test_helpers(self):
        data = {
            "metadata": {"namespace": "test", "name": "name", "uid": "1234"},
            "spec": {"test_field": "testing string"},
            "status": {"some": "thing"},
        }

        inst = TestCrd(group_version=test_api_group, api=client.CustomObjectsApi())
        inst.load_data(data)

        with patch("kuroboros.schema.BaseCRD.patch") as mock_patch:
            mock_patch.return_value = None
            inst.add_finalizer("test-finalizer")
            inst.add_finalizer("test-finalizer-2")
            self.assertEqual(len(inst.finalizers), 2)

            inst.remove_finalizer("test-finalizer-2")
            self.assertEqual(len(inst.finalizers), 1)
            self.assertEqual(inst.has_finalizers(), True)

            owner_ref = inst.get_owner_ref()
            self.assertEqual(owner_ref.api_version, test_api_group.api_version)
            self.assertEqual(owner_ref.kind, test_api_group.kind)
            self.assertEqual(owner_ref.uid, inst.uid)
            self.assertEqual(owner_ref.name, inst.name)
            self.assertEqual(owner_ref.block_owner_deletion, True)
        pass
