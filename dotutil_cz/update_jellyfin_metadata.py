import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import click
import docker

from dotutil_cz.util import config_global_log


@dataclass
class MdcData:
    src_path: str
    dst_path: str
    config_dir: str

    def output_path(self) -> Path:
        return Path(self.src_path).joinpath("JAV_output")


class AvUpdater:
    def __init__(self, non_interactive: bool, mdc: MdcData) -> None:
        self._mdc = mdc
        self._docker = docker.from_env()
        self._stop_timeout = 2
        self._non_interactive = non_interactive
        self.log = logging.getLogger(__name__)

    def run(self):
        # auto remove empty by mdc
        self._remove_src_files(lambda p: p.stat().st_size <= 1024 * 1024 * 100)  # 100mb
        self._start_mdc()
        self._merge_mdc_output_to_dst()
        self._start_gfriends_inputer()
        self.log.info("jellyfin av update successful")

    def _merge_mdc_output_to_dst(self):
        """合并mdc的文件到dst后移除mdc的输出"""
        out = self._mdc.output_path()
        dst = Path(self._mdc.dst_path)
        if not out.exists():
            self.log.info(f"skip merge empty mdc output: {out}")
            return
        elif not out.is_dir():
            raise Exception(f"mdc output path is not a dir: {out}")

        self.log.info(f"merging mdc output {out} to {dst}")
        for src in out.rglob("*"):
            if src.is_file():
                new_dst = dst.joinpath(os.path.relpath(str(src), str(out)))
                if new_dst.exists():
                    if os.path.samefile(src, new_dst):
                        self.log.debug(f"skipping move same files: {src}, {new_dst}")
                        continue
                    elif self._non_interactive or click.confirm(
                        f"Are you sure you want to overwrite `{str(new_dst)}` with `{str(src)}`?"
                    ):
                        self.log.info(f"overwrite {new_dst} from {src}")
                        os.remove(new_dst)
                    else:
                        self.log.warning(f"skipped overwrite {new_dst} with {src}")

                self.log.debug(f"moving {src} to {new_dst}")
                os.makedirs(new_dst.parent, exist_ok=True)
                shutil.move(str(src), str(new_dst))

        self.log.info(f"removing merged mdc output {out}")
        shutil.rmtree(out)

    def _remove_src_files(self, filter: Callable[[Path], bool]):
        """过滤移除掉不需要的文件减少请求mdc"""
        src = Path(self._mdc.src_path)
        out = str(self._mdc.output_path())

        self.log.info(f"removing redundant files in {src}")
        for path in src.rglob("*"):
            # skip mdc output
            if path.is_relative_to(out):
                continue
            if path.is_file() and filter(path):
                if self._non_interactive or click.confirm(
                    f"Are you sure you want to delete `{str(path)}`?"
                ):
                    self.log.info(f"removing {str(path)}, size={path.stat().st_size}")
                    os.remove(path)
                else:
                    self.log.warning(f"skipped remove {str(path)}")
            else:
                self.log.debug(f"skip removing {path}")

    def _docker_attach_check(self, name):
        c = self._docker.containers.get(name)
        self.log.debug(f"Fetching container {name} follow logs")
        try:
            for line in c.attach(stdout=True, stderr=True, stream=True):
                print(line.decode(), end="", flush=True)
            print()
        except KeyboardInterrupt:
            self.log.warning(
                f"Stopping fetching logs and stopping container {name} by timeout {self._stop_timeout}s"
            )
            c.stop(timeout=self._stop_timeout)
            raise

        self.log.debug(f"waiting timeout {self._stop_timeout} for {name}")
        res = c.wait(timeout=self._stop_timeout)
        self.log.debug(f"found {name} wait result: {res}")
        if res["StatusCode"] != 0:
            raise Exception(f"container {name} exit failed {res['StatusCode']}")

    def _docker_pull_if(self, repo):
        try:
            self._docker.images.get(repo)
        except docker.errors.NotFound:
            self.log.info(f"Pulling image {repo}")
            for s in self._docker.api.pull(repo, stream=True, decode=True):
                # {'status': 'Pulling from navyd/mdc', 'id': 'latest'}
                # {'status': 'Pulling fs layer', 'progressDetail': {}, 'id': '9d21b12d5fab'}
                # {'status': 'Downloading', 'progressDetail': {'current': 666, 'total': 666}, 'progress': '[==================================================>]     666B/666B', 'id': '0ee564007c6b'}
                self.log.debug(f"{s['status']}: {s.setdefault('progress', s['id'])}")
            pass

    def _start_mdc(self):
        """generate jellyfin metadata from av videos"""
        name = "mdc"
        try:
            c = self._docker.containers.get(name)
        except docker.errors.NotFound:
            repo = "navyd/mdc"
            self._docker_pull_if(repo)
            self.log.info(f"Creating new container {name}")
            c = self._docker.containers.create(
                repo,
                name=name,
                stdin_open=True,
                tty=True,
                volumes=[
                    f"{self._mdc.config_dir}:/config",
                    f"{self._mdc.src_path}:/data",
                ],
                environment=[
                    f"UID={os.getuid()}",
                    f"GID={os.getgid()}",
                    "TZ=Asia/Shanghai",
                    "UMASK=022",
                ],
            )
        self.log.info(f"starting container {name}")
        c.start()
        self._docker_attach_check(name)

    def _start_gfriends_inputer(self):
        """update actor thumb"""
        name = "gfriends-inputer"
        try:
            c = self._docker.containers.get(name)
        except docker.errors.NotFound:
            repo = "navyd/gfriends-inputer"
            self._docker_pull_if(repo)
            self.log.info(f"Creating new container {name}")
            volname = "gfriends-inputer-data"
            self._docker.volumes.create(volname)

            c = self._docker.containers.create(
                repo,
                "-q --debug",
                name=name,
                # resolve jellyfin host url, jellyfin network: c875a2a000c8   rpi4_default   bridge    local
                network_mode="host",
                volumes=[f"{volname}:/data"],
                environment=[
                    "GF_HOST_API=949288a6d42b44c3bce3eb541964830d",
                    "GF_HOST_URL=https://m.navyd.xyz",
                    "TZ=Asia/Shanghai",
                    "UMASK=022",
                ],
            )

        self.log.info(f"starting container {name}")
        c.start()
        self._docker_attach_check(name)


@click.command
@click.option(
    "-v",
    "--verbose",
    count=True,
    default=2,
    type=click.IntRange(0, 3),
)
@click.option("--src", required=True, type=click.Path())
@click.option("--dst", required=True, type=click.Path())
@click.option("--config-dir", "-C", type=click.Path())
@click.option("--non-interactive", "-n", default=False)
def main(src: Path, dst: Path, config_dir: Path, verbose: int, non_interactive: bool):
    config_global_log()
    log = logging.getLogger(__name__)
    log.setLevel(logging.ERROR - verbose * 10)

    mdc = MdcData(str(src), str(dst), str(config_dir))
    avup = AvUpdater(non_interactive, mdc)
    try:
        avup.run()
    except KeyboardInterrupt:
        print("Interrupt by user")
        exit(1)
    except Exception as e:
        print(f"failed to update metadata by {e}", file=sys.stderr)
        exit(2)


if __name__ == "__main__":
    main()