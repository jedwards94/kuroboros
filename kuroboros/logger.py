import logging
import sys


FMT = 'timestamp=%(asctime)s name=%(name)s level=%(levelname)s msg="%(message)s"'


root_logger = logging.getLogger()
formater = logging.Formatter(FMT)

stdout_handler = logging.StreamHandler(stream=sys.stdout)
stdout_handler.setFormatter(formater)

root_logger.addHandler(stdout_handler)