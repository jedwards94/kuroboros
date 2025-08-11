import copy
from inspect import isclass
from typing import (
    Any,
    ClassVar,
    List,
    Dict,
    Type,
    TypeVar,
    Tuple,
    cast,
    get_args,
    get_origin,
)
import caseconverter
from kubernetes import client

from kubernetes.client import V1OwnerReference

from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.utils import NamespaceName


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

    __attr_map: Dict[str, str] = {}
    _data: dict
    _parent_data: dict | None
    _parent_key: str | None

    def __init__(self, *, data, _parent_data=None, _parent_key=None, **_):
        self._data = data
        self._parent_key = _parent_key
        # ALWAYS set _parent_data at the end of __init__ to avoid recursion
        self._parent_data = _parent_data

    def __init_subclass__(cls) -> None:
        for attribute, value in cls.__dict__.items():
            if (
                attribute[:2] != "__"
                and not callable(value)
                and isinstance(value, CRDProp)
            ):
                cls.__attr_map[attribute] = cls.__case_function(attribute)

    @staticmethod
    def __case_function(text: str) -> str:
        return caseconverter.camelcase(text)

    @classmethod
    def attr_name(cls, text: str) -> str:
        """
        Returns the atribute name in the cased attribute map
        """

        return copy.copy(cls.__attr_map[text])

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
                cased_name = self.attr_name(name)
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
            cased_name = self.attr_name(name)
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
    if isclass(typ) and issubclass(typ, BaseCRDProp):
        if properties is not None:
            raise RuntimeError(
                "a prop of a type inherited from BaseCRDProp cannot have properties defined in it"
            )
        t = "object"
        properties = typ.to_prop_dict()
        if typ.__doc__ is not None:
            kwargs["description"] = typ.__doc__.strip()
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

    status: Any
    print_columns: Dict[str, Tuple[str, str]]

    __attr_map: ClassVar[Dict[str, str]] = {}
    __group_version: ClassVar[GroupVersionInfo | None]
    api: client.CustomObjectsApi | None
    read_only: bool
    _data: dict

    T = TypeVar("T", bound="BaseCRD")

    @staticmethod
    def __case_function(text: str) -> str:
        return caseconverter.camelcase(text)

    @classmethod
    def set_gvi(cls, gvi: GroupVersionInfo) -> None:
        """
        Sets the GroupVersionInfo of the class
        """
        cls.__group_version = gvi

    @classmethod
    def attr_name(cls, text: str) -> str:
        """
        Returns the atribute name in the cased attribute map
        """

        return copy.copy(cls.__attr_map[text])

    @classmethod
    def create_cluster_scoped(
        cls: Type[T],
        api: client.CustomObjectsApi,
        name: str,
        spec: Dict,
        metadata: Dict | None = None,
    ) -> T:
        """
        Creates a new instance of the CRD in the cluster.
        """
        if cls.__group_version is None:
            raise RuntimeError(
                "`create_cluster_scoped` used when group_version is `None`"
            )
        if cls.__group_version.is_namespaced():
            raise RuntimeError("`create_cluster_scoped` used in a namespaced CRD")
        if metadata is None:
            metadata = {}
        metadata["name"] = name
        data = {
            "metadata": metadata,
            "spec": spec,
        }
        instance = cls(api=api, read_only=False, data=data)

        instance = cls(api=api, read_only=False, data=data)
        cluster_data = api.create_cluster_custom_object(
            group=cls.__group_version.group,
            version=cls.__group_version.api_version,
            plural=cls.__group_version.plural,
            body={
                "kind": cls.__group_version.kind,
                "apiVersion": f"{cls.__group_version.group}/{cls.__group_version.api_version}",
                **instance.get_data(),
            },
        )
        instance.load_data(cluster_data)
        return instance

    @classmethod
    def create_namespaced(
        cls: Type[T],
        api: client.CustomObjectsApi,
        namespace: str,
        name: str,
        spec: Dict,
        metadata: Dict | None = None,
    ) -> T:
        """
        Creates a new instance of the CRD in the specified namespace.
        """
        if cls.__group_version is None:
            raise RuntimeError("`create_namespaced` used when group_version is `None`")
        if not cls.__group_version.is_namespaced():
            raise RuntimeError("`create_namespaced` used in a cluster-scoped CRD")
        if metadata is None:
            metadata = {}
        metadata["name"] = name
        data = {
            "metadata": metadata,
            "spec": spec,
        }
        instance = cls(api=api, read_only=False, data=data)
        cluster_data = api.create_namespaced_custom_object(
            group=cls.__group_version.group,
            namespace=namespace,
            version=cls.__group_version.api_version,
            plural=cls.__group_version.plural,
            body={
                "kind": cls.__group_version.kind,
                "apiVersion": f"{cls.__group_version.group}/{cls.__group_version.api_version}",
                **instance.get_data(),
            },
        )
        instance.load_data(cluster_data)
        return instance

    @classmethod
    def get_cluster_scoped(
        cls: Type[T],
        api: client.CustomObjectsApi,
        name: str,
    ) -> T:
        """
        Get a CRD with name from the cluster
        """
        if cls.__group_version is None:
            raise RuntimeError("`get_cluster_scoped` used when group_version is `None`")
        if cls.__group_version.is_namespaced():
            raise RuntimeError("`get_cluster_scoped` used in a namespaced CRD")
        response = api.get_cluster_custom_object(
            group=cls.__group_version.group,
            name=name,
            version=cls.__group_version.api_version,
            plural=cls.__group_version.plural,
        )
        instance = cls(api=api, read_only=False)
        instance.load_data(response)
        return instance

    @classmethod
    def get_namespaced(
        cls: Type[T],
        api: client.CustomObjectsApi,
        namespace: str,
        name: str,
    ) -> T:
        """
        Get a CRD with name and namespace from the cluster
        """
        if cls.__group_version is None:
            raise RuntimeError("`get_namespaced` used when group_version is `None`")
        if not cls.__group_version.is_namespaced():
            raise RuntimeError("`get_namespaced` used in a cluster-scoped CRD")
        response = api.get_namespaced_custom_object(
            group=cls.__group_version.group,
            namespace=namespace,
            name=name,
            version=cls.__group_version.api_version,
            plural=cls.__group_version.plural,
        )
        instance = cls(api=api, read_only=False)
        instance.load_data(response)
        return instance

    @classmethod
    def list_namespaced(
        cls: Type[T],
        api: client.CustomObjectsApi,
        namespace: str,
        **kwargs,
    ) -> List[T]:
        """
        Get a CRD List from the cluster
        """
        if cls.__group_version is None:
            raise RuntimeError("`list_namespaced` used when group_version is `None`")

        if not cls.__group_version.is_namespaced():
            raise RuntimeError("`list_namespaced` used in a cluster-scoped CRD")

        instances = []
        response = api.list_namespaced_custom_object(
            group=cls.__group_version.group,
            namespace=namespace,
            version=cls.__group_version.api_version,
            plural=cls.__group_version.plural,
            **kwargs,
        )
        for raw in response:
            inst = cls(api=api, read_only=False)
            inst.load_data(raw)
            instances.append(inst)

        return instances

    @classmethod
    def list_cluster_scoped(
        cls: Type[T],
        api: client.CustomObjectsApi,
        **kwargs,
    ) -> List[T]:
        """
        Get a CRD List from the cluster
        """
        if cls.__group_version is None:
            raise RuntimeError(
                "`list_cluster_scoped` used when group_version is `None`"
            )

        if cls.__group_version.is_namespaced():
            raise RuntimeError("`list_cluster_scoped` used in a namespaced CRD")

        instances = []
        response = api.list_cluster_custom_object(
            group=cls.__group_version.group,
            version=cls.__group_version.api_version,
            plural=cls.__group_version.plural,
            **kwargs,
        )
        for raw in response:
            inst = cls(api=api, read_only=False)
            inst.load_data(raw)
            instances.append(inst)

        return instances

    def __init_subclass__(cls) -> None:

        if "status" not in cls.__dict__:
            cls.status = prop(dict, x_kubernetes_preserve_unknown_fields=True)
        elif not isinstance(cls.status, CRDProp):
            raise RuntimeError("status must by a prop().")

        if "print_columns" not in cls.__dict__:
            cls.print_columns = {}

        for attribute, value in cls.__dict__.items():
            if (
                attribute[:2] != "__"
                and not callable(value)
                and isinstance(value, CRDProp)
            ):
                cls.__attr_map[attribute] = cls.__case_function(attribute)

    def __init__(
        self,
        api: client.CustomObjectsApi | None = None,
        read_only: bool = False,
        data: Dict | None = None,
    ):
        if data is None:
            data = {}
        if read_only and data == {}:
            raise ValueError("read_only CRD must have data provided")
        self._data = copy.deepcopy(data)
        self.api = api
        self.read_only = read_only

    def __repr__(self) -> str:
        if self.__group_version is not None:
            return f"{self.__group_version.pretty_kind_str(self.namespace_name)}"
        return super().__repr__()

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
        if self.__group_version is None:
            raise RuntimeError("`patch` used when group_version is `None`")

        if self.read_only:
            raise RuntimeError(f"Cannot call `patch` on read-only CRD object `{self}`")

        new_state = self.get_data()
        patcher = None
        if self.__group_version.is_namespaced():
            patcher = self.api.patch_namespaced_custom_object_status
        else:
            patcher = self.api.patch_cluster_custom_object_status

        assert patcher is not None
        if "status" in self._data and patch_status:
            response = patcher(
                group=self.__group_version.group,
                namespace=self.metadata["namespace"],
                name=self.metadata["name"],
                version=self.__group_version.api_version,
                plural=self.__group_version.plural,
                body={"status": self._data["status"]},
            )
            self.load_data(response)

        response = patcher(
            group=self.__group_version.group,
            namespace=self.metadata["namespace"],
            name=self.metadata["name"],
            version=self.__group_version.api_version,
            plural=self.__group_version.plural,
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
                cased_name = self.attr_name(name)
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
                cased_name = self.attr_name(name)
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
        if self.__group_version is None:
            raise RuntimeError("`patch` used when group_version is `None`")
        return V1OwnerReference(
            api_version=self.__group_version.api_version,
            kind=self.__group_version.kind,
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
        data = object.__getattribute__(self, "_data")
        if "metadata" not in data.keys():
            raise RuntimeError(
                f"method called at wrong time, no metadata present at {self.__class__.__name__}"
            )
        return data["metadata"]

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
    def namespace(self) -> str | None:
        """
        Quick access to `metadata["namespace"]`
        """

        return self.metadata.get("namespace", None)

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
    def namespace_name(self) -> NamespaceName:
        """
        Returns a tuple of `(namespace, name)` of the resource
        """
        return (self.namespace, self.name)

    @property
    def resource_version(self) -> str | None:
        """
        Returns the `metadata.resourceVersion`
        """
        return self.metadata["resourceVersion"]
