import hashlib
import json
import logging
import os
import re
import sys
import textwrap
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from subprocess import (PIPE, CalledProcessError, Popen, check_call,
                        check_output, run)
from typing import IO, Dict, Set, Union
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
        logging.debug(
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


def has_changed_su(src: Path, dst: Path) -> bool:
    def get_mode(path):
        return check_output(['sudo', 'stat', '--format', '%a', path], text=True)
    smode = get_mode(src)
    dmode = get_mode(dst)
    return smode != dmode or get_digest(src) != get_digest(dst)


def has_changed(src: Path, dst: Path) -> bool:
    if not src.exists():
        raise SetupExcetion(f"{src} is not exists")
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


def config_log_cz(level=logging.DEBUG):
    '''
    优先从cz读取log配置，如果未找到则为level
    '''
    try:
        ChezmoiArgs().init_log()
        return
    except Exception:
        pass

    config_log(level=level)


def chezmoi_data(cz_path='chezmoi'):
    return json.loads(check_output(f'{cz_path} data --format json'.split(), text=True))


def is_windows():
    return os.name == 'nt'


def elevate_copy_file(src: Path, dst: Path):
    # [How do I check if I'm running on Windows in Python? [duplicate]](https://stackoverflow.com/a/1325587/8566831)
    if is_windows():
        # [Proper indentation for multiline strings?](https://stackoverflow.com/a/2504454/8566831)
        pycp_str = textwrap.dedent(f"""\
                                        from pathlib import Path
                                        import shutil
                                        dst = Path(r'{str(dst)}')
                                        dst.parent.mkdir(parents=True, exist_ok=True)
                                        shutil.copyfile(r'{str(src)}', dst, follow_symlinks=False)
                                        """)
        # [How to preserve file attributes when one copies files in Windows?](https://superuser.com/a/1326224)
        args = ['gsudo', sys.executable, '-c', pycp_str]
    else:
        args = ['sudo', 'cp', '--preserve=links,mode,timestamps',
                '--no-dereference', str(src), str(dst)]
        if not dst.parent.exists():
            check_call(f"sudo mkdir -p {dst.parent}".split())

    logging.info(
        f'copying file {src} -> {dst}')
    out = check_output(args)
    logging.debug(f'`{" ".join(args)}` output: {out.decode(errors="ignore")}')


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


def paths2str(paths, delimiter=',') -> str:
    # only for iterable type, except str like __getitem__
    # [In Python, how do I determine if an object is iterable?](https://stackoverflow.com/a/1952481/8566831)
    if not isinstance(paths, Iterable):
        paths = [paths]
    return delimiter.join(str(p) for p in paths)


def elevate_writefile(path: str, src: Union[IO[bytes], str], chunk_size=4096):
    """
    从src读取数据并使用sudo/gsudo启动另一个py进程写入path中。没有其它依赖
    """

    if type(src) is str:
        src = BytesIO(src.encode())

    pycp_str = f"""
import sys
with open('{path}', 'wb+') as f, sys.stdin.buffer as i:
    while buf := i.read({chunk_size}):
        f.write(buf)
"""
    args = []
    if is_windows():
        args += ['gsudo.exe']
    else:
        args += ['sudo']
    args += [sys.executable, '-c', pycp_str]
    logging.debug(
        f'starting new process with {args} for write file {path}')
    with Popen(args, stdin=PIPE) as p, src as s:
        with p.stdin as i:
            while buf := s.read(chunk_size):
                i.write(buf)
        if (code := p.wait()) != 0:
            logging.error(
                f'Process {p.pid} writing to file {path} failed with exit code {code}')
            raise Exception(f'failed to write {path} for process {p.pid}')


class ChezmoiArgs:
    def __init__(self, args=None) -> None:
        if not args:
            args = os.environ['CHEZMOI_ARGS']

        m = re.compile(
            r'^(.*?chezmoi(\.exe)?)((\s+--?\w+(-\w+)*)*)\s+(\w+(-\w+)*)((\s+--?\w+(-\w+)*)*)((\s+.+?)*)$').match(args)
        if not m:
            raise SetupExcetion(f'failed to parse chezmoi args: {args}')

        global_opts = (m.group(3) or '').strip()
        self._subcommand = (m.group(6) or '').strip()
        sub_opts = (m.group(8) or '').strip()
        paths = (m.group(11) or '').strip()
        opts = set((global_opts + ' ' + sub_opts).strip().split())

        self._target_paths = set(Path(s).expanduser() for s in paths.split())
        self._is_debug = bool(opts) and '--debug' in opts

        pat_multi_opts = re.compile(r'^-\w*v')
        self._is_verbose = any(v in opts for v in [
                               '-v', '--verbose']) or any(pat_multi_opts.match(v) for v in opts)

        self._data = None

    def has_debug(self) -> bool:
        return self._is_debug

    def has_verbose(self) -> bool:
        return self._is_verbose

    def subcommand(self) -> str:
        return self._subcommand

    def target_paths(self) -> Set[Path]:
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
            self._data = json.loads(check_output(
                [self.bin_path(), 'data', '--format', 'json'], text=True))
        return self._data

    def init_log(self):
        level = logging.ERROR
        if self.has_debug():
            level = logging.DEBUG
        elif self.has_verbose():
            level = logging.INFO
        config_log(level=level)

    def get_source_path(self, target: Path) -> Path:
        if target is None:
            raise SetupExcetion('target is none')
        p = run([self.bin_path(), 'source-path', target],
                stdout=PIPE, stderr=PIPE, text=True)
        return Path(p.stdout.strip()) if p.returncode == 0 else None
