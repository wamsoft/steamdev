"""Core device abstraction: HTTP pairing endpoints + SSH command channel."""

from __future__ import annotations

import json
import logging
import os
import platform
import shlex
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Iterator

import paramiko

from . import keys
from .discovery import DEFAULT_HTTP_PORT, resolve_name

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 5

# Bundled cygwin tools of the official client (Windows). Reusing them
# guarantees an ssh/rsync pair that interoperates.
OFFICIAL_CYGROOT = (
    r"C:\Program Files (x86)\Steam\steamapps\common\SteamOSDevkitClient"
    r"\windows-client\cygroot\bin"
)
MSYS2_BIN = r"C:\msys64\usr\bin"


def _locate_transfer_tools() -> tuple[str | None, str, str]:
    """Returns (cygpath, ssh, rsync) executable paths."""
    if platform.system() != "Windows":
        for tool in ("ssh", "rsync"):
            if shutil.which(tool) is None:
                raise RuntimeError(f"{tool} not found - please install")
        return None, "ssh", "rsync"
    for root in (OFFICIAL_CYGROOT, MSYS2_BIN):
        ssh = os.path.join(root, "ssh.exe")
        rsync = os.path.join(root, "rsync.exe")
        cygpath = os.path.join(root, "cygpath.exe")
        if all(os.path.exists(p) for p in (ssh, rsync, cygpath)):
            return cygpath, ssh, rsync
    raise RuntimeError(
        "No cygwin-compatible ssh/rsync found. Install msys2 or the official devkit client."
    )


@dataclass
class SteamOSStatus:
    """Parsed output of steamos-get-status --json (device side)."""

    raw: dict = field(default_factory=dict)

    @property
    def hostname(self) -> str | None:
        return self.raw.get("hostname")

    @property
    def os_name(self) -> str | None:
        return self.raw.get("os_name")

    @property
    def session(self) -> str | None:
        return self.raw.get("session_status")

    @property
    def steam_status(self) -> str | None:
        return self.raw.get("steam_status")

    @property
    def cef_debugging(self) -> bool:
        return bool(self.raw.get("cef_debugging_enabled"))


