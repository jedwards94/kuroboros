import logging
import sys

from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.schema import BaseCRD

FMT = 'timestamp=%(asctime)s name=%(name)s level=%(levelname)s msg="%(message)s"'


root_logger = logging.getLogger()
formater = logging.Formatter(FMT)

stdout_handler = logging.StreamHandler(stream=sys.stdout)
stdout_handler.setFormatter(formater)

root_logger.addHandler(stdout_handler)


class StaticInfoFilter(logging.Filter):
    """
    class to add static field to the logger
    """

    def __init__(self, static_fields):
        super().__init__()
        self.static_fields = static_fields

    def filter(self, record):
        for key, value in self.static_fields.items():
            setattr(record, key, value)
        return True


def reconciler_logger(group_version: GroupVersionInfo, crd: BaseCRD):
    """
    Creates a new logger with the format
    "timestamp=%(asctime)s name=%(name)s "
    "resource_version=%(resource_version)s "
    "level=%(levelname)s msg=\"%(message)s\""
    used in the `reconcile` function
    """
    crd_logger = logging.getLogger(
        f"{group_version.pretty_kind_str(crd.namespace_name)}"
    )
    crd_logger.propagate = False
    filt = StaticInfoFilter({"resource_version": crd.resource_version})
    # Add filter only if not already present
    if not any(isinstance(f, StaticInfoFilter) for f in crd_logger.filters):
        crd_logger.addFilter(filt)
    # Add handler only if not already present
    if not any(isinstance(h, logging.StreamHandler) for h in crd_logger.handlers):
        crd_logger.setLevel(logging.INFO)
        new_format = (
            "timestamp=%(asctime)s name=%(name)s "
            "resource_version=%(resource_version)s "
            'level=%(levelname)s msg="%(message)s"'
        )
        handler = logging.StreamHandler()
        formatter = logging.Formatter(new_format)
        handler.setFormatter(formatter)
        crd_logger.addHandler(handler)
    return crd_logger, filt
