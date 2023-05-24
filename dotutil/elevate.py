import logging
import os
import shutil
import subprocess as sp
import sys

import psutil

from dotutil import SetupException

log = logging.getLogger(__name__)
gsudo_path = shutil.which("gsudo.exe")
sudo_path = shutil.which("sudo")


class ElevateExcetion(SetupException):
    pass


def _gen_elevate_py_args(codestr: str, non_interactive=False):
    """
    在windows平台使用[gsudo](https://github.com/gerardog/gsudo)提权运行，
    默认可能与unix sudo不一样，最明显是cache是默认相关调用者进程的，每次父进程
    退出后调用gsudo都会重新授权，可以使用`gsudo cache on -p 0`取消限制
    参考：https://gerardog.github.io/gsudo/docs/credentials-cache#usage
    """
    args = []
    if psutil.POSIX:
        if not sudo_path:
            raise ElevateExcetion("not found sudo")
        args += [sudo_path]
        if non_interactive is True:
            args += ["-n"]
        args += [sys.executable, "-c", codestr]
    elif psutil.WINDOWS:
        if not gsudo_path:
            raise ElevateExcetion("not found gsudo.exe")

        if non_interactive is True:
            st_args = [gsudo_path, "status"]
            log.debug(f"checking if gsudo is non interactive by {st_args}")
            s = sp.check_output([gsudo_path, "status"], text=True)
            has_elevated = "Available for this process: True" in s
            log.debug(
                f"found gsudo evaluated={has_elevated} from {st_args} output: {s}"
            )
            if not has_elevated:
                raise ElevateExcetion("unelevate with non_interactive for gsudo")
        #  -d | --direct: Skip Shell detection. Assume CMD shell or CMD {command}.
        args += [gsudo_path, "-d", sys.executable, "-c", codestr]
    else:
        raise ElevateExcetion(f"unsupported os {os.name}")
    return args


def py_popen(codestr: str, non_interactive=False, **proc_kwargs):
    args = _gen_elevate_py_args(codestr, non_interactive=non_interactive)
    log.debug(f"elevate running {args}")
    return sp.Popen(args, **proc_kwargs)


def py_run(codestr: str, non_interactive=False, **proc_kwargs) -> sp.CompletedProcess:
    args = _gen_elevate_py_args(codestr, non_interactive=non_interactive)
    log.debug(f"elevate running {args}")
    return sp.run(args, **proc_kwargs)


def py_check_output(codestr: str, non_interactive=False, **proc_kwargs):
    args = _gen_elevate_py_args(codestr, non_interactive=non_interactive)
    log.debug(f"elevate running {args}")
    return sp.check_output(args, **proc_kwargs)
