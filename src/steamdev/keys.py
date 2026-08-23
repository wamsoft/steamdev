"""Devkit SSH key management.

The official SteamOS Devkit Client stores a passwordless RSA key under
appdirs.user_config_dir('steamos-devkit'):

  Windows: %LOCALAPPDATA%/steamos-devkit/steamos-devkit/devkit_rsa
  Linux:   ~/.config/steamos-devkit/devkit_rsa

We reuse the exact same location so that a device already paired with the
official client works immediately, and a key we generate also works from the
official client.
"""

from __future__ import annotations

import getpass
import os
import platform
import socket
import subprocess
import logging

import paramiko

logger = logging.getLogger(__name__)

# Shared secret appended to the public key on POST /register.
# Taken from the official client (devkit_client/__init__.py); the device-side
# service checks for it before accepting a key.
MAGIC_PHRASE = "900b919520e4cf601998a71eec318fec"


def key_dir() -> str:
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        # appdirs user_config_dir('steamos-devkit') doubles the name (appauthor/appname)
        return os.path.join(base, "steamos-devkit", "steamos-devkit")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "steamos-devkit")


def key_path() -> str:
    return os.path.join(key_dir(), "devkit_rsa")


def public_key_comment() -> str:
    return " devkit-client:{}@{}".format(getpass.getuser(), socket.gethostname())


def public_key_line(key: paramiko.PKey) -> str:
    return "ssh-rsa " + key.get_base64() + public_key_comment() + "\n"


def _fix_permissions(paths: list[str]) -> None:
    if platform.system() != "Windows":
        for p in paths:
            os.chmod(p, 0o400)
        return
    # Restrict ACLs so OpenSSH accepts the private key file
    username = subprocess.check_output("whoami", text=True).strip()
    for p in paths:
        for cmd in (
            ["icacls.exe", p, "/Reset"],
            ["icacls.exe", p, "/Inheritance:r"],
            ["icacls.exe", p, "/Grant:r", f"{username}:(R)"],
        ):
            subprocess.run(cmd, capture_output=True)


def ensure_devkit_key() -> tuple[paramiko.PKey, str, str]:
    """Load the devkit key, generating one on first use.

    Returns (key, private_key_path, public_key_path).
    """
    kp = key_path()
    pub = kp + ".pub"
    try:
        key = paramiko.RSAKey.from_private_key_file(kp)
        return key, kp, pub
    except FileNotFoundError:
        pass
    logger.info("generating new devkit key at %s", kp)
    os.makedirs(key_dir(), exist_ok=True)
    key = paramiko.RSAKey.generate(2048)
    key.write_private_key_file(kp)
    with open(pub, "w") as f:
        f.write(public_key_line(key))
    _fix_permissions([kp, pub])
    return key, kp, pub
