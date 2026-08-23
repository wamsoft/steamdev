"""Project-definition driven build/stage/deploy pipeline.

A project keeps a ``deckproject.toml`` at its root describing, per target
(``linux`` / ``windows`` / anything), how to build, how to compose the files
to send (staging), and how to launch on the Steam Deck::

    [project]
    gameid = "mygame"                  # base id; per-target id becomes mygame_<target>

    [targets.linux.build]
    kind = "deckbuild"                 # build inside the steamrt sniper container
    preset = "x64-linux"
    build_type = "Release"
    cmakeopt = "-DKRKRZ_USE_SJIS=YES"

    [targets.linux.stage]
    # krkrz_android app-config / krkrz_web web-config の assetPack.sources と同書式
    sources = [
        { type = "mirror", from = "bin/x64-linux/Release", to = "" },
        { type = "mirror", from = "src/core/data", to = "data",
          exclude = ["**/*.psd", "**/*.bak"] },
    ]
    script = "python tools/stage_extra.py"   # optional, runs with cwd=project dir

    [targets.linux.deploy]
    command = "./krkrz64 data"
    settings = { }                     # e.g. compat_tool = "SteamLinuxRuntime_sniper"
    env = { }

    [targets.windows.build]
    kind = "shell"
    command = "make PRESET=x64-windows prebuild build install"

    [targets.windows.deploy]
    command = "krkrz64.exe data"
    settings = { steam_play = "1", compat_tool = "proton-experimental" }

Build/stage scripts run with cwd = project dir and env vars
``STEAMCTL_PROJECT_DIR``, ``STEAMCTL_STAGE_DIR``, ``STEAMCTL_TARGET`` set.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from typing import Any

from .deploy import DeploySpec, deploy as do_deploy
from .device import Device

logger = logging.getLogger(__name__)

PROJECT_FILE = "deckproject.toml"
STEAMCTL_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


@dataclass
class Target:
    name: str
    build: dict = field(default_factory=dict)
    stage: dict = field(default_factory=dict)
    deploy: dict = field(default_factory=dict)


@dataclass
class Project:
    root: str
    gameid: str
    targets: dict[str, Target] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> "Project":
        """``path`` is the project dir or the toml file itself."""
        if os.path.isdir(path):
            path = os.path.join(path, PROJECT_FILE)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"project file not found: {path}")
        with open(path, "rb") as f:
            data = tomllib.load(f)
        root = os.path.dirname(os.path.abspath(path))
        proj = data.get("project", {})
        gameid = proj.get("gameid") or os.path.basename(root)
        targets = {}
        for name, tconf in data.get("targets", {}).items():
            targets[name] = Target(
                name=name,
                build=tconf.get("build", {}),
                stage=tconf.get("stage", {}),
                deploy=tconf.get("deploy", {}),
            )
        return cls(root=root, gameid=gameid, targets=targets)

    def target(self, name: str) -> Target:
        if name not in self.targets:
            raise KeyError(f"target {name!r} not defined (available: {list(self.targets)})")
        return self.targets[name]

    def stage_dir(self, target: str) -> str:
        return os.path.join(self.root, ".deckstage", target)

    def target_gameid(self, target: str) -> str:
        t = self.target(target)
        return t.deploy.get("gameid") or f"{self.gameid}_{target}"


def _run_env(project: Project, target: str) -> dict:
    env = dict(os.environ)
    env["STEAMCTL_PROJECT_DIR"] = project.root
    env["STEAMCTL_STAGE_DIR"] = project.stage_dir(target)
    env["STEAMCTL_TARGET"] = target
    # プロジェクトに .venv があればその python を優先させる
    # (build/stage script の `python` がプロジェクト環境で解決される)
    venv = os.path.join(project.root, ".venv")
    venv_bin = os.path.join(venv, "Scripts" if sys.platform == "win32" else "bin")
    if os.path.isdir(venv_bin):
        env["VIRTUAL_ENV"] = venv
        env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    return env


def _run_command(cmd: Any, project: Project, target: str) -> None:
    """Run a config-specified command (string -> shell, list -> argv)."""
    shell = isinstance(cmd, str)
    logger.info("run: %s", cmd)
    subprocess.run(cmd, shell=shell, cwd=project.root,
                   env=_run_env(project, target), check=True)


def _wslpath(path: str) -> str:
    out = subprocess.check_output(["wsl", "-e", "wslpath", "-a", path.replace("\\", "/")])
    return out.decode().replace("\x00", "").strip()


def build(project: Project, target: str) -> None:
    t = project.target(target)
    conf = t.build
    if not conf:
        logger.info("target %s has no build step, skipping", target)
        return
    kind = conf.get("kind", "shell")
    if kind == "deckbuild":
        script = os.path.join(STEAMCTL_DIR, "deckbuild", "deckbuild.sh")
        if sys.platform == "win32":
            cmd = ["wsl", "-e", "bash", "-c",
                   "PRESET={preset} BUILD_TYPE={bt} CMAKEOPT={opt} bash {script} -s {src} all".format(
                       preset=conf.get("preset", "x64-linux"),
                       bt=conf.get("build_type", "Release"),
                       opt="'" + conf.get("cmakeopt", "") + "'",
                       script="'" + _wslpath(script) + "'",
                       src="'" + _wslpath(project.root) + "'",
                   )]
        else:
            cmd = ["env", f"PRESET={conf.get('preset', 'x64-linux')}",
                   f"BUILD_TYPE={conf.get('build_type', 'Release')}",
                   f"CMAKEOPT={conf.get('cmakeopt', '')}",
                   "bash", script, "-s", project.root, "all"]
        logger.info("deckbuild: %s", cmd)
        subprocess.run(cmd, check=True)
    elif kind == "shell":
        command = conf.get("command")
        if not command:
            raise ValueError(f"target {target}: build.kind=shell requires build.command")
        _run_command(command, project, target)
    else:
        raise ValueError(f"target {target}: unknown build.kind {kind!r}")


def _stage_sources(t: Target) -> list:
    """stage 定義から sources リストを得る。

    推奨は krkrz_android app-config / krkrz_web web-config と同書式の ``sources``。
    旧 ``copy = [[src, dst], ...]`` は mirror エントリに変換して互換維持する。
    """
    sources = list(t.stage.get("sources", []))
    for entry in t.stage.get("copy", []):
        src_rel, dst_rel = entry
        if any(c in src_rel for c in "*?["):
            raise ValueError(
                f"stage copy の glob ({src_rel!r}) は廃止。sources 書式の "
                "include/exclude を使ってください")
        dst_rel = (dst_rel or "").strip("./\\")
        sources.append({"type": "mirror", "from": src_rel, "to": dst_rel})
    return sources


def stage(project: Project, target: str, clean: bool = False) -> str:
    from . import stage as stage_engine
    t = project.target(target)
    stage_dir = project.stage_dir(target)
    if clean and os.path.isdir(stage_dir):
        shutil.rmtree(stage_dir)
    os.makedirs(stage_dir, exist_ok=True)
    sources = _stage_sources(t)
    if sources:
        ctx = {"PROJECT_DIR": project.root}
        stats = stage_engine.run_copy_rules(sources, stage_dir, project.root, ctx)
        logger.info(
            "staged %d files into %s (linked %d / copied %d / unchanged %d / removed %d)",
            stats["total"], stage_dir, stats["linked"], stats["copied"],
            stats["unchanged"], stats["removed"])
    script = t.stage.get("script")
    if script:
        _run_command(script, project, target)
    if not os.listdir(stage_dir):
        raise RuntimeError(f"stage dir is empty: {stage_dir} (no sources / script output?)")
    return stage_dir


def deploy_target(project: Project, dev: Device, target: str,
                  start: bool = False, clean_upload: bool = False) -> None:
    t = project.target(target)
    conf = t.deploy
    command = conf.get("command")
    if not command:
        raise ValueError(f"target {target}: deploy.command is required")
    stage_dir = project.stage_dir(target)
    if not os.path.isdir(stage_dir):
        raise RuntimeError(f"stage dir missing - run stage first: {stage_dir}")
    settings = {k: str(v) for k, v in conf.get("settings", {}).items()}
    if settings.get("compat_tool", "").startswith("proton") and "steam_play" not in settings:
        settings["steam_play"] = "1"
    spec = DeploySpec(
        gameid=project.target_gameid(target),
        local_dir=stage_dir,
        argv=[command],
        env={k: str(v) for k, v in conf.get("env", {}).items()},
        settings=settings,
        force_appid=str(conf.get("appid", "")),
        start_after=start,
        delete_extraneous=clean_upload or bool(conf.get("clean_upload", False)),
        filter_args=list(conf.get("filter", [])),
    )
    do_deploy(dev, spec, on_line=lambda line: logger.debug("%s", line))
    logger.info("deployed %s as %s", target, spec.gameid)
