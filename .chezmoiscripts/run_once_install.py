#!/usr/bin/env python3

import gzip
import logging as log
import os
import platform
import re
import subprocess as proc
import sys
from io import BytesIO
from shutil import which
from typing import List


def run_fn(cmd: str, shell=True, stderr=None, **kwargs):
    log.debug(f"running command `{cmd}`")
    return proc.check_output(cmd, shell=shell, stderr=stderr, **kwargs)


def run_cmd(cmd: str, shell=True, ex_env=None, **kwargs):
    log.debug(f"running command `{cmd}`")
    env = os.environ.copy()
    if ex_env:
        env.update(ex_env)
    proc.check_call(cmd, shell=shell, env=env, **kwargs)


def install_import(package: str):
    """
    [Installing python module within code](https://stackoverflow.com/a/24773951/8566831)
    [How to install and import Python modules at runtime?](https://stackoverflow.com/a/45375636/8566831)
    [exec (builtImportString, sys._getframe(1).f_globals)](https://github.com/ryanniehaus/impstall/blob/e28207f30f4041cb6ad6596151cb90f7d8a4197c/impstall/core.py#L182)
    [Using pip from your program](https://pip.pypa.io/en/latest/user_guide/#using-pip-from-your-program)
    """
    run_cmd(f'{sys.executable} -m pip install {package}', shell=True)
    exec(f"import {package}", sys._getframe(1).f_globals)


try:
    import requests
except ImportError:
    install_import("requests")


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


# pip_install("psutil")
def check_sys_deps(names: List[str]):
    from shutil import which
    for name in names:
        if not which(name):
            raise Exception(f"not found dependent command: {name}")


def install():
    run_cmd("curl -L https://nixos.org/nix/install | sh -s -- --no-daemon")


def install_clash(bin_path=None):
    """
    curl -sSL -H "Accept: application/json" https://api.github.com/repos/Dreamacro/clash/releases/tags/premium | grep browser_download_url | cut -d '"' -f 4 | grep linux-amd64-2022
    """
    resp = requests.get("https://api.github.com/repos/Dreamacro/clash/releases/tags/premium",
                        headers={"Accept": "application/json"})
    resp.raise_for_status()
    tag_data = resp.json()
    log.debug(f"fetched clash tag data: {tag_data}")

    re_ver = re.compile("\d+\.\d+\.\d+")
    latest_ver = re_ver.search(tag_data["name"]).group()
    if cur_ver := re_ver.search(run_fn(f"{bin_path} -v" if bin_path else "clash -v")).group():
        if cur_ver >= latest_ver:
            log.info(f"clash up to date: {cur_ver}")
            return

    log.info(f"found update available: {cur_ver} -> {latest_ver}")
    if arch := {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]:
        raise Exception("unsupported system: " + platform.machine())
    os = platform.system().lower()
    url = f"https://github.com/Dreamacro/clash/releases/download/premium/clash-{os}-{arch}-{latest_ver}.gz"
    path = f"/tmp/clash-{latest_ver}"
    # with requests.get(url, stream=True) as resp:
    #     resp.raise_for_status()

if __name__ == "__main__":
    log.basicConfig(
        level=log.DEBUG,
        format="%(asctime)s [%(levelname)8s] %(message)s (%(filename)s:%(lineno)s)",
        datefmt="%Y-%m-%d %H:%M:%S")
    print("installing for test")
    # check_sys_deps(["git", "python3", "curl", "xz"])
