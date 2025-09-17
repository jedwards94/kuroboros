from typing import ClassVar, Dict, Tuple, TypeVar
from kubernetes import client
from kuroboros.utils import NamespaceName
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.schema.openapi_model import OpenAPISchema
from kuroboros.schema.properties import PropYaml, prop


class CRDSchema(OpenAPISchema):
    """
    Defines the CRD class for your Reconciler and Webhooks
    """

    __group_version: ClassVar[GroupVersionInfo | None]
    print_columns: ClassVar[Dict[str, Tuple[str, str]]]
    metadata: ClassVar[client.V1ObjectMeta]
    T = TypeVar("T", bound="CRDSchema")

    def __init_subclass__(cls) -> None:
        if "status" not in cls.__dict__:
            setattr(
                cls, "status", prop(dict, x_kubernetes_preserve_unknown_fields=True)
            )
        if "metadata" not in cls.__dict__:
            setattr(cls, "metadata", prop(client.V1ObjectMeta))

        if not isinstance(getattr(cls, "status"), PropYaml):
            raise RuntimeError("status must by a prop().")

        if "print_columns" not in cls.__dict__:
            cls.print_columns = {}

        return super().__init_subclass__()

    @classmethod
    def set_gvi(cls, gvi: GroupVersionInfo) -> None:
        """
        Sets the GroupVersionInfo of the class
        """
        cls.__group_version = gvi

    @classmethod
    def get_gvi(cls) -> GroupVersionInfo | None:
        """
        Gets the GroupVersionInfo of the class
        """
        return cls.__group_version

    @property
    def namespace_name(self) -> NamespaceName:
        """
        Returns a tuple of `(namespace, name)` of the resource
        """
        assert self.metadata.name is not None
        return (self.metadata.namespace, self.metadata.name)
