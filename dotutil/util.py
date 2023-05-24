import hashlib
import json
import logging
import os
import re
import subprocess as sp
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from typing import IO, Dict, Generator, List, Set, Union
from urllib.request import urlopen

from dotutil import SetupException, elevate, logger

log = logging.getLogger(__name__)


def get_digest(path: Path) -> str:
    chunk_size = 1024 * 4
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while buf := f.read(chunk_size):
                h.update(buf)
            return h.hexdigest()
    except PermissionError as e:
        log.debug(f"try elevate to read file {path} without read permission")
        pycode = f"""
import hashlib
h = hashlib.sha256()
with open({repr(str(path))}, "rb") as f:
    while buf := f.read({chunk_size}):
        h.update(buf)
    print(h.hexdigest(), end='')
"""
        try:
            return elevate.py_check_output(pycode).decode()
        except sp.CalledProcessError as e1:
            log.warning(
                f"failed to read file {path} using {e1.cmd}: "
                f"{e1.stderr.decode()}. Please enter password with sudo in advance"
            )
            raise e


def has_changed_su(src: Path, dst: Path) -> bool:
    def get_mode(path):
        return sp.check_output(["sudo", "stat", "--format", "%a", path], text=True)

    smode = get_mode(src)
    dmode = get_mode(dst)
    return smode != dmode or get_digest(src) != get_digest(dst)


def has_changed(src: Path, dst: Path) -> bool:
    if not src.exists():
        raise SetupException(f"{src} is not exists")
    if not src.is_file():
        raise SetupException(f"{src} is not a file")
    s = src.stat()
    d = dst.stat()
    return s.st_mode != d.st_mode or get_digest(src) != get_digest(dst)


