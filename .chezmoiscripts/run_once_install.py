#!/usr/bin/env python3

def preimport(st: str):
    """
    [Dynamic import: How to import * from module name from variable?](https://stackoverflow.com/a/44492879/8566831)
    [Installing python module within code](https://stackoverflow.com/a/24773951/8566831)
    [How to install and import Python modules at runtime?](https://stackoverflow.com/a/45375636/8566831)
    [exec (builtImportString, sys._getframe(1).f_globals)](https://github.com/ryanniehaus/impstall/blob/e28207f30f4041cb6ad6596151cb90f7d8a4197c/impstall/core.py#L182)
    [Using pip from your program](https://pip.pypa.io/en/latest/user_guide/#using-pip-from-your-program)
    """
    import re
    import sys
    pat = re.compile(r'^\s*(from|import)\s+(\w+)')
    if not pat.match(st):
        st = f'import {st}'
    try:
        exec(st, sys._getframe(1).f_globals)
    except ModuleNotFoundError:
        import subprocess
        m = pat.search(st)
        package = m.group(2)
        if not package:
            raise Exception(f'not found package for statement: {st}')
        subprocess.check_call(
            f'{sys.executable} -m pip install {package}', shell=True)
        exec(st, sys._getframe(1).f_globals)


import gzip
import logging as log
import os
import platform
import re
import shutil
import sys
import tempfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from shutil import which
from subprocess import check_call
from typing import List
from urllib.parse import urljoin

preimport('psutil')
import psutil
import requests


def run_fn(cmd: str, shell=True, stderr=None, **kwargs):
    log.debug(f"running command `{cmd}`")
    return proc.check_output(cmd, shell=shell, stderr=stderr, **kwargs)


def run_cmd(cmd: str, shell=True, ex_env=None, **kwargs):
    log.debug(f"running command `{cmd}`")
    env = os.environ.copy()
    if ex_env:
        env.update(ex_env)
    proc.check_call(cmd, shell=shell, env=env, **kwargs)


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


# pip_install("psutil")
def check_sys_deps(names: List[str]):
    for name in names:
        if not which(name):
            raise Exception(f"not found dependent command: {name}")


class InstallException(Exception):
    pass


if __name__ == "__main__":
    log.basicConfig(
        level=log.DEBUG,
        format="%(asctime)s [%(levelname)8s] %(message)s (%(filename)s:%(lineno)s)",
        datefmt="%Y-%m-%d %H:%M:%S")
    print("installing for test")
    # check_sys_deps(["git", "python3", "curl", "xz"])


def apt_install(packages, updated=True):
    cmd = 'sudo apt-get update && ' if updated else '' + \
        f'sudo DEBIAN_FRONTEND=noninteractive apt-get -yq install {packages}'
    check_call(cmd, shell=True)


def setup_source(mirrors=[
    'http://mirrors.ustc.edu.cn'
    'http://mirrors.163.com'
    'http://mirrors.aliyun.com'
    'https://mirrors.tuna.tsinghua.edu.cn'
    'https://mirrors.ustc.edu.cn'
], source_path='/etc/apt/sources.list'):
    mirror: str = mirrors[0]
    # pat = re.compile(rf'^([^#].*)(https?://(.+.ubuntu.com|.+.debian.org)|{mirror})', re.MULTILINE)
    pat = re.compile(
        rf'^([^#].*)((https?://(.+\.ubuntu\.com|.+\.debian\.org))|({mirror}))', re.MULTILINE)
    text = Path(source_path).read_text()
    if not pat.search(text):
        log.info(f'skipped setup apt source for non-original source')
        return
    if mirror.startswith('https'):
        apt_install('apt-transport-https ca-certificates')

    now = datetime.now().strftime('%Y%m%d%H:%M:%S')
    backup_path = f'{source_path}.backup.{now}'
    log.info(f'backing up old source list {source_path} to {backup_path}')
    check_call(f'sudo cp -p {source_path} {backup_path}', shell=True)

    text = re.sub(r'^([^#]*)http[s]?://[^/\.]+(\.[^/\.]+)+', text)
    i = sys.stdin
    i.write(text)
    i.flush()
    # TODO: 输出到pipe再使用sudo tee写入
    check_call(f'cat /dev/stdin', shell=True)
    pass
