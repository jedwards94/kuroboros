from typing import Callable, Generic, Tuple, Type, TypeVar, get_args, get_origin
from kuroboros import logger
from kuroboros.exceptions import ValidationWebhookError
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.schema import BaseCRD
import json
from kubernetes import client

T = TypeVar("T", bound=BaseCRD)


class BaseValidationWebhook(Generic[T]):
    name: str
    
    _endpoint: str
    
    _logger = logger.root_logger.getChild(__name__)
    _type: Type[T]
    _group_version_info: GroupVersionInfo
    
    
    def __init__(self, group_version_info: GroupVersionInfo):
        self._group_version_info = group_version_info
        major = self._group_version_info.major
        stability = self._group_version_info.stability.capitalize()
        minor = self._group_version_info.minor if self._group_version_info.minor != 0 else ""
        self._endpoint = f"/{group_version_info.api_version}/{group_version_info.singular}/validate"
        self.name = f"{group_version_info.singular.capitalize()}V{major}{stability}{minor}ValidationWebhook"
        self._logger = self._logger.getChild(self.name)
        t_type = None
        for base in getattr(self.__class__, "__orig_bases__", []):
            origin = get_origin(base)
            if origin is BaseValidationWebhook:
                t_type = get_args(base)[0]
                break
        
        if t_type is None or BaseCRD not in t_type.__mro__:
            raise RuntimeError(
                "Could not determine generic type T. "
                "Subclass BaseValidationWebhook with a concrete type (e.g., `class MyValidationWebhook(BaseValidationWebhook[MyCRD]): ...`)"
            )
        
        self._type = t_type
    
    
    def validate_create(self, data: T) -> None:
        # Implement your validation logic here
        # For now, we assume the data is always valid
        pass
    def validate_update(self, data: T, old_data: T) -> None:
        # Implement your validation logic here
        # For now, we assume the data is always valid
        pass
    def validate_delete(self) -> None:
        # Implement your validation logic here
        # For now, we assume the data is always valid
        pass
    
    def process(self, body: bytes):
        self._logger.info("processing validation webhook")
        request = None
        try:
            admission_review = json.loads(body.decode('utf-8'))
            request = admission_review.get('request', {})
            operation = request.get('operation')
            obj = request.get('object')
            old_obj = request.get('oldObject')

            # Convert obj/old_obj to CRD instance if needed
            crd_instance = self._type(api=None, group_version=None, read_only=True, data=obj) if obj else None
            old_crd_instance = self._type(api=None, group_version=None, read_only=True, data=old_obj) if old_obj else None

            if operation == 'CREATE':
                assert crd_instance is not None, "CRD instance cannot be None for create operation"
                self.validate_create(crd_instance)
            elif operation == 'UPDATE':
                assert crd_instance is not None, "CRD instance cannot be None for update operation"
                assert old_crd_instance is not None, "old CRD instance cannot be None for update operation"
                self.validate_update(crd_instance, old_crd_instance)
            elif operation == 'DELETE':
                assert crd_instance is None, "CRD instance should be None for delete operation"
                self.validate_delete()
            else:
                raise ValidationWebhookError(f"Unsupported operation: {operation}")

            self._logger.info("validation passed")
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": request.get('uid'),
                    "allowed": True
                }
            }
            return json.dumps(response).encode('utf-8'), 200, {"Content-Type": "application/json"}
        except ValidationWebhookError as e:
            self._logger.warning(f"Validation failed: {e.reason}")
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": request.get('uid') if request else None,
                    "allowed": False,
                    "status": {"message": e.reason}
                }
            }
            return json.dumps(response).encode('utf-8'), 200, {"Content-Type": "application/json"}
        except AssertionError as e:
            self._logger.warning(f"Validation failed: {e}")
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": request.get('uid') if request else None,
                    "allowed": False,
                    "status": {"message": e.args[0] if e.args else "Validation failed due to assertion error"}
                }
            }
            return json.dumps(response).encode('utf-8'), 400, {"Content-Type": "application/json"}
        except Exception as e:
            self._logger.error(f"Failed to decode webhook body: {e}")
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": request.get('uid') if request else None,
                    "allowed": False,
                    "status": {"message": "Validation webhook error"}
                }
            }
            return json.dumps(response).encode('utf-8'), 500, {"Content-Type": "application/json"}

    def endpoint(self) -> Tuple[str, Callable]:
        return (self._endpoint, self.process)