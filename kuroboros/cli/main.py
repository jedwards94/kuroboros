import os
from pathlib import Path
from importlib.metadata import version as pyver
import sys

import click

from kuroboros.cli.build import build_operator_image
from kuroboros.cli.generate import (
    crd_schema,
    kustomize_file,
    mutation_webhook_config,
    operator_config,
    operator_deployment,
    operator_metrics_service,
    operator_webhook_service,
    rbac_leader_role,
    rbac_leader_role_binding,
    rbac_operator_role,
    rbac_operator_role_binding,
    rbac_sa,
    validation_webhook_config,
)

from kuroboros.cli.new import (
    new_config,
    new_crd,
    new_dockerfile,
    new_group_versions,
    new_mutation_webhook,
    new_pyproject,
    new_reconciler,
    new_validation_webhook,
)
from kuroboros.cli.deploy import kubectl_kustomize_apply
from kuroboros.cli.utils import create_file, get_image_info, load_controller_configs
from kuroboros.config import KuroborosConfig
from kuroboros.operator import Operator
from kuroboros.logger import root_logger


VERSION_NUM = pyver("kuroboros")


KUSTOMIZE_OUT = "config/base"
KUSTOMIZE_OVERLAYS = "config/overlays"
CRD_OUT = "crd"
RBAC_OUT = "rbac"
WEBHOOKS_OUT = "webhooks"
DEPLOYMENT_OUT = "deployment"
CONTROLLERS_PATH = "controllers"

sys.path.insert(0, str(Path().absolute()))


@click.group(help=f"Kuroboros Framework {VERSION_NUM}")
@click.option(
    "-c",
    "--config",
    "config_file",
    default="operator.toml",
    help="Configuration file to use [default: opearator.toml]",
)
@click.option(
    "-l",
    "--log-level",
    "log_level",
    help="Log level for the app",
)
@click.pass_context
def cli(ctx: click.Context, config_file, log_level):
    """
    The CLI entrypoint

    Kuroboros Framework {VERSION_NUM}
    """

    ctx.ensure_object(dict)
    ctx.obj["config_file"] = config_file
    KuroborosConfig.load(config_file)
    if log_level is not None:
        root_logger.setLevel(log_level.upper())
    else:
        root_logger.setLevel(KuroborosConfig.get("operator", "log_level", typ=str))
    ctx.obj["controllers"] = load_controller_configs(CONTROLLERS_PATH)


@cli.command("version", help="Get kuroboros version")
def version_cli():
    """
    Get kuroboros version
    """
    click.echo(VERSION_NUM)


@cli.group(help="Generate the kubernetes resources manifests to deploy the operator")
@click.pass_context
def generate(ctx: click.Context):  # pylint: disable=unused-argument
    """
    Generate the kubernetes resources manifests to deploy the operator
    """


@generate.command(help="Generates the CRDs YAML manifests")
@click.pass_context
def crd(ctx):
    """
    Generates the CRDs YAML manifests
    """
    controllers = ctx.obj["controllers"]
    click.echo("🌀 Generating CRD YAMLs")
    click.echo(f"{KUSTOMIZE_OUT}/{CRD_OUT}/")
    output = os.path.join(Path().absolute(), KUSTOMIZE_OUT, CRD_OUT)

    resources = []
    versions_dict = {}
    for ctrl_conf in controllers:
        versions_dict[ctrl_conf.name] = {}
        for version in ctrl_conf.versions:
            versions_dict[ctrl_conf.name][version.name] = version.crd

        create_file(
            output,
            f"{ctrl_conf.group_version_info.kind}.yaml",
            crd_schema(versions_dict[ctrl_conf.name], ctrl_conf.group_version_info),
        )
        resources.append(f"{ctrl_conf.group_version_info.kind}.yaml")

    create_file(output, "kustomization.yaml", kustomize_file(resources))


@generate.command(help="Generates the RBAC YAML manifests")
@click.pass_context
def rbac(ctx):
    """
    Generates the RBAC YAML manifests
    """
    controllers = ctx.obj["controllers"]
    click.echo("🌀 Generating RBAC YAMLs")
    click.echo(f"{KUSTOMIZE_OUT}/{RBAC_OUT}/")
    output = os.path.join(Path().absolute(), KUSTOMIZE_OUT, RBAC_OUT)

    resources = [
        "service-account.yaml",
        "operator-role.yaml",
        "operator-role-binding.yaml",
        "leader-election-role.yaml",
        "leader-election-role-binding.yaml",
    ]

    create_file(output, "service-account.yaml", rbac_sa())
    create_file(output, "operator-role.yaml", rbac_operator_role(controllers))
    create_file(output, "operator-role-binding.yaml", rbac_operator_role_binding())
    create_file(output, "leader-election-role.yaml", rbac_leader_role())
    create_file(output, "leader-election-role-binding.yaml", rbac_leader_role_binding())
    create_file(output, "kustomization.yaml", kustomize_file(resources))


