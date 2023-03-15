#!/usr/bin/env python3
import logging
import os
import sys
from pathlib import Path
from shutil import which
from subprocess import DEVNULL, check_call

sys.path.append(str(
    Path(os.environ['CHEZMOI_SOURCE_DIR']).joinpath('vendor/dotutil')))
from util import (ChezmoiArgs, SetupExcetion, config_log,  # noqa: E402
                  elevate_copy_file, has_changed, is_windows)

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


def copy_from_root(args: ChezmoiArgs):
    mapped_root_dir = args.mapped_root()
    target_paths = args.target_paths()
    if target_paths and all(mapped_root_dir not in p.parents and mapped_root_dir != p for p in target_paths):
        logging.info(
            f'skipped copy root to {mapped_root_dir} for target paths: {target_paths}')
        return
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


def check_passhole(args: ChezmoiArgs):
    if is_windows():
        pass
    elif which('ph'):
        check_call(['ph', 'list'], stdout=DEVNULL)


def check_super_permission(args: ChezmoiArgs):
    if is_windows():
        pass
    elif which('sudo') and (not args.target_paths() or any(args.mapped_root() in pp for p in args.target_paths() for pp in p.parents)):
        cmd = ['sudo', 'echo']
        logging.info(f'checking super permission with `{cmd}`')
        check_call(cmd, stdout=DEVNULL)


def print_env():
    for key, value in os.environ.items():
        print(f'{key}={value}')


def main():
    level = logging.ERROR
    s = os.environ['CHEZMOI_ARGS']
    args = ChezmoiArgs(s)
    if args.has_debug():
        level = logging.DEBUG
    elif args.has_verbose():
        level = logging.INFO
    config_log(level=level)
    logging.info(f'parsed chezmoi {args.__dict__} for args `{s}`')

    if args.has_debug():
        print_env()

    check_passhole(args)
    check_super_permission(args)

    copy_from_root(args)


if __name__ == '__main__':
    main()
