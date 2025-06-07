import os
from pathlib import Path
import sys
import click

from kuroboros.cli.build import docker_build
from kuroboros.cli.generate import (
    crd_schema,
    kustomize_file,
    operator_config,
    operator_deployment,
    rbac_leader_role,
    rbac_leader_role_binding,
    rbac_operator_role,
    rbac_operator_role_binding,
    rbac_sa,
)

from kuroboros.cli.new import (
    new_config,
    new_crd,
    new_dockerfile,
    new_group_versions,
    new_reconciler,
)
from kuroboros.cli.deploy import kubectl_kustomize_apply
from kuroboros.cli.utils import create_file, load_controller_configs
from kuroboros.config import config
from kuroboros.operator import Operator

from importlib.metadata import version
VERSION_NUM = version("kuroboros")


KUSTOMIZE_OUT = "config/base"
KUSTOMIZE_OVERLAYS = "config/overlays"
CRD_OUT = "crd"
RBAC_OUT = "rbac"
DEPLOYMENT_OUT = "deployment"

sys.path.insert(0, str(Path().absolute()))
# crds = load_from_path(CRD_PATH, BaseCRD, api=None)
# reconcilers = load_from_path(RECONCILER_PATH, BaseReconciler)

controllers = load_controller_configs("controllers")


@click.group(help=f"Kuroboros Framework {VERSION_NUM}")
@click.option(
    "-c",
    "--config",
    "config_file",
    default="operator.conf",
    help="Configuration file to use [default: opearator.conf]",
)
@click.pass_context
def cli(ctx, config_file):
    ctx.ensure_object(dict)
    ctx.obj["config_file"] = config_file
    config.read(config_file)
    pass

@cli.command("version",help="Get kuroboros version")
def version_cli():
    click.echo(VERSION_NUM)

@cli.group(help="Generate the kubernetes resources manifests to deploy the operator")
@click.pass_context
def generate(ctx: click.Context):
    pass


@generate.command(help="Generates the CRDs YAML manifests")
def crd():
    click.echo("🌀 Generating CRD YAMLs")
    click.echo(f"{KUSTOMIZE_OUT}/{CRD_OUT}/")
    output = os.path.join(Path().absolute(), KUSTOMIZE_OUT, CRD_OUT)

    resources = []
    versions_dict = {}
    for ctrl_conf in controllers:
        for version in ctrl_conf.versions:
            versions_dict[version.name] = version.crd

        create_file(
            output,
            f"{ctrl_conf.group_version_info.kind}.yaml",
            crd_schema(versions_dict, ctrl_conf.group_version_info),
        )
        resources.append(f"{ctrl_conf.group_version_info.kind}.yaml")

    create_file(output, "kustomization.yaml", kustomize_file(resources))


@generate.command(help="Generates the RBAC YAML manifests")
def rbac():
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


@generate.command(help="Generates the Deployment YAML manifests")
@click.pass_context
def deployment(ctx):
    click.echo("🌀 Generating Deployment YAMLs")
    click.echo(f"{KUSTOMIZE_OUT}/{DEPLOYMENT_OUT}/")
    output = os.path.join(Path().absolute(), KUSTOMIZE_OUT, DEPLOYMENT_OUT)

    config_file = ctx.obj["config_file"]
    resources = ["operator-deployment.yaml", "operator-config.yaml"]
    image_config = []
    if "generate.deployment.image" in config.sections():
        reg = config.get("generate.deployment.image", "registry", fallback="")
        repo = config.get(
            "generate.deployment.image", "repository", fallback="kuroboros-operator"
        )
        tag = config.get("generate.deployment.image", "tag", fallback="latest")
        img = repo
        if reg != "":
            img = f"{reg}/{repo}"
        image_config = [
            {
                "name": "kuroboros-operator:latest",
                "new_name": img,
                "new_tag": tag,
            }
        ]

    create_file(output, "operator-deployment.yaml", operator_deployment())
    create_file(output, "operator-config.yaml", operator_config(config_file))
    create_file(output, "kustomization.yaml", kustomize_file(resources, image_config))


