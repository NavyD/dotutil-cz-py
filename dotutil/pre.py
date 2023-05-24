#!/usr/bin/env python3
import logging
import os
import re
import sys
from pathlib import Path
from shutil import which
from subprocess import DEVNULL, check_call, check_output, run

sys.path.append(str(
    Path(os.environ['CHEZMOI_SOURCE_DIR']).joinpath('vendor/dotutil')))
from util import (ChezmoiArgs, SetupExcetion, elevate_copy_file,  # noqa: E402
                  has_changed, has_changed_su, is_windows, paths2str)

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


def sync_from_root(args: ChezmoiArgs):
    mapped_root_dir = args.mapped_root()
    target_paths = args.target_paths()
    if target_paths and all(mapped_root_dir not in p.parents and mapped_root_dir != p for p in target_paths):
        logging.debug(
            f'skipped copy root to {paths2str(mapped_root_dir)} for target paths: {paths2str(target_paths)}')
        return
    if not mapped_root_dir.exists():
        logging.info(
            f"skipped copy mapped root is not dir: {paths2str(mapped_root_dir)}")
        return
    elif mapped_root_dir.is_file():
        raise SetupExcetion(
            f"mapped root is not dir: {paths2str(mapped_root_dir)}")

    logging.info(f'syncing root to {paths2str(mapped_root_dir)} if changed')
    count = 0
    for path in mapped_root_dir.rglob("*"):
        if path.is_file():
            root_path = Path(
                "/").joinpath(os.path.relpath(path, mapped_root_dir))

            privated_path = False
            try:
                root_path.exists()
            except PermissionError:
                logging.debug(f'checking exists for private {str(root_path)}')
                privated_path = True
                privated_path_exists = run(
                    ['sudo', 'test', '-e', root_path]).returncode == 0

            # remove mapped root path if root path not exists
            if (privated_path and not privated_path_exists) or (not privated_path and not root_path.exists()):
                logging.info(
                    f'removing {paths2str(path)} for non exists {paths2str(root_path)}')
                os.remove(path)
                continue

            changed = None
            try:
                changed = has_changed(
                    root_path, path) if not privated_path else has_changed_su(root_path, path)
            except PermissionError:
                logging.error(
                    f'skipped copying file {paths2str(path)} for permission error')
            if changed:
                logging.info(
                    f"copying changed file {paths2str(root_path)} -> {paths2str(path)}")
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
        logging.debug(f'finding passhole template in {paths2str(src_paths)}')

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
                        with open(path, encoding='utf8') as file:
                            for line in file:
                                if pat.search(line):
                                    has_ph = True
                                    logging.info(
                                        f'found passhole template in {paths2str(path)}')
                                    break
                        if has_ph:
                            break
                    except UnicodeDecodeError:
                        logging.info(
                            f'skipped check passhole for non-text {paths2str(path)}')
                        continue
    else:
        has_ph = True

    if has_ph:
        if is_windows():
            args = ['wsl.exe', '--', 'ph', 'list']
            # 用户ctrl+c终止后retcode=0但无输出
            if not check_output(args, encoding='utf8').strip():
                logging.error(f'failed to check passhole with {args}')
                raise SetupExcetion('no passhole output found')
        elif which('ph'):
            check_call(['ph', 'list'], stdout=DEVNULL)


def check_super_permission(args: ChezmoiArgs):
    target_paths = args.target_paths()
    if is_windows():
        pass
    elif which('sudo') and args.mapped_root().is_dir() and (not target_paths or any(args.mapped_root() in p.parents for p in target_paths)):
        cmd = ['sudo', 'echo']
        logging.info(
            f'checking super permission for {paths2str(target_paths)}')
        check_call(cmd, stdout=DEVNULL)


def check_wsl(args: ChezmoiArgs):
    data = args.data()
    if data['is_wsl2'] is not True:
        return
    p = Path.home().joinpath('.root/etc/wsl.conf')
    if data['has_systemd'] is False or not p.exists():
        # allow this `chezmoi apply ~/.root/etc/wsl.conf` pass
        if p not in args.target_paths() and len(args.target_paths()) != 1:
            print(
                f'found uninit wsl2 configuration {str(p)}. please run `chezmoi apply {str(p)}` to enable and reboot then reinit `chezmoi init`')
            exit(1)
        elif not p.parent.exists():
            # fix: stat .root/etc not exists
            p.parent.mkdir(parents=True)


def check_restic(args: ChezmoiArgs):
    data = args.data()
    if data['has_restic']:
        p = Path(data['restic']['path'])
        # # allow this `chezmoi apply ~/.config/passhole.ini` pass
        if not p.exists() and len(args.target_paths()) != 1 and p not in args.target_paths():
            print(
                f'not found restic bin in {paths2str(p)}. please run `chezmoi apply {paths2str(p)}` at first')
            exit(1)


def print_env():
    for key, value in os.environ.items():
        print(f'{key}={value}')


def main():
    s = os.environ['CHEZMOI_ARGS']
    args = ChezmoiArgs(s)
    if args.has_debug():
        print(f'parsed chezmoi {args.__dict__} for args `{s}`')
    args.init_log()

    try:
        check_passhole(args)
        check_super_permission(args)
        check_wsl(args)
        check_restic(args)

        sync_from_root(args)
    except KeyboardInterrupt:
        print('Interrupt by user', file=sys.stderr)
        exit(1)
    except SetupExcetion as e:
        print(f'{e}', file=sys.stderr)
        exit(2)


if __name__ == '__main__':
    main()
