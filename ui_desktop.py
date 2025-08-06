# ui_desktop.py  ——  响应式三栏布局 + 主题切换 + 固定输入
import json, pathlib, datetime, hashlib, sys, socket
from typing import List, Dict
from file_loader import (
    remove_file_from_disk,
    add_or_replace_file_with_content_batch as add_file_batch,
    load_uploaded_files,
    save_uploaded_files,
    check_uploaded_files, add_or_replace_file_with_content_batch
)
from config_helper import UPLOAD_DIR
from threading import Lock
import ollama
from pywebio import start_server
from pywebio.output import *
from pywebio.input import *
from pywebio.pin import *
from pywebio.session import run_js
import time
from pywebio.pin import pin_on_change

from tools import extract_text
from config_helper import load_model_config, save_model_config
from history_helper import (
    save_history, load_history, list_histories,
    delete_history, rename_history, export_history, new_conversation
)
from file_loader import _content_hash, save_file_to_disk, add_or_replace_file_with_content
from logger import get_logger

# ---------- 常量 ----------
SUPPORTED_TYPES = [
    "txt","md","py","json","csv","pdf","docx","html","htm","php","js","css","xlsx",
    "jpg","jpeg","png","zip","rar","7z","tar","gz"
]

logger = get_logger(__name__)

# ---------- 全局 ----------
session_state = {
    "messages": [],
    "uploaded_files": load_uploaded_files(),
    "model_config": load_model_config(),
    "theme": "light",
    "_first_run": True          # 新增
}


# ---------- 工具 ----------
def find_free_port(start: int = 8080, max_tries: int = 100) -> int:
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise RuntimeError("未找到可用端口")

# ---------- 主题 ----------
def toggle_theme():
    session_state["theme"] = "dark" if session_state["theme"] == "light" else "light"
    run_js(f'document.body.className="{session_state["theme"]}"')


def handle_file_upload(file_list):
    logger.info(f"[handle_file_upload] 收到文件列表: {file_list}")
    files = file_list or []
    files = pin.files
    logger.info(f"[handle_file_upload] 开始处理，收到文件数: {len(files) if files else 0}")

    if not files:
        logger.warning("[handle_file_upload] 上传文件列表为空")
        toast("请先选择要上传的文件", color="warn")
        return

    current_files = session_state.get("uploaded_files", [])
    logger.info(f"[handle_file_upload] 当前已有文件数: {len(current_files)}")
    logger.debug(f"[handle_file_upload] 当前文件列表: {[f['filename'] for f in current_files]}")

    success_count = 0
    new_files = []
    for file_data in files:
        try:
            filename = file_data['filename']
            content = file_data['content']
            logger.info(f"[handle_file_upload] 处理文件: {filename} (大小: {len(content)} bytes)")

            # 计算内容哈希
            file_hash = _content_hash(content)
            logger.info(f"[handle_file_upload] 文件哈希: {filename} -> {file_hash[:8]}...")

            # 检查是否已存在相同内容文件
            if any(f.get('key') == file_hash for f in current_files):
                logger.info(f"[handle_file_upload] 跳过重复内容文件: {filename}")
                continue

            saved_path = save_file_to_disk({'filename': filename, 'content': content})
            logger.info(f"[handle_file_upload] 文件落盘成功: {saved_path}")

            file_record = {
                'filename': saved_path.name,
                'path': str(saved_path),
                'key': file_hash,
                'content': content,
                'size': len(content),
                'timestamp': datetime.datetime.now().isoformat()
            }
            new_files.append(file_record)
            success_count += 1
            logger.info(f"[handle_file_upload] 成功处理文件: {filename}")
        except Exception as e:
            logger.error(f"[handle_file_upload] 处理文件失败: {filename}, 错误: {e}")
            put_error(f"上传失败: {filename} ({str(e)})")

    if not new_files:
        logger.warning("[handle_file_upload] 没有新文件需要添加")
        toast("没有新文件被上传", color="warn")
        return

    # 合并文件列表
    merged_files = add_or_replace_file_with_content_batch(new_files, current_files)
    session_state["uploaded_files"] = merged_files
    save_uploaded_files(session_state["uploaded_files"])
    logger.info(f"[handle_file_upload] 合并后文件总数: {len(merged_files)}")
    logger.debug(f"[handle_file_upload] 合并后文件列表: {[f['filename'] for f in merged_files]}")

    # 更新状态
    session_state["uploaded_files"] = merged_files
    save_uploaded_files(session_state["uploaded_files"])
    logger.info("[handle_file_upload] 已保存上传文件状态")

    refresh_file_tags()
    toast(f"成功上传 {success_count}/{len(files)} 个文件", color="success")

    # 必须在所有操作完成后重置上传控件
    pin.files = []
    logger.info("[handle_file_upload] 已重置上传控件")

