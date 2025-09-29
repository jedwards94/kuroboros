from kuroboros.config import KuroborosConfig
from kuroboros.cli.utils import get_image_info, run_command_stream_simple


def build_operator_image() -> None:
    """
    Builds a Docker image with given build-args and image tag
    """
    binary = KuroborosConfig.get("build", "builder", "binary", typ=str)
    args = KuroborosConfig.get("build", "builder", "args", typ=list)
    img = get_image_info()
    command = " ".join([binary, " ".join(args)])
    run_command_stream_simple(command, env={"IMG": img})