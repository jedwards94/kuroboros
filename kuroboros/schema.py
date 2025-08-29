import copy
from inspect import isclass
import pprint
from typing import (
    Any,
    ClassVar,
    List,
    Dict,
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
from kuroboros.utils import NamespaceName, islistofsubclass


class CRDProp:
    """
    The class that is mapped to YAML
    """

    typ: str
    required: bool
    args: dict
    subprops: dict | None
    subtype: str | None
    subtype_props: dict | None
    subtype_desc: str | None
    real_type: Any

    def __init__(
        self,
        typ: str,
        subtype: str | None = None,
        subtype_props: dict | None = None,
        subtype_desc: str | None = None,
        required: bool = False,
        properties: dict | None = None,
        **kwargs,
    ):
        self.typ = typ
        self.required = required
        self.subprops = properties
        self.subtype = subtype
        self.subtype_props = subtype_props
        self.subtype_desc = subtype_desc
        self.args = kwargs


B = TypeVar("B", bound="BaseCRDProp")


class BaseCRDProp:
    """
    The base class for a object prop of a CRD
    """

    __attr_map: Dict[str, str] = {}
    __rev_attr_map: Dict[str, str] = {}
    _data: dict

    def __repr__(self) -> str:
        return pprint.pformat(self.get_data())

    def __init__(self, **kwargs):
        data = {}
        object.__setattr__(self, "_data", {})
        for attr, val in self.__class__.__dict__.items():
            aux = None
            if attr[:2] != "__" and not callable(val):
                cased_attr = self.attr_name(attr)
                if (
                    isinstance(val, CRDProp)
                    and isclass(val.real_type)
                    and issubclass(val.real_type, BaseCRDProp)
                ):
                    aux = val.real_type(**copy.deepcopy(kwargs[cased_attr]))
                elif islistofsubclass(val.real_type, BaseCRDProp):
                    aux = []
                    for el in kwargs[cased_attr]:
                        aux.append(get_args(val.real_type)[0](**copy.deepcopy(el)))

                if attr in kwargs:
                    data[cased_attr] = kwargs[attr] if aux is None else aux
                elif cased_attr in kwargs:
                    data[cased_attr] = kwargs[cased_attr] if aux is None else aux
        self._data = data

    def __init_subclass__(cls) -> None:
        for attr, value in cls.__dict__.items():
            if attr[:2] != "__" and not callable(value) and isinstance(value, CRDProp):
                cls.__attr_map[attr] = cls.__case_function(attr)
                cls.__attr_map[cls.__case_function(attr)] = attr

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
                return data[cased_name]
            return attr
        except Exception:  # pylint: disable=broad-except
            return None

    def __setattr__(self, name, value):
        # If setting a property, update both self._data and parent if present
        attr = object.__getattribute__(self, name)
        if isinstance(attr, CRDProp):
            cased_name = self.attr_name(name)
            self._data[cased_name] = value
        else:
            object.__setattr__(self, name, value)

    def get_data(self) -> Dict[str, Any]:
        """
        Returns the data of the prop as a dictionary
        """
        ret = {}
        for attr, val in self._data.items():
            if isinstance(val, BaseCRDProp):
                ret[attr] = val.get_data()
            elif isinstance(val, list) and (
                all(isinstance(item, BaseCRDProp) for item in val) or len(val) == 0
            ):
                ret[attr] = [el.get_data() for el in val]
            else:
                ret[attr] = val

        return ret

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
    def rev_attr_name(cls, text: str) -> str | None:
        """
        Returns the atribute name in the cased attribute map
        """

        return (
            copy.copy(cls.__rev_attr_map[text]) if text in cls.__rev_attr_map else None
        )

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
        bytes: "byte",
    }
    t = type_map.get(typ, None)
    subtype = None
    subprops = None
    subtype_desc = None
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
        if get_origin(typ) is list:
            t = "array"
            subtype = type_map.get(get_args(typ)[0], None)
            if islistofsubclass(typ, BaseCRDProp):
                subtype = "object"
                subtyp = get_args(typ)[0]
                subprops = subtyp.to_prop_dict()
                if subtyp.__doc__ is not None:
                    subtype_desc = subtyp.__doc__.strip()

    if t is None or (t == "array" and subtype is None):
        supported_types = "`, `".join([k.__name__ for k in type_map])
        raise TypeError(
            f"`{typ}` not suported",
            f"`{supported_types}` and subclasses of `BaseCRDProp` (and it's lists) are allowed",
        )

    p = CRDProp(
        typ=t,
        required=required,
        properties=properties,
        subtype=subtype,
        subtype_props=subprops,
        subtype_desc=subtype_desc,
        **kwargs,
    )
    p.real_type = typ
    return cast(T, p)


