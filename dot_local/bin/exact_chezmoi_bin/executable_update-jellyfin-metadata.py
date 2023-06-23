#!/usr/bin/env python3
import logging
import mimetypes
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import docker


@dataclass
class MdcData:
    src_path: str
    dst_path: str

    def output_path(self) -> Path:
        return Path(self.src_path).joinpath("JAV_output")


class AvUpdater:
    def __init__(self, mdc: MdcData) -> None:
        self._mdc = mdc
        self._docker = docker.from_env()
        self._stop_timeout = 2
        self.log = logging.getLogger(__name__)

    def run(self):
        self._remove_src_files(
            lambda p: p.stat().st_size <= 1024 * 1024 * 100  # noqa: W504,W503  # 100mb
            and (v := mimetypes.guess_type(p))  # noqa: W503
            and v[0]  # noqa: W503
            and v[0].startswith("video")
        )  # noqa: W503
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
                    else:
                        self.log.info(f"overwrite {new_dst} from {src}")
                        os.remove(new_dst)

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
                self.log.info(f"removing {str(path)}, size={path.stat().st_size}")
                os.remove(path)
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

    def _start_mdc(self):
        """generate jellyfin metadata from av videos"""
        name = "mdc"
        try:
            c = self._docker.containers.get(name)
        except docker.errors.NotFound:
            self.log.info(f"Creating new container {name}")
            volname = "mdc-config"
            created_vol = False
            try:
                self._docker.volumes.get(volname)
            except docker.errors.NotFound:
                self.log.debug(f"creating volume {volname}")
                self._docker.volumes.create(volname)
                created_vol = True

            c = self._docker.containers.create(
                "vergilgao/mdc:6",
                name=name,
                stdin_open=True,
                tty=True,
                volumes=[f"{volname}:/config", f"{self._mdc.src_path}:/data"],
                environment=[
                    f"UID={os.getuid()}",
                    f"GID={os.getgid()}",
                    "TZ=Asia/Shanghai",
                    "UMASK=022",
                ],
            )
            #  config file missing, we create a new config file, modify the config file and restart container please!
            # 没有找到配置文件，我们创建了一个新的配置文件，请修改后重启镜像
            if created_vol:
                self.log.info(f"starting new mdc for new volume {volname} at first")
                c.start()
                self._docker_attach_check(name)

        self.log.info(f"starting container {name}")
        c.start()
        self._docker_attach_check(name)

    def _start_gfriends_inputer(self):
        """update actor thumb"""
        name = "gfriends-inputer"
        try:
            c = self._docker.containers.get(name)
        except docker.errors.NotFound:
            self.log.info(f"Creating new container {name}")
            volname = "gfriends-inputer-data"
            self._docker.volumes.create(volname)

            c = self._docker.containers.create(
                "navyd/gfriends-inputer",
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


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s.%(msecs)03d [%(levelname)-8s] [%(name)s.%(funcName)s]: %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    mdc = MdcData(
        "/mnt/share/Downloads/completed/avs", "/mnt/share/.Magics/AVs/JAV_output"
    )
    avup = AvUpdater(mdc)
    try:
        avup.run()
    except KeyboardInterrupt:
        print("Interrupt by user")
        exit(1)
    except Exception as e:
        print(f"failed to update metadata by {e}", file=sys.stderr)
        exit(2)
