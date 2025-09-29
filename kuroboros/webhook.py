from dataclasses import dataclass
from typing import ClassVar, Generic, Type, TypeVar, cast, get_args, get_origin
import json
import base64
import uuid

import caseconverter
import falcon
import jsonpatch

from kuroboros import logger as klogger
from kuroboros.exceptions import MutationWebhookError, ValidationWebhookError
from kuroboros.extended_api import ExtendedClient
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.schema import CRDSchema

T = TypeVar("T", bound=CRDSchema)


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


@dataclass
class Request(Generic[T]):
    """
    AdmissionReview v1 request.
    https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/#request
    """

    uid: str
    kind: dict
    resource: dict
    sub_resource: str | None
    request_kind: dict
    request_resource: dict
    request_sub_resource: str | None
    name: str
    namespace: str | None
    user_info: dict
    object: T | None
    old_object: T | None
    options: dict | None
    dry_run: bool
    operation: str


class BaseWebhook(Generic[T]):
    """
    The Base Webhook Class, all webhook should implement this
    """

    __group_version_info: ClassVar[GroupVersionInfo]
    _generic_base_type: ClassVar = None

    name: str
    logger = klogger.root_logger.getChild(__name__)
    register_on = []

    _endpoint: str
    _type: Type[T]
    _webhook_type: str
    _endpoint_suffix: str
    _extended_client: ExtendedClient

    @classmethod
    def set_gvi(cls, gvi: GroupVersionInfo) -> None:
        """
        Sets the GroupVersionInfo of the Reconciler
        """
        cls.__group_version_info = gvi

    @classmethod
    def get_config_dict(cls):
        """
        Returns the config dict to generate the kubernetes YAML
        """
        return {
            "crd_name": cls.__group_version_info.crd_name,
            "group": cls.__group_version_info.group,
            "singular": cls.__group_version_info.singular,
            "plural": cls.__group_version_info.plural,
            "api_version": cls.__group_version_info.api_version,
            "operations": cls.register_on,
            "scope": cls.__group_version_info.scope,
        }

    @classmethod
    def crd_type(cls) -> Type[T]:
        """
        Returns the type of CRD that this webhook handle
        """
        t_type = None
        for base in getattr(cls, "__orig_bases__", []):
            origin = get_origin(base)
            if origin in (BaseValidationWebhook, BaseMutationWebhook):
                t_type = get_args(base)[0]
                break

        if t_type is None or CRDSchema not in t_type.__mro__:
            raise RuntimeError("Could not determine generic type T. ")

        return t_type

    @property
    def endpoint(self) -> str:
        """
        Returns the endpoint to this webhook.
        Its formed by <apiVersion>/<singular>/<webhook_suffix>
        """
        gvi = self.__group_version_info
        return f"/{gvi.api_version}/{gvi.singular}/{self._endpoint_suffix}"

    def __init__(self) -> None:
        pretty_version = self.__group_version_info.pversion()
        singular = self.__group_version_info.singular.capitalize()
        self.name = caseconverter.pascalcase(
            f"{singular}{pretty_version}{self._webhook_type}Webhook"
        )
        self.logger = self.logger.getChild(self.name)
        self._extended_client = ExtendedClient()

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
        uid = json.loads(raw.decode("utf-8")).get("request", {}).get("uid", str(uuid.uuid4()))
        try:
            response, status, headers = self.process(raw)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.logger.error(e)
            response, status, headers = (
                json.dumps(
                    {
                        "apiVersion": "admission.k8s.io/v1",
                        "kind": "AdmissionReview",
                        "response": {
                            "uid": uid,
                            "allowed": False,
                            "status": {
                                "message": f"{self._webhook_type} webhook error"
                            },
                        },
                    }
                ),
                falcon.HTTP_500,
                {"Content-Type": "application/json"},
            )
        resp.status = status
        resp.text = response
        for k, v in (headers or {}).items():
            resp.set_header(k, v)

    def _serialize(self, obj):
        return self._extended_client.sanitize_for_serialization(obj)