# ---------- 左侧历史栏 ----------
def history_panel():
    with use_scope("history_panel"):
        put_markdown("### 📚 历史")
        put_input("history_filter", placeholder="关键词过滤…")
        put_scope("history_list")
        put_button("管理", onclick=history_manage_popup, small=True)
    refresh_history_list()

def refresh_history_list():
    q = pin.history_filter or ""
    df = list_histories(query=q)
    with use_scope("history_list", clear=True):
        if df.empty:
            put_text("暂无记录")
        else:
            for _, row in df.iterrows():
                put_button(
                    row["name"],
                    onclick=lambda n=row["name"]: load_history_click(n),
                    small=True,
                    outline=True
                )

def load_history_click(name: str):
    data = load_history(name)
    session_state["messages"] = data["messages"]
    session_state["model_config"] = data["meta"].get("config", load_model_config())
    # 不再重置 uploaded_files，保留当前上传的文件
    refresh_chat_area()
    toast(f"已加载：{name}")


def history_manage_popup():
    df = list_histories()
    if df.empty:
        toast("无历史记录"); return
    names = df["name"].tolist()
    opt = select("选择会话", names)
    action = actions("操作", ["重命名","删除","导出","关闭"])
    if action=="关闭": return
    if action=="重命名":
        new = input("新名称", value=opt)
        if rename_history(opt, new):
            toast("已重命名"); refresh_history_list()
    elif action=="删除":
        if delete_history(opt):
            toast("已删除"); refresh_history_list()
    elif action=="导出":
        fmt = select("格式", ["json","txt","md"])
        path = export_history(opt, fmt=fmt)
        toast(f"已导出：{path}")

# ---------- 顶部栏 ----------
# ---------- 顶部栏 ----------
def header_bar():
    put_row([
        put_text(f"当前会话: {session_state.get('current_session_id', '新会话')}"),
    ])
    try:
        models = [m.get("name") or m.get("model") for m in ollama.list().get("models", [])]
        ollama_ok = True
    except Exception as e:
        ollama_ok = False
        put_error(f"⚠️ 获取模型失败：{e}，请启动 Ollama 或检查服务状态")
        models = []

    if ollama_ok and models:
        cfg = session_state["model_config"]
        put_row([
            put_select("model", label="模型", options=models, value=cfg.get("model", models[0])),
            put_collapse("⚙️ 高级", [
                put_slider("temp", label="Temperature", value=cfg["temperature"],
                           min_value=0.1, max_value=2.0, step=0.1),
                put_slider("top_p", label="Top-P", value=cfg["top_p"],
                           min_value=0.1, max_value=1.0, step=0.1),
                put_slider("repeat", label="Repeat", value=cfg["repeat_penalty"],
                           min_value=1.0, max_value=2.0, step=0.1),
            ], open=False),
            put_button("🆕 新会话", onclick=new_conversation_click, color="success", small=True),
            put_button("🗑️ 清空", onclick=clear_chat, small=True),
            put_button("🌗", onclick=toggle_theme, small=True)
        ])
    else:
        put_row([
            put_button("▶️ 启动 Ollama", onclick=start_ollama_service, color="success"),
            put_button("🔄 重试", onclick=refresh_page, small=True),
            put_button("🌗", onclick=toggle_theme, small=True)
        ])


# 在 ui_desktop.py 中修改以下方法

