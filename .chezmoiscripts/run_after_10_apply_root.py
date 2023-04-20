#!/usr/bin/env python3
import logging
import os
import re
import sys
from pathlib import Path
from subprocess import PIPE, CalledProcessError, check_call, run
from sys import stdin
from typing import Iterable, Set

sys.path.append(
    str(Path(os.environ['CHEZMOI_SOURCE_DIR']).joinpath('vendor/dotutil')))
from util import (ChezmoiArgs, SetupExcetion,  # noqa: E402
                  elevate_copy_file, has_changed, has_changed_su, is_windows)

"""
在chezmoi更新.root文件后比较/root并应用到/root中
"""


def get_root_path(mapped_path, mapped_root) -> Path:
    return Path("/").joinpath(os.path.relpath(mapped_path, mapped_root)).absolute()


def copy_to_root(mapped_root: Path):
    diff_count = 0

    def elevate_copy(src: Path, dst: Path):
        nonlocal diff_count
        elevate_copy_file(src, dst)
        diff_count += 1

    for path in mapped_root.rglob("*"):
        if path.is_file() or path.is_symlink():
            root_path = get_root_path(path, mapped_root)

            try:
                root_path.exists()
            except PermissionError:
                logging.info(f'checking exists for private {str(root_path)}')
                privated_path_exists = run(
                    ['sudo', 'test', '-e', root_path]).returncode == 0
                privated_path_is_file = run(
                    ['sudo', 'test', '-f', root_path]).returncode == 0
                if not privated_path_exists:
                    elevate_copy(path, root_path)
                elif privated_path_is_file:
                    if has_changed_su(path, root_path):
                        elevate_copy(path, root_path)
                else:
                    raise SetupExcetion(f"invalid file {root_path}")
                continue

            if not root_path.exists():
                elevate_copy(path, root_path)
            elif root_path.is_file():
                changed = None
                try:
                    changed = has_changed(path, root_path)
                except PermissionError as e:
                    logging.error(
                        f'Checking for changes fails with permission issues on files {path} -> {root_path}: {e}')
                    raise SetupExcetion(e)
                if changed:
                    elevate_copy(path, root_path)
            else:
                raise SetupExcetion(f"invalid file {root_path}")
    logging.info(f"copied {diff_count} files from {mapped_root}")


