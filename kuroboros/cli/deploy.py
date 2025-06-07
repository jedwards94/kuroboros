import os
from pathlib import Path
import subprocess

from kuroboros.cli.utils import run_command_stream_simple


def kubectl_kustomize_apply(overlay: str):
    overlay = f"config/overlays/{overlay}"
    if os.path.exists(os.path.join(Path().absolute(), overlay)):
        kubectl = subprocess.run("which kubectl", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if kubectl == "":
            raise Exception("kubectl not found, please install it in your $PATH first")
        kubectl_cmd = kubectl.stdout.strip()
        run_command_stream_simple(f"{kubectl_cmd} kustomize {overlay} | {kubectl_cmd} apply -f-")
    else:
        raise Exception(f"{overlay} does not exists")
