#!/usr/bin/env python3

import logging as log
import os
import subprocess as proc
import sys
from typing import List


def run_fn(cmd: str, shell=True, stderr=None, **kwargs):
    log.debug(f"running command `{cmd}`")
    return proc.check_output(cmd, shell=shell, stderr=stderr, **kwargs)

def run_cmd(cmd: str, shell=True, ex_env=None, **kwargs):
    log.debug(f"running command `{cmd}`")
    env=os.environ.copy()
    if ex_env:
        env.update(ex_env)
    proc.check_call(cmd, shell=shell, env=env, **kwargs)

def import_or_install(package: str):
    """
    [Installing python module within code](https://stackoverflow.com/a/24773951/8566831)
    [How to install and import Python modules at runtime?](https://stackoverflow.com/a/45375636/8566831)
    [exec (builtImportString, sys._getframe(1).f_globals)](https://github.com/ryanniehaus/impstall/blob/e28207f30f4041cb6ad6596151cb90f7d8a4197c/impstall/core.py#L182)
    [Using pip from your program](https://pip.pypa.io/en/latest/user_guide/#using-pip-from-your-program)
    """
    import importlib
    try:
        importlib.import_module(package)
    except ImportError:
        run_cmd(f'{sys.executable} -m pip install {package}', shell=True)
    finally:
        exec(f"import {package}", sys._getframe(1).f_globals)


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

if __name__ == "__main__":
    log.basicConfig(
        level=log.DEBUG,
        format="%(asctime)s [%(levelname)8s] %(message)s (%(filename)s:%(lineno)s)",
        datefmt="%Y-%m-%d %H:%M:%S")
    print("installing for test")
    # check_sys_deps(["git", "python3", "curl", "xz"])