class RootCleaner:
    def __init__(self, mapped_root: Path, rootlist_path: Path, cz_bin) -> None:
        self.log = logging.getLogger(__name__)
        self._rootlist_path = rootlist_path
        self._mapped_root = mapped_root
        self._cz_bin = cz_bin
        self._exact_pat = re.compile(r'^(\w+_)*exact_.+')

        # rootlist仅保存上次跳过删除的文件
        old_removable_mapped_paths = set(Path(line) for line in self._rootlist_path.read_text().splitlines(
        ) if line.strip()) if self._rootlist_path.is_file() else set()
        self.log.debug(
            f'loaded removable mapped root {len(old_removable_mapped_paths)} paths: {", ".join([str(p) for p in old_removable_mapped_paths])}')
        self._old_removable_mapped_paths = old_removable_mapped_paths

    def clean(self, target_paths: Iterable[Path]):
        # get removed root paths
        removable_paths = self.find_removable_mapped_paths(target_paths)
        root_paths = set()
        for path in removable_paths:
            if rp := get_root_path(path, self._mapped_root):
                if rp.exists():
                    root_paths.add(rp)

        # remove paths
        self.log.info(
            f'trying to remove exists root {len(root_paths)} paths for mapped root {len(removable_paths)} paths')
        removed_root_paths = self.confirm_rm(root_paths)
        self.log.debug(
            f'removed {len(removed_root_paths)} files: {" ".join(str(p) for p in removed_root_paths)}')

        removed_mapped_root_paths = set(
            self.root_target_path(p) for p in removed_root_paths)
        rest_paths = removable_paths - removed_mapped_root_paths
        rest_paths.update(self._old_removable_mapped_paths -  # noqa: W504
                          removed_mapped_root_paths)
        rest_paths = set(p for p in rest_paths if get_root_path(
            p, self._mapped_root).exists())

        # save current root list and skipped for next apply
        self.log.info(
            f"saving rest {len(rest_paths)} paths to {self._rootlist_path} after apply")
        with open(self._rootlist_path, 'w') as f:
            for path in rest_paths:
                f.write(f"{str(path)}\n")

    def root_target_path(self, root_path: Path) -> Path:
        return self._mapped_root.joinpath(str(root_path).lstrip(os.sep))

    def find_removable_mapped_paths(self, target_paths: Iterable[Path]) -> Set[Path]:
        self.log.info(
            f'finding all removable paths for target paths {" ".join(str(p) for p in target_paths)} in old mapped {len(self._old_removable_mapped_paths)} paths')

        target_mapped_paths = None
        if target_paths:
            target_mapped_paths = set(
                p for path in target_paths for p in path.rglob('*'))
        else:
            target_mapped_paths = set(
                p for p in self._mapped_root.rglob('*'))

        target_mapped_paths.update(self._old_removable_mapped_paths)

        exact_paths = {}
        removable_paths = set()
        # 找到在target不存在并找出exact目录
        for path in target_mapped_paths:
            pp = path.parent
            if pp not in exact_paths:
                exact_paths[pp] = self.is_exact(pp)

            if not path.exists():
                removable_paths.add(path)
            elif path.is_dir() and path not in exact_paths:
                exact_paths[path] = self.is_exact(path)
        self.log.debug(
            f'found removable {len(removable_paths)} paths for non exist files: {" ".join(str(p) for p in removable_paths)}')

        # 对于exact目录找到对应root中多余存在的文件
        for path in exact_paths:
            if exact_paths[path]:
                mapped_root_files = {p for p in path.glob('*')}
                virtual_mapped_root_files = {self.root_target_path(p) for p in get_root_path(
                    path, self._mapped_root).glob('*')}
                paths = virtual_mapped_root_files - mapped_root_files
                self.log.debug(
                    f'found removable {len(paths)} paths for mapped exact {path}: {" ".join(str(p) for p in paths)}')
                removable_paths.update(paths)

        return removable_paths

    def is_exact(self, path: Path) -> bool:
        p = run([self._cz_bin, 'source-path', path],
                stdout=PIPE, stderr=PIPE, text=True)
        if p.returncode == 0:
            return self._exact_pat.match(Path(p.stdout.strip()).name) is not None
        else:
            self.log.debug(
                f'failed to run source path {path} on status {p.returncode}: {p.stderr.strip()}')
            return False

    def confirm_rm(self, paths: Set[Path]) -> Set[Path]:
        removed_paths = set()

        if paths:
            print(f'WARN: preparing to remove root {len(paths)} files')

            try:
                remove_all = False
                for path in paths:
                    skipped = False
                    if not remove_all:
                        while True:
                            print(
                                f"whether to remove root file {path}?[remove, all-remove, skip]:", end='', flush=True)
                            line = stdin.readline().strip()
                            if "remove".startswith(line):
                                pass
                            elif "all-remove".startswith(line):
                                remove_all = True
                            elif "skip".startswith(line):
                                skipped = True
                            else:
                                print(f"unkown option: {line}")
                                continue
                            break

                    if not skipped:
                        try:
                            self.elevate_rm(path)
                            removed_paths.add(path)
                        # skipped if failed to remove
                        except CalledProcessError as e:
                            self.log.error(
                                f'failed to remove {path}: returncode={e.returncode}')
                        except SetupExcetion:
                            pass
                    else:
                        self.log.info(f"skipped remove {path}")
            except KeyboardInterrupt:
                self.log.warning(
                    f'skipping removable {len(paths) - len(removed_paths)} for Interrupt')

        return removed_paths

    def elevate_rm(self, path: Path):
        if not path.exists():
            self.log.warning(f"ignore not found root file {path}")
            return
        elif path == Path(os.sep):
            raise SetupExcetion(f"invalid path {path}")

        if is_windows():
            cmd = ['gsudo', 'del', str(path)]
        else:
            cmd = ['sudo', 'rm', '-rf', str(path)]
        self.log.info(f'removing {path} with command: {cmd}')
        check_call(cmd)


def sync(args: ChezmoiArgs):
    mapped_root = args.mapped_root()
    rootlist_path = args.root_list()
    target_paths = args.target_paths()

    if not mapped_root.is_dir():
        logging.warning(
            f"skipped apply mapped root {mapped_root} is not dir")
    elif args.subcommand() != 'apply':
        logging.info(f'skipped apply for subcommand: {args.subcommand()}')
    # only run once when apply post and run script
    elif not target_paths and Path(__file__).name.startswith('run_after_'):
        logging.info('skipped apply for chezmoi scripts')
    # target is not a sub path or self of mapped root
    elif target_paths and all(mapped_root not in p.parents and mapped_root != p for p in target_paths):
        logging.info(
            f'skipped apply non mapped root {mapped_root} in target paths: {target_paths}')
    else:
        copy_to_root(mapped_root)
        RootCleaner(mapped_root, rootlist_path,
                    args.bin_path()).clean(target_paths)


if __name__ == "__main__":
    args = ChezmoiArgs()
    args.init_log()

    try:
        sync(args)
    except SetupExcetion as e:
        logging.error(f"{e}")
        exit(1)
