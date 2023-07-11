#!/usr/bin/env python3
import logging
import os
import re
import subprocess as sp
import sys
from pathlib import Path
from shutil import which
from typing import Iterable, Set

import psutil

from dotutil_cz import elevate
from dotutil_cz.util import (
    ChezmoiArgs,
    SetupException,
    config_log_cz,
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

log = logging.getLogger(__name__)


def pre_sync_from_root(args: ChezmoiArgs):
    mapped_root_dir = args.mapped_root()
    target_paths = args.target_paths()
    if target_paths and all(
        mapped_root_dir not in p.parents and mapped_root_dir != p for p in target_paths
    ):
        log.debug(
            f"skipped copy root to {paths2str(mapped_root_dir)} for target paths: {paths2str(target_paths)}"
        )
        return
    if not mapped_root_dir.exists():
        log.info(f"skipped copy mapped root is not dir: {paths2str(mapped_root_dir)}")
        return
    elif mapped_root_dir.is_file():
        raise SetupException(f"mapped root is not dir: {paths2str(mapped_root_dir)}")

    log.info(f"syncing root to {paths2str(mapped_root_dir)} if changed")
    count = 0
    for path in mapped_root_dir.rglob("*"):
        if path.is_file():
            root_path = Path("/").joinpath(os.path.relpath(path, mapped_root_dir))

            privated_path = False
            try:
                root_path.exists()
            except PermissionError:
                log.debug(f"checking exists for private {str(root_path)}")
                privated_path = True
                privated_path_exists = (
                    sp.run(["sudo", "test", "-e", root_path]).returncode == 0
                )

            # remove mapped root path if root path not exists
            if (privated_path and not privated_path_exists) or (
                not privated_path and not root_path.exists()
            ):
                log.info(
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
                log.error(
                    f"skipped copying file {paths2str(path)} for permission error"
                )
            if changed:
                log.info(
                    f"copying changed file {paths2str(root_path)} -> {paths2str(path)}"
                )
                elevate_copy_file(root_path, path)
                count += 1
    log.info(f"found changed {count} files")


def check_passhole(cz: ChezmoiArgs):
    if cz.data()["has_keepass"] is not True:
        return
    elif not psutil.WINDOWS:
        p = cz.home_dir().joinpath(".config/passhole.ini")
        # # allow this `chezmoi apply ~/.config/passhole.ini` pass
        if (
            not p.is_file()
            and len(cz.target_paths()) != 1
            and p not in cz.target_paths()
        ):
            log.error(
                f"not found config passhole in {p}. "
                f"please run `chezmoi apply ~/.config/passhole.ini` at first"
            )
            raise SetupException("not found passhole config")

    has_ph = False
    if cz.target_paths():
        # filtered if src path is none
        src_paths = [
            p for p in map(lambda p: cz.get_source_path(p), cz.target_paths()) if p
        ]
        log.debug(f"finding passhole template in {paths2str(src_paths)}")

        if src_paths:
            pat = re.compile(
                r'\{\{.*(passhole(\s+".+"){2})|(includeTemplate\s+"\s*restic-dump\s*"\s+).*\}\}'
            )
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
                                    log.info(
                                        f"found passhole template in {paths2str(path)}"
                                    )
                                    break
                        if has_ph:
                            break
                    except UnicodeDecodeError:
                        log.info(
                            f"skipped check passhole for non-text {paths2str(path)}"
                        )
                        continue
    else:
        has_ph = True

    if has_ph:
        args = []
        if psutil.WINDOWS:
            if bin := which("wsl.exe"):
                args = [bin, "--", "ph", "list"]
            else:
                raise SetupException("not found wsl.exe")
        else:
            if bin := which("ph"):
                args = ["ph", "list"]
            else:
                raise SetupException("not found ph")
        log.debug(
            f"checking if passhole is active with {args} for "
            f"{paths2str(cz.target_paths() or ['~'])}"
        )
        # 用户ctrl+c终止后retcode=0但无输出
        if not sp.check_output(args, encoding="utf8").strip():
            log.error(f"failed to check passhole with {args}")
            raise SetupException("no passhole output found")


def check_super_permission(cz: ChezmoiArgs):
    target_paths = cz.target_paths()
    if not cz.mapped_root().is_dir() or (
        target_paths and all(cz.mapped_root() not in p.parents for p in target_paths)
    ):
        return

    args = []
    if psutil.WINDOWS:
        # -p | --pid {pid} Specify which process can use the cache. (Use 0 for any, Default=caller pid)
        # https://gerardog.github.io/gsudo/docs/credentials-cache#usage
        args = [elevate.gsudo_path, "cache", "on", "-p", "0"]
    elif psutil.POSIX:
        args = [elevate.sudo_path, "echo"]
    else:
        raise SetupException("unsupported os")

    log.info(
        f"checking super permission with {args} "
        f"for {paths2str(target_paths) or '~'}"
    )
    p = sp.run(args, stdout=sp.DEVNULL, stderr=sp.PIPE)
    if p.returncode != 0:
        log.error(f"failed to check permission exited {p} with {args}")
        p.check_returncode()


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
    cz = ChezmoiArgs()
    config_log_cz(cz=cz)

    try:
        check_passhole(cz)
        check_super_permission(cz)
        check_wsl(cz)
        check_restic(cz)

        pre_sync_from_root(cz)
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
                log.debug(f"checking exists for private {str(root_path)}")
                privated_path_exists = (
                    sp.run(["sudo", "test", "-e", root_path]).returncode == 0
                )
                privated_path_is_file = (
                    sp.run(["sudo", "test", "-f", root_path]).returncode == 0
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
                    log.error(
                        f"Checking for changes fails with permission issues on files {paths2str(path)} -> {paths2str(root_path)}: {e}"
                    )
                    raise SetupException(e)
                if changed:
                    elevate_copy(path, root_path)
            else:
                raise SetupException(f"invalid file {paths2str(root_path)}")
    log.info(f"copied {diff_count} files from {paths2str(mapped_root)}")


class RootCleaner:
    def __init__(
        self, mapped_root: Path, rootlist_path: Path, cz_bin, cz_src_path: Path
    ) -> None:
        self.log = logging.getLogger(__name__)
        self._rootlist_path = rootlist_path
        self._mapped_root = mapped_root
        self._cz_bin = cz_bin
        self._exact_pat = re.compile(r"^(\w+_)*exact_.+")
        self._cz_src_path = cz_src_path

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
        """
        检查path所在的目录是否包含exact属性。
        首先使用glob在cz源码目录中检查是否存在相应目录，如果存在1个目录则检查，以避免使用命令
        不存在则使用cz source-path命令查找
        """
        # include mapped root self:
        # ~/.root relative_to mappedroot:~/.root => .
        # ~/.root relative_to mappedroot:~ => .root
        relp = path.relative_to(self._mapped_root.parent)
        glob_pat = ""
        for name in relp.parts:
            s = "*"
            # Source state attribute: https://www.chezmoi.io/reference/source-state-attributes/
            # only replace first dot
            if name.startswith("."):
                s += f"dot_{name[1:]}"
            else:
                s += name
            glob_pat += f"{s}{os.sep}"
        self.log.debug(
            f"finding recursive {self._cz_src_path} with "
            f"glob pat {glob_pat} for relative {relp} in {path}"
        )
        # no relative path: . => empty
        glob_paths = [p for p in self._cz_src_path.rglob(glob_pat)] if glob_pat else []
        self.log.debug(
            f"checking if {len(glob_paths)} paths {paths2str(glob_paths)} is exact for {path}"
        )
        if len(glob_paths) == 1:
            return self._exact_pat.match(glob_paths[0].name) is not None

        args = [self._cz_bin, "source-path", path]
        self.log.debug(f"checking if {path} is exact with {args}")
        name = sp.run(
            args,
            stdout=sp.PIPE,
            stderr=sp.PIPE,
            text=True,
        )
        if name.returncode == 0:
            return self._exact_pat.match(Path(name.stdout.strip()).name) is not None
        else:
            self.log.debug(
                f"failed to run source path {paths2str(path)} "
                f"on status {name.returncode}: {name.stderr.strip()}"
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
                        except sp.CalledProcessError as e:
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
        sp.check_call(cmd)