def config_global_log(level=logging.CRITICAL, stream=None):
    """
    config global log
    """
    # logging.basicConfig(format='{asctime}.{msecs:<10f} [{levelname:4}] [{pathname}:{name}.{funcName}]: {message}',
    # style='{',
    # [Python logging.Formatter(): is there any way to fix the width of a field and justify it left/right?](https://stackoverflow.com/questions/20618570/python-logging-formatter-is-there-any-way-to-fix-the-width-of-a-field-and-jus)
    # TODO: fixed width for long pathname
    logging.basicConfig(
        format="%(asctime)s.%(msecs)03d [%(levelname)-5s] [%(name)s.%(funcName)s]: %(message)s",
        level=level,
        stream=stream,
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def config_log_cz(log: logging.Logger = None, cz=None, level=logging.DEBUG):
    """
    优先从cz读取log配置，如果未找到则为level
    """
    if cz is None:
        try:
            cz = ChezmoiArgs()
        except SetupException():
            pass
    if cz:
        if cz.has_debug():
            level = logging.DEBUG
        elif cz.has_verbose():
            level = logging.INFO

    config_global_log()
    logger.setLevel(level)
    if log:
        log.setLevel(level)


def elevate_copy_file(src: Path, dst: Path):
    pycode = f"""\
from pathlib import Path
import shutil
dst = Path(r'{str(dst)}')
dst.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(r'{str(src)}', dst, follow_symlinks=False)
"""
    logging.info(f"copying file {src} -> {dst}")
    out = elevate.py_check_output(pycode)
    logging.debug(f'coppied output: {out.decode(errors="ignore")}')


def download_file(url, file):
    CHUNK = 10 * 1024
    logging.info(f"downloading to {file.name} from {url}")
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

    pat = re.compile(r"^\s*(from|import)\s+(\w+)")
    if not pat.match(st):
        st = f"import {st}"
    try:
        exec(st, sys._getframe(1).f_globals)
    except ModuleNotFoundError:
        import subprocess

        m = pat.search(st)
        package = m.group(2)
        if not package:
            raise Exception(f"not found package for statement: {st}")
        subprocess.check_call(f"{sys.executable} -m pip install {package}", shell=True)
        exec(st, sys._getframe(1).f_globals)


def paths2str(paths, delimiter=",") -> str:
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
with open({repr(str(path))}, 'wb+') as f, sys.stdin.buffer as i:
    while buf := i.read({chunk_size}):
        f.write(buf)
"""
    with elevate.py_popen(pycp_str, stdin=sp.PIPE) as p, src as s:
        with p.stdin as i:
            while buf := s.read(chunk_size):
                i.write(buf)
        if (code := p.wait()) != 0:
            logging.error(
                f"Process {p.pid} writing to file {path} failed with exit code {code}"
            )
            raise SetupException(f"failed to write {path} for process {p.pid}")


class ChezmoiArgs:
    def __init__(self, args=None) -> None:
        if not args:
            args = os.environ.get("CHEZMOI_ARGS", "")

        m = re.compile(
            r"^(.*?chezmoi(\.exe)?)((\s+--?\w+(-\w+)*)*)\s+(\w+(-\w+)*)((\s+--?\w+(-\w+)*)*)((\s+.+?)*)$"
        ).match(args)
        if not m:
            raise SetupException(f"failed to parse chezmoi args: {args}")

        global_opts = (m.group(3) or "").strip()
        self._subcommand = (m.group(6) or "").strip()
        sub_opts = (m.group(8) or "").strip()
        paths = (m.group(11) or "").strip()
        opts = set((global_opts + " " + sub_opts).strip().split())

        self._target_paths = set(Path(s).expanduser() for s in paths.split())
        self._is_debug = bool(opts) and "--debug" in opts

        pat_multi_opts = re.compile(r"^-\w*v")
        self._is_verbose = any(v in opts for v in ["-v", "--verbose"]) or any(
            pat_multi_opts.match(v) for v in opts
        )

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
        if v := os.environ["CHEZMOI_HOME_DIR"]:
            return Path(v).joinpath(".root")
        else:
            raise SetupException("not found env CHEZMOI_HOME_DIR")

    def home_dir(self) -> Path:
        if s := os.environ.get("CHEZMOI_HOME_DIR", self.data().get("homeDir")):
            return Path(s)
        raise SetupException("not found chezmoi home dir")

    def root_list(self) -> Path:
        if v := os.environ["CHEZMOI_CACHE_DIR"]:
            return Path(v).joinpath(".root")
        else:
            raise SetupException("not found env CHEZMOI_CACHE_DIR")

    def bin_path(self) -> Path:
        if v := os.environ["CHEZMOI_EXECUTABLE"]:
            return Path(v)
        else:
            raise SetupException("not found env CHEZMOI_EXECUTABLE")

    def data(self) -> Dict[str, str]:
        if self._data is None:
            out = sp.check_output(
                [self.bin_path(), "data", "--format", "json"], text=True
            )
            self._data = json.loads(out)
        return self._data

    def get_source_path(self, target: Path) -> Path:
        if target is None:
            raise SetupException("target is none")
        p = sp.run(
            [self.bin_path(), "source-path", target],
            stdout=sp.PIPE,
            stderr=sp.PIPE,
            text=True,
        )
        return Path(p.stdout.strip()) if p.returncode == 0 else None


class Restic:
    def __init__(self, bin: str, env=None) -> None:
        self.log = logging.getLogger(__name__)
        self._bin = Path(bin)
        if not self._bin.exists():
            raise Exception("not found restic bin")
        self._env = os.environ.copy()
        if env:
            self.log.debug(f"using restic env: {env}")
            # restic error: unable to open cache: unable to locate cache directory: neither $XDG_CACHE_HOME nor $HOME are defined
            self._env.update(env)

    def dump(
        self, file: Path, snapshot_id="latest", **kwargs
    ) -> Generator[bytes, None, None]:
        chunk_size = 1024 * 4
        args = [str(self._bin), "dump"]
        for k, v in kwargs.items():
            args += [f"--{k}", v]
        args += [snapshot_id, str(file)]
        self.log.debug(f"start running command {args}")

        with sp.Popen(args, stdout=sp.PIPE, text=False, env=self._env) as p:
            with p.stdout as f:
                self.log.debug(
                    f"reading the stdout output data of restic process {p.pid} "
                    f"with chunk size={chunk_size}"
                )
                count = 0
                while chunk := f.read(chunk_size):
                    count += len(chunk)
                    yield chunk

                self.log.debug(
                    f"read the stdout of restic process {p.pid} for a total of {count} bytes"
                )

    def restore(
        self, target: Path, include_pats: List[str], snapshot_id="latest", **kwargs
    ):
        args = ["sudo", "-E", str(self._bin), "restore", "--target", str(target)]
        if include_pats:
            for p in include_pats:
                args += ["--include", p]
        for k, v in kwargs.items():
            args += [f"--{k}", v]
        args += [snapshot_id]
        self.log.debug(f"start running command {args}")
        sp.check_call(args, env=self._env)
