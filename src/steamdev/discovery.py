"""mDNS discovery of SteamOS devkit devices.

Devices announce a DNS-SD service ``_steamos-devkit._tcp.local.``. The TXT
record carries ``txtvers``, ``login`` (ssh user), ``settings`` (json) and
``devkit1`` (legacy entry point). The service port is the devkit HTTP service
(default 32000).
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field

SERVICE_TYPE = "_steamos-devkit._tcp.local."
DEFAULT_HTTP_PORT = 32000


def _zeroconf():
    """zeroconf は optional extra。無ければ導線を示して落とす。"""
    try:
        import zeroconf
    except ImportError as e:
        raise RuntimeError(
            "mDNS 探索には zeroconf が必要です: pip install 'steamdev[discovery]' "
            "(IP 直指定なら不要)"
        ) from e
    return zeroconf


@dataclass
class DiscoveredDevice:
    name: str
    address: str
    port: int = DEFAULT_HTTP_PORT
    login: str | None = None
    txt: dict = field(default_factory=dict)


class _Listener:
    def __init__(self, zc):
        self.zc = zc
        self.devices: dict[str, DiscoveredDevice] = {}

    def _decode(self, type_, name):
        info = self.zc.get_service_info(type_, name, timeout=3000)
        if info is None or not info.addresses:
            return
        short = name[: -len("." + type_)]
        txt = {}
        for k, v in (info.properties or {}).items():
            try:
                txt[k.decode()] = v.decode() if isinstance(v, bytes) else v
            except Exception:
                pass
        self.devices[short] = DiscoveredDevice(
            name=short,
            address=socket.inet_ntoa(info.addresses[0]),
            port=info.port or DEFAULT_HTTP_PORT,
            login=txt.get("login"),
            txt=txt,
        )

    def add_service(self, zc, type_, name):
        self._decode(type_, name)

    def update_service(self, zc, type_, name):
        self._decode(type_, name)

    def remove_service(self, zc, type_, name):
        self.devices.pop(name[: -len("." + type_)], None)


def discover(timeout: float = 3.0) -> list[DiscoveredDevice]:
    """Browse the LAN for devkit devices for ``timeout`` seconds."""
    zc = _zeroconf().Zeroconf()
    try:
        listener = _Listener(zc)
        _zeroconf().ServiceBrowser(zc, SERVICE_TYPE, listener)
        time.sleep(timeout)
        return list(listener.devices.values())
    finally:
        zc.close()


def resolve_name(name: str, timeout: float = 3.0) -> DiscoveredDevice | None:
    """Resolve a single device by its mDNS instance name."""
    zc = _zeroconf().Zeroconf()
    try:
        listener = _Listener(zc)
        listener._decode(SERVICE_TYPE, f"{name}.{SERVICE_TYPE}")
        if name in listener.devices:
            return listener.devices[name]
        # fall back to a browse (name may differ in case)
        _zeroconf().ServiceBrowser(zc, SERVICE_TYPE, listener)
        deadline = time.time() + timeout
        while time.time() < deadline:
            for k, v in listener.devices.items():
                if k.lower() == name.lower():
                    return v
            time.sleep(0.1)
        return None
    finally:
        zc.close()
