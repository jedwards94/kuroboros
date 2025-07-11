import unittest

import falcon
from kuroboros.webhook import BaseValidationWebhook
from kuroboros.schema import BaseCRD
from kuroboros.exceptions import ValidationWebhookError
from kuroboros.group_version_info import GroupVersionInfo
import json

class DummyCRD(BaseCRD):
    pass

class DummyValidationWebhook(BaseValidationWebhook[DummyCRD]):
    _endpoint = "/validate"
    def __init__(self, group_version_info):
        super().__init__(group_version_info)
        self.group_version_info = group_version_info
    def validate_create(self, data: DummyCRD) -> None:
        if getattr(data, "fail", False):
            raise ValidationWebhookError("Create failed")
    def validate_update(self, data: DummyCRD, old_data: DummyCRD) -> None:
        if getattr(data, "fail", False):
            raise ValidationWebhookError("Update failed")
    def validate_delete(self) -> None:
        pass

def make_group_version():
    return GroupVersionInfo(
        group="testgroup",
        api_version="v1",
        plural="dummies",
        kind="Dummy"
    )

class TestWebhook(unittest.TestCase):
    def setUp(self):
        self.webhook = DummyValidationWebhook(make_group_version())

    def test_validate_create_success(self):
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {
                "uid": "123",
                "operation": "CREATE",
                "object": {"fail": False}
            }
        }
        body = json.dumps(admission_review).encode("utf-8")
        resp, status, headers = self.webhook.process(body)
        self.assertEqual(status, falcon.HTTP_200)
        self.assertIn('"allowed": true', resp)

    def test_validate_create_failure(self):
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {
                "uid": "123",
                "operation": "CREATE",
                "object": None
            }
        }
        body = json.dumps(admission_review).encode("utf-8")
        resp, status, headers = self.webhook.process(body)
        self.assertEqual(status, falcon.HTTP_400)
        self.assertIn('"allowed": false', resp)

    def test_validate_update_success(self):
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {
                "uid": "123",
                "operation": "UPDATE",
                "object": {"fail": False},
                "oldObject": {"fail": False}
            }
        }
        body = json.dumps(admission_review).encode("utf-8")
        resp, status, headers = self.webhook.process(body)
        self.assertEqual(status, falcon.HTTP_200)
        self.assertIn('"allowed": true', resp)

    def test_validate_update_failure(self):
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {
                "uid": "123",
                "operation": "UPDATE",
                "object": {"fail": True},
                "oldObject": None
            }
        }
        body = json.dumps(admission_review).encode("utf-8")
        resp, status, headers = self.webhook.process(body)
        self.assertEqual(status, falcon.HTTP_400)
        self.assertIn('"allowed": false', resp)

    def test_validate_delete_success(self):
        admission_review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {
                "uid": "123",
                "operation": "DELETE",
                "object": None
            }
        }
        body = json.dumps(admission_review).encode("utf-8")
        resp, status, headers = self.webhook.process(body)
        self.assertEqual(status, falcon.HTTP_200)
        self.assertIn('"allowed": true', resp)

    def test_invalid_json(self):
        body = b"not a json"
        resp, status, headers = self.webhook.process(body)
        self.assertEqual(status, falcon.HTTP_500)
        self.assertIn('Validation webhook error', resp)

if __name__ == "__main__":
    unittest.main()
