#!/usr/bin/env python3
import os
from pathlib import Path
from typing import List

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

class SetupExcetion(Exception):
    pass

MAPPED_ROOT_DIR = Path.home().joinpath(".root")
if not MAPPED_ROOT_DIR.is_dir():
    raise SetupExcetion(f"mapped root is not dir: {MAPPED_ROOT_DIR}")

for root, dirs, files in os.walk(MAPPED_ROOT_DIR):
    for file in files:
        cur_file = os.path.join(root, file)
        root_file = os.path.relpath(cur_file, MAPPED_ROOT_DIR)
        print(f"{cur_file} -> {root_file}")
