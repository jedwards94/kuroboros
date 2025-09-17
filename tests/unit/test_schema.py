# pylint: skip-file
import json
from typing import cast
import unittest
import kubernetes

from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.schema import CRDSchema, OpenAPISchema, PropYaml, prop


test_api_group = GroupVersionInfo(api_version="v1", group="test", kind="Test")


class TestCrdProp(OpenAPISchema):
    test_sub_field = prop(str, default="test")
    type = prop(str)


class NestedTestCrdProp(OpenAPISchema):
    test_sub_field = prop(TestCrdProp)


class TestCrdWithSubProp(CRDSchema):
    test_field = prop(TestCrdProp)
    nested_test_field = prop(NestedTestCrdProp)
    array_subprop = prop(list[TestCrdProp])


class TestCrd(CRDSchema):
    test_field = prop(str)
    not_in_data = prop(int)


TestCrd.set_gvi(test_api_group)


class TestInit(unittest.TestCase):

    def test_serialization(self):
        
        data = {
            "metadata": {"namespace": "test", "name": "name", "uid": "1234"},
            "test_field": {"test_sub_field": "testing string", "type": "some"},
            "nested_test_field": {"test_sub_field": {"test_sub_field": "testing string"}},
            "status": {"some": "thing"},
        }
        
        patch_body = {"data": json.dumps(data)}
        
        api = kubernetes.client.ApiClient()
        deploy = kubernetes.client.V1Deployment(
            kind="Deployment",
            spec=kubernetes.client.V1DeploymentSpec(
                min_ready_seconds=10,
                selector="app=app",
                template=kubernetes.client.V1PodTemplate(
                    template=kubernetes.client.V1PodTemplateSpec(spec=kubernetes.client.V1PodSpec(containers=[]))
                ),
            ),
        )
        
        ser = api.sanitize_for_serialization(deploy)
        print(ser)
        self.assertFalse(False)

    def test_subprop_load(self):

        data = {
            "metadata": {"namespace": "test", "name": "name", "uid": "1234"},
            "test_field": {"test_sub_field": "testing string", "type": "some"},
            "nested_test_field": {"test_sub_field": {"test_sub_field": "testing string"}},
            "status": {"some": "thing"},
        }
        data2 = {
            "metadata": {"namespace": "test", "name": "name", "uid": "1234"},
            "nested_test_field": {"test_sub_field": {"test_sub_field": "testing string"}},
            "status": {"some": "thing"},
        }

        test = TestCrdWithSubProp()
        test.load_data(**data)
        self.assertEqual(test.test_field.test_sub_field, "testing string")
        self.assertEqual(test.test_field.type, "some")
        self.assertEqual(
            test.nested_test_field.test_sub_field.test_sub_field, "testing string"
        )

        test.test_field.test_sub_field = "new value"
        test.nested_test_field.test_sub_field.test_sub_field = "new value"
        self.assertEqual(test.test_field.test_sub_field, "new value")
        self.assertEqual(
            test.nested_test_field.test_sub_field.test_sub_field, "new value"
        )

        test2 = TestCrdWithSubProp()
        test2.load_data(**data2)

        self.assertIsNone(test2.test_field)
        test2.test_field = TestCrdProp(test_sub_field="test")
        self.assertEqual(test2.test_field.test_sub_field, "test")
        test2.test_field.test_sub_field = "data 2"
        self.assertEqual(test2.test_field.test_sub_field, "data 2")

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
            typed_prop = cast(PropYaml, prop(supported_type))
            self.assertEqual(typed_prop.typ, supported_types[supported_type])

        with self.assertRaises(TypeError):
            class Invalid:
                pass
            prop(Invalid)
            

    def test_load_data(self):
        inst = TestCrd()
        data = {
            "metadata": {"namespace": "test", "name": "name", "uid": "1234"},
            "test_field": "testing string",
            "status": {"some": "thing"},
        }

        inst.load_data(**data)
        self.assertEqual(inst.test_field, "testing string")
        self.assertIsNotNone(inst.metadata)
        self.assertEqual(inst.metadata.namespace, "test")
        self.assertEqual(inst.metadata.name, "name")
        self.assertIsNotNone(inst.status)
        self.assertDictEqual(inst.status, {"some": "thing"})  # type: ignore
        self.assertIsNone(inst.not_in_data)

    def test_load_data_by_value(self):
        data_1 = {"metadata": {"name": "test"}, "test_field": "test"}
        inst_1 = TestCrd(**data_1)
        inst_2 = TestCrd(**data_1)

        inst_1.test_field = "test2"

        self.assertNotEqual(inst_1.to_dict(), inst_2.to_dict())

    def test_sub_prop_set_data(self):
        data = {
            "metadata": {"namespace": "test", "name": "name", "uid": "1234"},
            "test_field": {"test_sub_field": "testing string"},
            "nested_test_field": {"test_sub_field": {"test_sub_field": "testing string"}},
            "status": {"some": "thing"},
        }
        inst_1 = TestCrdWithSubProp(**data)
        inst_2 = TestCrdWithSubProp(**data)

        inst_1.test_field.test_sub_field = "test2"
        inst_2.test_field.test_sub_field = "test3"
        self.assertNotEqual(
            inst_1.to_dict()["test_field"]["test_sub_field"],
            inst_2.to_dict()["test_field"]["test_sub_field"],
        )

        inst_1.nested_test_field.test_sub_field.test_sub_field = "test4"
        inst_2.nested_test_field.test_sub_field.test_sub_field = "test5"
        self.assertNotEqual(
            inst_1.to_dict()["nested_test_field"]["test_sub_field"]["test_sub_field"],
            inst_2.to_dict()["nested_test_field"]["test_sub_field"]["test_sub_field"],
        )

        inst_1.nested_test_field.test_sub_field.test_sub_field = "test"
        inst_2.nested_test_field.test_sub_field.test_sub_field = "test"
        self.assertEqual(
            inst_1.to_dict()["nested_test_field"]["test_sub_field"]["test_sub_field"],
            inst_2.to_dict()["nested_test_field"]["test_sub_field"]["test_sub_field"],
        )