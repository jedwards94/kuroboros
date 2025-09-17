import re
from typing import cast

import inflect

from kuroboros.utils import NamespaceName


class GroupVersionInfo:
    """
    Contains the static information about the version to run in the controller
    """

    __STABILITY_ORDER = {"alpha": 0, "beta": 1, "stable": 2}
    __VERSION_PATTERN = r"^v(\d+)(?:([a-z]+)(\d+))?$"

    api_version: str
    group: str
    major: int
    stability: str
    minor: int

    kind: str
    singular: str
    plural: str
    crd_name: str
    short_names: list[str]
    scope: str

    @staticmethod
    def is_valid_api_version(api_version: str) -> bool:
        """
        Validates if the given API version string matches the expected format.
        """
        return re.match(GroupVersionInfo.__VERSION_PATTERN, api_version) is not None

    def is_namespaced(self):
        """
        Returns `True` if the GVI is scoped `Namespaced`
        """
        return self.scope == "Namespaced"

    def pkind(self, namespace_name: NamespaceName | None = None) -> str:
        """
        Return a string to represent the CRD as `MyCRDV1Stable` or
        `MyCRD(Namespace="string", Name="string")` if available
        """
        if namespace_name is not None and namespace_name != (None, None):
            ns = namespace_name[0]
            n = namespace_name[1]
            return f"{self.kind}{self.pversion()}(Namespace={ns}, Name={n})"
        return f"{self.kind}{self.pversion()}"

    def pversion(self) -> str:
        """
        Get a pretty version string as `V1Stable`
        """
        major = self.major
        stability = self.stability.capitalize()
        minor = self.minor if self.minor != 0 else ""

        return f"V{major}{stability}{minor}"

    def __init__(
        self,
        api_version: str,
        group: str,
        kind: str,
        scope: str = "Namespaced",
        **kwargs,
    ):
        inf = inflect.engine()
        self.api_version = api_version
        self.group = group
        self.scope = scope

        if self.scope not in ("Namespaced", "Cluster"):
            raise ValueError("scope must be one of `Namespaced` or `CLuster`")

        match = re.match(self.__VERSION_PATTERN, self.api_version)
        if not match:
            raise ValueError(f"Invalid format {self.api_version}")

        self.major = int(match.group(1))
        stability = match.group(2) or "stable"
        self.minor = int(match.group(3)) if match.group(3) else 0

        if stability not in self.__STABILITY_ORDER:
            raise ValueError(f"Unknown stability level: {stability}")
        self.stability = stability

        self.kind = kind
        self.singular = kwargs.get("singular", kind.lower())
        self.plural = kwargs.get(
            "plural", inf.plural_noun(cast(inflect.Word, kind)).lower()
        )
        self.crd_name = kwargs.get("crd_name", f"{self.plural}.{self.group}")
        self.short_names = kwargs.get("short_names", [])

    def _key(self):
        return (self.major, self.__STABILITY_ORDER[self.stability], self.minor)

    def __eq__(self, other):
        return self._key() == other._key()

    def __lt__(self, other):
        return self._key() < other._key()

    def __repr__(self):
        return f"GroupVersionInfo(version={self.api_version}, group={self.group})"
