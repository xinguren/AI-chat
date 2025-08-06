# history_helper.py  ——  会话历史 CRUD + 导入导出 + 全文搜索
import json, shutil, pathlib, datetime
import sys
from random import choices
from typing import List, Dict, Optional
import pandas as pd
from tools import logger

HISTORY_DIR = pathlib.Path("chat_histories")
HISTORY_DIR.mkdir(exist_ok=True)

# ---------- 基础 ----------
def _sanitize(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " -_")[:50]

def generate_history_name(messages: List[Dict]) -> str:
    """唯一历史记录名称"""
    first = next((m["content"] for m in messages if m["role"] == "user"), "对话")
    safe = _sanitize(first)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    random_str = ''.join(choices('abcdefghijklmnopqrstuvwxyz1234567890', k=4))  # ② 直接调用
    return f"{ts}_{safe}_{random_str}"

# ---------- CRUD ----------
def save_history(messages: List[Dict],
                 file_context: str,
                 model_config: Dict,
                 name: Optional[str] = None) -> str:
    """原子化保存历史记录"""
    if not messages:
        return ""

    name = name or generate_history_name(messages)
    temp_file = HISTORY_DIR / f".{name}.tmp"
    data = {
        "meta": {
            "created_at": datetime.datetime.now().isoformat(),
            "config": model_config
        },
        "messages": messages,
        "file_context": file_context
    }

    try:
        # 先写入临时文件
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 原子替换
        target_file = HISTORY_DIR / f"{name}.json"
        if sys.platform == 'win32':
            # Windows需要特殊处理
            if target_file.exists():
                target_file.unlink()
            temp_file.rename(target_file)
        else:
            # POSIX系统可以直接替换
            temp_file.replace(target_file)

        return name
    except Exception as e:
        logger.error(f"保存历史失败: {e}")
        if temp_file.exists():
            temp_file.unlink()
        raise


def new_conversation() -> str:
    """
    创建新会话并返回会话ID
    1. 自动保存当前会话(如果有)
    2. 生成新的空会话记录
    """
    from ui_desktop import session_state

    # 如果有消息，先保存当前会话
    if session_state.get("messages"):
        try:
            file_ctx = "\n\n".join([
                f"【{f['filename']}】\n{extract_text(pathlib.Path(f['path']))}"
                for f in session_state.get("uploaded_files", [])
            ]).strip()

            save_history(
                messages=session_state["messages"],
                file_context=file_ctx,
                model_config=session_state.get("model_config", {})
            )
            logger.info("已自动保存当前会话")
        except Exception as e:
            logger.error(f"自动保存会话失败: {e}")

    # 生成新的会话ID
    new_id = generate_history_name([])
    logger.info(f"创建新会话: {new_id}")
    return new_id

def load_history(name: str) -> Dict:
    file = HISTORY_DIR / f"{name}.json"
    if file.exists():
        return json.loads(file.read_text(encoding="utf-8"))
    return {"messages": [], "file_context": "", "meta": {}}

def list_histories(query: str = "", limit: int = 50) -> pd.DataFrame:
    rows = []
    files = sorted(HISTORY_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    for f in files:
        try:
            meta = json.loads(f.read_text())["meta"]
            rows.append({"name": f.stem, "created": meta.get("created_at", "")})
        except Exception:
            rows.append({"name": f.stem, "created": ""})
    df = pd.DataFrame(rows)
    if query:
        df = df[df["name"].str.contains(query, case=False)]
    return df

def delete_history(name: str) -> bool:
    file = HISTORY_DIR / f"{name}.json"
    if file.exists():
        file.unlink()
        return True
    return False

def rename_history(old: str, new: str) -> bool:
    old_file = HISTORY_DIR / f"{old}.json"
    new_file = HISTORY_DIR / f"{_sanitize(new)}.json"
    if old_file.exists() and not new_file.exists():
        old_file.rename(new_file)
        return True
    return False

# ---------- 导入/导出 ----------
def export_history(name: str, fmt: str = "json") -> pathlib.Path:
    data = load_history(name)
    if fmt == "json":
        path = HISTORY_DIR / f"{name}.json"
        return path
    elif fmt == "txt":
        path = HISTORY_DIR / f"{name}.txt"
        lines = [f"{m['role']}: {m['content']}" for m in data["messages"]]
        path.write_text("\n".join(lines), encoding="utf-8")
    elif fmt == "md":
        path = HISTORY_DIR / f"{name}.md"
        lines = [f"**{m['role'].capitalize()}**: {m['content']}\n" for m in data["messages"]]
        path.write_text("\n".join(lines), encoding="utf-8")
    else:
        raise ValueError("fmt must be json/txt/md")
    return path

def import_history(file_path: pathlib.Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".json":
        data = json.loads(file_path.read_text(encoding="utf-8"))
    elif suffix in {".txt", ".md"}:
        # 简易文本导入：按行切割，> 角色: 内容
        messages = []
        for line in file_path.read_text(encoding="utf-8").splitlines():
            if ":" in line:
                role, content = line.split(":", 1)
                messages.append({"role": role.strip().lower(), "content": content.strip()})
        data = {"messages": messages, "file_context": "", "meta": {}}
    else:
        raise ValueError("仅支持 json/txt/md")
    return save_history(data["messages"], data["file_context"], data["meta"].get("config", {}))