# file_tools.py  ——  统一文件解析 + 超大文件保护 + 批量处理
import contextlib
import io, json, zipfile, tarfile, logging, pathlib, hashlib, os
from typing import Union, BinaryIO, List, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pypdf, docx, rarfile, py7zr
from bs4 import BeautifulSoup
from PIL import Image
import pytesseract

logger = logging.getLogger(__name__)

# 50 MB 阈值
MAX_BYTES = 50 * 1024 * 1024
SUPPORTED_TYPES = {
    "txt", "md", "py", "json", "csv", "pdf", "docx",
    "html", "htm", "php", "js", "css", "xlsx",
    "jpg", "jpeg", "png", "zip", "rar", "7z", "tar", "gz"
}

# ---------- 基础工具 ----------
def _safe_decode(data: bytes) -> str:
    for enc in ("utf-8", "gbk", "latin1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")

# ---------- 超大文件分片 ----------
def _extract_large_file(fp: BinaryIO, file_name: str, chunk_size: int = 1 << 16) -> str:
    """逐块读取文本文件，防止一次性加载内存爆炸"""
    parts, total = [], 0
    while True:
        chunk = fp.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        parts.append(_safe_decode(chunk))
        if total > 50 * 1024 * 1024:          # 50 MB 文本直接截断
            parts.append("\n[内容过长，已截断]")
            break
    return "".join(parts)

# ---------- 核心提取 ----------
def extract_text(file_obj: Union[BinaryIO, str, pathlib.Path],
                 file_name: str = None,
                 ocr_lang: str = "chi_sim+eng") -> str:
    """改进后的文件提取方法，增加安全检查和资源管理"""
    close_after = False
    try:
        if isinstance(file_obj, (str, pathlib.Path)):
            file_path = pathlib.Path(file_obj)
            file_name = file_path.name
            if not file_path.exists():
                return f"[错误] 文件不存在: {file_path}"
            fp = open(file_path, "rb")
            close_after = True
        else:
            fp = file_obj
            if not hasattr(fp, 'read'):
                return "[错误] 无效的文件对象"
            if file_name is None and hasattr(fp, "name"):
                file_name = getattr(fp, "name", "unknown")

        suffix = pathlib.Path(file_name or "").suffix.lower()

        # 安全检查文件大小
        if hasattr(fp, 'seekable') and fp.seekable():
            fp.seek(0, os.SEEK_END)
            size = fp.tell()
            fp.seek(0)
            if size > MAX_BYTES and suffix not in {".zip", ".rar", ".7z", ".tar", ".gz"}:
                return _extract_large_file(fp, file_name)
        else:
            logger.warning(f"无法检查文件大小: {file_name}")

        # 使用上下文管理器处理需要关闭的资源
        if suffix == ".pdf":
            with contextlib.closing(pypdf.PdfReader(fp)) as reader:
                return "\n".join(p.extract_text() or "" for p in reader.pages)

        if suffix == ".docx":
            with contextlib.closing(docx.Document(fp)) as doc:
                return "\n".join(p.text for p in doc.paragraphs)

        if suffix == ".csv":
            return pd.read_csv(fp).to_string(index=False)

        if suffix == ".json":
            data = json.load(fp)
            return json.dumps(data, ensure_ascii=False, indent=2)

        if suffix in {".html", ".htm", ".php"}:
            soup = BeautifulSoup(fp.read(), "lxml")
            return soup.get_text(separator="\n", strip=True)

        if suffix in {".jpg", ".jpeg", ".png"}:
            image = Image.open(fp)
            return pytesseract.image_to_string(image, lang=ocr_lang)

        if suffix in {".zip", ".rar", ".7z", ".tar", ".gz"}:
            return process_archive(fp, suffix)

        # 其它按纯文本
        return _safe_decode(fp.read())


    except Exception as e:
        logger.exception(f"读取文件失败: {file_name}")
        return f"[读取失败] {str(e)}"
    finally:
        if close_after and 'fp' in locals():
            fp.close()

# ---------- 压缩包 ----------
def process_archive(fp: BinaryIO, suffix: str) -> str:
    """改进的压缩包处理方法，增加内存保护"""
    results = []
    try:
        if suffix == ".zip":
            with contextlib.closing(zipfile.ZipFile(fp)) as z:
                for name in z.namelist():
                    if name.endswith("/") or name.startswith("__MACOSX/"):
                        continue

                    # 检查文件大小
                    info = z.getinfo(name)
                    if info.file_size > 10 * 1024 * 1024:  # 10MB限制
                        results.append(f"【{name}】\n[文件过大跳过: {info.file_size // 1024}KB]")
                        continue

                    with z.open(name) as f:
                        try:
                            content = f.read()
                            if len(content) > MAX_BYTES:
                                results.append(f"【{name}】\n[内容过大: {len(content) // 1024}KB]")
                            else:
                                text = extract_text(io.BytesIO(content), name)
                                results.append(f"【{name}】\n{text}")
                        except Exception as e:
                            results.append(f"【{name}】\n[提取失败: {str(e)}]")

        elif suffix == ".rar":
            with rarfile.RarFile(fp) as r:
                for info in r.infolist():
                    if info.isdir():
                        continue
                    text = extract_text(io.BytesIO(r.read(info)), info.filename)
                    results.append(f"【{info.filename}】\n{text}")

        elif suffix == ".7z":
            with py7zr.SevenZipFile(fp, mode="r") as z:
                for name, bio in z.readall().items():
                    text = extract_text(io.BytesIO(bio.read()), name)
                    results.append(f"【{name}】\n{text}")

        elif suffix in {".tar", ".gz"}:
            with tarfile.open(fileobj=fp) as t:
                for m in t.getmembers():
                    if m.isfile():
                        f = t.extractfile(m)
                        text = extract_text(io.BytesIO(f.read()), m.name)
                        results.append(f"【{m.name}】\n{text}")
    except Exception as e:
        logger.exception("解压错误")
        return f"[解压失败] {str(e)}"

    return "\n\n".join(results) if results else "[空压缩包]"

# ---------- 批量 ----------
def batch_extract(directory: Union[str, pathlib.Path],
                  include_sub: bool = False,
                  max_workers: int = 4) -> Dict[str, str]:
    """
    批量提取目录下所有支持文件
    返回  {文件名: 内容}
    """
    directory = pathlib.Path(directory)
    files = directory.rglob("*") if include_sub else directory.iterdir()
    files = [f for f in files if f.suffix.lower().lstrip(".") in SUPPORTED_TYPES]

    def _worker(f: pathlib.Path) -> Tuple[str, str]:
        return f.name, extract_text(f)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return dict(pool.map(_worker, files))