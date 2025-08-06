# file_loader.py —— 文件缓存 + 哈希索引
import contextlib
import hashlib, pathlib, functools, sqlite3, json
from typing import List, Dict, Optional
from logger import get_logger
import datetime
from logger import get_logger
import time, threading


_lock = threading.Lock()

import json, pathlib, hashlib
from typing import List, Dict

from config_helper import UPLOAD_DIR

logger = get_logger(__name__)


class FileManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._file_lock = threading.RLock()
        return cls._instance

    @contextlib.contextmanager
    def file_transaction(self):
        """提供线程安全的文件操作上下文"""
        with self._file_lock:
            temp_dir = pathlib.Path("temp_transactions")
            temp_dir.mkdir(exist_ok=True)
            try:
                yield temp_dir
            finally:
                # 清理临时文件
                for f in temp_dir.glob("*"):
                    try:
                        f.unlink()
                    except:
                        pass



@functools.lru_cache(maxsize=512)
def _content_hash(content: bytes) -> str:
    """带缓存的哈希计算，增加日志"""
    logger.debug(f"[_content_hash] 计算内容哈希，长度: {len(content)} bytes")
    h = hashlib.sha256(content).hexdigest()
    logger.debug(f"[_content_hash] 哈希结果: {h[:8]}...")
    return h

@functools.lru_cache(maxsize=128)
def _file_hash(path: str) -> str:
    """带缓存的文件哈希计算，优化分块读取"""
    logger.info(f"[_file_hash] 开始计算文件哈希: {path}")
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):  # 1MB chunks
                h.update(chunk)
        digest = h.hexdigest()
        logger.info(f"[_file_hash] 完成哈希计算: {path} -> {digest[:8]}...")
        return digest
    except Exception as e:
        logger.error(f"[_file_hash] 计算失败: {path}, 错误: {e}")
        raise


class FileEntry:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.hash = _file_hash(str(path))

    @property
    def text(self) -> str:
        from tools import extract_text
        return extract_text(self.path)

