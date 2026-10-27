"""Find the boards on the network (PROTOCOL.md section 1)."""
from __future__ import annotations

import json
import socket
import time

DISCOVERY_PORT = 4210
MESSAGE = b"VV?DISCOVER"


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def discover(timeout: float = 2.0, extra_hosts: list[str] | None = None, port: int = DISCOVERY_PORT) -> dict[str, dict]:
    """Return {"cam": info, "mic": info} for every board that answered."""
    targets = {"255.255.255.255"}
    ip = local_ip()
    if ip and not ip.startswith("127."):
        targets.add(".".join(ip.split(".")[:3] + ["255"]))
    for h in extra_hosts or []:
        if h:
            targets.add(h)
    found: dict[str, dict] = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.25)
    try:
        deadline = time.time() + timeout
        next_send = 0.0
        while time.time() < deadline:
            if time.time() >= next_send:  # resend a few times; Wi-Fi drops broadcasts
                for t in targets:
                    try:
                        s.sendto(MESSAGE, (t, port))
                    except OSError:
                        pass
                next_send = time.time() + 0.5
            try:
                data, addr = s.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                continue
            try:
                info = json.loads(data.decode())
            except ValueError:
                continue
            if info.get("vv") == 1 and info.get("role") in ("cam", "mic"):
                info.setdefault("ip", addr[0])
                found[info["role"]] = info
            if "cam" in found and "mic" in found:
                break
    finally:
        s.close()
    return found


def resolve_mdns(name: str, timeout: float = 2.0) -> str | None:
    """Best-effort lookup of e.g. 'vv-cam.local' (works on most Windows 10+/macOS/Linux)."""
    import threading

    result: list[str] = []

    def work():
        try:
            result.append(socket.gethostbyname(name))
        except OSError:
            pass

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    return result[0] if result else None