class BaseValidationWebhook(BaseWebhook, Generic[T]):
    """
    Kuroboros BaseValidationWebhook.
    Registers an endpoint in /<apiVersion>/<singular>/validate
    """

    _webhook_type = WebhookTypes.VALIDATION
    _endpoint_suffix = "validate"

    register_on = [OperationsEnum.CREATE, OperationsEnum.UPDATE, OperationsEnum.DELETE]

    def validate(self, request: Request[T]) -> None:
        """
        Define validation logic
        """

    def process(self, body: bytes):
        self.logger.debug("processing validation webhook")
        request: Request[T] | None = None
        admission_review = json.loads(body.decode("utf-8"))
        r = admission_review.get("request", {})
        try:
            obj = self._extended_client.deserialize(r.get("object"), self.crd_type())
            old_obj = self._extended_client.deserialize(
                r.get("oldObject"), self.crd_type()
            )
            operation = r.get("operation")

            if operation not in self.register_on:
                raise ValidationWebhookError(f"unsupported operation: {operation}")

            request = Request(
                uid=r["uid"],
                name=r["name"],
                namespace=r.get("namespace"),
                kind=r["kind"],
                request_kind=r["requestKind"],
                request_resource=r["requestResource"],
                request_sub_resource=r.get("requestSubResource"),
                resource=r["resource"],
                sub_resource=r.get("subResource"),
                user_info=r["userInfo"],
                options=r.get("options"),
                dry_run=r["dryRun"],
                object=cast(T, obj),
                old_object=cast(T, old_obj),
                operation=operation,
            )

            self.validate(request)

            self.logger.debug("validation passed")
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {"uid": r.get("uid"), "allowed": True},
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
                    "uid": r.get("uid") if request else None,
                    "allowed": False,
                    "status": {"message": e.reason},
                },
            }
            return (
                json.dumps(response),
                falcon.HTTP_200,
                {"Content-Type": "application/json"},
            )


class BaseMutationWebhook(BaseWebhook, Generic[T]):
    """
    Kuroboros BaseMutationWebhook.
    Registers an endpoint in /<apiVersion>/<singular>/mutate
    """

    _webhook_type = WebhookTypes.MUTATION
    _endpoint_suffix = "mutate"

    register_on = [OperationsEnum.CREATE, OperationsEnum.UPDATE]

    def __init__(self):
        for reg in self.register_on:
            if reg not in (OperationsEnum.CREATE, OperationsEnum.UPDATE):
                raise RuntimeError(
                    "unsupported type to register.",
                    "supported `CREATE` or `UPDATE`",
                    reg,
                )
        super().__init__()

    def mutate(self, request: Request[T]) -> T:
        """
        Define the mutation logic
        """
        assert request.object is not None
        return request.object

    def process(self, body: bytes):
        self.logger.debug("processing mutation webhook")
        request: Request[T] | None = None
        admission_review = json.loads(body.decode("utf-8"))
        r = admission_review.get("request", {})
        try:
            obj = cast(
                T, self._extended_client.deserialize(r.get("object"), self.crd_type())
            )
            old_obj = cast(
                T | None,
                self._extended_client.deserialize(r.get("oldObject"), self.crd_type()),
            )
            operation = r.get("operation")

            crd_instance = self.crd_type()(**obj.to_dict())

            if operation not in self.register_on:
                raise MutationWebhookError(f"unsupported operation: {operation}")

            request = Request(
                uid=r["uid"],
                name=r["name"],
                namespace=r.get("namespace"),
                kind=r["kind"],
                request_kind=r["requestKind"],
                request_resource=r["requestResource"],
                request_sub_resource=r.get("requestSubResource"),
                resource=r["resource"],
                sub_resource=r.get("subResource"),
                user_info=r.get("userInfo"),
                options=r.get("options"),
                dry_run=r.get("dryun"),
                object=self.crd_type()(**obj.to_dict()),
                old_object=(self.crd_type()(**old_obj.to_dict()) if old_obj else None),
                operation=operation,
            )

            mutated_crd = self.mutate(request)
            assert mutated_crd is not None, "Mutated CRD instance cannot be None"
            patch_ops = jsonpatch.JsonPatch.from_diff(
                self._extended_client.serialize(crd_instance.to_dict()),
                self._extended_client.serialize(mutated_crd.to_dict()),
            ).patch
            self.logger.debug(f"crd_instance: {crd_instance.to_dict()}")
            self.logger.debug(f"mutated_crd: {mutated_crd.to_dict()}")
            self.logger.debug(f"patch operations: {patch_ops}")
            patch_b64 = base64.b64encode(json.dumps(patch_ops).encode("utf-8")).decode(
                "utf-8"
            )

            self.logger.debug("mutation passed")
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": r.get("uid"),
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
                    "uid": r.get("uid") if request else None,
                    "allowed": False,
                    "status": {"message": e.reason},
                },
            }
            return (
                json.dumps(response),
                falcon.HTTP_200,
                {"Content-Type": "application/json"},
            )
