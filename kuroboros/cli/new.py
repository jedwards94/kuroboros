import sys
import kuroboros.cli.templates as temps


def new_crd(kind: str) -> str:
    """
    Creates the new CRD python class file
    """
    crd_template = temps.env.get_template("new/controller/types.py.j2")
    return crd_template.render(kind=kind)


def new_reconciler(kind: str, module: str) -> str:
    """
    Creates the new reconciler python class file
    """
    reconciler_template = temps.env.get_template("new/controller/reconciler.py.j2")
    return reconciler_template.render(kind=kind, module=module)


def new_config(name):
    """
    Creates a base operator.toml
    """
    conf_template = temps.env.get_template("new/project/operator.toml.j2")
    return conf_template.render(name=name)


def new_dockerfile():
    """
    Creates a base Dockerfile
    """
    version = ".".join([str(num) for num in sys.version_info[0:3]])
    dockerfile_template = temps.env.get_template("new/project/docker.j2")
    return dockerfile_template.render(python_version=version)

def new_pyproject(name, version) -> str:
    """
    Creates the pyproject.toml file of the operator
    """
    pyproject_template = temps.env.get_template("new/project/pyproject.toml.j2")
    return pyproject_template.render(name=name, kuroboros_version=version)


def new_group_versions(version: str, group: str, kind: str):
    """
    Creates the controller GVI
    """
    group_version_template = temps.env.get_template(
        "new/controller/group_version.py.j2"
    )
    return group_version_template.render(version=version, group=group, kind=kind)


def new_validation_webhook(kind: str, module: str):
    """
    Creates a base validation webhook
    """
    validation_template = temps.env.get_template("new/webhooks/validation.py.j2")
    return validation_template.render(kind=kind, module=module)


def new_mutation_webhook(kind: str, module: str):
    """
    Creates a base mutation webhook
    """
    mutation_template = temps.env.get_template("new/webhooks/mutation.py.j2")
    return mutation_template.render(kind=kind, module=module)
