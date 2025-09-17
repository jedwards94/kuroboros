from typing import Dict, List, cast

from kuroboros.config import KuroborosConfig
from kuroboros.controller import ControllerConfig
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.schema import CRDSchema, PropYaml
from kuroboros.cli.utils import x_kubernetes_kebab, yaml_format
import kuroboros.cli.templates as temps

temps.env.filters["maybekebab"] = x_kubernetes_kebab
temps.env.filters["yaml"] = yaml_format


def crd_schema(
    versions: Dict[str, CRDSchema], group_version_info: GroupVersionInfo
) -> str:
    """
    Generates the `CustomResourceDefinition` for the inherited `CRDSchema` class
    """
    version_print_columns = {}
    version_props = {}
    version_desc = {}
    for version in versions:
        crd = versions[version]
        props = {}

        base_attr = dir(CRDSchema)
        child_attr = [attr for attr in dir(crd) if attr not in base_attr]

        for attr_name in child_attr:
            if attr_name in base_attr:
                continue
            attr = object.__getattribute__(crd, attr_name)
            if isinstance(attr, PropYaml):
                cased_attr_name = crd.attribute_map[attr_name]
                props[cased_attr_name] = attr

        if cast(PropYaml, crd.status).typ != "object":
            raise TypeError("status can only be a `dict` type object")

        print_columns: List[Dict] = []
        for name, (path, typ) in crd.print_columns.items():
            print_columns.append({"json_path": path, "name": name, "type": typ})
        if len(print_columns) > 0:
            version_print_columns[version] = print_columns
        if crd.__doc__ is not None:
            version_desc[version] = crd.__doc__.strip()
        version_props[version] = {"props": props}

    crd_template = temps.env.get_template("generate/crd/crd.yaml.j2")
    return crd_template.render(
        gvi=group_version_info,
        version_props=version_props,
        version_desc=version_desc,
        version_print_columns=version_print_columns,
    )


def rbac_sa() -> str:
    """
    Generates the operator `ServiceAccount`
    """
    rbac_sa_template = temps.env.get_template("generate/rbac/service-account.yaml.j2")
    return rbac_sa_template.render(name=KuroborosConfig.get("operator", "name"))


def rbac_operator_role(controllers: List[ControllerConfig]) -> str:
    """
    Generates the operator `Role`.
    Loads custom `Policies` to use in the `Role` from all
    the sections that start with `generate.rbac.policies.`
    """
    policies_conf = KuroborosConfig.get("generate", "rbac", "policies")

    policies = []
    for policy in cast(list[dict], policies_conf):
        policy_obj = {
            "api_groups": policy["api_groups"] or [],
            "resources": policy["resources"] or [],
            "verbs": policy["verbs"] or [],
        }
        policies.append(policy_obj)

    for ctrl in controllers:
        ctrl_crd_policy = {
            "api_groups": [ctrl.group_version_info.group],
            "resources": [
                ctrl.group_version_info.plural,
                f"{ctrl.group_version_info.plural}/status",
            ],
            "verbs": ["create", "list", "watch", "delete", "get", "patch", "update"],
        }
        policies.append(ctrl_crd_policy)

    rbac_operator_role_template = temps.env.get_template(
        "generate/rbac/operator-role.yaml.j2"
    )
    return rbac_operator_role_template.render(
        name=KuroborosConfig.get("operator", "name"), policies=policies
    )


def rbac_leader_role() -> str:
    """
    Generates leader election `Role`
    """
    rbac_leader_election_role_template = temps.env.get_template(
        "generate/rbac/leader-election-role.yaml.j2"
    )
    return rbac_leader_election_role_template.render(
        name=KuroborosConfig.get("operator", "name")
    )


def rbac_operator_role_binding() -> str:
    """
    Generates the operator `RoleBinding` of the `ServiceAccount` and the `Role`
    """
    rbac_operator_role_binding_template = temps.env.get_template(
        "generate/rbac/operator-role-binding.yaml.j2"
    )
    return rbac_operator_role_binding_template.render(
        name=KuroborosConfig.get("operator", "name")
    )


def rbac_leader_role_binding() -> str:
    """
    Generates the leader election `RoleBinding` of the `ServiceAccount` and the `Role`
    """
    rbac_leader_election_role_binding_template = temps.env.get_template(
        "generate/rbac/leader-election-role-binding.yaml.j2"
    )
    return rbac_leader_election_role_binding_template.render(
        name=KuroborosConfig.get("operator", "name")
    )


def operator_deployment() -> str:
    """
    Generates the `Deployment` of the operator.
    takes the `image` spec of the container from the `generate.deployment.image` section
    of the `config_file` passed in the arguments
    """
    deployment_template = temps.env.get_template(
        "generate/deployment/operator-deployment.yaml.j2"
    )
    return deployment_template.render(name=KuroborosConfig.get("operator", "name"))


def operator_metrics_service() -> str:
    """
    Generates the `Service` for the operator metrics.
    The service is used to expose the operator's metrics server
    """
    deployment_metrics_service = temps.env.get_template(
        "generate/deployment/metrics-service.yaml.j2"
    )
    return deployment_metrics_service.render(
        name=KuroborosConfig.get("operator", "name")
    )


def operator_webhook_service() -> str:
    """
    Generates the `Service` for the operator webhook.
    The service is used to expose the operator's webhook server
    """
    deployment_webhook_service = temps.env.get_template(
        "generate/deployment/webhook-service.yaml.j2"
    )
    return deployment_webhook_service.render(
        name=KuroborosConfig.get("operator", "name")
    )


def operator_config() -> str:
    """
    Generates the `ConfigMap` from the `config_file` passed in the parameters
    for the `Deployment` to use. Only takes the `operator` section of the file
    """
    kuroboros_config = KuroborosConfig.dumps("operator")
    deployment_config_template = temps.env.get_template(
        "generate/deployment/operator-config.yaml.j2"
    )
    return deployment_config_template.render(
        name=KuroborosConfig.get("operator", "name"), config=kuroboros_config
    )


def validation_webhook_config(controllers: List[ControllerConfig]) -> str:
    """
    Generates the `ValidatingWebhookConfiguration` for the controllers
    """
    validation_webhooks: List[Dict] = []
    for ctrl in controllers:
        if ctrl.validation_webhook is not None:
            validation_webhooks.append(ctrl.validation_webhook.get_config_dict())

    validation_webhook_configs = temps.env.get_template(
        "generate/webhooks/validation-webhook-config.yaml.j2"
    )
    return validation_webhook_configs.render(
        name=KuroborosConfig.get("operator", "name"), webhooks=validation_webhooks
    )


def mutation_webhook_config(controllers: List[ControllerConfig]) -> str:
    """
    Generates the `ValidatingWebhookConfiguration` for the controllers
    """
    mutation_webhooks: List[Dict] = []
    for ctrl in controllers:
        if ctrl.mutation_webhook is not None:
            mutation_webhooks.append(ctrl.mutation_webhook.get_config_dict())

    mutation_webhook_configs = temps.env.get_template(
        "generate/webhooks/mutation-webhook-config.yaml.j2"
    )
    return mutation_webhook_configs.render(
        name=KuroborosConfig.get("operator", "name"), webhooks=mutation_webhooks
    )


def kustomize_file(resources: list[str], images: list[dict] | None = None):
    """
    Generates a Kustomization file
    """
    if images is None:
        images = []

    kustomization_template = temps.env.get_template("generate/kustomization.yaml.j2")
    return kustomization_template.render(resources=resources, images=images)
