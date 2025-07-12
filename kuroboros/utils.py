import datetime
import multiprocessing
import queue
import threading
import time
from typing import Callable, ParamSpec, TypeVar
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import hashes, serialization

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
            
def create_tls_files(cert_path: str, key_path: str, service_dns: str):
    priv_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    
    subject = x509.Name([
        x509.NameAttribute(x509.NameOID.COUNTRY_NAME, u"CL"),
        x509.NameAttribute(x509.NameOID.STATE_OR_PROVINCE_NAME, u"RM"),
        x509.NameAttribute(x509.NameOID.LOCALITY_NAME, u"Santiago"),
        x509.NameAttribute(x509.NameOID.ORGANIZATION_NAME, u"Kuroboros"),
        x509.NameAttribute(x509.NameOID.COMMON_NAME, u"{%s}" % service_dns),
    ])
    
    
    san_names = [
        x509.DNSName(u"{%s}" % service_dns)
    ]
    
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(priv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName(san_names),
            critical=False,
        )
        .sign(priv_key, hashes.SHA256())
    )
    with open(key_path, "wb") as f:
        f.write(priv_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    # Save certificate to file
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))