class ChatSearcher:
    """
    基于 SQLite FTS5 的全文搜索
    """
    def __init__(self, db_path: str = "chat_index.db"):
        self.db_path = pathlib.Path(db_path)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    history_id TEXT,
                    role TEXT,
                    content TEXT,
                    timestamp TEXT
                )
            """)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                USING fts5(content, tokenize='porter unicode61')
            """)

    def index_history(self, history_id: str, messages: List[Dict]):
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO messages(history_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                [(history_id, m["role"], m["content"], m.get("timestamp", "")) for m in messages]
            )
            conn.executemany(
                "INSERT INTO messages_fts(content) VALUES (?)",
                [(m["content"],) for m in messages]
            )

    def search(self, query: str, limit: int = 10) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("""
                SELECT history_id, role, content, timestamp
                FROM messages
                JOIN messages_fts ON messages.rowid = messages_fts.rowid
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (query, limit))
            return [
                {"history_id": row[0], "role": row[1], "content": row[2], "timestamp": row[3]}
                for row in cur.fetchall()
            ]


import json, pathlib, hashlib
from typing import List, Dict

UPLOAD_STATE_FILE = pathlib.Path("upload_cache/.state.json")


def load_uploaded_files() -> List[Dict]:
    logger.info("[load_uploaded_files] 开始加载上传文件")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    files = []
    for fp in sorted(UPLOAD_DIR.iterdir(), key=lambda f: f.stat().st_mtime):
        if fp.is_file() and not fp.name.startswith('.'):
            try:
                content = fp.read_bytes()
                files.append({
                    "filename": fp.name,
                    "path": str(fp),
                    "key": _content_hash(content),
                    "content": content,
                    "size": len(content),
                    "timestamp": datetime.datetime.fromtimestamp(fp.stat().st_mtime).isoformat()
                })
            except Exception as e:
                logger.error(f"[load_uploaded_files] 加载文件失败: {fp.name}, 错误: {e}")

    logger.info(f"[load_uploaded_files] 扫描完成，共加载 {len(files)} 个文件")
    return files


def save_uploaded_files(files: List[Dict]) -> None:
    """保存上传状态；确保路径一致，避免误过滤"""
    temp_file = UPLOAD_STATE_FILE.with_suffix('.tmp')
    serializable = []

    logger.info(f"[save_uploaded_files] 收到待保存列表，共 {len(files)} 条记录")

    for idx, f in enumerate(files, 1):
        try:
            # 统一为绝对路径
            raw_path = pathlib.Path(f["path"])
            file_path = raw_path if raw_path.is_absolute() else (UPLOAD_DIR / raw_path.name).resolve()
            logger.info(f"[save_uploaded_files] 处理第 {idx} 条记录 -> 绝对路径: {file_path}")

            # 只要记录里有 content 就保留，防止误过滤
            size = len(f.get("content", b'')) if not file_path.exists() else file_path.stat().st_size
            logger.info(f"[save_uploaded_files] 文件大小: {size} bytes")

            record = {
                "filename": f["filename"],
                "path": str(file_path),
                "key": f.get("key", ""),
                "size": size
            }
            serializable.append(record)
            logger.info(f"[save_uploaded_files] 加入写盘列表: {record}")
        except Exception as e:
            logger.error(f"[save_uploaded_files] 处理第 {idx} 条记录异常: {e}")

    logger.info(f"[save_uploaded_files] 最终写入 .state.json 的文件数: {len(serializable)}")
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
        if UPLOAD_STATE_FILE.exists():
            UPLOAD_STATE_FILE.unlink()
        temp_file.rename(UPLOAD_STATE_FILE)
        logger.info(f"[save_uploaded_files] 状态文件已保存: {UPLOAD_STATE_FILE}")
    except Exception as e:
        logger.error(f"[save_uploaded_files] 保存失败: {e}")
        if temp_file.exists():
            temp_file.unlink()


def save_file_to_disk(file_data: dict) -> pathlib.Path:
    logger.info(f"[save_file_to_disk] 开始保存文件: {file_data['filename']}")

    with _lock:
        try:
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            file_path = UPLOAD_DIR / file_data['filename']

            # 写入文件内容
            with open(file_path, 'wb') as f:
                f.write(file_data['content'])

            logger.info(f"[save_file_to_disk] 文件保存成功: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"[save_file_to_disk] 保存失败: {e}")
            raise


def load_all_uploaded_files() -> list[dict]:
    """返回 upload_cache 目录下所有文件（按修改时间排序）"""
    files = []
    for fp in sorted(UPLOAD_DIR.iterdir(), key=lambda f: f.stat().st_mtime):
        if fp.is_file():
            files.append({
                "filename": fp.name,
                "content": fp.read_bytes(),
                "path": str(fp)
            })
    return files

def remove_file_from_disk(file_path: str | pathlib.Path) -> bool:
    """从磁盘删除文件"""
    file_path = pathlib.Path(file_path)
    if file_path.exists():
        file_path.unlink()
        logger.info(f"已删除文件: {file_path}")
        return True
    logger.warning(f"[remove_file_from_disk] 文件不存在: {file_path}")
    return False


def add_or_replace_file_with_content(
        new_file: Dict,
        existing: List[Dict],
        check_content: bool = True
) -> List[Dict]:
    """独立的方法，不依赖 session_state"""
    logger.info(f"[add_or_replace_file_with_content] 处理文件: {new_file.get('filename')}")

    if check_content and 'key' in new_file:
        # 检查是否已存在相同内容的文件
        if any(f.get('key') == new_file['key'] for f in existing):
            logger.info("[add_or_replace_file_with_content] 跳过重复内容文件")
            return existing

    # 处理同名文件
    return [f for f in existing if f['filename'] != new_file['filename']] + [new_file]


def add_or_replace_file_with_content_batch(
        new_files: List[Dict],
        existing: List[Dict]
) -> List[Dict]:
    """
    批量追加或替换文件，逻辑：
    1. 按内容哈希去重（内容相同直接跳过）
    2. 按文件名冲突时，仅替换内容不同的同名文件
    3. 保留其余所有文件
    """
    logger.info(f"[add_or_replace_file_with_content_batch] 收到 {len(new_files)} 个新文件")
    logger.debug(f"[add_or_replace_file_with_content_batch] 新文件名列表: {[f['filename'] for f in new_files]}")
    logger.debug(f"[add_or_replace_file_with_content_batch] 现有文件数: {len(existing)}")

    # 用哈希做内容去重键
    content_map = {f['key']: f for f in existing}
    logger.debug(f"[add_or_replace_file_with_content_batch] 现有内容哈希: {list(content_map.keys())}")

    result = existing.copy()   # 先复制一份，避免直接修改原列表
    logger.debug(f"[add_or_replace_file_with_content_batch] 复制现有文件列表，当前长度: {len(result)}")

    for new_file in new_files:
        filename = new_file['filename']
        file_key = new_file['key']
        logger.info(f"[add_or_replace_file_with_content_batch] 开始处理新文件: {filename} (key={file_key[:8]}...)")

        # 1. 内容哈希已存在 → 直接跳过
        if file_key in content_map:
            logger.info(f"[add_or_replace_file_with_content_batch] 内容已存在，跳过: {filename}")
            continue

        # 2. 内容不同但文件名相同 → 仅替换同名文件
        same_name_indices = [i for i, f in enumerate(result) if f['filename'] == filename]
        if same_name_indices:
            logger.info(f"[add_or_replace_file_with_content_batch] 发现同名文件（内容不同）: {filename}，准备替换")
            # 删除所有同名文件（它们的内容哈希一定不同，否则上一步已跳过）
            for idx in sorted(same_name_indices, reverse=True):
                removed = result.pop(idx)
                logger.debug(f"[add_or_replace_file_with_content_batch] 移除旧文件: {removed['filename']} (key={removed['key'][:8]}...)")
        else:
            logger.debug(f"[add_or_replace_file_with_content_batch] 无同名冲突，直接追加: {filename}")

        # 3. 追加新文件
        result.append(new_file)
        content_map[file_key] = new_file
        logger.info(f"[add_or_replace_file_with_content_batch] 已追加新文件: {filename}")

    logger.info(f"[add_or_replace_file_with_content_batch] 最终文件列表长度: {len(result)}")
    logger.debug(f"[add_or_replace_file_with_content_batch] 最终文件名列表: {[f['filename'] for f in result]}")
    return result


def check_uploaded_files(uploaded_files: List[Dict]) -> str:
    """检查已上传文件状态"""
    logger.info(f"[check_uploaded_files] 开始检查 {len(uploaded_files)} 个文件")

    if not uploaded_files:
        logger.warning("[check_uploaded_files] 上传文件列表为空")
        return "未收到任何文件"

    # 验证文件实际存在
    valid_files = []
    for f in uploaded_files:
        file_path = pathlib.Path(f.get('path', ''))
        if not file_path.is_absolute():
            file_path = UPLOAD_DIR / file_path

        logger.info(f"[check_uploaded_files] 检查文件路径: {file_path}")

        if file_path.exists() and file_path.is_file():
            try:
                size = file_path.stat().st_size
                valid_files.append({
                    'filename': file_path.name,
                    'path': str(file_path),
                    'size': size
                })
                logger.info(f"[check_uploaded_files] 文件有效: {file_path.name} ({size} bytes)")
            except Exception as e:
                logger.error(f"[check_uploaded_files] 获取文件信息失败: {file_path}, 错误: {e}")
        else:
            logger.warning(f"[check_uploaded_files] 文件不存在或无效: {file_path}")

    if not valid_files:
        logger.error("[check_uploaded_files] 所有上传文件均无效")
        return "文件已丢失，请重新上传"

    logger.info(f"[check_uploaded_files] 有效文件数: {len(valid_files)}")
    file_list = "\n".join([f"- {f['filename']} ({f['size']} bytes)" for f in valid_files])
    return f"已收到 {len(valid_files)} 个文件：\n{file_list}"

