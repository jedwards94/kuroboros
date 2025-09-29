import importlib
import inspect
import os
from pathlib import Path
import select
import subprocess
from typing import List
import caseconverter
import click
import tomlkit
from kuroboros.config import KuroborosConfig

from kuroboros.controller import ControllerConfig, ControllerConfigVersions
from kuroboros.exceptions import MultipleDefinitionsException
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.reconciler import BaseReconciler
from kuroboros.schema import CRDSchema
from kuroboros.webhook import BaseMutationWebhook, BaseValidationWebhook


def yaml_format(value):
    """Converts Python types to YAML-compatible strings with proper quoting"""
    if isinstance(value, bool):
        return "true" if value else "false"  # Handle booleans
    if value is None:
        return "null"  # Handle None
    if isinstance(value, (int, float)):
        return str(value)  # Numbers remain unquoted
    if isinstance(value, str):
        # Quote strings that are numeric or contain special characters
        try:
            # Check if string is numeric
            float(value)
            return f'"{value}"'  # Quote numeric-looking strings
        except ValueError:
            # Quote strings with colons, spaces, etc.
            if any(c in value for c in ":[]{}, "):
                return f'"{value}"'
            return value  # Unquoted for simple strings
    else:
        return str(value)  # Fallback for other types


def x_kubernetes_kebab(name: str) -> str:
    """
    Handle special cases for x_kubernetes_ prop prop names
    """
    if name.startswith("x_kubernetes_"):
        return caseconverter.kebabcase(name)

    return name


def create_file(
    output: str, file_name: str, data: str, overwrite: bool = True, parents: bool = True
):
    """
    Creates a file in a given path
    """
    p = Path(f"{output}/{file_name}")
    p.parent.mkdir(parents=parents, exist_ok=True)
    if p.is_file() and not overwrite:
        click.echo(f"not overwriten: {file_name}.")
        return
    try:
        action = ""
        if p.is_file():
            action = "overwriten"
        else:
            action = "created"
        with open(f"{output}/{file_name}", "w", encoding="utf-8") as file:
            file.write(data)
            file.close()

        click.echo(f"{action}: {file_name}")

    except Exception as e:
        click.echo(f"error while craeting file {output}")
        raise e


def run_command(command) -> str:
    """
    Runs a command and return the ouput once finished
    """
    result = subprocess.run(
        command,
        check=True,
        text=True,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.stdout


def get_image_info():
    """
    Gets the operator image from the KuroborosConfig
    """
    reg = KuroborosConfig.get("build", "image", "registry", typ=str)
    repo = KuroborosConfig.get("build", "image", "repository", typ=str)
    tag_suffix = KuroborosConfig.get("build", "image", "tag_suffix", typ=str)
    tag_prefix = KuroborosConfig.get("build", "image", "tag_prefix", typ=str)
    tag = KuroborosConfig.get("build", "image", "tag", typ=str)
    if tag in ("$PYPROJECT_VERSION", "$TAG_OR_SHORT_COMMIT_SHA"):
        if tag == "$PYPROJECT_VERSION":
            with open("./pyproject.toml", "r", encoding="utf-8") as pyproject:
                content = pyproject.read()
                pyproject_toml = tomlkit.loads(content)
                tag = pyproject_toml.get("project", {}).get("version", "latest")
        else:
            tag = run_command(
                "git describe --exact-match --tags 2> /dev/null || git rev-parse --short HEAD"
            )

    img = f"{repo}:{tag_prefix}{tag}{tag_suffix}"
    if reg != "":
        img = f"{reg}/{img}"

    return img


def run_command_stream_simple(command, env=None):
    """
    Runs a siumple shell command and stream the output
    """
    if env is None:
        env = {}
    print(f"running command: {command}")
    with subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, **env},
        bufsize=1,
        universal_newlines=True,
    ) as process:

        stdout = []
        stderr = []

        assert process.stdout is not None
        assert process.stderr is not None
        fd_stdout = process.stdout.fileno()
        fd_stderr = process.stderr.fileno()

        while True:
            # Check stdout
            reads, _, _ = select.select([fd_stdout, fd_stderr], [], [], 0.1)
            if fd_stdout in reads:
                line = process.stdout.readline()
                if line:
                    print(line, end="", flush=True)  # Force flush
                    stdout.append(line)

            if fd_stderr in reads:
                line = process.stderr.readline()
                if line:
                    print(line, end="", flush=True)  # Force flush
                    stderr.append(line)

            # Check if process has finished
            if process.poll() is not None:
                # Read any remaining output
                for line in process.stdout:
                    print(line, end="", flush=True)
                    stdout.append(line)
                for line in process.stderr:
                    print(line, end="", flush=True)
                    stderr.append(line)
                break


