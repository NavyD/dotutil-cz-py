#!/usr/bin/env python3
import logging
import os
import re
import sys
from pathlib import Path
from shutil import which
from subprocess import DEVNULL, PIPE, CalledProcessError, check_call, check_output, run
from typing import Iterable, Set

import psutil

from dotutil.util import (
    ChezmoiArgs,
    SetupException,
    elevate_copy_file,
    has_changed,
    has_changed_su,
    paths2str,
)

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
    if target_paths and all(
        mapped_root_dir not in p.parents and mapped_root_dir != p for p in target_paths
    ):
        logging.debug(
            f"skipped copy root to {paths2str(mapped_root_dir)} for target paths: {paths2str(target_paths)}"
        )
        return
    if not mapped_root_dir.exists():
        logging.info(
            f"skipped copy mapped root is not dir: {paths2str(mapped_root_dir)}"
        )
        return
    elif mapped_root_dir.is_file():
        raise SetupException(f"mapped root is not dir: {paths2str(mapped_root_dir)}")

    logging.info(f"syncing root to {paths2str(mapped_root_dir)} if changed")
    count = 0
    for path in mapped_root_dir.rglob("*"):
        if path.is_file():
            root_path = Path("/").joinpath(os.path.relpath(path, mapped_root_dir))

            privated_path = False
            try:
                root_path.exists()
            except PermissionError:
                logging.debug(f"checking exists for private {str(root_path)}")
                privated_path = True
                privated_path_exists = (
                    run(["sudo", "test", "-e", root_path]).returncode == 0
                )

            # remove mapped root path if root path not exists
            if (privated_path and not privated_path_exists) or (
                not privated_path and not root_path.exists()
            ):
                logging.info(
                    f"removing {paths2str(path)} for non exists {paths2str(root_path)}"
                )
                os.remove(path)
                continue

            changed = None
            try:
                changed = (
                    has_changed(root_path, path)
                    if not privated_path
                    else has_changed_su(root_path, path)
                )
            except PermissionError:
                logging.error(
                    f"skipped copying file {paths2str(path)} for permission error"
                )
            if changed:
                logging.info(
                    f"copying changed file {paths2str(root_path)} -> {paths2str(path)}"
                )
                elevate_copy_file(root_path, path)
                count += 1
    logging.info(f"found changed {count} files")


def check_passhole(args: ChezmoiArgs):
    if args.data()["has_keepass"] is not True:
        return
    elif not psutil.WINDOWS:
        p = Path(os.environ.get("CHEZMOI_HOME_DIR", str(Path.home()))).joinpath(
            ".config/passhole.ini"
        )
        # # allow this `chezmoi apply ~/.config/passhole.ini` pass
        if (
            not p.is_file()
            and len(args.target_paths()) != 1
            and p not in args.target_paths()
        ):
            print(
                "not found passhole.ini. please run `chezmoi apply ~/.config/passhole.ini` at first"
            )
            exit(1)

    has_ph = False
    if args.target_paths():
        # filtered if src path is none
        src_paths = [
            p for p in map(lambda p: args.get_source_path(p), args.target_paths()) if p
        ]
        logging.debug(f"finding passhole template in {paths2str(src_paths)}")

        if src_paths:
            pat = re.compile(r'\{\{.*(passhole(\s+".+"){2}).*\}\}')
            paths = set()
            # find all files
            for path in src_paths:
                if path.is_dir():
                    paths.update(p for p in path.rglob("*") if p.is_file())
                elif path.exists():
                    # cz diff -v ~/.local/bin/gitea
                    # /home/navyd/.local/share/chezmoi/dot_local/bin/create_executable_: No such file or directory (os error 2)
                    # src_paths = [p for p in src_paths if p.exists()]
                    paths.add(path)

            # find one file if contains ph
            for path in paths:
                if path.suffix == ".tmpl":
                    try:
                        with open(path, encoding="utf8") as file:
                            for line in file:
                                if pat.search(line):
                                    has_ph = True
                                    logging.info(
                                        f"found passhole template in {paths2str(path)}"
                                    )
                                    break
                        if has_ph:
                            break
                    except UnicodeDecodeError:
                        logging.info(
                            f"skipped check passhole for non-text {paths2str(path)}"
                        )
                        continue
    else:
        has_ph = True

    if has_ph:
        if psutil.WINDOWS:
            args = ["wsl.exe", "--", "ph", "list"]
            # 用户ctrl+c终止后retcode=0但无输出
            if not check_output(args, encoding="utf8").strip():
                logging.error(f"failed to check passhole with {args}")
                raise SetupException("no passhole output found")
        elif which("ph"):
            check_call(["ph", "list"], stdout=DEVNULL)


