import logging
import multiprocessing
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from shutil import which
from subprocess import (DEVNULL, CalledProcessError, Popen, TimeoutExpired,
                        check_call)
from typing import Set

# import util
sys.path.append(
    str(Path(os.environ['CHEZMOI_SOURCE_DIR']).joinpath('vendor/dotutil')))
from util import ChezmoiArgs  # noqa: E402

POOL = ThreadPoolExecutor(max_workers=multiprocessing.cpu_count() + 1)


def install_winget(pkgs: Set[str], ipkgs: Set[str]):
    name = 'winget'
    if not which(name):
        raise Exception(f'not found {name} in PATH')

    logging.info(f'checking interactive {len(ipkgs)} packages if installed')
    ipkgs_un = ipkgs - winget_installed(ipkgs)
    logging.info(
        f'installing interactive winget uninstalled {len(ipkgs_un)} packages for orignal {len(ipkgs)} packages: {" ".join(ipkgs_un)}')
    for pkg in ipkgs_un:
        try:
            check_call([name, 'install', '--no-upgrade', '--interactive', pkg])
        except CalledProcessError as e:
            # 已安装包版本。已取消安装: Command 'winget install --no-upgrade --interactive NetEase.CloudMusic' returned non-zero exit status 2316632161.
            if e.returncode == 2316632161:
                continue
            else:
                raise e
    pkgs_un = pkgs - winget_installed(pkgs)
    logging.info(
        f'installing winget uninstalled {len(pkgs_un)} packages for orignal {len(pkgs)} packages: {" ".join(pkgs_un)}')
    for pkg in pkgs_un:
        try:
            check_call([name, 'install', '--no-upgrade', '--silent', pkg])
        except CalledProcessError as e:
            if e.returncode == 2316632161:
                logging.debug(f'skipped installed package {name}')
                continue
            else:
                raise e


def winget_install_windows_exporter():
    # [Prometheus exporter for Windows machines](https://github.com/prometheus-community/windows_exporter)
    name = 'Prometheus.WMIExporter'
    if winget_installed({name}):
        return
    logging.info('installing windows_exporter')

    # windows_exporter msi安装使用配置文件时要求其存在，否则无法安装成功一直阻塞重启进程，可查看event viewer日志
    config_path = 'C:\\Program Files\\windows_exporter\\config.yml'
    logging.info(
        f'creating empty config file for windows_exporter if {config_path} not exists')
    check_call(['gsudo', 'python3', '-c',
                f'''from pathlib import Path; Path("{config_path}").touch();'''])

    # https://github.com/prometheus-community/windows_exporter#installation
    check_call(['winget',
                'install',
                '--id',
                name,
                # msi参数
                '--override',
                f'EXTRA_FLAGS="--config.file=""{config_path}"""'
                ])


def winget_installed(pkgs: Set[str], wait_timeout=30) -> Set[str]:
    """
    并发winget list -q 进程查询每个pkg是否已安装并返回安装的pkg。
    """
    def wait_status(a):
        (name, p) = a
        try:
            if not p.wait(wait_timeout):
                return name
        except TimeoutExpired:
            p.kill()
        return None

    logging.info(
        f'Checking for {len(pkgs)} packages waiting timeout {wait_timeout}s for installation')
    pkgs = list(pkgs)
    res = set()
    step = POOL._max_workers
    # 限制一次启动太多进程
    for names in [pkgs[x:x + step] for x in range(0, len(pkgs), step)]:
        logging.debug(
            f'Starting {len(names)} winget processes to check if the packages is installed')
        procs = [(name, Popen(['winget', 'list', '-q', name], stdout=DEVNULL))
                 for name in names]
        res.update(pkg for pkg in POOL.map(wait_status, procs) if pkg)
    return res


