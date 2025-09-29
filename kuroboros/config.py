from typing import Any, Dict, Type, cast, TypeVar

import tomlkit

OPERATOR_NAMESPACE = "default"
try:
    with open(
        "/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r", encoding="utf-8"
    ) as f:
        ns = f.read().strip()
        OPERATOR_NAMESPACE = ns
except Exception:  # pylint: disable=broad-exception-caught
    pass


T = TypeVar("T")


class KuroborosConfig:
    """
    Configuration class
    """

    _config: Dict[str, Any] = {
        "operator": {
            "name": "kuroboros-operator",
            "leader_acquire_interval_seconds": 10.0,
            "log_level": "INFO",
            "cleanup_interval_seconds": 5.0,
            "metrics": {
                "interval_seconds": 5.0,
                "port": 8080,
            },
            "webhook_server": {
                "port": 443,
                "cert_path": "/etc/tls/tls.crt",
                "key_path": "/etc/tls/tls.key",
                "gunicorn_workers": 1,
            },
        },
        "build": {
            "builder": {
                "binary": "docker",
                "args": ["build", ".", "-t", "$IMG"],
            },
            "image": {
                "registry": "",
                "repository": "kuroboros-operator",
                "tag_prefix": "",
                "tag": "$PYPROJECT_VERSION",
                "tag_suffix": "",
            },
        },
        "generate": {"rbac": {"policies": []}},
    }

    @classmethod
    def get(cls, *keys: *tuple[str, ...], typ: Type[T] = cast(Type[T], None)) -> T:
        """
        Gets a config field from the toml field and optionally casts it to the given type.
        """
        current = cls._config
        for key in keys:
            if key in current:
                current = current[key]
            else:
                raise KeyError(f"config for {'.'.join(keys)} not found")
        if typ is not None:
            if typ == float and isinstance(current, int):
                return int(current) # type: ignore
            if not isinstance(current, typ):
                raise AssertionError(
                    f"type of {'.'.join(keys)} ({current.__class__}) is not {typ}"
                )
            return current.unwrap() if hasattr(current, "unwrap") else current # type: ignore
        return current.unwrap() if hasattr(current, "unwrap") else current

    @staticmethod
    def _merge(default, user):
        for key, value in user.items():
            if (
                isinstance(value, dict)
                and key in default
                and isinstance(default[key], dict)
            ):
                default[key] = KuroborosConfig._merge(default[key], value)
            else:
                default[key] = value
        return default

    @classmethod
    def load(cls, path: str):
        """
        Loads a TOML file as the config for the operator
        """
        toml_config = None
        try:
            with open(path, "r", encoding="utf-8") as config_file:
                content = config_file.read()
                toml_config = tomlkit.loads(content)

            if toml_config is not None:
                cls._config = cls._merge(cls._config, toml_config)
        except FileNotFoundError:
            pass

    @classmethod
    def dumps(cls, key: str) -> str:
        """
        Dumps the given section into a string
        """
        temp = tomlkit.document()
        temp[key] = cls._config[key]

        return tomlkit.dumps(temp)
