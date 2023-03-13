#!/usr/bin/env python3
import logging
import os
import sys
from pathlib import Path
from subprocess import check_call
from sys import stdin
from typing import Set

sys.path.append(
    str(Path(os.environ['CHEZMOI_SOURCE_DIR']).joinpath('vendor/dotutil')))
from util import (ChezmoiArgs, SetupExcetion, config_log, elevate_copy_file,  # noqa: E402
                  has_changed, is_windows)

"""
在chezmoi更新.root文件后比较/root并应用到/root中
"""


class SyncRoot:
    def __init__(self, mapped_root: Path, rootlist_path: Path) -> None:
        self.mapped_root = mapped_root
        self.rootlist_path = rootlist_path
        self.log = logging.getLogger(__name__)

    def root_path(self, path) -> Path:
        return Path("/").joinpath(os.path.relpath(path, self.mapped_root)).absolute()

    def get_removed_mapped_files(self) -> Set[Path]:
        files = set()

        def readfiles(path):
            with open(path, 'r') as f:
                while line := f.readline():
                    file = Path(line.rstrip())
                    if not file.exists():
                        files.add(file)

        if self.rootlist_path.is_file():
            self.log.debug(f"found root list path {self.rootlist_path}")
            readfiles(self.rootlist_path)

        self.log.debug(
            f'loaded {len(files)} files in rootlist {self.rootlist_path}')
        return files

    def save_rootlist(self):
        self.log.debug(
            f"saving current root list after apply to {self.rootlist_path}")
        count = 0
        with open(self.rootlist_path, 'w') as f:
            for path in self.mapped_root.rglob("*"):
                if path.is_file():
                    count += 1
                    f.write(f"{path}\n")
        self.log.debug(f"saved {count} files to {self.rootlist_path}")

    def remove_root_files(self):
        def rm(path: Path):
            if not path.exists():
                self.log.warning(f"ignore not found root file {path}")
                return
            elif path == Path("/"):
                raise SetupExcetion(f"invalid path {path}")

            if is_windows():
                cmd = ['gsudo', 'del', str(path)]
            else:
                cmd = ['sudo', 'rm', '-rf', str(path)]
            self.log.info(f'removing {path} with command: {" ".join(cmd)}')
            check_call(cmd)

        removed_files = self.get_removed_mapped_files()
        removed_files = [p for p in removed_files if p.exists()]
        if not removed_files:
            self.log.debug("empty removed files")
            return
        logging.info(f"found removal {len(removed_files)} files")
        logging.debug(
            f'removing root files: {", ".join([ str(p) for p in removed_files])}')

        remove_all = False
        for path in removed_files:
            root_file = self.root_path(path)
            skipped = False
            if not remove_all:
                while True:
                    print(
                        f"whether to remove root file {root_file}?[remove, all-remove, skip]:", end='', flush=True)
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
                rm(root_file)
            else:
                self.log.info(f"skipped remove {root_file}")

    def sync(self):
        diff_count = 0

        def copy_to_root(src: Path, dst: Path):
            nonlocal diff_count
            elevate_copy_file(src, dst)
            diff_count += 1

        self.remove_root_files()

        for path in self.mapped_root.rglob("*"):
            if path.is_file():
                root_path = self.root_path(path)
                if not root_path.exists():
                    copy_to_root(path, root_path)
                elif root_path.is_file():
                    changed = None
                    try:
                        changed = has_changed(path, root_path)
                    except PermissionError as e:
                        self.log.error(
                            f'Checking for changes fails with permission issues on files {path} -> {root_path}: {e}')
                        raise SetupExcetion(e)
                    if changed:
                        copy_to_root(path, root_path)
                else:
                    raise SetupExcetion(f"invalid file {root_path}")

        self.log.info(f"copied {diff_count} files from {self.mapped_root}")

        self.save_rootlist()


if __name__ == "__main__":
    level = logging.ERROR
    args = ChezmoiArgs(os.environ['CHEZMOI_ARGS'])
    if args.has_debug():
        level = logging.DEBUG
    elif args.has_verbose():
        level = logging.INFO
    config_log(level=level)

    mapped_root_dir = Path(os.environ['CHEZMOI_HOME_DIR']).joinpath('.root')
    rootlist_path = Path(os.environ['CHEZMOI_CACHE_DIR']).joinpath('.root')

    target_paths = args.target_paths()
    if not mapped_root_dir.is_dir():
        print(
            f"apply error: mapped root is not dir: {mapped_root_dir}")
        exit(1)
    elif args.subcommand() != 'apply':
        logging.info(f'skipped apply for subcommand: {args.subcommand()}')
    # only run once when apply post and run script
    elif not target_paths and Path(__file__).name.startswith('run_after_'):
        logging.info('skipped apply for chezmoi scripts')
    # target is not a sub path or self of mapped root
    elif target_paths and all(mapped_root_dir not in p.parents and mapped_root_dir != p for p in target_paths):
        logging.info(
            f'skipped apply non mapped root {mapped_root_dir} in target paths: {target_paths}')
    else:
        try:
            SyncRoot(mapped_root_dir, rootlist_path).sync()
        except SetupExcetion as e:
            logging.error(f"{e}")
            exit(1)