def new_conversation_click():
    """点击"新会话"按钮后的动作"""
    # 调用历史管理模块创建新会话
    new_id = new_conversation()

    # 重置会话状态
    session_state["messages"] = []
    session_state["uploaded_files"] = []
    session_state["current_session_id"] = new_id

    # 保存状态
    save_uploaded_files([])

    # 刷新UI
    refresh_chat_area()
    refresh_file_tags()
    refresh_history_list()

    toast(f"已创建新会话: {new_id}", color="success")

def start_ollama_service():
    """尝试启动 Ollama 服务"""
    import subprocess, platform, os, sys
    try:
        system = platform.system()
        if system == "Windows":
            subprocess.Popen(["ollama", "serve"], creationflags=subprocess.DETACHED_PROCESS)
        else:
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        toast("正在启动 Ollama 服务，请稍候...", color="info")
        time.sleep(3)
        refresh_page()
    except FileNotFoundError:
        toast("未找到 ollama 命令，请先安装 Ollama 并加入系统 PATH", color="error")
    except Exception as e:
        toast(f"启动失败：{e}", color="error")

def refresh_page():
    """刷新页面"""
    run_js("window.location.reload();")

def clear_chat():
    session_state["messages"] = []
    refresh_chat_area()

# ---------- 文件上传 ----------
def upload_card():
    put_file_upload(
        "files",
        label="📁 上传附件 (支持多选)",
        accept=SUPPORTED_TYPES,
        multiple=True,
        placeholder="点击或拖拽文件到此处上传"
    )
    put_scope("upload_status")
    put_scope("file_tags")
    refresh_file_tags()

    # ✅ 监听上传事件
    pin_on_change("files", onchange=handle_file_upload)



def put_tag(param, color, closable, onclick):
    pass


def refresh_file_tags():
    from pywebio.output import put_html, put_text  # 避免循环 import

    with use_scope("file_tags", clear=True):
        fs = session_state["uploaded_files"]
        if not fs:
            put_text("🛑 当前没有加载任何文件")
            return

        # 每行：文件名 + 删除按钮
        rows = []
        for idx, f in enumerate(fs):
            size_kb = round(pathlib.Path(f['path']).stat().st_size / 1024, 1)
            rows.append(
                put_row([
                    put_text(f"{f['filename']} ({size_kb}KB)"),
                    put_button("×", onclick=lambda i=idx: delete_file_with_confirm(i),
                               small=True, outline=True, color="danger")
                ], size="auto 40px")
            )

        put_collapse(
            f"📂 已加载 {len(fs)} 个文件",
            rows,
            open=False
        )

def delete_file_with_confirm(idx: int):
    if 0 <= idx < len(session_state["uploaded_files"]):
        file_info = session_state["uploaded_files"][idx]
        if actions(f"确认删除文件「{file_info['filename']}」？", ["删除", "取消"]) == "删除":
            try:
                # 从磁盘删除
                remove_file_from_disk(file_info["path"])
                # 从 session 中移除
                session_state["uploaded_files"].pop(idx)
                save_uploaded_files(session_state["uploaded_files"])
                toast(f"已删除：{file_info['filename']}", color="success")
                refresh_file_tags()
            except Exception as e:
                logger.error(f"[delete_file_with_confirm] 删除失败: {e}")
                toast(f"删除失败：{e}", color="error")
        else:
            toast("已取消删除", color="info")

def delete_file(idx: int):
    """保留兼容接口，内部调用带确认的版本"""
    delete_file_with_confirm(idx)

# ---------- 对话区 ----------
def chat_area():
    put_scope("chat")

def refresh_chat_area():
    with use_scope("chat", clear=True):
        for m in session_state["messages"]:
            role = "👤 用户" if m["role"] == "user" else "🤖 AI"
            put_markdown(f"**{role}：** {m['content']}")
            put_html("<hr style='margin:4px 0'>")