@generate.command(help="Generates all the YAML manifests")
@click.pass_context
def manifests(ctx):
    ctx.invoke(crd)
    ctx.invoke(rbac)
    ctx.invoke(deployment)


@generate.command(help="Generate a new overlay in config/overlays")
@click.argument("name")
def overlay(name):
    click.echo(f"🌀 Creating new overlay {name}")
    output = os.path.join(Path().absolute(), KUSTOMIZE_OVERLAYS, name)

    file = kustomize_file(
        ["../../base/rbac", "../../base/crd", "../../base/deployment"]
    )
    create_file(output, "kustomization.yaml", file)


@cli.group(help="Creates a new Kuroboros Resource")
def new():
    pass


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
    click.echo(f"🐍 Creating {kind} Controller")
    rec = new_reconciler(kind)
    crd = new_crd(kind)
    group_versions = new_group_versions(api_version, group, kind)

    click.echo(f"controllers/")
    create_file(
        f"controllers/{kind.lower()}",
        "group_version.py",
        group_versions,
        overwrite=False,
    )
    create_file(
        f"controllers/{kind.lower()}/{api_version}",
        "reconciler.py",
        rec,
        overwrite=False,
    )
    create_file(
        f"controllers/{kind.lower()}/{api_version}", "crd.py", crd, overwrite=False
    )


@new.command(help="Creates a new Kuroboros Operator project")
@click.argument("name", type=str)
def operator(name):
    click.echo(f"🌀🐍 Creating {name} Operator")
    conf = new_config(name)
    dockerfile = new_dockerfile()

    create_file(".", "operator.conf", conf)
    create_file(".", "Dockerfile", dockerfile)
    create_file("controllers", "__init__.py", "")


@cli.command(help="Applies the given overlay to the current context")
@click.argument("overlay", type=str)
def deploy(overlay):
    click.echo(f"🌀 Deploying Operator from {overlay} overlay")
    kubectl_kustomize_apply(overlay)


@cli.command(help="Build the image")
def build():
    reg = config.get("generate.deployment.image", "registry", fallback="")
    repo = config.get(
        "generate.deployment.image", "repository", fallback="kuroboros-operator"
    )
    tag = config.get("generate.deployment.image", "tag", fallback="latest")
    img = f"{repo}:{tag}"
    if reg != "":
        img = f"{reg}/{img}"
    click.echo(f"🌀 Building Kuroboros Operator image with tag {img}")
    docker_build(img)
    click.echo(f"🌀 Done building Kuroboros Operator image")


@cli.command(help="Starts the Kuroboros Operator")
def start():
    operator = Operator()
    click.echo(f"🌀🐍 Starting {operator.name} ...")
    last_ctrl = controllers[-1]
    for ctrl in controllers:
        is_last = ctrl == last_ctrl
        decoration = "  └─" if is_last else "  ├─"
        run_version = ctrl.get_run_version()
        major = ctrl.group_version_info.major
        stability = ctrl.group_version_info.stability.capitalize()
        minor = ctrl.group_version_info.minor if ctrl.group_version_info.minor != 0 else ""
        if run_version.reconciler is None:
            raise RuntimeError(f"reconciler `None` in {ctrl.name} {run_version.name}")

        name = f"{ctrl.name.capitalize()}V{major}{stability}{minor}Controller"
        try:
            operator.add_controller(
                name=name,
                group_version=ctrl.group_version_info,
                reconciler=run_version.reconciler,
            )
            click.echo(
                f"{decoration} {name} ({ctrl.group_version_info.group}/{ctrl.group_version_info.api_version}/{ctrl.group_version_info.plural}) [OK]"
            )

        except Exception as e:
            click.echo(
                f"{decoration} {name} ({ctrl.group_version_info.group}/{ctrl.group_version_info.api_version}/{ctrl.group_version_info.plural}) [FAILED]"
            )
            click.echo(e)
            continue

    operator.start()


if __name__ == "__main__":
    cli(auto_envvar_prefix="KUROBOROS")