class BaseCRD:
    """
    Defines the CRD class for your Reconciler and Webhooks
    """

    __attr_map: ClassVar[Dict[str, str]] = {}
    __rev_attr_map: ClassVar[Dict[str, str]] = {}
    __group_version: ClassVar[GroupVersionInfo | None]
    _data: dict

    print_columns: Dict[str, Tuple[str, str]]
    api: client.CustomObjectsApi | None
    read_only: bool

    T = TypeVar("T", bound="BaseCRD")

    def __init_subclass__(cls) -> None:

        if "status" not in cls.__dict__:
            setattr(
                cls, "status", prop(dict, x_kubernetes_preserve_unknown_fields=True)
            )
        elif not isinstance(getattr(cls, "status"), CRDProp):
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
                cls.__rev_attr_map[cls.__case_function(attribute)] = attribute

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
        self.load_data(data)
        self.api = api
        self.read_only = read_only

    def __repr__(self) -> str:
        if self.__group_version is not None:
            return pprint.pformat(self.get_data())
        return pprint.pformat(self.get_data())

    def __str__(self) -> str:
        if self.__group_version is not None:
            return f"{self.__group_version.pretty_kind_str(self.namespace_name)}"
        return object.__str__(self)

    def __getattribute__(self, name: str):
        attr = object.__getattribute__(self, name)
        data = None
        try:
            data = object.__getattribute__(self, "_data")
        except AttributeError:
            data = {}

        try:
            if name in ("status", "metadata"):
                return data[name]
            if isinstance(attr, CRDProp):
                cased_name = self.attr_name(name)
                return data["spec"][cased_name]
            return attr
        except (KeyError, AttributeError):
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
        except (KeyError, AttributeError):
            object.__setattr__(self, name, value)

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
    def get_gvi(cls) -> GroupVersionInfo | None:
        """
        Gets the GroupVersionInfo of the class
        """
        return cls.__group_version

    @classmethod
    def attr_name(cls, text: str) -> str:
        """
        Returns the atribute name in the cased attribute map
        """

        return copy.copy(cls.__attr_map[text])

    @classmethod
    def rev_attr_name(cls, text: str) -> str | None:
        """
        Returns the atribute name in the uncased attribute map
        """

        return (
            copy.copy(cls.__rev_attr_map[text]) if text in cls.__rev_attr_map else None
        )

    def load_data(self, data: Any):
        """
        loads an object as a `dict` into the class to get the values
        """
        if isinstance(data, self.__class__):
            self._data = copy.deepcopy(data.get_data())
            return
        aux_data = {}
        if isinstance(data, dict):
            aux_data["metadata"] = (
                copy.deepcopy(data["metadata"]) if "metadata" in data else {}
            )

            status_attr = object.__getattribute__(self, "status")
            status = None
            if "status" in data and isinstance(status_attr, CRDProp):
                if isclass(status_attr.real_type) and issubclass(
                    status_attr.real_type, BaseCRDProp
                ):
                    status = status_attr.real_type(**copy.deepcopy(data["status"]))
                elif islistofsubclass(status_attr.real_type, BaseCRDProp):
                    status = [
                        get_args(status_attr.real_type)[0](**copy.deepcopy(el))
                        for el in data
                    ]

                aux_data["status"] = data["status"] if status is None else status

            if "spec" in data:
                aux_data["spec"] = {}
                for attr, val in data["spec"].items():
                    aux = None
                    cased_attr = self.rev_attr_name(attr)
                    if cased_attr is None:
                        continue
                    attr_prop = object.__getattribute__(self, cased_attr)
                    if isinstance(attr_prop, CRDProp):
                        if isclass(attr_prop.real_type) and issubclass(
                            attr_prop.real_type, BaseCRDProp
                        ):
                            aux = attr_prop.real_type(**copy.deepcopy(val))
                        elif islistofsubclass(attr_prop.real_type, BaseCRDProp):
                            aux = [
                                get_args(attr_prop.real_type)[0](**copy.deepcopy(el))
                                for el in val
                            ]
                    aux_data["spec"][self.__case_function(attr)] = (
                        val if aux is None else aux
                    )
        self._data = aux_data

    def get_data(self) -> Dict[str, Any]:
        """
        Returns the data of the CRD object as a dict
        """
        data = object.__getattribute__(self, "_data")
        metadata = {
            **{
                k: v
                for k, v in data["metadata"].items()
                if k not in ["resourceVersion", "managedFields"]
            },
        }
        status_data = data.get("status", {})
        status = None
        if isinstance(status_data, BaseCRDProp):
            status = status_data.get_data()
        elif isinstance(status_data, list) and (
            all(isinstance(item, BaseCRDProp) for item in status_data)
            or len(status_data) == 0
        ):
            status = [d.get_data() for d in status_data]
        else:
            status = status_data

        spec = {}
        for prop_name, val in data.get("spec", {}).items():
            aux = None
            if isinstance(val, BaseCRDProp):
                aux = val.get_data()
            elif isinstance(val, list) and (
                all(isinstance(item, BaseCRDProp) for item in val) or len(val) == 0
            ):
                aux = [d.get_data() for d in val]
            spec[prop_name] = val if aux is None else aux

        return {
            "metadata": metadata,
            "spec": spec,
            "status": status,
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

        patcher = None
        body = self.get_data()
        common_args: Dict[str, Any] = {
            "name": self.name,
            "group": self.__group_version.group,
            "version": self.__group_version.api_version,
            "plural": self.__group_version.plural,
        }
        if self.__group_version.is_namespaced():
            common_args["namespace"] = self.namespace
            status_patcher = self.api.patch_namespaced_custom_object_status
            patcher = self.api.patch_namespaced_custom_object
        else:
            status_patcher = self.api.patch_cluster_custom_object_status
            patcher = self.api.patch_cluster_custom_object

        assert patcher is not None
        if "status" in self._data and patch_status:
            response = status_patcher(
                **{
                    **common_args,
                    "body": {"status": body["status"]},
                }
            )
            self.load_data(response)

        response = patcher(
            **{
                **common_args,
                "body": body,
            }
        )
        self.load_data(response)

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
    def api_version(self) -> str | None:
        """
        return the CRD api_version
        """
        return (
            self.__group_version.api_version
            if self.__group_version is not None
            else None
        )

    @property
    def group(self) -> str | None:
        """
        return the CRD group
        """
        return self.__group_version.group if self.__group_version is not None else None

    @property
    def kind(self) -> str | None:
        """
        return the CRD kind
        """
        return self.__group_version.kind if self.__group_version is not None else None

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
