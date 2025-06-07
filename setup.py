from setuptools import setup, find_packages

setup(
    name='kuroboros',
    version='0.1.0',
    packages=find_packages(include=["kuroboros*", "cli"]),
    entry_points={
        'console_scripts': [
            'kuroboros=kuroboros.cli.main:cli',
        ],
    },
    package_data={
        "kuroboros": ["cli/templates/*", "cli/templates/**/*"]
    },
    include_package_data=True,
    install_requires=[
        "click",
        "kubernetes",
        "inflect",
        "jinja2",
        "prometheus-client",
        "docker"
    ],
)