@generate.command(help="Generates the Webhooks YAML manifests")
@click.pass_context
def webhooks(ctx):
    """
    Generates the Webhooks YAML manifests
    """
    controllers = ctx.obj["controllers"]
    click.echo("🌀 Generating Webhooks YAMLs")
    click.echo(f"{KUSTOMIZE_OUT}/{WEBHOOKS_OUT}/")
    output = os.path.join(Path().absolute(), KUSTOMIZE_OUT, WEBHOOKS_OUT)

    resources = []
    ctrls_with_validation_webhooks = [
        ctrl for ctrl in controllers if ctrl.validation_webhook is not None
    ]
    ctrls_with_mutation_webhooks = [
        ctrl for ctrl in controllers if ctrl.mutation_webhook is not None
    ]

    if len(ctrls_with_validation_webhooks) > 0:
        create_file(
            output,
            "validation-webhooks.yaml",
            validation_webhook_config(ctrls_with_validation_webhooks),
        )
        resources.append("validation-webhooks.yaml")

    if len(ctrls_with_mutation_webhooks) > 0:
        create_file(
            output,
            "mutation-webhooks.yaml",
            mutation_webhook_config(ctrls_with_mutation_webhooks),
        )
        resources.append("mutation-webhooks.yaml")
    if len(resources) > 0:
        create_file(output, "kustomization.yaml", kustomize_file(resources))
    else:
        click.echo("Nothing to create")


@generate.command(help="Generates the Deployment YAML manifests")
@click.pass_context
def deployment(ctx):
    """
    Generates the Deployment YAML manifests
    """
    click.echo("🌀 Generating Deployment YAMLs")
    click.echo(f"{KUSTOMIZE_OUT}/{DEPLOYMENT_OUT}/")
    output = os.path.join(Path().absolute(), KUSTOMIZE_OUT, DEPLOYMENT_OUT)

    controllers = ctx.obj["controllers"]
    resources = [
        "operator-deployment.yaml",
        "operator-config.yaml",
        "metrics-service.yaml",
    ]
    include_webhook_service = False
    for ctrl in controllers:
        if ctrl.has_webhooks():
            include_webhook_service = True
    image_config = []

    img = get_image_info()

    if img != "kuroboros-operator:latest":
        image_config = [
            {
                "name": "kuroboros-operator:latest",
                "new_name": img.split(":")[0],
                "new_tag": img.split(":")[1],
            }
        ]

    if include_webhook_service:
        create_file(output, "webhook-service.yaml", operator_webhook_service())
        resources.append("webhook-service.yaml")
    create_file(output, "metrics-service.yaml", operator_metrics_service())
    create_file(output, "operator-deployment.yaml", operator_deployment())
    create_file(output, "operator-config.yaml", operator_config())
    create_file(output, "kustomization.yaml", kustomize_file(resources, image_config))


@generate.command(help="Generates all the YAML manifests")
@click.pass_context
def manifests(ctx):
    """
    Generates all the YAML manifests
    """
    ctx.invoke(crd)
    ctx.invoke(rbac)
    ctx.invoke(deployment)
    ctx.invoke(webhooks)


@generate.command(help="Generate a new overlay in config/overlays")
@click.argument("name")
@click.pass_context
def overlay(ctx, name):
    """
    Generate a new overlay in config/overlays
    """
    controllers = ctx.obj["controllers"]
    click.echo(f"🌀 Creating new overlay {name}")
    output = os.path.join(Path().absolute(), KUSTOMIZE_OVERLAYS, name)
    paths = ["../../base/rbac", "../../base/crd", "../../base/deployment"]
    for ctrl in controllers:
        if ctrl.has_webhooks():
            paths.append("../../base/webhooks")
            break

    file = kustomize_file(paths)
    create_file(output, "kustomization.yaml", file)


@cli.group(help="Creates a new Kuroboros Resource")
def new():
    """
    Creates a new Kuroboros Resource
    """