def check_super_permission(args: ChezmoiArgs):
    target_paths = args.target_paths()
    if psutil.WINDOWS:
        pass
    elif (
        which("sudo")
        and args.mapped_root().is_dir()
        and (
            not target_paths
            or any(args.mapped_root() in p.parents for p in target_paths)
        )
    ):
        cmd = ["sudo", "echo"]
        logging.info(f"checking super permission for {paths2str(target_paths)}")
        check_call(cmd, stdout=DEVNULL)


def check_wsl(args: ChezmoiArgs):
    data = args.data()
    if data["is_wsl2"] is not True:
        return
    p = Path.home().joinpath(".root/etc/wsl.conf")
    if data["has_systemd"] is False or not p.exists():
        # allow this `chezmoi apply ~/.root/etc/wsl.conf` pass
        if p not in args.target_paths() and len(args.target_paths()) != 1:
            print(
                f"found uninit wsl2 configuration {str(p)}. please run `chezmoi apply {str(p)}` to enable and reboot then reinit `chezmoi init`"
            )
            exit(1)
        elif not p.parent.exists():
            # fix: stat .root/etc not exists
            p.parent.mkdir(parents=True)


def check_restic(args: ChezmoiArgs):
    data = args.data()
    if data["has_restic"]:
        p = Path(data["restic"]["path"])
        # # allow this `chezmoi apply ~/.config/passhole.ini` pass
        if (
            not p.exists()
            and len(args.target_paths()) != 1
            and p not in args.target_paths()
        ):
            print(
                f"not found restic bin in {paths2str(p)}. please run `chezmoi apply {paths2str(p)}` at first"
            )
            exit(1)


def print_env():
    for key, value in os.environ.items():
        print(f"{key}={value}")


def pre_run():
    s = os.environ["CHEZMOI_ARGS"]
    args = ChezmoiArgs(s)
    if args.has_debug():
        print(f"parsed chezmoi {args.__dict__} for args `{s}`")
    args.init_log()

    try:
        check_passhole(args)
        check_super_permission(args)
        check_wsl(args)
        check_restic(args)

        sync_from_root(args)
    except KeyboardInterrupt:
        print("Interrupt by user", file=sys.stderr)
        exit(1)
    except SetupException as e:
        print(f"{e}", file=sys.stderr)
        exit(2)


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
                logging.debug(f"checking exists for private {str(root_path)}")
                privated_path_exists = (
                    run(["sudo", "test", "-e", root_path]).returncode == 0
                )
                privated_path_is_file = (
                    run(["sudo", "test", "-f", root_path]).returncode == 0
                )
                if not privated_path_exists:
                    elevate_copy(path, root_path)
                elif privated_path_is_file:
                    if has_changed_su(path, root_path):
                        elevate_copy(path, root_path)
                else:
                    raise SetupException(f"invalid file {paths2str(root_path)}")
                continue

            if not root_path.exists():
                elevate_copy(path, root_path)
            elif root_path.is_file():
                changed = None
                try:
                    changed = has_changed(path, root_path)
                except PermissionError as e:
                    logging.error(
                        f"Checking for changes fails with permission issues on files {paths2str(path)} -> {paths2str(root_path)}: {e}"
                    )
                    raise SetupException(e)
                if changed:
                    elevate_copy(path, root_path)
            else:
                raise SetupException(f"invalid file {paths2str(root_path)}")
    logging.info(f"copied {diff_count} files from {paths2str(mapped_root)}")


