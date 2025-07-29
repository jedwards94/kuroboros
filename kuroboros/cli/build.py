import docker
from docker.errors import BuildError, APIError


def docker_build(image: str, args: dict[str, str] | None = None) -> None:
    """
    Builds a Docker image with given build-args and image tag
    """
    # Use the low-level APIClient for real-time logging
    if args is None:
        args = {}

    low_level_client = docker.APIClient(base_url="unix://var/run/docker.sock")

    try:
        build_generator = low_level_client.build(
            path=".", tag=image, buildargs=args, rm=True, decode=True
        )

        for chunk in build_generator:
            if chunk:
                if "stream" in chunk:
                    line = chunk["stream"].strip()
                    if line:
                        print(line)
                elif "error" in chunk:
                    raise BuildError(chunk["error"], build_log=chunk)
                elif "status" in chunk:
                    print(f"Progress: {chunk['status']}")
                elif "aux" in chunk:
                    print(f"Final Image ID: {chunk['aux']['ID']}")

        print("\nBuild succeeded!")

    except BuildError as e:
        print("\nBuild failed: {e.msg}")
        if e.build_log:
            for entry in e.build_log:
                print(entry.get("stream", "").strip())
    except APIError as e:
        print(f"\nDocker API error: {e.explanation}")
    finally:
        low_level_client.close()  # Clean up the client
