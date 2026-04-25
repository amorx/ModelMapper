import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

ALLOWED_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class UnsafeProviderUrlError(ValueError):
    pass


@dataclass(frozen=True)
class SafeHttpClient:
    timeout_seconds: int
    allow_local_network: bool = False

    def validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise UnsafeProviderUrlError("Provider URL must use http or https")
        if not parsed.hostname:
            raise UnsafeProviderUrlError("Provider URL must include a hostname")
        if parsed.scheme == "http" and parsed.hostname not in ALLOWED_LOCAL_HOSTS:
            raise UnsafeProviderUrlError("HTTP provider URLs are only allowed for localhost")
        if self._is_private_host(parsed.hostname) and not self.allow_local_network:
            raise UnsafeProviderUrlError("Provider URL points to a private network")

    async def post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> httpx.Response:
        self.validate_url(url)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
            return await client.post(url, json=payload, headers=headers)

    async def get_json(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        self.validate_url(url)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
            return await client.get(url, headers=headers)

    @staticmethod
    def _is_private_host(hostname: str) -> bool:
        if hostname in ALLOWED_LOCAL_HOSTS:
            return True
        try:
            addresses = [ipaddress.ip_address(hostname)]
        except ValueError:
            try:
                addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(hostname, None)]
            except socket.gaierror:
                return False
        return any(address.is_private or address.is_loopback or address.is_link_local for address in addresses)