def load_controller_configs(controllers_path) -> List[ControllerConfig]:
    """
    Loads the controller config given the controllers path, by default is /controllers
    """
    controllers_configs: List[ControllerConfig] = []
    path = os.path.join(Path().absolute(), controllers_path)
    directory = Path(path)
    try:
        # each folder in /controllers
        controllers = [entry.name for entry in directory.iterdir() if entry.is_dir()]
    except Exception:  # pylint: disable=broad-except
        controllers = []
    for controller in controllers:
        # we assume that each controller has a group_version.py file
        # and a versions folder with the versions of the controller
        ctrl_conf = ControllerConfig()
        ctrl_conf.name = controller
        try:
            group_version_module = importlib.import_module(
                f"{controllers_path}.{controller}.group_version"
            )
        except ModuleNotFoundError:
            # If we dont find any GVI we skip this controller, as its not a controller (?)
            continue
        group_version = None
        for _, obj in inspect.getmembers(group_version_module):
            if isinstance(obj, GroupVersionInfo):
                group_version = obj

        if group_version is None:
            # If we find a group_versin.py file but it doesn't contain any GVI object
            continue
        ctrl_conf.group_version_info = group_version
        versions_path = os.path.join(path, controller)
        versions_dir = Path(versions_path)
        versions = [
            entry.name
            for entry in versions_dir.iterdir()
            if entry.is_dir() and GroupVersionInfo.is_valid_api_version(entry.name)
        ]
        for version in versions:
            # each version folder should have python files with the reconciler, crd classes and
            # posibly validation and mutation webhook
            ctrl_versions = ControllerConfigVersions()
            ctrl_versions.name = version
            modules = [
                {"name": "crd", "required": True},
                {"name": "reconciler", "required": True},
                {"name": "mutation", "required": False},
                {"name": "validation", "required": False},
            ]

            for module_conf in modules:
                module_name = module_conf["name"]
                module = None
                try:
                    module = importlib.import_module(
                        f"{controllers_path}.{controller}.{version}.{module_name}"
                    )
                except ModuleNotFoundError as e:
                    if module_conf["required"]:
                        raise e
                    continue

                assert module is not None
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    if (
                        module_name == "reconciler"
                    ):  # Load reconciler from reconciler.py
                        if BaseReconciler in obj.__bases__:
                            if ctrl_versions.reconciler is not None:
                                raise MultipleDefinitionsException(
                                    ctrl_versions.reconciler, controller, version
                                )
                            obj.set_gvi(group_version)
                            ctrl_versions.reconciler = obj
                    elif module_name == "crd":  # Load CRD from crd.py
                        if CRDSchema in obj.__bases__:
                            if ctrl_versions.crd is not None:
                                raise MultipleDefinitionsException(
                                    ctrl_versions.crd, controller, version
                                )

                            obj.set_gvi(group_version)
                            ctrl_versions.crd = obj
                    elif (
                        module_name == "validation"
                    ):  # Load validation webhook from validation.py
                        if BaseValidationWebhook in obj.__bases__:
                            if ctrl_versions.validation_webhook is not None:
                                raise MultipleDefinitionsException(
                                    ctrl_versions.validation_webhook,
                                    controller,
                                    version,
                                )
                            obj.set_gvi(group_version)
                            ctrl_versions.validation_webhook = obj
                    elif (
                        module_name == "mutation"
                    ):  # Load mutation webhook from validation.py
                        if BaseMutationWebhook in obj.__bases__:
                            if ctrl_versions.mutation_webhook is not None:
                                raise MultipleDefinitionsException(
                                    ctrl_versions.mutation_webhook, controller, version
                                )
                            obj.set_gvi(group_version)
                            ctrl_versions.mutation_webhook = obj
                # Only append after all fields are set and only if valid
                if (
                    ctrl_versions.reconciler is not None
                    and ctrl_versions.crd is not None
                ):
                    ctrl_conf.versions.append(ctrl_versions)

        # If the controller has at least 1 valid version we append it, otherwhise we ignore it
        if len(ctrl_conf.versions) > 0:
            controllers_configs.append(ctrl_conf)

    return controllers_configs
