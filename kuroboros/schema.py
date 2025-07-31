import copy
from typing import (
    Any,
    List,
    Tuple,
    Dict,
    Type,
    TypeVar,
    cast,
    get_args,
    get_origin,
)
import caseconverter
from kubernetes import client

from kubernetes.client import V1OwnerReference

from kuroboros.group_version_info import GroupVersionInfo


class CRDProp:
    """
    The class that is mapped to YAML
    """

    typ: str
    required: bool
    args: dict
    subprops: dict | None
    subtype: str | None
    real_type: Any

    def __init__(
        self,
        typ: str,
        subtype: str | None = None,
        required: bool = False,
        properties: dict | None = None,
        **kwargs,
    ):
        self.typ = typ
        self.required = required
        self.subprops = properties
        self.subtype = subtype
        self.args = kwargs


class BaseCRDProp:
    """
    The base class for a object prop of a CRD
    """

    _data: dict
    _parent_data: dict | None
    _parent_key: str | None
    _attr_map: Dict[str, str]

    def __init__(self, *, data, _parent_data=None, _parent_key=None, **_):
        object.__setattr__(self, "_attr_map", {})
        for attribute, value in self.__class__.__dict__.items():
            if (
                attribute[:2] != "__"
                and not callable(value)
                and isinstance(value, CRDProp)
            ):
                self._attr_map[attribute] = self.__case_function(attribute)

        self._data = data
        self._parent_key = _parent_key
        # ALWAYS set _parent_data at the end of __init__ to avoid recursion
        self._parent_data = _parent_data

    @staticmethod
    def __case_function(text: str) -> str:
        return caseconverter.camelcase(text)

    @classmethod
    def to_prop_dict(cls) -> dict:
        """
        Returns a dict of all CRDProp properties defined in the class (including inherited).
        """
        props = {}
        for base in reversed(cls.__mro__):
            for k, v in base.__dict__.items():
                if isinstance(v, CRDProp):
                    props[cls.__case_function(k)] = v
        return props

    def __getattribute__(self, name: str):
        attr = object.__getattribute__(self, name)
        data = None
        try:
            data = object.__getattribute__(self, "_data")
        except AttributeError:
            data = {}
        try:
            if isinstance(attr, CRDProp):
                cased_name = self._attr_map[name]
                if issubclass(attr.real_type, BaseCRDProp):
                    inst = attr.real_type(
                        data=data[cased_name], _parent_data=data, _parent_key=cased_name
                    )
                    return inst
                return data[cased_name]
            return attr
        except Exception:  # pylint: disable=broad-except
            return None

    def __setattr__(self, name, value):
        # If setting a property, update both self._data and parent if present
        if hasattr(self, "_parent_data") and self._parent_data is not None:
            cased_name = self._attr_map[name]
            self._data[cased_name] = value
            self._parent_data[self._parent_key][cased_name] = value
        else:
            object.__setattr__(self, name, value)


T = TypeVar("T")


