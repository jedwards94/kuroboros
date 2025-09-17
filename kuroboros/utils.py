import concurrent
import concurrent.futures
import threading
import time
from typing import Callable, ParamSpec, Tuple, TypeVar


NamespaceName = Tuple[str | None, str]


def event_aware_sleep(event: threading.Event, timeout: float):
    """
    Checks for `event.is_set()` every 0.1 seconds within the `timeout`
    range. If the `event` is set in any time, the sleep will finish and return
    """
    start_time = time.time()
    remaining = timeout
    while remaining > 0:
        if event.is_set():
            return
        sleep_time = min(remaining, 0.1)
        time.sleep(sleep_time)
        elapsed = time.time() - start_time
        remaining = timeout - elapsed
    return


T = TypeVar("T")
P = ParamSpec("P")


def with_timeout(
    stop: threading.Event,
    retriable: bool,
    timeout_seconds: float | None,
    func: Callable[P, T],
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """
    Runs a function in a ThreadPoolExecutor and send a cancel event to it on SIGINT or timeout
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func, *args, **kwargs)
    start_time = time.time()

    try:
        while not stop.is_set():
            # Calculate remaining time if timeout is specified
            current_time = time.time()
            if timeout_seconds is not None:
                elapsed = current_time - start_time
                if elapsed >= timeout_seconds:
                    if not retriable:
                        stop.set()
                    raise TimeoutError(
                        f"`{func.__name__}` timed out after {timeout_seconds} seconds"
                    )
                wait_time = min(0.1, timeout_seconds - elapsed)
            else:
                wait_time = 0.1

            # Check future completion with dynamic timeout
            try:
                return future.result(timeout=wait_time)
            except concurrent.futures.TimeoutError:
                # Continue loop to check stop event or timeout
                pass

        # Stop event was set
        raise InterruptedError(f"`{func.__name__}` was stopped before completion")
    finally:
        # Cancel future if not completed
        if not future.done():
            future.cancel()
        # Shutdown executor without waiting for thread termination
        executor.shutdown(wait=False)
