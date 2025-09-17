import pprint
from typing import Any, ClassVar, Dict, get_origin, get_args

import caseconverter

from kuroboros.schema.properties import PropYaml


class OpenAPISchema:
    """
    A Base OpenAPI Model to define CRD Properties.
    It auotogenerates attribuite_map and openapi_types based on props
    """

    attribute_map: ClassVar[Dict[str, str]]
    openapi_types: ClassVar[Dict[str, Any]]
    _data: dict

    def __init__(self, **kwargs) -> None:
        self.load_data(**kwargs)

    def __init_subclass__(cls) -> None:
        ## populate the attribute_map and openapi_types
        if "openapi_types" not in cls.__dict__:
            cls.openapi_types = {}

        if "attribute_map" not in cls.__dict__:
            cls.attribute_map = {}

        for attribute, value in cls.__dict__.items():
            if (
                attribute[:2] != "__"
                and not callable(value)
                and isinstance(value, PropYaml)
            ):
                cls.openapi_types[attribute] = value.real_type
                cls.attribute_map[attribute] = caseconverter.camelcase(attribute)

    def __getattribute__(self, name: str):
        attr = object.__getattribute__(self, name)
        data = None
        try:
            data = object.__getattribute__(self, "_data")
        except AttributeError:
            data = {}
        try:
            if isinstance(attr, PropYaml):
                return data[name]
            return attr
        except KeyError:
            return None

    def __setattr__(self, name, value):
        # If setting a property, update both self._data and parent if present
        attr = object.__getattribute__(self, name)
        if isinstance(attr, PropYaml):
            self._data[name] = value
        else:
            object.__setattr__(self, name, value)

    def __repr__(self) -> str:
        return pprint.pformat(self.to_dict())

    def load_data(self, **kwargs):
        """
        load data given given the Schema attributes in attribute_map keys
        """
        data = {}
        for arg, val in kwargs.items():
            if arg not in self.attribute_map:
                continue
            propertie = object.__getattribute__(self, arg)
            if isinstance(propertie, PropYaml):
                prop_type = propertie.real_type
                if get_origin(prop_type) is list:
                    data[arg] = [
                        (
                            el
                            if hasattr(el, "openapi_types")
                            else get_args(prop_type)[0](**el)
                        )
                        for el in val
                    ]

                elif hasattr(prop_type, "openapi_types") and val is not None:
                    data[arg] = (
                        prop_type(**val.to_dict())
                        if hasattr(val, "to_dict")
                        else prop_type(**val)
                    )
                else:
                    data[arg] = val
        object.__setattr__(self, "_data", data)

    def to_dict(self):
        """Returns the model properties as a dict"""
        result = {}

        for attr in self.attribute_map:
            value = getattr(self, attr)
            if isinstance(value, list):
                result[attr] = list(
                    map(lambda x: x.to_dict() if hasattr(x, "to_dict") else x, value)
                )
            elif hasattr(value, "to_dict"):
                result[attr] = value.to_dict()
            elif isinstance(value, dict):
                result[attr] = dict(
                    map(
                        lambda item: (
                            (item[0], item[1].to_dict())
                            if hasattr(item[1], "to_dict")
                            else item
                        ),
                        value.items(),
                    )
                )
            else:
                result[attr] = value
        return result
