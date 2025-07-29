import sys
import kuroboros.cli.templates as temps

crd_template = temps.env.get_template("new/controller/types.py.j2")
reconciler_template = temps.env.get_template("new/controller/reconciler.py.j2")
group_version_template = temps.env.get_template("new/controller/group_version.py.j2")
conf_template = temps.env.get_template("new/project/operator.conf.j2")
dockerfile_template = temps.env.get_template("new/project/docker.j2")


def new_crd(kind: str) -> str:
    """
    Creates the new CRD python class file
    """
    return crd_template.render(kind=kind)


def new_reconciler(kind: str, module: str) -> str:
    """
    Creates the new reconciler python class file
    """
    return reconciler_template.render(kind=kind, module=module)


def new_config(name):
    """
    Creates a base operator.conf
    """
    return conf_template.render(name=name)


def new_dockerfile():
    """
    Creates a base Dockerfile
    """
    version = ".".join([str(num) for num in sys.version_info[0:3]])
    return dockerfile_template.render(python_version=version)


def new_group_versions(version: str, group: str, kind: str):
    """
    Creates the controller GVI
    """
    return group_version_template.render(version=version, group=group, kind=kind)
