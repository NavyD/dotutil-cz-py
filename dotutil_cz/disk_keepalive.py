#!/usr/bin/env python3

import argparse
import logging
import os
from datetime import datetime, timedelta
from time import sleep

from psutil import disk_io_counters, disk_partitions


class AliveDisk:
    def __init__(self, path: str) -> None:
        self.log = logging.getLogger(__name__)
        self.log.debug(f"Finding the device where path {path} is located")

        # get disk dev name and mount path
        dev_infos = [
            (disk.device, path.lstrip(disk.mountpoint))
            for disk in disk_partitions()
            if path.startswith(disk.mountpoint) and disk.mountpoint != "/"
        ]
        # find min
        devs = [
            os.path.basename(dev)
            for dev, _ in sorted(dev_infos, key=lambda s: len(s[1]))
        ]

        if not devs or (len(devs) > 1 and len(devs[0]) == len(devs[1])):
            raise Exception(f"invalid devices for path {path}: {devs}")
        self.log.debug(f"Found {len(devs)} devices {devs} for path {path}")

        self._path = path
        self._dev = devs[0]
        self._count = 0

    def get_io_count(self) -> int:
        stats = disk_io_counters(perdisk=True)[self._dev]
        return stats.read_count + stats.write_count

    def keepalive(self, shake=None):
        """
        shake 将会影响保活时输出到磁盘的内容长度
        """
        self._count += 1
        content = str(self._count)
        if shake and shake > 0:
            content = content.zfill(shake)

        count = self.get_io_count()
        self.log.info(f"keeping alive {content} for cur io {count}")
        # no buf
        with open(self._path, "w") as f:
            f.write(content)
            # flush cache
            f.flush()
            # sync to disk
            os.fsync(f)


class Keeper:
    """
    让磁盘保持活动一段时间后再休眠。

    当前实现由于usb机械硬盘无法使用hdparm管理，只能通过写文件的方式保持硬盘活动

    当硬盘活动时不会写文件，否则在一段时间内没有发现io时主动写入文件保持活动，
    在长时间没有io后停止写文件将自动休眠

    使用shake可以减少对一次少量的磁盘io的检查，只有在间隔时间内的io超过shake才会启动保活
    """

    def __init__(self, idle_interval, alive_interval, shake=0) -> None:
        self._interval = alive_interval
        self._idle_interval = timedelta(seconds=idle_interval)
        self._alive_interval = timedelta(seconds=alive_interval)
        self.log = logging.getLogger(__name__)
        self._shake = shake

    def run(self, disk: AliveDisk):
        self.log.info(
            f"running alive service with idle interval {self._idle_interval.seconds}, alive interval {self._alive_interval.seconds}"
        )

        last_io_time = datetime.now()
        last_io_count = disk.get_io_count()
        cur_io_count = last_io_count
        keepalived = False
        is_idle = False

        while True:
            cur_io_count = disk.get_io_count()
            now = datetime.now()
            if cur_io_count > last_io_count + self._shake:
                # ignore io in alive_interval
                self.log.debug(
                    f"found new io {cur_io_count} from last io {last_io_count}"
                )
                if keepalived:
                    self.log.info(f"reset keepalive timing for new io {cur_io_count}")

                last_io_time = now
                is_idle = False
                keepalived = False
            # keep alive interval
            elif now > (last_io_time + self._alive_interval) and now <= (
                last_io_time + self._idle_interval
            ):
                disk.keepalive(shake=self._shake)

                new_io_count = disk.get_io_count()
                # 防止io被缓存在sleep后生效导致一直重置last_io_time作为新io
                assert (
                    cur_io_count < new_io_count
                ), f"Unchanged keepalive io {cur_io_count} before and after {new_io_count}"
                # update keepalive's io count
                cur_io_count = new_io_count
                keepalived = True

                self.log.debug(
                    f"keepalived io {cur_io_count} from last io {last_io_count}"
                )
            else:
                # too idle
                if not is_idle:
                    self.log.info(
                        f"Idling at io {cur_io_count} after {(now - last_io_time).seconds}s from last io {last_io_count}"
                    )
                else:
                    self.log.debug(
                        f"Idling at io {cur_io_count} for last io {last_io_count}"
                    )
                is_idle = True

            sleep(self._interval)
            last_io_count = cur_io_count


def main():
    parser = argparse.ArgumentParser(description="Optional app description")
    parser.add_argument(
        "-i",
        "--idle-interval",
        type=int,
        required=True,
        help="最大的保活时间，一旦程序保活达到时间后将不再保活",
    )
    parser.add_argument(
        "-a",
        "--alive-interval",
        type=int,
        required=True,
        help="磁盘两次io的间隔时间，应该保证在这间隔内没有io磁盘不会休眠",
    )
    parser.add_argument(
        "path", type=str, help="to write the file for keepalive the disk"
    )
    args = parser.parse_args()

    if not args.idle_interval or not args.alive_interval or not args.path:
        raise Exception(f"has empty args: {args}")

    logging.basicConfig(
        format="%(asctime)s.%(msecs)03d [%(levelname)-8s] [%(name)s.%(funcName)s]: %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    disk = AliveDisk(args.path)
    Keeper(args.idle_interval, args.alive_interval, shake=5).run(disk)


class AliveDiskTest(AliveDisk):
    def __init__(self, counts) -> None:
        super(AliveDiskTest, self)
        self._io_count_idx = 0
        self._io_counts = counts
        pass

    def keepalive(self):
        # self.keepalive_count += 1
        for i in range(self._io_count_idx, len(self._io_counts)):
            self._io_counts[i] += 1
        # self.log.info(f'keepalive plus 1: {self._io_counts}')
        pass

    def get_io_count(self) -> int:
        c = self._io_counts[self._io_count_idx]
        self._io_count_idx += 1
        return c


def test():
    counts = [
        1,
        1,
        4,
        5,
        5,
        5,
        5,
        5,
        5,
        5,
        5,
        5,
        8,
        9,
        9,
        9,
        9,
        9,
        9,
        9,
        9,
        9,
        9,
        9,
        9,
        9,
        9,
        9,
        9,
        9,
        9,
        9,
        9,
        10,
    ]
    disk = AliveDiskTest(counts)
    Keeper(5, 2).run(disk)


if __name__ == "__main__":
    main()
