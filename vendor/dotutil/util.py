import hashlib
import json
import logging
import os
import re
from pathlib import Path
from subprocess import PIPE, CalledProcessError, check_call, check_output, run
from typing import Dict
from urllib.request import urlopen


class SetupExcetion(Exception):
    pass


def get_digest(path: Path) -> str:
    h = hashlib.sha256()
    buf = memoryview(bytearray(128 * 1024))
    try:
        with open(path, "rb", buffering=0) as f:
            while n := f.readinto(buf):
                h.update(buf[:n])
    except PermissionError as e:
        logging.info(
            f"try using sudo to read file {path} without read permission")
        try:
            s = check_output(
                f'sudo --non-interactive cat {path}'.split(), stderr=PIPE)
        except CalledProcessError as e1:
            logging.warning(
                f'failed to read file {path} using {e1.cmd}: {e1.stderr.decode().strip()}. Please enter password with sudo in advance')
            raise e
        h.update(s)
    return h.hexdigest()


def has_changed(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    if not src.is_file():
        raise SetupExcetion(f"{src} is not a file")
    s = src.stat()
    d = dst.stat()
    return s.st_mode != d.st_mode or get_digest(src) != get_digest(dst)


def config_log(level=logging.CRITICAL, stream=None):
    # logging.basicConfig(format='{asctime}.{msecs:<10f} [{levelname:4}] [{pathname}:{name}.{funcName}]: {message}',
    # style='{',
    # [Python logging.Formatter(): is there any way to fix the width of a field and justify it left/right?](https://stackoverflow.com/questions/20618570/python-logging-formatter-is-there-any-way-to-fix-the-width-of-a-field-and-jus)
    # TODO: fixed width for long pathname
    logging.basicConfig(format='%(asctime)s.%(msecs)03d [%(levelname)-5s] [%(name)s.%(funcName)s]: %(message)s',
                        level=level,
                        stream=stream,
                        datefmt='%Y-%m-%d %H:%M:%S')


def chezmoi_data(cz_path='chezmoi'):
    return json.loads(check_output(f'{cz_path} data --format json'.split(), text=True))


def is_windows():
    return os.name == 'nt'


def elevate_copy_file(src: Path, dst: Path):
    # [How do I check if I'm running on Windows in Python? [duplicate]](https://stackoverflow.com/a/1325587/8566831)
    if is_windows():
        # [How to preserve file attributes when one copies files in Windows?](https://superuser.com/a/1326224)
        cmd = ['gsudo', 'robocopy', str(src.parent), str(
            dst.parent), str(src.name), '/COPY:DT', '/R:0']
    else:
        cmd = f'sudo cp --preserve=links,mode,timestamps --no-dereference {src} {dst}'.split(
        )
        if not dst.parent.exists():
            check_call(f"sudo mkdir -p {dst.parent}".split())

    logging.info(
        f'copying file {src} -> {dst}')
    res = run(cmd, stdout=PIPE)
    logging.debug(
        f'`{" ".join(cmd)}` output: {res.stdout.decode(errors="ignore")}')
    if not is_windows() or res.returncode not in range(0, 8):
        # https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/robocopy#exit-return-codes
        res.check_returncode()


def download_file(url, file):
    CHUNK = 10 * 1024
    logging.info(f'downloading to {file.name} from {url}')
    response = urlopen(url)
    while chunk := response.read(CHUNK):
        file.write(chunk)


def dyn_import(st: str):
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


class ChezmoiArgs:
    def __init__(self, s=os.environ['CHEZMOI_ARGS']) -> None:
        if not s:
            raise SetupExcetion('empty args')
        if m := re.compile(r'^(.*?chezmoi(\.exe)?)\s+(--?\w+(\s+)?)*((\w+(-\w+)?)\s+?(--?\w+(\s+)?)*)?(.*)$').match(s):
            self._global_opts = m.group(3)
            self._subcommand = m.group(6)
            self._sub_opts = m.group(8)
            self._target_paths = [Path(s) for s in m.group(
                10).split()] if m.group(10) else []
        else:
            raise SetupExcetion(f'failed to parse chezmoi args: {s}')

        self._is_verbose = None
        self._is_debug = None
        self._data = None

    def has_debug(self) -> bool:
        if self._is_debug is None:
            self._is_debug = self._global_opts and '--debug' in self._global_opts
        return self._is_debug

    def has_verbose(self) -> bool:
        if self._is_verbose is None:
            if not self._sub_opts:
                self._is_verbose = False
            opts = self._sub_opts.split()
            if any(v in opts for v in ['-v', '--verbose']):
                self._is_verbose = True
            pat = re.compile(r'^-\w*v')
            self._is_verbose = any(pat.match(v) for v in opts)
        return self._is_verbose

    def subcommand(self) -> str:
        return self._subcommand

    def target_paths(self) -> list[Path]:
        return self._target_paths

    def mapped_root(self) -> Path:
        if v := os.environ['CHEZMOI_HOME_DIR']:
            return Path(v).joinpath('.root')
        else:
            raise SetupExcetion('not found env CHEZMOI_HOME_DIR')

    def root_list(self) -> Path:
        if v := os.environ['CHEZMOI_CACHE_DIR']:
            return Path(v).joinpath('.root')
        else:
            raise SetupExcetion('not found env CHEZMOI_CACHE_DIR')

    def bin_path(self) -> Path:
        if v := os.environ['CHEZMOI_EXECUTABLE']:
            return Path(v)
        else:
            raise SetupExcetion('not found env CHEZMOI_EXECUTABLE')

    def data(self) -> Dict[str, str]:
        if self._data is None:
            self._data = json.loads(check_output([self.bin_path(), 'data', '--format', 'json'], text=True))
        return self._data