# ---------- 发送 ----------
def on_send():
    user_text = pin.user_text.strip()
    if not user_text:
        toast("请输入内容", color="warn")
        return

    model = pin.model or ollama.list().get("models", [{}])[0].get("name", "")
    cfg = dict(temperature=pin.temp, top_p=pin.top_p, repeat_penalty=pin.repeat)
    session_state["model_config"] = cfg
    save_model_config(cfg)

    file_ctx = "\n\n".join([
        f"【{f['filename']}】\n{extract_text(pathlib.Path(f['path']))}"
        for f in session_state["uploaded_files"]
    ]).strip()
    final_prompt = f"{user_text}\n\n{file_ctx}".strip()

    session_state["messages"].append({"role": "user", "content": user_text})
    refresh_chat_area()

    reply = ""
    with use_scope("chat", clear=False):
        put_scope("thinking")
    with use_scope("thinking"):
        put_loading(color="primary")

    try:
        for chunk in ollama.chat(
            model=model,
            messages=[{"role": "user", "content": final_prompt}],
            stream=True,
            options=cfg
        ):
            reply += chunk["message"]["content"]
            with use_scope("thinking", clear=True):
                put_markdown(f"**🤖 AI：** {reply}▌")
        with use_scope("thinking", clear=True):
            put_markdown(f"**🤖 AI：** {reply}")
        session_state["messages"].append({"role": "assistant", "content": reply})
        save_history(session_state["messages"], file_ctx, cfg)
    except ollama.ResponseError as e:
        with use_scope("thinking", clear=True):
            put_error(f"模型返回错误，请检查模型是否存在: {e}")
    except ConnectionError:
        with use_scope("thinking", clear=True):
            put_error("无法连接 Ollama 服务，请确认已启动")
    except Exception as e:
        with use_scope("thinking", clear=True):
            put_error(f"调用模型失败: {e}")

    pin.user_text = ""

# ---------- 主页面 ----------
def main():
    run_js('''
document.body.className="light";
window.toggleTheme=function(){
    document.body.className=document.body.className==="light"?"dark":"light";
};
''')
    put_html("""
<style>
:root{--bg:#fff;--fg:#000;--border:#ddd;}
body.dark{--bg:#1e1e1e;--fg:#f0f0f0;--border:#444;}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,"Noto Sans",sans-serif;background:var(--bg);color:var(--fg);}
#container{height:100vh;display:flex;}
#sidebar{width:260px;border-right:1px solid var(--border);display:flex;flex-direction:column;}
#sidebar-header{padding:8px;border-bottom:1px solid var(--border);}
#sidebar-content{flex:1;overflow-y:auto;padding:8px;}
#sidebar-footer{padding:8px;border-top:1px solid var(--border);}
#main{flex:1;display:flex;flex-direction:column;}
#header{padding:8px;border-bottom:1px solid var(--border);}
#content{flex:1;overflow-y:auto;padding:8px;}
#footer{border-top:1px solid var(--border);padding:8px;}
@media(max-width:768px){#sidebar{display:none;}}
</style>
""")

    put_row([
        put_column([put_scope("sidebar")], size="260px"),
        put_column([put_scope("main")])
    ], size="260px auto").style("height:100vh;display:flex;")

    # ---------- 左侧 ----------
    with use_scope("sidebar"):
        with use_scope("sidebar-header"):
            put_markdown("### 📚 历史会话")
            put_row([
                put_input("history_filter", placeholder="关键词过滤…"),
                put_button("🔍", onclick=refresh_history_list, small=True)
            ], size="80% 20%")
        with use_scope("sidebar-content"):
            put_scope("history_list")
        with use_scope("sidebar-footer"):
            put_button("管理历史", onclick=history_manage_popup, small=True)
        refresh_history_list()

    # ---------- 右侧 ----------
    with use_scope("main"):
        with use_scope("header"):
            header_bar()
        with use_scope("content"):
            chat_area()
            upload_card()
            refresh_chat_area()
        with use_scope("footer"):
            put_row([
                put_textarea("user_text", rows=3, placeholder="请输入您的问题…"),
                put_button("发送", onclick=on_send, color="primary")
            ], size="auto 100px")

        if session_state.get("_first_run", False):
            session_state["messages"] = []
            session_state["uploaded_files"] = []
            save_uploaded_files([])
            session_state["_first_run"] = False
            # ----------------------------------------------------
        run_js(...)





if __name__ == "__main__":
    try:
        port = find_free_port(8080)
        start_server(main, port=port, host="127.0.0.1", debug=False, auto_open_webbrowser=True)
    except Exception as e:
        logger.error(f"启动失败：{e}")
        sys.exit(1)