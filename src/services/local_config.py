import ipaddress
import os
from urllib.parse import urlparse


def is_local_only_mode() -> bool:
    return os.getenv("MODELMAPPER_LOCAL_ONLY", "1").strip().lower() in ("1", "true", "yes", "on")


def is_allowed_base_url_in_local_mode(base_url: str) -> bool:
    """Allow localhost, loopback, and typical LAN hosts when local-only is enabled."""
    parsed = urlparse(base_url)
    host = str(parsed.hostname or "")
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    if host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_private or address.is_loopback or address.is_link_local)
