"""SSH port forwarding for app-side REPL / socket services.

The official client never forwards ports (gdbserver/msvsmon listen on the LAN
directly), but for controlling app-side services (REPL, debug sockets, HTTP
servers bound to localhost on the device) an SSH tunnel is the reliable path.

- ``LocalForward``: host:local_port -> device:remote_port (connect *to* the app)
- ``RemoteForward``: device:remote_port -> host:local_port (let the app reach
  a service running on the host)
"""

from __future__ import annotations

import logging
import select
import socket
import socketserver
import threading

import paramiko

logger = logging.getLogger(__name__)


def _pump(sock: socket.socket, chan) -> None:
    try:
        while True:
            r, _, _ = select.select([sock, chan], [], [])
            if sock in r:
                data = sock.recv(16384)
                if not data:
                    break
                chan.sendall(data)
            if chan in r:
                data = chan.recv(16384)
                if not data:
                    break
                sock.sendall(data)
    except (OSError, EOFError):
        pass
    finally:
        try:
            chan.close()
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass


class LocalForward:
    """Forward a local TCP port to a port on the device (over SSH)."""

    def __init__(self, transport: paramiko.Transport, local_port: int,
                 remote_host: str, remote_port: int, bind: str = "127.0.0.1"):
        self.transport = transport
        self.local_port = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port

        outer = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                try:
                    chan = outer.transport.open_channel(
                        "direct-tcpip",
                        (outer.remote_host, outer.remote_port),
                        self.request.getpeername(),
                    )
                except Exception as e:
                    logger.error("tunnel open failed: %s", e)
                    return
                logger.debug("tunnel: %s -> %s:%d", self.request.getpeername(),
                             outer.remote_host, outer.remote_port)
                _pump(self.request, chan)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = Server((bind, local_port), Handler)
        self.local_port = self.server.server_address[1]  # resolves port 0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> "LocalForward":
        self.thread.start()
        logger.info("local forward 127.0.0.1:%d -> %s:%d",
                    self.local_port, self.remote_host, self.remote_port)
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self) -> "LocalForward":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


class RemoteForward:
    """Forward a port on the device back to a host-side service."""

    def __init__(self, transport: paramiko.Transport, remote_port: int,
                 local_host: str = "127.0.0.1", local_port: int | None = None):
        self.transport = transport
        self.remote_port = remote_port
        self.local_host = local_host
        self.local_port = local_port if local_port is not None else remote_port

    def _on_channel(self, chan, origin, server) -> None:
        sock = socket.create_connection((self.local_host, self.local_port))
        threading.Thread(target=_pump, args=(sock, chan), daemon=True).start()

    def start(self) -> "RemoteForward":
        self.transport.request_port_forward("", self.remote_port, self._on_channel)
        logger.info("remote forward device:%d -> %s:%d",
                    self.remote_port, self.local_host, self.local_port)
        return self

    def stop(self) -> None:
        self.transport.cancel_port_forward("", self.remote_port)

    def __enter__(self) -> "RemoteForward":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
