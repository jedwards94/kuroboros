from inspect import isclass
import re
from typing import Any, Dict, TypeVar, cast, get_args, get_origin
from kubernetes import client

T = TypeVar("T")

NATIVE_TYPES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "dict": "object",
    "bool": "boolean",
    "bytes": "byte",
    "datetime": "string",
    "object": "object",
}


def parse_kube_prop(name: str, klass: type | str):
    """
    Recursively parse a kubernetes.client model class (e.g., V1ObjectMeta) or a string type
    into a PropYaml, handling lists and nested models.
    """
    # Handle string type hints like 'list[V1Container]'
    if isinstance(klass, str):
        if klass.startswith("list["):
            sub_type_str = re.match(r"list\[(.*)\]", klass).group(1)  # type: ignore
            # Try to resolve the class from the client module
            sub_type = getattr(client, sub_type_str, sub_type_str)
            subtype_yaml = parse_kube_prop(name, sub_type)
            return PropYaml(
                typ="array",
                subtype=subtype_yaml.typ,
                subtype_props=subtype_yaml.subprops,
            )
        if klass.startswith("dict("):
            sub_type_str = re.match(r"dict\((.*)\)", klass).group(1)  # type: ignore
            # Try to resolve the class from the client module
            sub_type = getattr(client, sub_type_str, sub_type_str)
            return PropYaml(typ="object")
        # Try to resolve the class from the client module
        klass_obj = getattr(client, klass, None)
        if klass_obj and isclass(klass_obj):
            return parse_kube_prop(name, klass_obj)
        # Fallback: treat as primitive

        return PropYaml(typ=NATIVE_TYPES[klass])

    if klass in NATIVE_TYPES:
        return PropYaml(typ=NATIVE_TYPES[klass])

    # Handle kubernetes client models
    if hasattr(klass, "openapi_types") and hasattr(klass, "attribute_map"):
        properties = {}
        for attr, attr_type in getattr(klass, "openapi_types").items():
            prop_yaml = parse_kube_prop(attr, attr_type)
            # Use the attribute_map to get the serialized name
            yaml_name = klass.attribute_map[attr]
            properties[yaml_name] = prop_yaml
        desc = klass.__doc__.strip() if klass.__doc__ else None
        return PropYaml(
            typ="object",
            properties=properties,
            subtype_desc=desc,
        )

    # Fallback: treat as string
    return PropYaml(typ="string")


def prop(
    typ: type[T],
    required=False,
    properties: dict[str, Any] | None = None,
    **kwargs: Any,
) -> T:
    """
    Define a propertie of a CRD, the available types are
    `str`, `int`, `float`, `dict`, `bool`, `list[Any]`,
    subclasses of `OpenAPISchema` and classes from `kubernetes.client`
    """
    t = NATIVE_TYPES.get(typ.__name__, None)
    subtype = None
    subprops = None
    subtype_desc = None
    if isclass(typ) and hasattr(typ, "openapi_types") and hasattr(typ, "attribute_map"):
        if properties is not None:
            raise RuntimeError("a class-prop cannot have properties defined in it")
        t = "object"
        properties = {}
        if typ != client.V1ObjectMeta:
            for attr, attr_type in cast(
                Dict[str, Any], getattr(typ, "openapi_types")
            ).items():
                properties[getattr(typ, "attribute_map")[attr]] = (
                    getattr(typ, attr)
                    if isclass(attr_type) or get_origin(attr_type) is list
                    else parse_kube_prop(attr, attr_type)
                )

            if typ.__doc__ is not None:
                kwargs["description"] = typ.__doc__.strip()
    if t is None:
        if get_origin(typ) is list:
            t = "array"
            sub_kls = get_args(typ)[0]
            subtype = NATIVE_TYPES.get(sub_kls.__name__, None)
            if isclass(sub_kls) and hasattr(sub_kls, "openapi_types"):
                subtype = "object"
                subtyp = sub_kls
                subprops = {}
                for attr, attr_type in cast(
                    Dict[str, str], getattr(subtyp, "openapi_types")
                ).items():
                    subprops[getattr(subtyp, "attribute_map")[attr]] = (
                        getattr(subtyp, attr)
                        if isclass(attr_type) or get_origin(attr_type) is list
                        else parse_kube_prop(attr, attr_type)
                    )

                if subtyp.__doc__ is not None:
                    subtype_desc = subtyp.__doc__.strip()

    if t is None or (t == "array" and subtype is None):
        supported_types = "`, `".join(list(NATIVE_TYPES))
        raise TypeError(
            f"`{typ}` not suported",
            f"`{supported_types}`",
            "kubernetes.client classes and",
            "and lists of these types are allowed",
        )

    p = PropYaml(
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


class PropYaml:
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
