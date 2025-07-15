import logging
import sys

from kuroboros.group_version_info import GroupVersionInfo
from kuroboros.schema import BaseCRD

stdout_handler = logging.StreamHandler(stream=sys.stdout)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
fmt = "timestamp=%(asctime)s name=%(name)s level=%(levelname)s msg=\"%(message)s\""
formater = logging.Formatter(fmt)
stdout_handler.setFormatter(formater)
root_logger.addHandler(stdout_handler)

class StaticInfoFilter(logging.Filter):
    def __init__(self, static_fields):
        super().__init__()
        self.static_fields = static_fields

    def filter(self, record):
        for key, value in self.static_fields.items():
            setattr(record, key, value)
        return True


def reconciler_logger(group_version: GroupVersionInfo, crd: BaseCRD):
    crd_logger = logging.getLogger(f"{group_version.group}.{group_version.plural}")
    crd_logger.propagate = False
    static_fields = {
        "namespace_name": crd.namespace_name,
        "resource_version": crd.resource_version,
        "version": group_version.api_version,
    }
    filt = StaticInfoFilter(static_fields)
    # Add filter only if not already present
    if not any(isinstance(f, StaticInfoFilter) for f in crd_logger.filters):
        crd_logger.addFilter(filt)
    # Add handler only if not already present
    if not any(isinstance(h, logging.StreamHandler) for h in crd_logger.handlers):
        crd_logger.setLevel(logging.INFO)
        new_format = (
            "timestamp=%(asctime)s name=%(name)s version=%(version)s "
            "namespace_name=%(namespace_name)s resource_version=%(resource_version)s "
            "level=%(levelname)s msg=\"%(message)s\""
        )
        handler = logging.StreamHandler()
        formatter = logging.Formatter(new_format)
        handler.setFormatter(formatter)
        crd_logger.addHandler(handler)
    return crd_logger, filt
