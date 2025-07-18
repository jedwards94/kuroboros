import configparser
from typing import Dict, List

from kuroboros.config import config, get_operator_name
from kuroboros.controller import ControllerConfig
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.schema import BaseCRD, CRDProp
from kuroboros.cli.utils import parse_prop_name, yaml_format
import kuroboros.cli.templates as temps

temps.env.filters["camel"] = parse_prop_name
temps.env.filters["yaml"] = yaml_format

crd_template = temps.env.get_template("generate/crd/crd.yaml.j2")
deployment_template = temps.env.get_template("generate/deployment/operator-deployment.yaml.j2")
deployment_config_template = temps.env.get_template("generate/deployment/operator-config.yaml.j2")
deployment_metrics_service = temps.env.get_template("generate/deployment/metrics-service.yaml.j2")
deployment_webhook_service = temps.env.get_template("generate/deployment/webhook-service.yaml.j2")
rbac_sa_template = temps.env.get_template("generate/rbac/service-account.yaml.j2")
rbac_operator_role_template = temps.env.get_template("generate/rbac/operator-role.yaml.j2")
rbac_operator_role_binding_template = temps.env.get_template("generate/rbac/operator-role-binding.yaml.j2")
rbac_leader_election_role_template = temps.env.get_template("generate/rbac/leader-election-role.yaml.j2")
rbac_leader_election_role_binding_template = temps.env.get_template("generate/rbac/leader-election-role-binding.yaml.j2")
validation_webhook_configs = temps.env.get_template("generate/webhooks/validation-webhook-config.yaml.j2")
mutation_webhook_configs = temps.env.get_template("generate/webhooks/mutation-webhook-config.yaml.j2")
kustomization_template = temps.env.get_template("generate/kustomization.yaml.j2")

def crd_schema(versions: Dict[str, BaseCRD], group_version_info: GroupVersionInfo) -> str:
    """
    Generates the `CustomResourceDefinition` for the inherited `BaseCRD` class
    """
    version_props = {}
    for version in versions:
        crd = versions[version]
        props = {}
        required = []

        base_attr = dir(BaseCRD)
        child_attr = [attr for attr in dir(crd) if attr not in base_attr]

        for attr_name in child_attr:
            if attr_name in base_attr:
                continue
            attr = object.__getattribute__(crd, attr_name)
            if isinstance(attr, CRDProp):
                props[attr_name] = attr

        status = object.__getattribute__(crd, "status")
        if status.typ != "object":
            raise Exception("status can only be a `dict` type object")
        
        version_props[version] = {
            "props": props,
            "status": status
        }
        
    return crd_template.render(gvi=group_version_info, version_props=version_props)


def rbac_sa() -> str:
    """
    Generates the operator `ServiceAccount`
    """
    return rbac_sa_template.render(name=get_operator_name())


def rbac_operator_role(controllers: List[ControllerConfig]) -> str:
    """
    Generates the operator `Role`.
    Loads custom `Policies` to use in the `Role` from all the sections that start with `generate.rbac.policies.`
    """
    config_policies = [
        config[k] for k in config.sections() if k.startswith("generate.rbac.policies.")
    ]
    policies = []
    for policy in config_policies:
        policy_obj = {
            "api_groups": policy.get("api_groups", fallback="").split(","),
            "resources": policy.get("resources", fallback="").split(","),
            "verbs": policy.get("verbs", fallback="").split(","),
        }
        policies.append(policy_obj)

    for ctrl in controllers:
        ctrl_crd_policy = {
            "api_groups": [ctrl.group_version_info.group],
            "resources": [ctrl.group_version_info.plural],
            "verbs": ["create", "list", "watch", "delete", "get", "patch", "update"],    
        }
        policies.append(ctrl_crd_policy)

    return rbac_operator_role_template.render(name=get_operator_name(), policies=policies)


def rbac_leader_role() -> str:
    """
    Generates leader election `Role`
    """
    return rbac_leader_election_role_template.render(name=get_operator_name())


def rbac_operator_role_binding() -> str:
    """
    Generates the operator `RoleBinding` of the `ServiceAccount` and the `Role`
    """
    return rbac_operator_role_binding_template.render(name=get_operator_name())


def rbac_leader_role_binding() -> str:
    """
    Generates the leader election `RoleBinding` of the `ServiceAccount` and the `Role`
    """
    return rbac_leader_election_role_binding_template.render(name=get_operator_name())


def operator_deployment() -> str:
    """
    Generates the `Deployment` of the operator.
    takes the `image` spec of the container from the `generate.deployment.image` section
    of the `config_file` passed in the arguments
    """
    return deployment_template.render(name=get_operator_name())

def operator_metrics_service() -> str:
    """
    Generates the `Service` for the operator metrics.
    The service is used to expose the operator's metrics server
    """
    return deployment_metrics_service.render(name=get_operator_name())

def operator_webhook_service() -> str:
    """
    Generates the `Service` for the operator webhook.
    The service is used to expose the operator's webhook server
    """
    return deployment_webhook_service.render(name=get_operator_name())

def operator_config(config_file: str) -> str:
    """
    Generates the `ConfigMap` from the `config_file` passed in the parameters
    for the `Deployment` to use. Only takes the `operator` section of the file
    """
    temp_config = configparser.ConfigParser()
    temp_config.read(config_file)
    operator_config = temp_config["operator"]
    
    if "name" in operator_config.keys():
        operator_config.pop("name")

    return deployment_config_template.render(name=get_operator_name(), config=operator_config)
    

def validation_webhook_config(controllers: List[ControllerConfig]) -> str:
    """
    Generates the `ValidatingWebhookConfiguration` for the controllers
    """
    gvis = []
    for ctrl in controllers:
        gvi = ctrl.group_version_info
        gvis.append(gvi)

    return validation_webhook_configs.render(name=get_operator_name(), gvis=gvis)

def mutation_webhook_config(controllers: List[ControllerConfig]) -> str:
    """
    Generates the `ValidatingWebhookConfiguration` for the controllers
    """
    gvis = []
    for ctrl in controllers:
        gvi = ctrl.group_version_info
        gvis.append(gvi)

    return mutation_webhook_configs.render(name=get_operator_name(), gvis=gvis)

def kustomize_file(resources: list[str], images: list[dict] = []):
    return kustomization_template.render(resources=resources, images=images)