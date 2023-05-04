import logging
import multiprocessing
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from shutil import which
from subprocess import (DEVNULL, CalledProcessError, Popen, TimeoutExpired,
                        check_call, check_output)
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


def setup_scoop(pkgs: Set[str], global_pkgs: Set[str]):
    # [Scoop A command-line installer for Windows](https://scoop.sh/)
    if not which('scoop'):
        logging.info('installing scoop')
        # [Scoop (un)installer](https://github.com/ScoopInstaller/Install)
        check_call(['powershell.exe', '-c', """
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
irm get.scoop.sh | iex

scoop bucket add extras
scoop bucket add nerd-fonts

# disable aria2 warn
scoop config aria2-warning-enabled false
# retry 3
scoop config aria2-retry-wait 3
"""
                    ])

    apps_txt = check_output('scoop list', shell=True).decode(errors='ignore')

    pkgs_un = [p for p in pkgs if p not in apps_txt]
    if pkgs_un:
        logging.info(f'installing scoop {len(pkgs)} pkgs')
        # -u: disable update scoop when install
        check_call(['powershell.exe', '-c',
                    f'scoop install --no-update-scoop {" ".join(pkgs)}'])

    # Only need to run once, this setting is persistent.
    if not which('gsudo'):
        raise Exception('not found gsudo for elevate permission')
    if not re.search(r'^CacheMode\s*=\s*"Auto"\s+\(global\)$', check_output(['gsudo', 'config'], text=True), re.MULTILINE):
        logging.info('config gsudo CacheMode')
        # [Credentials Cache](https://gerardog.github.io/gsudo/docs/credentials-cache)
        check_call('gsudo config CacheMode Auto'.split())

    global_pkgs_un = [p for p in global_pkgs if p not in apps_txt]
    if global_pkgs_un:
        s = " ".join(global_pkgs_un)
        logging.info(
            f'installing scoop global {len(global_pkgs_un)} pkgs with gsudo: {s}')
        # install global pkgs with gsudo
        check_call(
            ['gsudo', f'scoop install --no-update-scoop --global {s}'])


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

scoop_pkgs = {
    'chezmoi',
    # [Scoop can utilize aria2 to use multi-connection downloads](https://github.com/ScoopInstaller/Scoop#multi-connection-downloads-with-aria2)
    'aria2',
    'gsudo',  # [A Sudo for Windows](https://github.com/gerardog/gsudo)
    # [A bash inspired readline implementation for PowerShell](https://github.com/PowerShell/PSReadLine)
    'PSReadLine',
    # [Efficient and Fast, Small and Portable.](https://geekuninstaller.com/)
    'geekuninstaller',
    # [Foreign language reading and translation assistant based on copy and translate.](https://github.com/CopyTranslator/CopyTranslator)
    'CopyTranslator',  # winget安装后异常，无法卸载，使用scoop安装
    # [The minimal, blazing-fast, and infinitely customizable prompt for any shell!](https://starship.rs/guide/#%F0%9F%9A%80-installation)
    'starship',
    # [Curl is a command-line tool for transferring data specified with URL syntax.](https://github.com/curl/curl)
    'curl',
    # 注意：使用scoop安装比winget msstore的更好，环境变量会自动配置。其它源没有scripts目录，winget源没有python3链接
    # [Python is a programming language that lets you work quickly and integrate systems more effectively](https://www.python.org/)
    'python',
    # [A smarter cd command. Supports all major shells.](https://github.com/ajeetdsouza/zoxide)
    'zoxide',
    # [The world’s fastest framework for building websites.](https://github.com/gohugoio/hugo)
    'hugo-extended',
    # [Fast, secure, efficient backup program](https://github.com/restic/restic)
    'restic',

    # scoop checkup: install apps
    # [Innounp is a console application, and it uses command-line options to find out what to do](https://innounp.sourceforge.net/)
    'innounp',
    # [The WiX toolset lets developers create installers for Windows Installer, the Windows installation engine.](https://wixtoolset.org/)
    'wixtoolset',
}

scoop_global_pkgs = {
    # global install: -g #################
    # [This is a fun, new monospaced font that includes programming ligatures and is designed to enhance the modern look and feel of the Windows Terminal.](https://github.com/microsoft/cascadia-code)
    'Cascadia-Code',
    # nerd-fonts [Iconic font aggregator, collection, & patcher. 3,600+ icons, 50+ patched fonts: Hack, Source Code Pro, more. Glyph collections: Font Awesome, Material Design Icons, Octicons, & more](https://github.com/ryanoasis/nerd-fonts)
    'Meslo-NF',
    # [安装 Nerd Font](https://learn.microsoft.com/zh-cn/windows/terminal/tutorials/custom-prompt-setup#install-a-nerd-font)
    # [ryanoasis/nerd-fonts Why Caskaydia Cove and not Cascadia Code?](https://github.com/ryanoasis/nerd-fonts/tree/master/patched-fonts/CascadiaCode)
    # [scoop CascadiaCode-NF-Mono.json](https://github.com/matthewjberger/scoop-nerd-fonts/blob/master/bucket/CascadiaCode-NF-Mono.json)
    'CascadiaCode-NF',
    # [Sarasa Gothic / 更纱黑体 / 更紗黑體 / 更紗ゴシック / 사라사 고딕](https://github.com/be5invis/Sarasa-Gothic)
    # [中文等宽字体（Monospace Chinese Font Family）](https://leonvision.online/technology/monospace-chinese-font-family/)
    'SarasaGothic-ttc',  # or `SarasaGothic-SC`
}


def main():
    args = ChezmoiArgs(os.environ['CHEZMOI_ARGS'])
    args.init_log()

    try:
        install_winget(winget_pkgs, winget_i_pkgs)
        setup_scoop(scoop_pkgs, scoop_global_pkgs)
        winget_install_windows_exporter()
    except KeyboardInterrupt:
        print('Interrupt by user', file=sys.stderr)
        exit(1)


if __name__ == '__main__':
    main()