def prop(
    typ: type[T],
    required=False,
    properties: dict[str, Any] | None = None,
    **kwargs: Any,
) -> T:
    """
    Define a propertie of a CRD, the available types are
    `str`, `int`, `float`, `dict`, `bool`, `list[Any]` and
    subclasses of `BaseCRDProp`
    """
    type_map = {
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
    t = type_map.get(typ, None)
    if issubclass(typ, BaseCRDProp):
        if properties is not None:
            raise RuntimeError(
                "a prop of a type inherited from BaseCRDProp cannot have properties defined in it"
            )
        t = "object"
        properties = typ.to_prop_dict()
    if t is None:
        supported_types = "`, `".join([k.__name__ for k in type_map])
        raise TypeError(
            f"`{typ}` not suported",
            f"only `{supported_types}` and subclasses of `BaseCRDProp` are allowed",
        )

    if t == "array":
        origin = get_origin(typ)
        if origin is list or origin is List:
            args = get_args(typ)
            if args:
                subtype = type_map.get(args[0], None)
                return cast(
                    T,
                    CRDProp(
                        typ=t,
                        required=required,
                        properties=properties,
                        subtype=subtype,
                        **kwargs,
                    ),
                )
    p = CRDProp(typ=t, required=required, properties=properties, **kwargs)
    p.real_type = typ
    return cast(T, p)


class BaseCRD:
    """
    Defines the CRD class for your Reconciler and Webhooks
    """

    status = prop(dict, x_kubernetes_preserve_unknown_fields=True)

    # instance properties
    _attr_map: Dict[str, str]
    api: client.CustomObjectsApi | None
    group_version: GroupVersionInfo | None
    read_only: bool
    _data: dict

    T = TypeVar("T", bound="BaseCRD")

    @staticmethod
    def __case_function(text: str) -> str:
        return caseconverter.camelcase(text)

    @classmethod
    def create_namespaced(
        cls: Type[T],
        api: client.CustomObjectsApi,
        group_version: GroupVersionInfo,
        namespace: str,
        name: str,
        spec: Dict,
        metadata: Dict | None = None,
    ) -> T:
        """
        Creates a new instance of the CRD in the specified namespace.
        """
        if metadata is None:
            metadata = {}
        metadata["name"] = name
        data = {
            "metadata": metadata,
            "spec": spec,
        }
        instance = cls(api=api, group_version=group_version, read_only=False, data=data)
        cluster_data = api.create_namespaced_custom_object(
            group=group_version.group,
            namespace=namespace,
            version=group_version.api_version,
            plural=group_version.plural,
            body={
                "kind": group_version.kind,
                "apiVersion": f"{group_version.group}/{group_version.api_version}",
                **instance.get_data(),
            },
        )
        instance.load_data(cluster_data)
        return instance

    @classmethod
    def get_namespaced(
        cls: Type[T],
        api: client.CustomObjectsApi,
        group_version: GroupVersionInfo,
        namespace: str,
        name: str,
    ) -> T:
        """
        Get a CRD with name and namespace from the cluster
        """
        response = api.get_namespaced_custom_object(
            group=group_version.group,
            namespace=namespace,
            name=name,
            version=group_version.api_version,
            plural=group_version.plural,
        )
        instance = cls(api=api, group_version=group_version, read_only=False)
        instance.load_data(response)
        return instance

    @classmethod
    def list_namespaced(
        cls: Type[T],
        api: client.CustomObjectsApi,
        group_version: GroupVersionInfo,
        namespace: str,
        **kwargs,
    ) -> List[T]:
        """
        Get a CRD List with name and namespace from the cluster
        """
        instances = []
        response = api.list_namespaced_custom_object(
            group=group_version.group,
            namespace=namespace,
            version=group_version.api_version,
            plural=group_version.plural,
            **kwargs,
        )
        for raw in response:
            inst = cls(api=api, group_version=group_version, read_only=False)
            inst.load_data(raw)
            instances.append(inst)

        return instances

    def __init__(
        self,
        api: client.CustomObjectsApi | None = None,
        group_version: GroupVersionInfo | None = None,
        read_only: bool = False,
        data: Dict | None = None,
    ):
        object.__setattr__(self, "_attr_map", {})
        for attribute, value in self.__class__.__dict__.items():
            if (
                attribute[:2] != "__"
                and not callable(value)
                and isinstance(value, CRDProp)
            ):
                self._attr_map[attribute] = self.__case_function(attribute)

        if data is None:
            data = {}
        if read_only and data == {}:
            raise ValueError("read_only CRD must have data provided")
        self._data = copy.deepcopy(data)
        self.api = api
        self.group_version = group_version
        self.read_only = read_only

    def __repr__(self) -> str:
        if self.group_version is not None:
            return f"{self.group_version.pretty_kind_str(self.namespace_name)}"
        return super().__repr__()

    def attr_name(self, text: str) -> str:
        """
        Returns the atribute name in the cased attribute map
        """
        return copy.copy(self._attr_map[text])

    def load_data(self, data: Any):
        """
        loads an object as a `dict` into the class to get the values
        """
        if isinstance(data, self.__class__):
            self._data = copy.deepcopy(data.get_data())
            return
        self._data = copy.deepcopy(dict(data))

    def get_data(self) -> Dict[str, Any]:
        """
        Returns the data of the CRD object as a dict
        """
        return {
            "metadata": {
                **{
                    k: v
                    for k, v in self._data["metadata"].items()
                    if k not in ["resourceVersion", "managedFields"]
                },
            },
            "spec": self._data.get("spec", {}),
            "status": self._data.get("status", {}),
        }

    def patch(self, patch_status: bool = True):
        """
        Patch the CRD object through the kubernetes API
        and loads the patched data into the CRD class. First patch the `status`
        if `patch_status=True`.
        then patches the complete object
        """
        if self.api is None:
            raise RuntimeError("`patch` used when api is `None`")
        if self.group_version is None:
            raise RuntimeError("`patch` used when group_version is `None`")

        if self.read_only:
            raise RuntimeError(f"Cannot call `patch` on read-only CRD object `{self}`")

        new_state = self.get_data()
        if self.group_version.scope == "Namespaced":
            if "status" in self._data and patch_status:
                response = self.api.patch_namespaced_custom_object_status(
                    group=self.group_version.group,
                    namespace=self.metadata["namespace"],
                    name=self.metadata["name"],
                    version=self.group_version.api_version,
                    plural=self.group_version.plural,
                    body={"status": self._data["status"]},
                )

                self.load_data(response)

            response = self.api.patch_namespaced_custom_object(
                group=self.group_version.group,
                namespace=self.metadata["namespace"],
                name=self.metadata["name"],
                version=self.group_version.api_version,
                plural=self.group_version.plural,
                body=new_state,
            )

            self.load_data(response)

    def __getattribute__(self, name: str):
        attr = object.__getattribute__(self, name)
        data = None
        try:
            data = object.__getattribute__(self, "_data")
        except AttributeError:
            data = {}

        try:
            if name in ("status", "metadata"):
                if isinstance(attr, CRDProp) and issubclass(
                    attr.real_type, BaseCRDProp
                ):
                    return attr.real_type(data=data[name])
                return data[name]
            if isinstance(attr, CRDProp):
                cased_name = self._attr_map[name]
                if issubclass(attr.real_type, BaseCRDProp):
                    return attr.real_type(
                        data=data["spec"][cased_name],
                        _parent_data=data["spec"],
                        _parent_key=cased_name,
                    )
                return data["spec"][cased_name]
            return attr
        except Exception:  # pylint: disable=broad-except
            return None

    def __setattr__(self, name: str, value: Any) -> None:
        if hasattr(self, "read_only") and self.read_only:
            raise RuntimeError(
                f"Cannot set attribute `{name}` on read-only CRD object `{self}`"
            )
        try:
            attr = object.__getattribute__(self, name)
            if name in ("status", "metadata"):
                self._data[name] = value
            elif isinstance(attr, CRDProp):
                cased_name = self._attr_map[name]
                self._data["spec"][cased_name] = value
            else:
                object.__setattr__(self, name, value)
        except Exception:  # pylint: disable=broad-except
            object.__setattr__(self, name, value)

    def add_finalizer(self, finalizer: str):
        """
        Appends a new `finalizer` to the list and patch the object
        """
        if "finalizers" not in self.metadata:
            self.metadata["finalizers"] = [finalizer]
        elif finalizer not in self.metadata["finalizers"]:
            self.metadata["finalizers"].append(finalizer)
        else:
            return

        self.patch()

    def remove_finalizer(self, finalizer: str):
        """
        Removes `finalizer` from the metadata and patch the object
        """
        if "finalizers" not in self.metadata:
            return
        if finalizer in self.metadata["finalizers"]:
            self.metadata["finalizers"].remove(finalizer)
            self.patch()
        else:
            return

    def get_owner_ref(self, block_self_deletion: bool = True) -> V1OwnerReference:
        """
        Creates a V1OwnerRef to the current CRD
        """
        if self.api is None:
            raise RuntimeError("`patch` used when api is `None`")
        if self.group_version is None:
            raise RuntimeError("`patch` used when group_version is `None`")
        return V1OwnerReference(
            api_version=self.group_version.api_version,
            kind=self.group_version.kind,
            name=self.name,
            uid=self.metadata["uid"],
            block_owner_deletion=block_self_deletion,
            controller=True,
        )

    def has_finalizers(self) -> bool:
        """
        Check if the metadata has an element called `finalizers`
        """
        return self.metadata["finalizers"] is not None

    @property
    def metadata(self) -> Dict[Any, Any]:
        """
        Gets the metadata of the resource as a `Dict`
        """
        if "metadata" not in self._data.keys():
            raise RuntimeError(
                f"method called at wrong time, no metadata present at {self}"
            )
        return self._data["metadata"]

    @metadata.setter
    def metadata(self, value):
        """
        Placeholder to set metadata
        """

    @property
    def name(self) -> str:
        """
        Quick access to `metadata["name"]`
        """

        return self.metadata["name"]

    @property
    def namespace(self) -> str:
        """
        Quick access to `metadata["namespace"]`
        """

        return self.metadata["namespace"]

    @property
    def marked_for_deletion(self) -> bool:
        """
        Checks for a element called `deletionTimestamp` in the
        object metadata
        """
        return "deletionTimestamp" in self.metadata

    @property
    def finalizers(self) -> List[str]:
        """
        Gets the finalizers of the resource
        """
        if "finalizers" not in self.metadata:
            return []

        return self.metadata["finalizers"]

    @property
    def uid(self) -> str:
        """
        Get the UID of the resource
        """
        return self.metadata["uid"]

    @property
    def namespace_name(self) -> Tuple[str, str]:
        """
        Returns a tuple of `(namespace, name)` of the resource
        """
        return (self.metadata["namespace"], self.metadata["name"])

    @property
    def resource_version(self) -> str | None:
        """
        Returns the `metadata.resourceVersion`
        """
        return self.metadata["resourceVersion"]
