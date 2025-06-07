import configparser
import os


config = configparser.ConfigParser()
config.read(os.environ.get("KUROBOROS_CONFIG","operator.conf"))


get_operator_name = lambda : config.get("operator", "name", fallback="kuroboros-operator")

OPERATOR_NAMESPACE = "default"
try:
    with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
        ns = f.read().strip()
        OPERATOR_NAMESPACE = ns
except:
    pass



