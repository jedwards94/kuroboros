import sys
import kuroboros.cli.templates as temps

crd_template = temps.env.get_template("new/controller/types.py.j2")
reconciler_template = temps.env.get_template("new/controller/reconciler.py.j2")
group_version_template = temps.env.get_template("new/controller/group_version.py.j2")
conf_template = temps.env.get_template("new/project/operator.conf.j2")
dockerfile_template = temps.env.get_template("new/project/docker.j2")


def new_crd(kind):
    return crd_template.render(kind=kind)


def new_reconciler(kind):
    return reconciler_template.render(kind=kind)


def new_config(name):
    return conf_template.render(name=name)


def new_dockerfile():
    version = ".".join([str(num) for num in sys.version_info[0:3]])
    return dockerfile_template.render(python_version=version)


def new_group_versions(version: str, group: str, kind: str):
    return group_version_template.render(version=version, group=group, kind=kind)
