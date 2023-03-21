#!/usr/bin/env python3
import logging
import os
import re
import sys
from pathlib import Path
from shutil import which
from subprocess import DEVNULL, check_call

sys.path.append(str(
    Path(os.environ['CHEZMOI_SOURCE_DIR']).joinpath('vendor/dotutil')))
from util import (ChezmoiArgs, SetupExcetion, elevate_copy_file,  # noqa: E402
                  has_changed, is_windows)

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
    if args.data()['has_keepass'] is not True:
        return
    elif not is_windows():
        p = Path(os.environ.get('CHEZMOI_HOME_DIR', str(Path.home()))
                 ).joinpath('.config/passhole.ini')
        # # allow this `chezmoi apply ~/.config/passhole.ini` pass
        if not p.is_file() and len(args.target_paths()) != 1 and p not in args.target_paths():
            print(
                'not found passhole.ini. please run `chezmoi apply ~/.config/passhole.ini` at first')
            exit(1)

    has_ph = False
    if args.target_paths():
        # filtered if src path is none
        src_paths = [p for p in map(
            lambda p: args.get_source_path(p), args.target_paths()) if p]
        logging.debug(f'finding passhole template in {src_paths}')

        if src_paths:
            pat = re.compile(r'\{\{.*(passhole(\s+".+"){2}).*\}\}')
            paths = set()
            # find all files
            for path in src_paths:
                if path.is_dir():
                    paths.update(p for p in path.rglob('*') if p.is_file())
                elif path.exists():
                    # cz diff -v ~/.local/bin/gitea
                    # /home/navyd/.local/share/chezmoi/dot_local/bin/create_executable_: No such file or directory (os error 2)
                    # src_paths = [p for p in src_paths if p.exists()]
                    paths.add(path)

            # find one file if contains ph
            for path in paths:
                if path.suffix == '.tmpl':
                    try:
                        with open(path) as file:
                            for line in file:
                                if pat.search(line):
                                    has_ph = True
                                    logging.info(
                                        f'found passhole template in {path}')
                                    break
                        if has_ph:
                            break
                    except UnicodeDecodeError:
                        logging.debug(
                            f'skipped check passhole for non-text {path}')
                        continue
    else:
        has_ph = True

    if has_ph:
        if is_windows():
            check_call(['wsl.exe', '--', 'ph', 'list'], stdout=DEVNULL)
        elif which('ph'):
            check_call(['ph', 'list'], stdout=DEVNULL)


def check_super_permission(args: ChezmoiArgs):
    target_paths = args.target_paths()
    if is_windows():
        pass
    elif which('sudo') and args.mapped_root().is_dir() and (not target_paths or any(args.mapped_root() in p.parents for p in target_paths)):
        cmd = ['sudo', 'echo']
        logging.info(f'checking super permission for {target_paths}')
        check_call(cmd, stdout=DEVNULL)


def check_wsl_systemd(args: ChezmoiArgs):
    data = args.data()
    if data['is_wsl2'] is True and data['has_systemd'] is False:
        p = Path.home().joinpath('.root/etc/wsl.conf')
        # allow this `chezmoi apply ~/.root/etc/wsl.conf` pass
        if p not in args.target_paths() and len(args.target_paths()) != 1:
            print('not found systemd on wsl2. please run `chezmoi apply ~/.root/etc/wsl.conf` enable systemd and reboot and reinit `chezmoi init`')
            exit(1)
        elif not p.parent.exists():
            # fix: stat .root/etc not exists
            p.parent.mkdir(parents=True)


def print_env():
    for key, value in os.environ.items():
        print(f'{key}={value}')


def main():
    s = os.environ['CHEZMOI_ARGS']
    args = ChezmoiArgs(s)
    if args.has_debug() or args.has_verbose():
        print(f'parsed chezmoi {args.__dict__} for args `{s}`')
    args.init_log()

    if args.has_debug():
        print_env()

    try:
        check_passhole(args)
        check_super_permission(args)
    except KeyboardInterrupt:
        exit(1)

    check_wsl_systemd(args)

    copy_from_root(args)


if __name__ == '__main__':
    main()
