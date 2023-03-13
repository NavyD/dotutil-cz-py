#!/usr/bin/env python3
import logging
import os
from pathlib import Path
from sys import stderr
import sys

sys.path.append(str(
    Path(os.environ['CHEZMOI_SOURCE_DIR']).joinpath('vendor/dotutil')))
from util import (ChezmoiArgs, SetupExcetion, config_log, elevate_copy_file,  # noqa: E402
                  has_changed)

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


def main():
    MAPPED_ROOT_DIR = Path.home().joinpath(".root")

    if not MAPPED_ROOT_DIR.exists():
        logging.info(f"skipped copy mapped root is not dir: {MAPPED_ROOT_DIR}")
        return
    elif MAPPED_ROOT_DIR.is_file():
        raise SetupExcetion(f"mapped root is not dir: {MAPPED_ROOT_DIR}")

    logging.info(f'copying root to {MAPPED_ROOT_DIR} if changed')
    count = 0
    for path in MAPPED_ROOT_DIR.rglob("*"):
        if path.is_file():
            root_path = Path(
                "/").joinpath(os.path.relpath(path, MAPPED_ROOT_DIR))
            logging.debug(f'checking changed: {path} and {root_path}')
            changed = None
            try:
                changed = has_changed(root_path, path)
            except PermissionError:
                logging.error(
                    f'skipped copying file {path} for permission error')
            if changed:
                logging.info(f"copying changed file {root_path} -> {path}")
                elevate_copy_file(root_path, path)
                count += 1
    logging.info(f"found changed {count} files")


if __name__ == '__main__':
    level = logging.ERROR
    s = os.environ['CHEZMOI_ARGS']
    args = ChezmoiArgs(s)
    if args.has_debug():
        level = logging.DEBUG
    elif args.has_verbose():
        level = logging.INFO
    config_log(level=level)
    logging.info(f'parsed chezmoi {args.__dict__} for args `{s}`')

    try:
        main()
    except SetupExcetion as e:
        logging.error(f"{e}", file=stderr)
        exit(1)