@new.command(help="Creates a Controller with a base version, a reconciler and its CRD")
@click.option("--kind", type=str, required=True, help="The kind of the CRD")
@click.option(
    "--api-version",
    type=str,
    required=True,
    help="The version to use (example: v1alpha1)",
)
@click.option("--group", type=str, required=True, help="The group owner of the CRD")
def controller(kind: str, api_version: str, group: str):
    """
    Creates a Controller with a base version, a reconciler and its CRD
    """
    click.echo(f"🐍 Creating {kind} Controller")
    python_module = f"{CONTROLLERS_PATH}.{kind.lower()}.{api_version}"
    rec = new_reconciler(kind, python_module)
    crd_data = new_crd(kind)
    group_versions = new_group_versions(api_version, group, kind)

    click.echo(f"{CONTROLLERS_PATH}/")
    create_file(
        f"{CONTROLLERS_PATH}/{kind.lower()}",
        "group_version.py",
        group_versions,
        overwrite=False,
    )
    create_file(
        f"{CONTROLLERS_PATH}/{kind.lower()}/{api_version}",
        "reconciler.py",
        rec,
        overwrite=False,
    )
    create_file(
        f"{CONTROLLERS_PATH}/{kind.lower()}/{api_version}",
        "crd.py",
        crd_data,
        overwrite=False,
    )


@new.command(help="Creates a Webhook for a Controller given its kind and version")
@click.option("--kind", type=str, required=True, help="The kind of the CRD")
@click.option(
    "--api-version",
    type=str,
    required=True,
    help="The version to use (example: v1alpha1)",
)
@click.option(
    "--type",
    "typ",
    type=str,
    required=True,
    help="The type of webhook to create (validation, mutation)",
)
def webhook(kind: str, api_version: str, typ: str):
    """
    Creates a Webhook for a Controller given its kind and version
    """
    if typ not in ("validation", "mutation"):
        raise ValueError("type must be one of (validation, mutation)")
    click.echo(f"🐍 Creating {kind} {typ.capitalize()}Webhook")
    python_module = f"{CONTROLLERS_PATH}.{kind.lower()}.{api_version}"
    wh = None
    if typ == "mutation":
        wh = new_mutation_webhook(kind, python_module)
    else:
        wh = new_validation_webhook(kind, python_module)

    click.echo(f"{CONTROLLERS_PATH}/")
    create_file(
        f"{CONTROLLERS_PATH}/{kind.lower()}/{api_version}",
        f"{typ}.py",
        wh,
        overwrite=False,
        parents=False,
    )


@new.command(help="Creates a new Kuroboros Operator project")
@click.argument("name", type=str)
def operator(name):
    """
    Creates a new Kuroboros Operator project
    """
    click.echo(f"🌀🐍 Creating {name} Operator")
    conf = new_config(name)
    dockerfile = new_dockerfile()
    pyproject = new_pyproject(name, VERSION_NUM)

    create_file(".", "operator.toml", conf)
    create_file(".", "pyproject.toml", pyproject)
    create_file(".", "Dockerfile", dockerfile)
    create_file("controllers", "__init__.py", "")


@cli.command(help="Applies the given overlay to the current context")
@click.argument("overlay", type=str)
def deploy(overlay):  # pylint: disable=redefined-outer-name
    """
    Applies the given overlay to the current context
    """
    click.echo(f"🌀 Deploying Operator from {overlay} overlay")
    kubectl_kustomize_apply(overlay)


@cli.command(help="Build the image")
def build():
    """
    Build the image
    """
    build_operator_image()


@cli.command(help="Starts the Kuroboros Operator")
@click.option(
    "--skip-controllers",
    is_flag=True,
    default=False,
    help="Skips all controllers startup",
)
@click.option(
    "--skip-webhook-server",
    is_flag=True,
    default=False,
    help="Skips the webhook server startup",
)
@click.pass_context
def start(ctx, skip_controllers, skip_webhook_server):
    """
    Starts the Kuroboros Operator

    --skip-controllers: Skips all controllers startup
    --skip-webhook-server: Skips the webhook server startup
    """
    controllers = ctx.obj["controllers"]
    op = Operator()
    click.echo(f"🌀🐍 Starting {op.name} ...")
    op.start(
        controllers=controllers,
        skip_webhook_server=skip_webhook_server,
        skip_controllers=skip_controllers,
    )


if __name__ == "__main__":
    cli(auto_envvar_prefix="KUROBOROS")  # pylint: disable=no-value-for-parameter
