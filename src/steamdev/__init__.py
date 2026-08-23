"""steamdev - headless control library for SteamOS devkit devices (Steam Deck).

Re-implements the control protocol of Valve's SteamOS Devkit Client
(MIT licensed) as a library + CLI, without the imgui GUI.

Quick start::

    from steamdev import Device, deploy, DeploySpec

    dev = Device("192.168.1.30")        # or Device.from_name("steamdeck")
    dev.register()                       # first time only (approve on device)
    dev.sync_utils()                     # push device-side helper scripts
    print(dev.status().raw)

    deploy(dev, DeploySpec(
        gameid="mygame",
        local_dir=r"D:/build/mygame",
        argv=["mygame.sh -console"],    # one string: full command line
        start_after=True,
    ))
"""

from .device import Device, SteamOSStatus
from .deploy import DeploySpec, deploy
from .discovery import DiscoveredDevice, discover
from .tunnel import LocalForward, RemoteForward

__all__ = [
    "Device", "SteamOSStatus", "DeploySpec", "deploy",
    "DiscoveredDevice", "discover", "LocalForward", "RemoteForward",
]

__version__ = "0.1.0"
