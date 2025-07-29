from typing import Generic, Type, TypeVar, get_args, get_origin
import json
import base64

import falcon
import jsonpatch

from kuroboros import logger as klogger
from kuroboros.exceptions import MutationWebhookError, ValidationWebhookError
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.schema import BaseCRD

T = TypeVar("T", bound=BaseCRD)


class WebhookTypes:
    """
    The available webhook types
    """

    VALIDATION = "Validation"
    MUTATION = "Mutation"


class OperationsEnum:
    """
    Enum of kubernetes operations
    """

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class BaseWebhook(Generic[T]):
    """
    The Base Webhook Class, all webhook should implement this
    """

    name: str
    logger = klogger.root_logger.getChild(__name__)
    _endpoint: str
    _type: Type[T]
    _group_version_info: GroupVersionInfo
    _webhook_type: str
    _generic_base_type = None
    _endpoint_suffix: str

    @property
    def crd_type(self) -> Type[T]:
        """
        Returns the type of CRD that this webhook handle
        """
        return self._type

    @property
    def endpoint(self) -> str:
        """
        Returns the endpoint to this webhook.
        Its formed by <apiVersion>/<singular>/<webhook_suffix>
        """
        gvi = self._group_version_info
        return f"/{gvi.api_version}/{gvi.singular}/{self._endpoint_suffix}"

    def __init__(self, group_version_info: GroupVersionInfo) -> None:
        self._group_version_info = group_version_info

        pretty_version = group_version_info.pretty_version_str()
        singular = group_version_info.singular.capitalize()
        self.name = f"{singular}{pretty_version}{self._webhook_type}Webhook"
        self.logger = self.logger.getChild(self.name)
        t_type = None
        for base in getattr(self.__class__, "__orig_bases__", []):
            origin = get_origin(base)
            if origin is self._generic_base_type:
                t_type = get_args(base)[0]
                break

        if t_type is None or BaseCRD not in t_type.__mro__:
            raise RuntimeError(
                "Could not determine generic type T. "
                f"Subclass Base{self._webhook_type}Webhook with a concrete CRD type"
            )

        self._type = t_type

    def process(self, body: bytes):
        """
        Processess the raw request
        """
        raise NotImplementedError("Subclasses must implement the process method")

    def on_post(self, req: falcon.Request, resp: falcon.Response) -> None:
        """
        POST on `endpoint`
        """
        raw = req.stream.read()
        response, status, headers = self.process(raw)
        resp.status = status
        resp.text = response
        for k, v in (headers or {}).items():
            resp.set_header(k, v)
        self.logger.info(f"{req.method} {req.path} {status} {req.access_route}")


class BaseValidationWebhook(BaseWebhook, Generic[T]):
    """
    Kuroboros BaseValidationWebhook.
    Registers an endpoint in /<apiVersion>/<singular>/validate
    """

    def __init__(self, group_version_info: GroupVersionInfo) -> None:
        self._webhook_type = WebhookTypes.VALIDATION
        self._generic_base_type = BaseValidationWebhook
        self._endpoint_suffix = "validate"
        super().__init__(group_version_info)

    def validate_create(self, data: T) -> None:
        """
        Define the create validation logic
        """

    def validate_update(self, data: T, old_data: T) -> None:
        """
        Define the update validation logic
        """

    def validate_delete(self, old_data: T) -> None:
        """
        Define the delete validation logic
        """

    def process(self, body: bytes):
        self.logger.debug("processing validation webhook")
        request = None
        try:
            admission_review = json.loads(body.decode("utf-8"))
            request = admission_review.get("request", {})
            operation = request.get("operation")
            obj = request.get("object")
            old_obj = request.get("oldObject")

            # Convert obj/old_obj to CRD instance if needed
            crd_instance = (
                self._type(api=None, group_version=None, read_only=True, data=obj)
                if obj
                else None
            )
            old_crd_instance = (
                self._type(api=None, group_version=None, read_only=True, data=old_obj)
                if old_obj
                else None
            )

            if operation == OperationsEnum.CREATE:
                assert (
                    crd_instance is not None
                ), "CRD instance cannot be None for create operation"
                self.validate_create(crd_instance)
            elif operation == OperationsEnum.UPDATE:
                assert (
                    crd_instance is not None
                ), "CRD instance cannot be None for update operation"
                assert (
                    old_crd_instance is not None
                ), "old CRD instance cannot be None for update operation"
                self.validate_update(crd_instance, old_crd_instance)
            elif operation == OperationsEnum.DELETE:
                assert (
                    crd_instance is None
                ), "CRD instance must be None for delete operation"
                assert (
                    old_crd_instance is not None
                ), "old CRD instance cannot be None for delete operation"
                self.validate_delete(old_crd_instance)
            else:
                raise ValidationWebhookError(f"unsupported operation: {operation}")

            self.logger.debug("validation passed")
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {"uid": request.get("uid"), "allowed": True},
            }
            return (
                json.dumps(response),
                falcon.HTTP_200,
                {"Content-Type": "application/json"},
            )
        except ValidationWebhookError as e:
            self.logger.warning(f"validation failed: {e.reason}")
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": request.get("uid") if request else None,
                    "allowed": False,
                    "status": {"message": e.reason},
                },
            }
            return (
                json.dumps(response),
                falcon.HTTP_200,
                {"Content-Type": "application/json"},
            )
        except AssertionError as e:
            self.logger.warning(f"validation failed: {e}")
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": request.get("uid") if request else None,
                    "allowed": False,
                    "status": {
                        "message": (
                            e.args[0]
                            if e.args
                            else "Validation failed due to assertion error"
                        )
                    },
                },
            }
            return (
                json.dumps(response),
                falcon.HTTP_400,
                {"Content-Type": "application/json"},
            )
        except Exception as e:  # pylint: disable=broad-except
            self.logger.error(f"failed to decode webhook body: {e}")
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": request.get("uid") if request else None,
                    "allowed": False,
                    "status": {"message": "Validation webhook error"},
                },
            }
            return (
                json.dumps(response),
                falcon.HTTP_500,
                {"Content-Type": "application/json"},
            )


