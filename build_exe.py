#!/usr/bin/env python3
"""用 PyInstaller 把 bodian_gui.py 打包为单文件 exe。

用法: py -3.11 build_exe.py
产物: dist/PyBodian.exe（构建完成后自动复制到项目根目录）
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "PyBodian"


def main():
    python = sys.executable
    print(f"使用 Python: {python}")
    print(f"Python 版本: {sys.version.split()[0]}")

    if shutil.which("ffplay") is None:
        print("提示: 当前 PATH 未找到 ffplay，打包后的 exe 播放音乐仍需要系统安装 FFmpeg。")

    icon_path = ""
    for candidate in (
        os.path.join(ROOT, "icon.ico"),
        os.path.join(ROOT, "assets", "icon.ico"),
    ):
        if os.path.isfile(candidate):
            icon_path = candidate
            break
    if icon_path:
        print(f"使用应用图标: {icon_path}")
    else:
        print("提示: 未找到 icon.ico（或 assets/icon.ico），exe 将使用 PyInstaller 默认图标。")
        print("      想自定义图标时，把 256x256 以上的 .ico 文件放到项目根目录命名 icon.ico 后重新打包即可。")

    args = [
        python, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", APP_NAME,
        "--hidden-import", "extract_credentials",
        "--hidden-import", "qrcode",
        "--hidden-import", "PIL._tkinter_finder",
    ]
    if icon_path:
        args.extend(["--icon", icon_path])
    args.append(os.path.join(ROOT, "bodian_gui.py"))
    print("开始打包（首次构建需要几分钟）...")
    subprocess.run(args, cwd=ROOT, check=True)

    exe_name = f"{APP_NAME}.exe"
    dist_exe = os.path.join(ROOT, "dist", exe_name)
    if not os.path.isfile(dist_exe):
        raise SystemExit(f"打包失败: 未找到 {dist_exe}")

    target = os.path.join(ROOT, exe_name)
    shutil.copy2(dist_exe, target)
    print(f"\n打包完成: {target}")
    print("说明: exe 与源码目录共用旁边的 .bodian 配置目录（便携模式），"
          "首次运行如未登录，请先在界面右上角“提取凭证”或扫码登录。")


if __name__ == "__main__":
    main()
