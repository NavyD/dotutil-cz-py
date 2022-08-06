#!/usr/bin/env python3
import hashlib
import os
import shutil
from pathlib import Path
from sys import stderr

"""
思路：对于root文件在home保存一份映射$HOME/.root
当在chezmoi源更新后，使用before脚本比较/root下面对应的
文件，

扫描before

* 如果.root存在则
  * 比较/root文件hash如果一致 不做任何事
  * 否则复制/root内容到.root文件中，并完成扫描后终止让chezmoi再次计算.root与源的差异
* 否则跳过

应用after，在chezmoi源更新到.root后，比较/root差异

* 如果/root存在则
  * 比较.root文件内容，如果发现改变
    * 复制.root文件内容到/root
  * 否则跳过
* 否则复制新文件到root，注意更新文件owner
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
    return get_digest(src) != get_digest(dst)


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
            if changed(root_path, path):
                print(f"copying changed file {root_path} -> {path}")
                shutil.copyfile(root_path, path)
                diff_count += 1

    if diff_count > 0:
        print(f"found changed {diff_count} files")


try:
    main()
except SetupExcetion as e:
    print(f"{e}", file=stderr)
    exit(1)