class RootCleaner:
    def __init__(self, mapped_root: Path, rootlist_path: Path, cz_bin) -> None:
        self.log = logging.getLogger(__name__)
        self._rootlist_path = rootlist_path
        self._mapped_root = mapped_root
        self._cz_bin = cz_bin
        self._exact_pat = re.compile(r"^(\w+_)*exact_.+")

        # rootlist仅保存上次跳过删除的文件
        old_removable_mapped_paths = (
            set(
                Path(line)
                for line in self._rootlist_path.read_text().splitlines()
                if line.strip()
            )
            if self._rootlist_path.is_file()
            else set()
        )
        self.log.debug(
            f"loaded removable mapped root {len(old_removable_mapped_paths)} paths: {paths2str(old_removable_mapped_paths)}"
        )
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
            f"trying to remove exists root {len(root_paths)} paths for mapped root {len(removable_paths)} paths"
        )
        removed_root_paths = self.confirm_rm(root_paths)
        self.log.debug(
            f"removed {len(removed_root_paths)} files: {paths2str(removed_root_paths)}"
        )

        removed_mapped_root_paths = set(
            self.root_target_path(p) for p in removed_root_paths
        )
        rest_paths = removable_paths - removed_mapped_root_paths
        rest_paths.update(
            self._old_removable_mapped_paths - removed_mapped_root_paths  # noqa: W504
        )
        rest_paths = set(
            p for p in rest_paths if get_root_path(p, self._mapped_root).exists()
        )

        # save current root list and skipped for next apply
        self.log.debug(
            f"saving rest {len(rest_paths)} paths to {self._rootlist_path} after apply"
        )
        with open(self._rootlist_path, "w") as f:
            for path in rest_paths:
                f.write(f"{str(path)}\n")

    def root_target_path(self, root_path: Path) -> Path:
        return self._mapped_root.joinpath(str(root_path).lstrip(os.sep))

    def find_removable_mapped_paths(self, target_paths: Iterable[Path]) -> Set[Path]:
        self.log.info(
            f"finding all removable paths for target paths {paths2str(target_paths)} in old mapped {len(self._old_removable_mapped_paths)} paths"
        )

        target_mapped_paths = None
        if target_paths:
            target_mapped_paths = set(
                p for path in target_paths for p in path.rglob("*")
            )
        else:
            target_mapped_paths = set(p for p in self._mapped_root.rglob("*"))

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
            f"found removable {len(removable_paths)} paths for non exist files: {paths2str(removable_paths)}"
        )

        # 对于exact目录找到对应root中多余存在的文件
        for path in exact_paths:
            if exact_paths[path]:
                mapped_root_files = {p for p in path.glob("*")}
                virtual_mapped_root_files = {
                    self.root_target_path(p)
                    for p in get_root_path(path, self._mapped_root).glob("*")
                }
                paths = virtual_mapped_root_files - mapped_root_files
                self.log.debug(
                    f"found removable {len(paths)} paths for mapped exact {paths2str(path)}: {paths2str(paths)}"
                )
                removable_paths.update(paths)

        return removable_paths

    def is_exact(self, path: Path) -> bool:
        p = run(
            [self._cz_bin, "source-path", path], stdout=PIPE, stderr=PIPE, text=True
        )
        if p.returncode == 0:
            return self._exact_pat.match(Path(p.stdout.strip()).name) is not None
        else:
            self.log.debug(
                f"failed to run source path {paths2str(path)} on status {p.returncode}: {p.stderr.strip()}"
            )
            return False

    def confirm_rm(self, paths: Set[Path]) -> Set[Path]:
        removed_paths = set()

        if paths:
            print(f"WARN: preparing to remove root {len(paths)} files")

            try:
                remove_all = False
                for path in paths:
                    skipped = False
                    if not remove_all:
                        while True:
                            print(
                                f"whether to remove root file {paths2str(path)}?[remove, all-remove, skip]:",
                                end="",
                                flush=True,
                            )
                            line = sys.stdin.readline().strip()
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
                                f"failed to remove {paths2str(path)}: returncode={e.returncode}"
                            )
                        except SetupException:
                            pass
                    else:
                        self.log.info(f"skipped remove {paths2str(path)}")
            except KeyboardInterrupt:
                self.log.warning(
                    f"skipping removable {len(paths) - len(removed_paths)} for Interrupt"
                )

        return removed_paths

    def elevate_rm(self, path: Path):
        if not path.exists():
            self.log.warning(f"ignore not found root file {paths2str(path)}")
            return
        elif path == Path(os.sep):
            raise SetupException(f"invalid path {paths2str(path)}")

        if psutil.WINDOWS:
            cmd = ["gsudo", "del", str(path)]
        else:
            cmd = ["sudo", "rm", "-rf", str(path)]
        self.log.info(f"removing {paths2str(path)} with command: {cmd}")
        check_call(cmd)


def sync(args: ChezmoiArgs):
    mapped_root = args.mapped_root()
    rootlist_path = args.root_list()
    target_paths = args.target_paths()

    if not mapped_root.is_dir():
        logging.warning(
            f"skipped apply mapped root {paths2str(mapped_root)} is not dir"
        )
    elif args.subcommand() != "apply":
        logging.debug(f"skipped apply for subcommand: {args.subcommand()}")
    # only run once when apply post and run script
    elif not target_paths and Path(__file__).name.startswith("run_after_"):
        logging.debug("skipped apply for chezmoi scripts")
    # target is not a sub path or self of mapped root
    elif target_paths and all(
        mapped_root not in p.parents and mapped_root != p for p in target_paths
    ):
        logging.info(
            f"skipped apply non mapped root {paths2str(mapped_root)} in target paths: {paths2str(target_paths)}"
        )
    else:
        logging.info(f"syncing {paths2str(mapped_root)} to /")
        copy_to_root(mapped_root)
        RootCleaner(mapped_root, rootlist_path, args.bin_path()).clean(target_paths)


def post_run():
    args = ChezmoiArgs()
    args.init_log()

    try:
        sync(args)
    except SetupException as e:
        logging.error(f"{e}")
        exit(1)
