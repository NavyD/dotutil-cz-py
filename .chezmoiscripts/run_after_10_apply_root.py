#!/usr/bin/env python3
import logging
import os
import re
import sys
from pathlib import Path
from subprocess import PIPE, CalledProcessError, check_call, run
from sys import stdin
from typing import Set

sys.path.append(
    str(Path(os.environ['CHEZMOI_SOURCE_DIR']).joinpath('vendor/dotutil')))
from util import (ChezmoiArgs, SetupExcetion,  # noqa: E402
                  elevate_copy_file, has_changed, is_windows)

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

    def clean(self):
        # get removed root paths
        paths = self.find_removable_mapped_paths()
        root_paths = set()
        for path in paths:
            if rp := get_root_path(path, self._mapped_root):
                if rp.exists():
                    root_paths.add(rp)

        # remove paths
        self.log.info(
            f'trying to remove exists root {len(root_paths)} paths for mapped root {len(paths)} paths')
        removed_root_paths = self.confirm_rm(root_paths)

        # save current root list and skipped for next apply
        self.log.debug(
            f"saving current root list to {self._rootlist_path} after apply")
        count = 0
        with open(self._rootlist_path, 'w') as f:
            for path in self._mapped_root.rglob("*"):
                if path.is_file():
                    f.write(f"{str(path)}\n")
                    count += 1

            if skipped := root_paths - removed_root_paths:
                self.log.debug(f'saving skipped {len(skipped)} paths')
                for path in skipped:
                    if path.is_file():
                        # root path as mapped path
                        mapped_path = self.root_target_path(path)
                        f.write(f"{mapped_path}\n")
                        count += 1
        self.log.info(f"saved {count} files to {self._rootlist_path}")

    def root_target_path(self, root_path: Path) -> Path:
        return self._mapped_root.joinpath(str(root_path).lstrip(os.sep))

    def find_removable_mapped_paths(self) -> Set[Path]:
        old_mapped_paths = set(Path(line) for line in self._rootlist_path.read_text().splitlines(
        ) if line.strip()) if self._rootlist_path.is_file() else set(p for p in self._mapped_root.rglob("*"))
        self.log.debug(
            f'loaded old mapped root {len(old_mapped_paths)} paths: {", ".join([str(p) for p in old_mapped_paths])}')

        self.log.info(
            f'finding all removable paths in mapped {len(old_mapped_paths)} paths')
        exact_paths = {}
        removable_paths = set()
        for path in old_mapped_paths:
            pp = path.parent
            if pp not in exact_paths:
                exact_paths[pp] = self.is_exact(pp)

            if not path.exists():
                removable_paths.add(path)
            elif path.is_dir() and path not in exact_paths:
                exact_paths[path] = self.is_exact(path)

        self.log.debug(
            f'found removable {len(removable_paths)} paths for non exist files: {" ".join(str(p) for p in removable_paths)}')

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
        print(
            f"apply error: mapped root is not dir: {mapped_root}")
        exit(1)
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
        RootCleaner(mapped_root, rootlist_path, args.bin_path()).clean()


if __name__ == "__main__":
    args = ChezmoiArgs()
    args.init_log()

    try:
        sync(args)
    except SetupExcetion as e:
        logging.error(f"{e}")
        exit(1)
