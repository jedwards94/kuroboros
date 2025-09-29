from inspect import isclass
import json
from typing import Any, Type, TypeVar, cast, get_args, get_origin
from kubernetes import client, dynamic
from kuroboros.schema import CRDSchema
from kuroboros.schema import OpenAPISchema

R = TypeVar("R", bound=Any)


class ExtendedClient(client.ApiClient):
    """
    Implements `kubernetes.client.ApiClient` to add
    `OpenAPISchema` desearialization
    """

    def __deserialize_openapi(self, data, klass):
        kwargs = {}
        if (
            data is not None
            and klass.openapi_types is not None
            and isinstance(data, (list, dict))
        ):
            for attr, attr_type in klass.openapi_types.items():
                if klass.attribute_map[attr] in data:
                    value = data[klass.attribute_map[attr]]
                    kwargs[attr] = self.deserialize(value, attr_type)

        return klass(**kwargs)

    def deserialize(self, response, response_type):
        if response is None:
            return None
        if response_type == "file":
            return self.__deserialize_file(response)
        # fetch data from response object
        if not hasattr(response, "data"):
            response = type("obj", (object,), {"data": json.dumps(response)})
        try:
            data = json.loads(response.data)  # type: ignore
        except ValueError:
            data = response.data  # type: ignore

        if isclass(response_type) and issubclass(response_type, OpenAPISchema):
            return self.__deserialize_openapi(data, response_type)
        if get_origin(response_type) is list:
            sub_kls = get_args(response_type)[0]
            return [self.__deserialize_openapi(sub_data, sub_kls) for sub_data in data]

        return super().deserialize(response, response_type)

    def serialize(self, data):
        """
        return a sanitzed representation
        """
        return self.sanitize_for_serialization(data)

class ExtendedApi:
    """
    Extends the kubernetes.dynamic.DynamicClient to use the
    kuroboros.extended_api.ExtendedClient
    """
    _api_client: ExtendedClient
    _dynamic_client: dynamic.DynamicClient

    def __init__(self, api_client: ExtendedClient | None = None) -> None:
        if api_client is None:
            api_client = ExtendedClient()

        self._api_client = api_client
        self._dynamic_client = dynamic.DynamicClient(self._api_client)

    def _api_info_from_klass(self, klass: Type):
        av = None
        kind = None
        if isclass(klass) and issubclass(klass, CRDSchema):
            gvi = klass.get_gvi()
            assert gvi is not None
            av = f"{gvi.group}/{gvi.api_version}"
            kind = gvi.kind
        return (av, kind)

    def deserialize(self, data, klass: type):
        """
        Deserialize a OpenAPI Model into a python class
        """
        return self._api_client.deserialize(
            response=data.to_dict() if hasattr(data, "to_dict") else data,
            response_type=klass,
        )

    def serialize(self, instance):
        """
        Serialize a python class into OpenAPI Model valid value
        """
        return self._api_client.serialize(instance)

    def create(
        self,
        body: Any,
        kind: str | None = None,
        api_version: str | None = None,
        namespace: str | None = None,
        klass: Type[R] = object,
    ) -> R:
        """
        Creates the resource in the clsuter and returns it deserialized.
        `api_version` and `kind` can be obtained from `CRDSchema` subclasses

        :param body: The camelCased dictonary to create in the cluster
        :param kind: the kind to create
        :param api_version: the target group/version if `None` will use prefered kind version
        :param namespace: the target namespace
        :param klass: the return type
        """
        av, k = (
            self._api_info_from_klass(klass)
            if (api_version, kind) == (None, None)
            else (api_version, kind)
        )
        return cast(
            R,
            self.deserialize(
                self._dynamic_client.resources.get(api_version=av, kind=k).create(
                    namespace=namespace, body=self.serialize(body)
                ),
                klass,
            ),
        )

    def get(
        self,
        api_version: str | None = None,
        kind: str | None = None,
        namespace: str | None = None,
        klass: Type[R] = object,
        **kwargs,
    ) -> R:
        """
        Gets the resources in the cluster givcen its name and returns it deserialized.
        `api_version` and `kind` can be obtained from `CRDSchema` subclasses

        :param kind: the kind to get
        :param api_version: the target group/version if `None` will use prefered kind version
        :param namespace: the target namespace
        :param name: the target name to retrieve
        :param klass: the return type of the object

        """
        av, k = (
            self._api_info_from_klass(klass)
            if (api_version, kind) == (None, None)
            else (api_version, kind)
        )
        data = self._dynamic_client.resources.get(api_version=av, kind=k).get(
            namespace=namespace, **kwargs
        )

        ret = (
            [self.deserialize(el, get_args(klass)[0]) for el in data.items]
            if hasattr(data, "items") and not callable(data.items)
            else self.deserialize(data, klass)
        )

        return cast(R, ret)

    def patch(
        self,
        patch_body: Any,
        name: str,
        kind: str | None = None,
        api_version: str | None = None,
        namespace: str | None = None,
        subresources: list[str] | None = None,
        content_type: str = "application/merge-patch+json",
        klass: Type[R] = object,
        **kwargs,
    ) -> R:
        """
        Patch the resource in the clsuter and returns it deserialized.
        `api_version` and `kind` can be obtained from `CRDSchema` subclasses

        :param patch_body: The camelCased dictonary to create in the cluster
        :param kind: the kind to create
        :param api_version: the target group/version if `None` will use prefered kind version
        :param namespace: the target namespace
        :param subresources: list of subresources to update
        :param content_type: the Content-Type header for the Patch api call
        :param klass: the return type
        :param **kwargs: extra arguments given to `kubernetes.dynamic.DynamicClient.patch()`

        """
        if subresources is None:
            subresources = []
        av, k = (
            self._api_info_from_klass(klass)
            if (api_version, kind) == (None, None)
            else (api_version, kind)
        )

        patch_body = self.serialize(patch_body)
        args = {
            "namespace": namespace,
            "name": name,
            "body": patch_body,
            "content_type": content_type,
        }

        resource = self._dynamic_client.resources.get(api_version=av, kind=k)
        for sub in subresources:
            if sub in resource.subresources.keys() and sub in patch_body:
                resource.subresources[sub].patch(
                    **{
                        **args,
                        **kwargs,
                        "body": {sub: patch_body.pop(sub)},
                    }
                )
        return cast(
            R,
            self.deserialize(
                resource.patch(**{**args, **kwargs}),
                klass,
            ),
        )

    def delete(
        self,
        name: str,
        api_version: str | None = None,
        kind: str | None = None,
        namespace: str | None = None,
        klass: Type = type(None),
        **kwargs,
    ):
        """
        Deletes a resource in the clsuter.
        `api_version` and `kind` can be obtained from `CRDSchema` subclasses

        :param kind: the kind to create
        :param api_version: the target group/version if `None` will use prefered kind version
        :param namespace: the target namespace
        :param klas: the return type
        :param **kwargs: extra arguments given to `kubernetes.dynamic.DynamicClient.delete()`
        """
        av, k = (
            self._api_info_from_klass(klass)
            if (api_version, kind) == (None, None)
            else (api_version, kind)
        )
        self._dynamic_client.resources.get(api_version=av, kind=k).delete(
            namespace=namespace, name=name, **kwargs
        )
