# setup.py
import os, sys, platform
from cx_Freeze import setup, Executable

# 需要额外拷贝的文件夹/文件
include_files = [
    "chat_histories",
    "LOG",
    "upload_cache",
]

# Windows 下把 tesseract 可执行文件也带上（如已装）
TESSERACT_DIR = None
if platform.system() == "Windows":
    # 常见安装路径，按需改
    tesseract = r"C:\Program Files\Tesseract-OCR"
    if os.path.isdir(tesseract):
        include_files.append((tesseract, "Tesseract-OCR"))

build_exe_options = {
    # 必须显式包含的 Python 包
    "packages": [
        "os", "sys", "logging", "json", "pathlib", "datetime",
        "ollama",                   # ① 关键
        "pandas", "bs4", "docx", "pypdf",
        "zipfile", "py7zr", "rarfile",
        "PIL",                      # ③ 关键
        "pytesseract",
    ],
    "include_files": include_files,
    "excludes": [
        "tkinter",                  # 不需要
        "streamlit", "streamlit.*"  # ④ 排除
    ],
    "include_msvcr": True,
}

base = None
if sys.platform == "win32":
    base = "Win32GUI"

setup(
    name="AI-Chat-Assistant",
    version="1.0",
    description="本地AI聊天助手",
    options={"build_exe": build_exe_options},
    executables=[Executable("ui_desktop.py", base=base)]
)