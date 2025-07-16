import re
from typing import Tuple, cast

import inflect


class GroupVersionInfo:
    
    __STABILITY_ORDER = {'alpha': 0, 'beta': 1, 'stable': 2}
    __VERSION_PATTERN = r'^v(\d+)(?:([a-z]+)(\d+))?$'
    
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
    scope: str = "Namespaced"
    
    @staticmethod
    def is_valid_api_version(api_version: str) -> bool:
        """
        Validates if the given API version string matches the expected format.
        """
        return re.match(GroupVersionInfo.__VERSION_PATTERN, api_version) is not None
    
    def pretty_kind_str(self, namespace_name: Tuple[str, str] | None = None) -> str:
        if namespace_name is not None and namespace_name != (None, None):
            return f"{self.kind}{self.pretty_version_str()}(Namespace={namespace_name[0]}, Name={namespace_name[1]})"
        return f"{self.kind}{self.pretty_version_str()}"
    
    def pretty_version_str(self) -> str:
        major = self.major
        stability = self.stability.capitalize()
        minor = (
            self.minor
            if self.minor != 0
            else ""
        )
        
        return f"V{major}{stability}{minor}"
    
    def __init__(self, api_version: str, group: str, kind: str, **kwargs):
        inf = inflect.engine()
        self.api_version = api_version
        self.group = group
        
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
        self.plural = kwargs.get("plural", inf.plural_noun(cast(inflect.Word, kind)).lower())
        self.crd_name = kwargs.get("crd_name", f"{self.plural}.{self.group}")
        self.short_names = kwargs.get("short_names", [])
        
        
    
    
    def _key(self):
        return (
            self.major,
            self.__STABILITY_ORDER[self.stability],
            self.minor
        )

    def __eq__(self, other):
        return self._key() == other._key()

    def __lt__(self, other):
        return self._key() < other._key()

    def __repr__(self):
        return f"GroupVersionInfo('version: {self.api_version}, group: {self.group}')"
    
    