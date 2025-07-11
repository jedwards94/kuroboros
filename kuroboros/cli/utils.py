import importlib
import inspect
import os
from pathlib import Path
import subprocess
from typing import List
import click

from kuroboros.controller import ControllerConfig, ControllerConfigVersions
from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.reconciler import BaseReconciler
from kuroboros.schema import BaseCRD
from kuroboros.webhook import BaseValidationWebhook

def yaml_format(value):
    """Converts Python types to YAML-compatible strings with proper quoting"""
    if isinstance(value, bool):
        return "true" if value else "false"  # Handle booleans
    elif value is None:
        return "null"  # Handle None
    elif isinstance(value, (int, float)):
        return str(value)  # Numbers remain unquoted
    elif isinstance(value, str):
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

def parse_prop_name(name: str) -> str:
    """
    Parses the name of props in python kubernetes to a camelCased name with some exceptions
    """
    if name.startswith("x_kubernetes_"):
        return name.replace("_", "-")
    else:
        return name



def create_file(output: str, file_name: str, data: str, overwrite: bool = True):
    p = Path(f"{output}/{file_name}")
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_file() and not overwrite:
        click.echo(f"not overwriten: {file_name}.")
        return
    try:
        with open(f"{output}/{file_name}", "w") as file:
            file.write(data)
            file.close()
        if p.is_file():
            click.echo(f"overwriten: {file_name}")
        else:
            click.echo(f"created: {file_name}")
    except Exception as e:
        click.echo(f"error while craeting file {output}")
        raise e

def run_command_stream_simple(command):
    print(f"running command: {command}")
    process = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    stdout = []
    stderr = []

    while True:
        # Check stdout
        out_line = process.stdout.readline() # type: ignore
        if out_line:
            print(out_line, end='')
            stdout.append(out_line)
            
        # Check stderr
        err_line = process.stderr.readline() # type: ignore
        if err_line:
            print(err_line, end='')
            stderr.append(err_line)
            
        # Check process termination
        if process.poll() is not None:
            break

    # Get remaining output
    for line in process.stdout: # type: ignore
        stdout.append(line)
        print(line, end='')
        
    for line in process.stderr: # type: ignore
        stderr.append(line)
        print(line, end='')
        
def load_controller_configs(controllers_path) -> List[ControllerConfig]:
    controllers_configs: List[ControllerConfig] = []
    path = os.path.join(Path().absolute(), controllers_path)
    directory = Path(path)
    try:
        # each folder in /controllers
        controllers = [entry.name for entry in directory.iterdir() if entry.is_dir()]
    except:
        controllers = []
    for controller in controllers:
        # we assume that each controller has a group_version.py file
        # and a versions folder with the versions of the controller
        ctrl_conf = ControllerConfig()
        ctrl_conf.name = controller
        try:
            group_version_module = importlib.import_module(f"{controllers_path}.{controller}.group_version")
        except:
            continue
        group_version = None
        for _, obj in inspect.getmembers(group_version_module):
            if isinstance(obj, GroupVersionInfo):
                group_version = obj
        
        if group_version is None:
            continue
        ctrl_conf.group_version_info = group_version
        versions_path = os.path.join(path, controller)
        versions_dir = Path(versions_path)
        versions = [entry.name for entry in versions_dir.iterdir() if entry.is_dir()]
        for version in versions:
            # each version folder should have python files with the reconciler, crd classes and 
            # posibly validation webhook
            ctrl_versions = ControllerConfigVersions()
            ctrl_versions.name = version
            version_path = os.path.join(versions_path, version)
            version_dir = Path(version_path)
            python_files = version_dir.glob("*.py")
            for file in python_files:
                module_name = file.stem
                if module_name == "__init__":
                    continue
                
                module = importlib.import_module(f"{controllers_path}.{controller}.{version}.{module_name}")
                for _, obj in inspect.getmembers(module):
                    if inspect.isclass(obj) and BaseReconciler in obj.__bases__:
                        if ctrl_versions.reconciler is not None and not isinstance(ctrl_versions.reconciler, obj):
                            raise RuntimeError(
                                f"Multiple reconciler classes found in {controller} {version}. "
                                "Only one reconciler class is allowed per version."
                            )
                        ctrl_versions.reconciler = obj(group_version)
                    if inspect.isclass(obj) and BaseCRD in obj.__bases__:
                        if ctrl_versions.crd is not None and not isinstance(ctrl_versions.crd, obj):
                            raise RuntimeError(
                                f"Multiple CRD classes found in {controller} {version}. "
                                "Only one CRD class is allowed per version."
                            )
                        ctrl_versions.crd = obj(group_version)
                    if inspect.isclass(obj) and BaseValidationWebhook in obj.__bases__:
                        if ctrl_versions.validation_webhook is not None and not isinstance(ctrl_versions.validation_webhook, obj):
                            raise RuntimeError(
                                f"Multiple validation webhook classes found in {controller} {version}. "
                                "Only one validation webhook class is allowed per version."
                            )
                        ctrl_versions.validation_webhook = obj(group_version)
                if ctrl_versions.reconciler is not None and ctrl_versions.crd is not None:
                    ctrl_conf.versions.append(ctrl_versions)
        if len(ctrl_conf.versions) > 0:
            controllers_configs.append(ctrl_conf)

    
    return controllers_configs
                            
                            
                            
