#!/usr/bin/env python3
import hashlib
import os
from pathlib import Path
from subprocess import check_call
from sys import stderr

"""
在chezmoi更新.root文件后比较/root并应用到/root中
"""

class SetupExcetion(Exception):
    pass


def get_digest(path: Path) -> str:
    h = hashlib.md5()
    buf = memoryview(bytearray(128*1024))
    with open(path, "rb", buffering=0) as f:
        while n := f.readinto(buf):
            h.update(buf[:n])
    return h.hexdigest()


def changed(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    if not src.is_file():
        raise SetupExcetion(f"{src} is not a file")
    s = src.stat()
    d = dst.stat()
    return s.st_mode != d.st_mode or get_digest(src) != get_digest(dst)


def main():
    MAPPED_ROOT_DIR = Path.home().joinpath(".root")
    if not MAPPED_ROOT_DIR.exists():
        return
    elif MAPPED_ROOT_DIR.is_file():
        raise SetupExcetion(f"mapped root is not dir: {MAPPED_ROOT_DIR}")

    diff_count = 0
    for path in MAPPED_ROOT_DIR.rglob("*"):
        if path.is_file():
            root_path = Path(
                "/").joinpath(os.path.relpath(path, MAPPED_ROOT_DIR))
            if not root_path.exists():
                print(f"copying {path} to {root_path} for new root file")
                check_call(
                    f"sudo mkdir -p {root_path.parent} && sudo cp --no-preserve=ownership {path} {root_path}", shell=True)
                diff_count += 1

            elif root_path.is_file():
                if changed(path, root_path):
                    print(f"copying changed file {path} -> {root_path}")
                    check_call(
                        f"sudo cp --preserve=mode {path} {root_path}", shell=True)
                    diff_count += 1
            else:
                raise SetupExcetion(f"invalid file {root_path}")

    if diff_count > 0:
        print(f"copied {diff_count} files from {MAPPED_ROOT_DIR}")


try:
    main()
except SetupExcetion as e:
    print(f"{e}", file=stderr)
    exit(1)
