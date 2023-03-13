#!/usr/bin/env python3
import logging
import os
import sys
from pathlib import Path
from sys import stderr

sys.path.append(str(
    Path(os.environ['CHEZMOI_SOURCE_DIR']).joinpath('vendor/dotutil')))
from util import (ChezmoiArgs, SetupExcetion, config_log,  # noqa: E402
                  elevate_copy_file, has_changed)

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


def copy_root(mapped_root_dir: Path):
    if not mapped_root_dir.exists():
        logging.info(f"skipped copy mapped root is not dir: {mapped_root_dir}")
        return
    elif mapped_root_dir.is_file():
        raise SetupExcetion(f"mapped root is not dir: {mapped_root_dir}")

    logging.info(f'copying root to {mapped_root_dir} if changed')
    count = 0
    for path in mapped_root_dir.rglob("*"):
        if path.is_file():
            root_path = Path(
                "/").joinpath(os.path.relpath(path, mapped_root_dir))
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

    mapped_root_dir = Path.home().joinpath(".root")

    target_paths = args.target_paths()
    if target_paths and all(mapped_root_dir not in p.parents and mapped_root_dir != p for p in target_paths):
        logging.info(
            f'skipped copy root to {mapped_root_dir} for target paths: {target_paths}')
    else:
        try:
            copy_root(mapped_root_dir)
        except SetupExcetion as e:
            logging.error(f"{e}", file=stderr)
            exit(1)