class Device:
    """A SteamOS devkit device reachable over LAN.

    Construct with an IP/hostname (``Device('192.168.1.30')``) or resolve by
    mDNS name (``Device.from_name('steamdeck')``).
    """

    def __init__(self, address: str, login: str | None = None,
                 http_port: int = DEFAULT_HTTP_PORT, name: str | None = None):
        self.address = address
        self.login = login
        self.http_port = http_port
        self.name = name or address
        self._ssh: paramiko.SSHClient | None = None

    # ---------- construction ----------

    @classmethod
    def from_name(cls, name: str, timeout: float = 3.0) -> "Device":
        d = resolve_name(name, timeout=timeout)
        if d is None:
            raise RuntimeError(f"devkit service {name!r} not found via mDNS")
        return cls(d.address, login=d.login, http_port=d.port, name=d.name)

    # ---------- devkit HTTP service (pre-auth pairing) ----------

    def _http(self, path: str, data: bytes | None = None,
              headers: dict | None = None, timeout: float = REQUEST_TIMEOUT) -> bytes:
        url = f"http://{self.address}:{self.http_port}{path}"
        req = urllib.request.Request(url, data=data, headers=headers or {},
                                     method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def properties(self) -> dict:
        """GET /properties.json - login user, entry point, device settings."""
        props = json.loads(self._http("/properties.json"))
        if self.login is None:
            self.login = props.get("login")
        return props

    def register(self) -> str:
        """Register our public key with the device (POST /register).

        The device may show an approval prompt (Steam client dialog) depending
        on its settings; the request blocks up to 30s for that.
        """
        key, _, _ = keys.ensure_devkit_key()
        payload = keys.public_key_line(key).strip("\n") + " " + keys.MAGIC_PHRASE + "\n"
        out = self._http(
            "/register",
            data=payload.encode("ascii"),
            headers={"Content-Type": "text/plain"},
            timeout=30,
        )
        return out.decode("utf-8", "strict").strip()

    # ---------- SSH ----------

    @property
    def ssh(self) -> paramiko.SSHClient:
        if self._ssh is not None:
            transport = self._ssh.get_transport()
            if transport is not None and transport.is_active():
                return self._ssh
        if self.login is None:
            self.properties()
        if self.login is None:
            raise RuntimeError("device login user unknown (properties.json unreachable?)")
        key, _, _ = keys.ensure_devkit_key()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(self.address, username=self.login, pkey=key,
                       timeout=REQUEST_TIMEOUT, look_for_keys=False)
        self._ssh = client
        return client

    def close(self) -> None:
        if self._ssh is not None:
            self._ssh.close()
            self._ssh = None

    def run(self, cmd: str, check: bool = False, stdin_data: str | None = None
            ) -> tuple[str, str, int]:
        """Run a remote command, return (stdout, stderr, exit_status)."""
        logger.debug("run: %s", cmd)
        stdin, stdout, stderr = self.ssh.exec_command(cmd)
        if stdin_data is not None:
            stdin.write(stdin_data)
            stdin.flush()
            stdin.channel.shutdown_write()
        status = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        if check and status != 0:
            raise subprocess.CalledProcessError(status, cmd, out, err)
        return out, err, status

    def run_json(self, cmd: str) -> dict | list:
        out, err, status = self.run(cmd, check=True)
        try:
            return json.loads(out)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"{cmd}: output is not json: {out!r} (stderr: {err!r})") from e

    def stream(self, cmd: str, on_line: Callable[[str], None] | None = None,
               get_pty: bool = False) -> Iterator[str]:
        """Run a remote command and yield stdout lines as they arrive.

        Suited for ``journalctl -f``, log tails, or long-running processes.
        stderr is merged into the stream when a pty is requested, otherwise
        interleaved on a best-effort basis.
        """
        transport = self.ssh.get_transport()
        chan = transport.open_session()
        if get_pty:
            chan.get_pty()
        chan.set_combine_stderr(True)
        chan.exec_command(cmd)
        f = chan.makefile("r")
        for line in f:
            line = line.rstrip("\r\n")
            if on_line:
                on_line(line)
            yield line
        chan.close()

    def sftp(self) -> paramiko.SFTPClient:
        return self.ssh.open_sftp()

    # ---------- rsync transfer ----------

    def _cygwin_path(self, cygpath_exe: str, path: str) -> str:
        out = subprocess.check_output([cygpath_exe, path], text=True)
        return out.splitlines()[0]

    def rsync(self, localdir: str, remotedir: str | list[str], upload: bool = True,
              delete_extraneous: bool = False, skip_newer_files: bool = False,
              verify_checksums: bool = False, extra_args: list[str] | None = None,
              on_line: Callable[[str], None] | None = None) -> None:
        """Directory sync in either direction, mirroring the official client.

        ``remotedir`` may be a list of remote source folders when downloading.
        """
        if self.login is None:
            self.properties()
        cygpath, ssh_exe, rsync_exe = _locate_transfer_tools()
        if upload and not os.path.isdir(localdir):
            raise RuntimeError(f"source directory does not exist: {localdir}")
        os.makedirs(localdir, exist_ok=True)
        if cygpath:
            localdir_t = self._cygwin_path(cygpath, os.path.abspath(localdir))
            keypath_t = self._cygwin_path(cygpath, keys.key_path())
        else:
            localdir_t = os.path.abspath(localdir)
            keypath_t = keys.key_path()
        rsh = (f"{shlex.quote(ssh_exe)} -o StrictHostKeyChecking=no "
               f"-o UserKnownHostsFile=/dev/null -i {shlex.quote(keypath_t)}")
        cmd = [rsync_exe, "-av", "-z",
               "--chmod=Du=rwx,Dgo=rx,Fu=rwx,Fog=rx", "-e", rsh]
        if delete_extraneous:
            cmd += ["--delete", "--delete-excluded", "--delete-delay"]
        if skip_newer_files:
            cmd += ["--update"]
        if verify_checksums:
            cmd += ["--checksum"]
        cmd += extra_args or []
        if isinstance(remotedir, list):
            if upload:
                raise RuntimeError("multiple remote paths only supported for download")
            cmd += [f"{self.login}@{self.address}:{r.rstrip('/')}" for r in remotedir]
            cmd.append(localdir_t.rstrip("/") + "/")
        else:
            pair = [localdir_t.rstrip("/") + "/",
                    f"{self.login}@{self.address}:{remotedir.rstrip('/')}/"]
            if not upload:
                pair.reverse()
            cmd += pair
        logger.info("rsync: %s", " ".join(cmd))
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=creationflags)
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if on_line:
                on_line(line)
            else:
                logger.info(line)
        if proc.wait() != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd)

    # ---------- device-side utility scripts ----------

    def sync_utils(self, utils_dir: str | None = None) -> None:
        """Push the devkit-utils scripts to ~/devkit-utils on the device.

        By default reuses the official client's bundled devkit-utils.
        """
        if utils_dir is None:
            utils_dir = os.path.normpath(os.path.join(OFFICIAL_CYGROOT, "..", "..", "devkit-utils"))
        if not os.path.isdir(utils_dir):
            raise RuntimeError(f"devkit-utils not found at {utils_dir}")
        home, _, _ = self.run("realpath ~", check=True)
        self.rsync(utils_dir, home.strip() + "/devkit-utils")

    def status(self) -> SteamOSStatus:
        data = self.run_json("python3 ~/devkit-utils/steamos-get-status --json")
        assert isinstance(data, dict)
        return SteamOSStatus(raw=data)

    def list_games(self) -> list[dict]:
        data = self.run_json("python3 ~/devkit-utils/steamos-list-games")
        assert isinstance(data, list)
        return data

    def rpc(self, command: str, **params: str) -> tuple[str, str, int]:
        """Generic Steam client RPC via the devkit pipe (steam-devkit-rpc).

        Known commands: run-game (gameid=...), create-shortcut, list-shortcuts,
        delete-shortcut, ...
        """
        args = " ".join(shlex.quote(f"{k}={v}") for k, v in params.items())
        return self.run(f"python3 ~/devkit-utils/steam-devkit-rpc {shlex.quote(command)} {args}")

    def run_game(self, gameid: str) -> None:
        self.run(f"python3 ~/devkit-utils/steam-devkit-rpc run-game gameid={shlex.quote(gameid)}",
                 check=True)

    def delete_title(self, gameid: str | None = None, delete_all: bool = False,
                     reset_steam_client: bool = False) -> str:
        cmd = ["python3", "~/devkit-utils/steamos-delete"]
        if gameid:
            cmd += ["--delete-title", shlex.quote(gameid)]
        if delete_all:
            cmd.append("--delete-all-titles")
        if reset_steam_client:
            cmd.append("--reset-steam-client")
        out, _, status = self.run(" ".join(cmd))
        if status != 0:
            raise RuntimeError(out)
        return out

    def screenshot(self, local_path: str, timeout: float = 10.0) -> str:
        """Trigger a gamescope screenshot and download it (Steam Deck)."""
        import time as _time
        self.run("rm -f /tmp/gamescope*.png")
        self.run("DISPLAY=:0 xprop -root -f GAMESCOPECTRL_DEBUG_REQUEST_SCREENSHOT 32c "
                 "-set GAMESCOPECTRL_DEBUG_REQUEST_SCREENSHOT 1", check=True)
        deadline = _time.time() + timeout
        remote = None
        while _time.time() < deadline:
            out, _, status = self.run(
                'find /tmp -maxdepth 1 -type f -name "gamescope*.png"')
            if status == 0 and out.strip():
                remote = out.strip().splitlines()[0]
                break
            _time.sleep(0.2)
        if remote is None:
            raise TimeoutError("screenshot did not appear on device")
        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
        with self.sftp() as sftp:
            sftp.get(remote, local_path)
        self.run(f"rm -f {shlex.quote(remote)}")
        return local_path
