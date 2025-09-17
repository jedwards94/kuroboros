from typing import cast
import unittest
from unittest.mock import patch

from kuroboros.schema import CRDSchema, prop
from kuroboros.extended_api import ExtendedApi


class TestSchema(CRDSchema):
    kind = prop(str)
    api_version = prop(str)


class TestApi(unittest.TestCase):

    @patch("kubernetes.dynamic.DynamicClient")
    def test_dict_serialization(self, _):
        api = ExtendedApi()
        data = {
            "kind": "TestSchema",
            "api_version": "test/v1",
            "metadata": {"name": "test", "namespace": "test"},
        }
        serialized = api.serialize(data)
        self.assertDictEqual(cast(dict, serialized), data)
    
    @patch("kubernetes.dynamic.DynamicClient")
    def test_class_serialization(self, _):
        api = ExtendedApi()
        data = TestSchema(api_version="test/v1", kind="TestSchema")
        serialized = api.serialize(data)
        self.assertIn("apiVersion", cast(dict, serialized))

    @patch("kubernetes.dynamic.DynamicClient")
    def test_dict_deserialization(self, _):
        api = ExtendedApi()
        data = {
            "kind": "TestSchema",
            "apiVersion": "test/v1",
            "metadata": {"name": "test", "namespace": "test"},
        }
        deserialized = cast(TestSchema, api.deserialize(data, TestSchema))
        self.assertIsInstance(deserialized, TestSchema)
        self.assertEqual(deserialized.kind, "TestSchema")
        self.assertEqual(deserialized.api_version, "test/v1")

    @patch("kubernetes.dynamic.DynamicClient")
    def test_openapi_deserialization(self, _):
        api = ExtendedApi()
        data = TestSchema(
            api_version="test/v1",
            kind="TestSchema",
            metadata={"name": "test", "namespace": "test"},
        )
        deserialized = cast(TestSchema, api.deserialize(data, TestSchema))
        self.assertIsInstance(deserialized, TestSchema)
        self.assertEqual(deserialized.kind, "TestSchema")