class BaseMutationWebhook(BaseWebhook, Generic[T]):
    """
    Kuroboros BaseMutationWebhook.
    Registers an endpoint in /<apiVersion>/<singular>/mutate
    """
    def __init__(self, group_version_info: GroupVersionInfo) -> None:
        self._webhook_type = WebhookTypes.MUTATION
        self._generic_base_type = BaseMutationWebhook
        self._endpoint_suffix = "mutate"
        super().__init__(group_version_info)

    def mutate(self, data: T) -> T:
        """
        Define the mutation logic
        """
        return data

    def process(self, body: bytes):
        self.logger.debug("processing mutation webhook")
        request = None
        try:
            admission_review = json.loads(body.decode("utf-8"))
            request = admission_review.get("request", {})
            operation = request.get("operation")
            obj = request.get("object")

            # Convert obj to CRD instance if needed
            crd_instance = (
                self._type(api=None, group_version=None, data=obj) if obj else None
            )
            mutate_instance = (
                self._type(api=None, group_version=None, data=obj) if obj else None
            )

            if operation not in (OperationsEnum.CREATE, OperationsEnum.UPDATE):
                raise MutationWebhookError(f"unsupported operation: {operation}")

            assert crd_instance is not None, "CRD instance cannot be None for mutation"
            assert (
                mutate_instance is not None
            ), "CRD instance cannot be None for mutation"

            mutated_crd = self.mutate(mutate_instance)
            assert mutated_crd is not None, "Mutated CRD instance cannot be None"
            patch_ops = jsonpatch.JsonPatch.from_diff(
                crd_instance.get_data(), mutated_crd.get_data()
            ).patch
            self.logger.debug(f"crd_instance: {crd_instance.get_data()}")
            self.logger.debug(f"mutated_crd: {mutated_crd.get_data()}")
            self.logger.debug(f"patch operations: {patch_ops}")
            patch_b64 = base64.b64encode(json.dumps(patch_ops).encode("utf-8")).decode(
                "utf-8"
            )

            self.logger.debug("mutation passed")
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": request.get("uid"),
                    "allowed": True,
                    "patchType": "JSONPatch",
                    "patch": (patch_b64),
                },
            }
            return (
                json.dumps(response),
                falcon.HTTP_200,
                {"Content-Type": "application/json"},
            )
        except MutationWebhookError as e:
            self.logger.warning(f"mutation failed: {e.reason}")
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": request.get("uid") if request else None,
                    "allowed": False,
                    "status": {"message": e.reason},
                },
            }
            return (
                json.dumps(response),
                falcon.HTTP_200,
                {"Content-Type": "application/json"},
            )
        except AssertionError as e:
            self.logger.warning(f"mutation failed: {e}")
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": request.get("uid") if request else None,
                    "allowed": False,
                    "status": {
                        "message": (
                            e.args[0]
                            if e.args
                            else "Mutation failed due to assertion error"
                        )
                    },
                },
            }
            return (
                json.dumps(response),
                falcon.HTTP_400,
                {"Content-Type": "application/json"},
            )
        except Exception as e:  # pylint: disable=broad-except
            self.logger.error(f"failed to decode mutation webhook body: {e}")
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": request.get("uid") if request else None,
                    "allowed": False,
                    "status": {"message": "Mutation webhook error"},
                },
            }
            return (
                json.dumps(response),
                falcon.HTTP_500,
                {"Content-Type": "application/json"},
            )
