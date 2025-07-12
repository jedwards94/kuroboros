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
        "click==8.2.0",
        "kubernetes==32.0.1",
        "inflect==7.5.0",
        "jinja2==3.1.6",
        "prometheus-client==0.21.1",
        "docker==7.1.0",
        "falcon==4.0.2",
        "gunicorn==23.0.0"
    ],
)