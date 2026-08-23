"""Title deployment: upload a local build and register it with the Steam client.

Mirrors the official client's "Update Title" flow:

1. ``steamos-prepare-upload --gameid X`` on the device -> target dir + user
2. rsync the local build into ``~/devkit-game/X``
3. ``steam-client-create-shortcut --parms <json>`` on the device -> the Steam
   client registers/updates the shortcut ("Devkit Game").
"""

from __future__ import annotations

import json
import logging
import shlex
from dataclasses import dataclass, field
from typing import Callable

from .device import Device

logger = logging.getLogger(__name__)

# Reserved gameids used to sideload a whole Steam client build
SIDELOAD_GAMEIDS = ["steam", "steamdeckard", "steamvr", "steamvrdeckard"]


@dataclass
class DeploySpec:
    gameid: str                       # devkit shortcut name; [A-Za-z0-9_-]
    local_dir: str                    # build folder to upload
    argv: list[str] = field(default_factory=list)   # launch command relative to the upload, e.g. ['mygame.sh']
    env: dict[str, str] = field(default_factory=dict)
    settings: dict = field(default_factory=dict)    # e.g. {'steam_play': '1', 'steam_play_version': 'proton-experimental'}
    force_appid: str = ""             # write steam_appid.txt with this appid
    start_after: bool = False         # launch right after deploy
    delete_extraneous: bool = False   # clean upload (rsync --delete)
    skip_newer_files: bool = False
    verify_checksums: bool = False
    filter_args: list[str] = field(default_factory=list)  # rsync --include/--exclude
    restart_steam: bool = False
    clear_settings: bool = False


def deploy(dev: Device, spec: DeploySpec,
           on_line: Callable[[str], None] | None = None) -> dict:
    """Run the full deploy flow; returns the create-shortcut response."""
    if not spec.argv:
        raise ValueError("DeploySpec.argv must name the executable to launch "
                         "(path relative to the uploaded folder)")

    prep_cmd = f"python3 ~/devkit-utils/steamos-prepare-upload --gameid {shlex.quote(spec.gameid)}"
    if spec.restart_steam:
        prep_cmd += " --restart-steam 1"
    prep = dev.run_json(prep_cmd)
    assert isinstance(prep, dict)
    user, destdir = prep["user"], prep["directory"]
    logger.info("uploading %s -> %s@%s:%s", spec.local_dir, user, dev.address, destdir)

    dev.rsync(
        spec.local_dir, destdir, upload=True,
        delete_extraneous=spec.delete_extraneous,
        skip_newer_files=spec.skip_newer_files,
        verify_checksums=spec.verify_checksums,
        extra_args=spec.filter_args,
        on_line=on_line,
    )

    parms = {
        "gameid": spec.gameid,
        "directory": destdir,
        "argv": spec.argv,
        "env": spec.env,
        "settings": dict(spec.settings),
        "force_appid": spec.force_appid,
    }
    if spec.clear_settings:
        parms["clear_settings"] = True

    out = dev.run_json(
        "python3 ~/devkit-utils/steam-client-create-shortcut --parms "
        + shlex.quote(json.dumps(parms))
    )
    assert isinstance(out, dict)
    if "error" in out:
        raise RuntimeError(f"create-shortcut failed: {out['error']}")
    logger.info("registered: %s", out.get("success"))

    if spec.start_after:
        dev.run_game(spec.gameid)
    return out