winget_pkgs = {
    # https://gitforwindows.org/
    'Git.Git',
    # [7-Zip is a file archiver with a high compression ratio.](https://www.7-zip.org/)
    '7zip.7zip',
    # 自动写入 keepassxc dep [System Error: VCRUNTIME140_1.dll was not found #7564](https://github.com/keepassxreboot/keepassxc/issues/7564#issuecomment-1074842970)
    # 'Microsoft.VC++2017Redist-x64',
    # [KeePassXC is a cross-platform community-driven port of the Windows application “Keepass Password Safe”.](https://github.com/keepassxreboot/keepassxc)
    'KeePassXCTeam.KeePassXC',
    # [The 7-Zip derivative intended for the modern Windows experience](https://github.com/M2Team/NanaZip)
    'M2Team.NanaZip',
    # [Bring macOS “Quick Look” feature to Windows](https://github.com/QL-Win/QuickLook)
    '9NV4BS3L1H4S',  # wins tore
    'Google.Chrome',
    # [SPACEDESK DRIVER SOFTWARE for Windows PRIMARY PC (server)](https://www.spacedesk.net/)
    'Datronicsoft.SpacedeskDriver.Server',
    # Restart to take effect [A lightweight utility which can automatically switch the display state of the Windows Taskbar.](https://github.com/ChanpleCai/SmartTaskbar)
    '9PJM69MPS6T9',
    # [Automatically switches between the dark and light theme of Windows 10 and Windows 11](https://github.com/AutoDarkMode/Windows-Auto-Night-Mode)
    'Armin2208.WindowsAutoNightMode',
    'Telegram.TelegramDesktop',
    # [A lightweight utility that makes the Windows taskbar translucent/transparent](https://github.com/TranslucentTB/TranslucentTB)
    '9PF4KZ2VN4W9',
    # [Snip& Paste Snipaste is a simple but powerful snipping tool, and also allows you to pin the screenshot back onto the screen](https://www.snipaste.com/)
    '9P1WXPKB68KX',
    # https://im.qq.com/index
    'Tencent.QQ',
    # [微信 Windows 版](https://windows.weixin.qq.com/)
    'Tencent.WeChat',
    # [Thunderbird 是一款免费的电子邮件应用程序，配置简单，定制自由 —— 还附有强大功能！](https://www.thunderbird.net/zh-CN/)
    'Mozilla.Thunderbird',
    # https://pan.baidu.com/
    'Baidu.BaiduNetdisk',
    # [A Windows/macOS GUI based on Clash](https://github.com/Fndroid/clash_for_windows_pkg)
    'Fndroid.ClashForWindows',
    # [[Unofficial] qBittorrent Enhanced, based on qBittorrent](https://github.com/c0re100/qBittorrent-Enhanced-Edition)
    'c0re100.qBittorrent-Enhanced-Edition',
    # [Everything 基于名称快速定位文件和文件夹。](https://www.voidtools.com/zh-cn/)
    'voidtools.Everything',
    # [Windows system utilities to maximize productivity](https://github.com/microsoft/PowerToys)
    'Microsoft.PowerToys',
    # [miui+ Beta手机电脑之间 协作无间](https://plus.miui.com/)
    'Xiaomi.MIUI+',
    # [Raspberry Pi Imaging Utility](https://github.com/raspberrypi/rpi-imager)
    'RaspberryPiFoundation.RaspberryPiImager',
    # [VLC 是一款自由、开源的跨平台多媒体播放器及框架，可播放大多数多媒体文件，以及 DVD、音频 CD、VCD 及各类流媒体协议。](https://www.videolan.org/)
    'VideoLAN.VLC',
    'EpicGames.EpicGamesLauncher',
    'Valve.Steam',
    'SoftDeluxe.FreeDownloadManager',
    # dev env #########
    # [A faster, better and more stable redis desktop manager [GUI client], compatible with Linux, Windows, Mac. What's more, it won't crash when loading massive keys.](https://github.com/qishibo/AnotherRedisDesktopManager)
    'qishibo.AnotherRedisDesktopManager',
    # 自定义安装位置和选择ffmp
    # [ScreenToGif allows you to record a selected area of your screen, edit and save it as a gif or video.](https://github.com/NickeManarin/ScreenToGif)
    '9N3SQK8PDS8G',
}

winget_i_pkgs = {
    # 自定义右键菜单
    'Microsoft.VisualStudioCode',
    # 选择默认音乐等 https://m.music.163.com
    'NetEase.CloudMusic',
    # 安装WHP 激活YF390-0HF8P-M81RQ-2DXQE-M2UT6。 [VMware Workstation Pro 是行业标准桌面 Hypervisor，使用它可在 Windows 或 Linux 桌面上运行 Windows、Linux 和 BSD 虚拟机。](https://www.vmware.com/cn/products/workstation-pro.html)
    # 为何不用virtualbox：在linux桌面太卡，无法有效使用gpu
    'VMware.WorkstationPro',
    # 写入当前用户 not provide access to additional cross-targets like rustup does. [rust Standalone installers](https://forge.rust-lang.org/infra/other-installation-methods.html#standalone-installers)
    # 'Rustlang.Rustup',
}


def main():
    args = ChezmoiArgs(os.environ['CHEZMOI_ARGS'])
    args.init_log()

    try:
        install_winget(winget_pkgs, winget_i_pkgs)
        winget_install_windows_exporter()
    except KeyboardInterrupt:
        print('Interrupt by user', file=sys.stderr)
        exit(1)


if __name__ == '__main__':
    main()
