# pylint: skip-file
import unittest

import falcon
from kuroboros.schema import CRDSchema, prop
from kuroboros.webhook import BaseMutationWebhook, BaseValidationWebhook, Request
from kuroboros.exceptions import ValidationWebhookError
from kuroboros.group_version_info import GroupVersionInfo
import json


class DummyCRD(CRDSchema):
    fail = prop(bool)


class DummyValidationWebhook(BaseValidationWebhook[DummyCRD]):

    def validate(self, request: Request[DummyCRD]) -> None:
        if getattr(request.object, "fail", False):
            raise ValidationWebhookError("Create failed")


def make_group_version():
    return GroupVersionInfo(
        group="testgroup", api_version="v1", plural="dummies", kind="Dummy"
    )


class TestValidationWebhook(unittest.TestCase):
    def setUp(self):
        DummyValidationWebhook.set_gvi(make_group_version())
        self.webhook = DummyValidationWebhook()

    def test_validate_create_success(self):
        DummyValidationWebhook.crd_type()
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {
                "kind": {},
                "resource": {},
                "requestKind": {},
                "requestResource": {},
                "name": "dummy",
                "namespace": "test",
                "userInfo": {},
                "dryRun": False,
                "uid": "123",
                "operation": "CREATE",
                "object": {"fail": False},
            },
        }
        body = json.dumps(admission_review).encode("utf-8")
        resp, status, _ = self.webhook.process(body)
        self.assertEqual(status, falcon.HTTP_200)
        self.assertIn('"allowed": true', resp)


    def test_validate_update_success(self):
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {
                "uid": "123",
                "kind": {},
                "resource": {},
                "requestKind": {},
                "requestResource": {},
                "name": "dummy",
                "namespace": "test",
                "userInfo": {},
                "dryRun": False,
                "operation": "UPDATE",
                "object": {"fail": False},
                "oldObject": {"fail": False},
            },
        }
        body = json.dumps(admission_review).encode("utf-8")
        resp, status, _ = self.webhook.process(body)
        self.assertEqual(status, falcon.HTTP_200)
        self.assertIn('"allowed": true', resp)

    def test_validate_update_failure(self):
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {
                "uid": "123",
                "kind": {},
                "resource": {},
                "requestKind": {},
                "requestResource": {},
                "name": "dummy",
                "namespace": "test",
                "userInfo": {},
                "dryRun": False,
                "operation": "UPDATE",
                "object": {"fail": True},
                "oldObject": None,
            },
        }
        body = json.dumps(admission_review).encode("utf-8")
        resp, status, _ = self.webhook.process(body)
        self.assertEqual(status, falcon.HTTP_200)
        self.assertIn('"allowed": false', resp)

    def test_validate_delete_success(self):
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {
                "uid": "123",
                "kind": {},
                "resource": {},
                "requestKind": {},
                "requestResource": {},
                "name": "dummy",
                "namespace": "test",
                "userInfo": {},
                "dryRun": False,
                "operation": "DELETE",
                "object": None,
                "oldObject": {"fail": False},
            },
        }
        body = json.dumps(admission_review).encode("utf-8")
        resp, status, headers = self.webhook.process(body)
        self.assertEqual(status, falcon.HTTP_200)
        self.assertIn('"allowed": true', resp)

    def test_invalid_json(self):
        body = b"not a json"
        with self.assertRaises(json.JSONDecodeError):
            self.webhook.process(body)


class DummyMutationCRD(CRDSchema):
    mutate = prop(bool)
    mutated = prop(bool)
    pass


class DummyMutationWebhook(BaseMutationWebhook[DummyMutationCRD]):

    def __init__(self):
        super().__init__()

    def mutate(self, request: Request[DummyMutationCRD]) -> DummyMutationCRD:
        # Example mutation: add a field if not present
        assert request.object is not None
        d = request.object.to_dict()
        if d.get("mutate", False):
            d["mutated"] = True
        else:
            d["mutated"] = False
        mutated = DummyMutationCRD(data=d)
        return mutated


class TestMutationWebhook(unittest.TestCase):
    def setUp(self):
        DummyMutationWebhook.set_gvi(make_group_version())
        self.webhook = DummyMutationWebhook()

    def test_mutate_create_success(self):
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {
                "uid": "456",
                "operation": "CREATE",
                "kind": {},
                "resource": {},
                "requestKind": {},
                "requestResource": {},
                "name": "dummy",
                "namespace": "test",
                "userInfo": {},
                "dryRun": False,
                "object": {
                    "metadata": {"name": "dummy", "namespace": "test"},
                    "mutate": True,
                },
            },
        }
        body = json.dumps(admission_review).encode("utf-8")
        resp, status, _ = self.webhook.process(body)
        self.assertEqual(status, falcon.HTTP_200)
        self.assertIn('"allowed": true', resp)
        self.assertIn('"patchType": "JSONPatch"', resp)

    def test_mutate_update_success(self):
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {
                "uid": "789",
                "operation": "UPDATE",
                "kind": {},
                "resource": {},
                "requestKind": {},
                "requestResource": {},
                "name": "dummy",
                "namespace": "test",
                "userInfo": {},
                "dryRun": False,
                "object": {
                    "metadata": {"name": "dummy", "namespace": "test"},
                    "mutate": False,
                },
            },
        }
        body = json.dumps(admission_review).encode("utf-8")
        resp, status, _ = self.webhook.process(body)
        self.assertEqual(status, falcon.HTTP_200)
        self.assertIn('"allowed": true', resp)
        self.assertIn('"patchType": "JSONPatch"', resp)

    def test_mutate_invalid_operation(self):
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {
                "uid": "999",
                "kind": {},
                "resource": {},
                "requestKind": {},
                "requestResource": {},
                "name": "dummy",
                "namespace": "test",
                "userInfo": {},
                "dryRun": False,
                "operation": "DELETE",
                "object": {
                    "metadata": {"name": "dummy", "namespace": "test"},
                    "mutate": True
                },
            },
        }
        body = json.dumps(admission_review).encode("utf-8")
        resp, status, _ = self.webhook.process(body)
        self.assertEqual(status, falcon.HTTP_200)
        self.assertIn('"allowed": false', resp)
        self.assertIn("unsupported operation", resp)

    def test_mutate_invalid_json(self):
        body = b"not a json"
        with self.assertRaises(json.JSONDecodeError):
            self.webhook.process(body)
