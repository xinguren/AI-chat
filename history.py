import datetime, json, pathlib
from typing import List, Dict, Any
from config_helper import ModelConfig

HISTORY_DIR = pathlib.Path("chat_histories")
HISTORY_DIR.mkdir(exist_ok=True)

class History:
    def __init__(self, name: str):
        self.name = name
        self.file = HISTORY_DIR / f"{name}.json"

    @classmethod
    def auto_name(cls, messages: List[Dict]) -> str:
        first = next((m["content"] for m in messages if m["role"] == "user"), "chat")
        safe = "".join(c for c in first[:30] if c.isalnum() or c in " -_")
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{ts}_{safe}"

    def save(self, messages: List[Dict], file_context: str, cfg: ModelConfig):
        self.file.write_text(
            json.dumps(
                {
                    "meta": {
                        "created_at": datetime.datetime.now().isoformat(),
                        "config": cfg.model_dump(),
                    },
                    "messages": messages,
                    "file_context": file_context,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def load(self) -> Dict[str, Any]:
        if not self.file.exists():
            return {"messages": [], "file_context": "", "meta": {}}
        return json.loads(self.file.read_text(encoding="utf-8"))