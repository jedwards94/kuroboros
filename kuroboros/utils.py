import multiprocessing
import queue
import threading
import time
from typing import Callable, ParamSpec, TypeVar

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
    pass


T = TypeVar('T')
P = ParamSpec('P')
def with_timeout(
    timeout_seconds,
    func: Callable[P, T], 
    *args: P.args, 
    **kwargs: P.kwargs,
) -> T:
    result_queue = multiprocessing.Queue()
    def wrapper():
        try:
            result = func(*args, **kwargs)
            result_queue.put(("success", result))
        except Exception as e:
            result_queue.put(("error", e))
            
    
    process = multiprocessing.Process(target=wrapper)
    process.start()
    process.join(timeout=timeout_seconds)
    
    if process.is_alive():
        process.terminate()
        process.join()
        raise TimeoutError(f"`{func.__name__}` timed out after {timeout_seconds} seconds")
    
    try:
        status, data = result_queue.get_nowait()
        if status == "error":
            raise data
        return data
    except queue.Empty:
        raise RuntimeError(f"`{func.__name__}` didn't return any result")