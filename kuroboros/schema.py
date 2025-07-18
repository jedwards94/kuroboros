from typing import Any, List, Tuple, Dict, TypeVar, cast, get_args, get_origin
from kubernetes import client
import copy

from kubernetes.client import V1OwnerReference

from kuroboros.group_version_info import GroupVersionInfo


class CRDProp:
    typ: str
    required: bool
    args: dict
    subprops: dict | None
    subtype: str | None

    def __init__(
        self,
        type: str,
        subtype: str | None = None,
        required: bool = False,
        properties: dict | None = None,
        **kwargs,
    ):
        self.typ = type
        self.required = required
        self.subprops = properties
        self.subtype = subtype
        self.args = kwargs


T = TypeVar("T")


def prop(
    typ: type[T],
    required=False,
    properties: dict[str, Any] | None = None,
    **kwargs: Any,
) -> T | None:
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
    if t is None:
        raise TypeError(
            f"the prop type cant be `{typ}`, only types `{'`, `'.join([k.__name__ for k in type_map.keys()])}` are allowed"
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
                        type=t,
                        required=required,
                        properties=properties,
                        subtype=subtype,
                        **kwargs,
                    ),
                )

    return cast(T, CRDProp(type=t, required=required, properties=properties, **kwargs))


class BaseCRD:
    # instance properties
    api: client.CustomObjectsApi | None
    group_version: GroupVersionInfo | None
    status = prop(dict, x_kubernetes_preserve_unknown_fields=True)
    read_only = False
    _data: dict = {}

    def __init__(
        self,
        api: client.CustomObjectsApi | None = None,
        group_version: GroupVersionInfo | None = None,
        read_only: bool = False,
        data: Dict = {},
    ):
        if read_only == True and data == {}:
            raise ValueError("read_only CRD must have data provided")
        self._data = copy.deepcopy(data)
        self.api = api
        self.group_version = group_version
        self.read_only = read_only
        return
    
    def __repr__(self) -> str:
        if self.group_version is not None:
            return f"{self.group_version.pretty_kind_str((self.metadata['namespace'], self.metadata['name']))}"
        return f"{self.__class__.__name__}(Name={self.metadata['name']}, Namespace={self.metadata['namespace']})"

    def load_data(self, data: Any):
        """
        loads an object as a `dict` into the class to get the values
        """
        if isinstance(data, self.__class__):
            self._data = copy.deepcopy(data._data)
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
            raise RuntimeError(
                f"Cannot call `patch` on read-only CRD object `{self}`"
            )

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
        data = object.__getattribute__(self, "_data")
        attr = object.__getattribute__(self, name)
        try:
            if name == "status" or name == "metadata":
                return data[name]
            elif isinstance(attr, CRDProp):
                return data["spec"][name]
            else:
                return attr
        except Exception:
            return None

    def __setattr__(self, name: str, value: Any) -> None:
        if self.read_only:
            raise RuntimeError(
                f"Cannot set attribute `{name}` on read-only CRD object `{self}`"
            )
        try:
            attr = object.__getattribute__(self, name)
            if name == "status" or name == "metadata":
                self._data[name] = value
            elif isinstance(attr, CRDProp):
                self._data["spec"][name] = value
            else:
                return object.__setattr__(self, name, value)
        except:
            return object.__setattr__(self, name, value)

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
        elif finalizer in self.metadata["finalizers"]:
            self.metadata["finalizers"].remove(finalizer)
            self.patch()
        else:
            return

    def get_owner_ref(self, block_self_deletion: bool = True) -> V1OwnerReference:
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
        if "metadata" not in self._data.keys():
            raise RuntimeError(f"method called at wrong time, no metadata present at {self}")
        return self._data["metadata"]

    @metadata.setter
    def metadata(self, value):
        """
        Placeholder to set metadata
        """
        pass

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
        if "finalizers" not in self.metadata:
            return []

        return self.metadata["finalizers"]

    @property
    def uid(self) -> str:
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
