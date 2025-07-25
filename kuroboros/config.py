import configparser
import os


config = configparser.ConfigParser()
config.read(os.environ.get("KUROBOROS_CONFIG", "operator.conf"))

OPERATOR_NAME = config.get("operator", "name", fallback="kuroboros-operator")
OPERATOR_NAMESPACE = "default"
try:
    with open(
        "/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r", encoding="utf-8"
    ) as f:
        ns = f.read().strip()
        OPERATOR_NAMESPACE = ns
except Exception:  # pylint: disable=broad-exception-caught
    pass
