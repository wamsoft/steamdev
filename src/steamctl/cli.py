"""steamctl command line interface."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shlex
import subprocess
import sys

from . import __version__
from .deploy import DeploySpec, deploy
from .device import Device, _locate_transfer_tools
from .discovery import discover
from . import keys
from .tunnel import LocalForward, RemoteForward

logger = logging.getLogger("steamctl")

GAMEID_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]+$")


def get_device(args: argparse.Namespace) -> Device:
    target = args.device or os.environ.get("STEAMCTL_DEVICE")
    if not target:
        raise SystemExit("no device given: pass -d/--device or set STEAMCTL_DEVICE")
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", target) or "." in target:
        return Device(target)
    return Device.from_name(target)


def cmd_discover(args) -> None:
    devices = discover(timeout=args.timeout)
    print(json.dumps([d.__dict__ for d in devices], indent=2, ensure_ascii=False))


def cmd_register(args) -> None:
    dev = get_device(args)
    print(dev.register())


def cmd_info(args) -> None:
    dev = get_device(args)
    props = dev.properties()
    # settings arrives as a nested JSON string
    if isinstance(props.get("settings"), str):
        try:
            props["settings"] = json.JSONDecoder(strict=False).decode(props["settings"])
        except json.JSONDecodeError:
            pass
    props["address"] = dev.address
    print(json.dumps(props, indent=2, ensure_ascii=False))


def cmd_status(args) -> None:
    dev = get_device(args)
    st = dev.status()
    st.raw["address"] = dev.address
    print(json.dumps(st.raw, indent=2, ensure_ascii=False))


def cmd_sync_utils(args) -> None:
    dev = get_device(args)
    dev.sync_utils(args.utils_dir)
    print("devkit-utils synced")


def cmd_list(args) -> None:
    dev = get_device(args)
    print(json.dumps(dev.list_games(), indent=2))


def cmd_deploy(args) -> None:
    if not GAMEID_PATTERN.fullmatch(args.gameid):
        raise SystemExit(f"invalid gameid {args.gameid!r} (must match {GAMEID_PATTERN.pattern})")
    dev = get_device(args)
    if args.sync_utils:
        dev.sync_utils()
    env = dict(kv.split("=", 1) for kv in (args.env or []))
    settings: dict = {}
    for kv in args.set or []:
        k, v = kv.split("=", 1)
        settings[k] = v
    if args.runtime:
        settings["compat_tool"] = args.runtime
        if args.runtime.startswith("proton"):
            settings["steam_play"] = "1"
    filter_args = shlex.split(args.filter) if args.filter else []
    spec = DeploySpec(
        gameid=args.gameid,
        local_dir=args.dir,
        argv=[args.command],
        env=env,
        settings=settings,
        force_appid=args.appid or "",
        start_after=args.start,
        delete_extraneous=args.clean,
        skip_newer_files=args.skip_newer,
        filter_args=filter_args,
    )
    out = deploy(dev, spec, on_line=lambda line: print(line))
    print(json.dumps(out, indent=2))


def cmd_run(args) -> None:
    dev = get_device(args)
    dev.run_game(args.gameid)
    print(f"launched {args.gameid}")


def cmd_delete(args) -> None:
    dev = get_device(args)
    print(dev.delete_title(gameid=args.gameid, delete_all=args.all,
                           reset_steam_client=args.reset_steam_client))


def cmd_exec(args) -> None:
    dev = get_device(args)
    cmd = " ".join(args.cmd) if len(args.cmd) > 1 else args.cmd[0]
    if args.stream:
        for line in dev.stream(cmd):
            print(line, flush=True)
        return
    out, err, status = dev.run(cmd)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    raise SystemExit(status)


def cmd_shell(args) -> None:
    dev = get_device(args)
    dev.properties()
    _, ssh_exe, _ = _locate_transfer_tools()
    cmd = [ssh_exe, "-o", "StrictHostKeyChecking=no",
           "-o", "UserKnownHostsFile=/dev/null", "-o", "IdentitiesOnly=yes",
           "-t", "-i", keys.key_path(), f"{dev.login}@{dev.address}"]
    if args.cmd:
        cmd += args.cmd
    raise SystemExit(subprocess.call(cmd))


def cmd_tunnel(args) -> None:
    dev = get_device(args)
    transport = dev.ssh.get_transport()
    assert transport is not None
    forwards = []
    for spec in args.local or []:
        parts = spec.split(":")
        if len(parts) == 2:
            lport, rhost, rport = int(parts[0]), "127.0.0.1", int(parts[1])
        elif len(parts) == 3:
            lport, rhost, rport = int(parts[0]), parts[1], int(parts[2])
        else:
            raise SystemExit(f"bad -L spec: {spec} (use LPORT:RPORT or LPORT:RHOST:RPORT)")
        forwards.append(LocalForward(transport, lport, rhost, rport).start())
    for spec in args.remote or []:
        parts = spec.split(":")
        if len(parts) == 2:
            rport, lhost, lport = int(parts[0]), "127.0.0.1", int(parts[1])
        elif len(parts) == 3:
            rport, lhost, lport = int(parts[0]), parts[1], int(parts[2])
        else:
            raise SystemExit(f"bad -R spec: {spec} (use RPORT:LPORT or RPORT:LHOST:LPORT)")
        forwards.append(RemoteForward(transport, rport, lhost, lport).start())
    if not forwards:
        raise SystemExit("no -L/-R forwards given")
    print("tunnels up; Ctrl+C to stop", flush=True)
    try:
        import time
        while transport.is_active():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for f in forwards:
            f.stop()


def cmd_logs(args) -> None:
    dev = get_device(args)
    dev.properties()
    login = dev.login
    out_dir = os.path.join(args.out, dev.name)
    os.makedirs(out_dir, exist_ok=True)
    dev.rsync(out_dir,
              [f"/home/{login}/.local/share/Steam/logs", "/tmp/dumps"],
              upload=False, on_line=lambda line: print(line))
    proton_dir = os.path.join(out_dir, "proton")
    os.makedirs(proton_dir, exist_ok=True)
    dev.rsync(proton_dir, f"/home/{login}", upload=False,
              extra_args=["--include=steam-*.log", "--exclude=*"],
              on_line=lambda line: print(line))
    print(f"logs synced to {out_dir}")


def cmd_screenshot(args) -> None:
    dev = get_device(args)
    path = dev.screenshot(args.out)
    print(path)


def cmd_ssh_command(args) -> None:
    # print the raw ssh command line for use by other tools (IDEs, scripts)
    dev = get_device(args)
    dev.properties()
    _, ssh_exe, _ = _locate_transfer_tools()
    print(subprocess.list2cmdline([
        ssh_exe, "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null", "-o", "IdentitiesOnly=yes",
        "-i", keys.key_path(), f"{dev.login}@{dev.address}"]))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="steamctl",
                                     description="Headless SteamOS devkit control")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--device", metavar="ADDR|NAME",
                        help="device IP address or mDNS name (or $STEAMCTL_DEVICE)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("discover", help="browse LAN for devkit devices")
    p.add_argument("--timeout", type=float, default=3.0)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("register", help="pair our ssh key with the device")
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("info", help="GET /properties.json")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("status", help="run steamos-get-status on the device")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("sync-utils", help="push devkit-utils scripts to the device")
    p.add_argument("--utils-dir", default=None)
    p.set_defaults(func=cmd_sync_utils)

    p = sub.add_parser("list", help="list installed devkit titles")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("deploy", help="upload a build and register it as a devkit title")
    p.add_argument("--gameid", required=True)
    p.add_argument("--dir", required=True, help="local build folder")
    p.add_argument("--command", required=True,
                   help="launch command line, relative to the uploaded folder")
    p.add_argument("--env", action="append", metavar="K=V")
    p.add_argument("--set", action="append", metavar="K=V",
                   help="extra settings (steam_play=1, compat_tool=..., gdbserver=1, ...)")
    p.add_argument("--runtime", help="compat tool alias: proton-stable, proton-experimental, "
                                     "SteamLinuxRuntime_sniper, SteamLinuxRuntime_4, ...")
    p.add_argument("--appid", help="write steam_appid.txt with this appid")
    p.add_argument("--filter", help="raw rsync filter args, e.g. \"--exclude=*.pdb\"")
    p.add_argument("--clean", action="store_true", help="rsync --delete")
    p.add_argument("--skip-newer", action="store_true")
    p.add_argument("--start", action="store_true", help="launch after deploy")
    p.add_argument("--sync-utils", action="store_true",
                   help="push devkit-utils before deploying")
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser("run", help="launch an installed devkit title")
    p.add_argument("gameid")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("delete", help="delete devkit title(s)")
    p.add_argument("gameid", nargs="?")
    p.add_argument("--all", action="store_true")
    p.add_argument("--reset-steam-client", action="store_true")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("exec", help="run a command on the device")
    p.add_argument("--stream", action="store_true", help="stream output line by line")
    p.add_argument("cmd", nargs="+")
    p.set_defaults(func=cmd_exec)

    p = sub.add_parser("shell", help="interactive ssh shell in this terminal")
    p.add_argument("cmd", nargs="*")
    p.set_defaults(func=cmd_shell)

    p = sub.add_parser("tunnel", help="ssh port forwarding (app REPL/socket services)")
    p.add_argument("-L", "--local", action="append", metavar="LPORT[:RHOST]:RPORT",
                   help="forward local port to device port")
    p.add_argument("-R", "--remote", action="append", metavar="RPORT[:LHOST]:LPORT",
                   help="forward device port back to host port")
    p.set_defaults(func=cmd_tunnel)

    p = sub.add_parser("logs", help="download Steam/Proton logs and crash dumps")
    p.add_argument("--out", default="./devkit-logs")
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("screenshot", help="take a gamescope screenshot")
    p.add_argument("-o", "--out", default="screenshot.png")
    p.set_defaults(func=cmd_screenshot)

    p = sub.add_parser("ssh-command", help="print the ssh command line for this device")
    p.set_defaults(func=cmd_ssh_command)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    args.func(args)


if __name__ == "__main__":
    main()
