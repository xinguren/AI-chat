import json
import pathlib
from typing import Dict, Any
from pydantic import BaseModel, Field, validator
from logger import get_logger
from tools import logger

CONFIG_FILE = pathlib.Path("model_config.json")
MODEL_CONFIG_FILE = pathlib.Path("model_config.json")

DEFAULT_CONFIG = {
    "temperature": 0.7,
    "top_p": 0.9,
    "repeat_penalty": 1.1,
    "num_ctx": 2048,
    "seed": -1
}

UPLOAD_DIR  = pathlib.Path("upload_cache")
HISTORY_DIR = pathlib.Path("chat_histories")
LOG_DIR     = pathlib.Path("LOG")


class ModelConfig(BaseModel):
    temperature: float = Field(0.7, ge=0.1, le=2.0,
                               description="控制输出的随机性，值越高输出越随机")
    top_p: float = Field(0.9, ge=0.1, le=1.0,
                         description="核采样概率阈值，影响输出的多样性")
    repeat_penalty: float = Field(1.1, ge=1.0, le=2.0,
                                  description="重复惩罚因子，减少重复内容")
    num_ctx: int = Field(2048, ge=512, le=128000,
                         description="上下文窗口大小")
    seed: int = Field(-1, description="随机种子，-1表示随机")

    @validator('temperature')
    def round_temp(cls, v):
        return round(v, 1)

    @classmethod
    def load(cls) -> "ModelConfig":
        if CONFIG_FILE.exists():
            try:
                return cls(**json.loads(CONFIG_FILE.read_text()))
            except Exception as e:
                logger.warning(f"加载配置失败，使用默认值: {e}")
        return cls()

    def save(self) -> None:
        CONFIG_FILE.write_text(
            self.json(indent=2, exclude_unset=True),
            encoding="utf-8"
        )

    def update(self, **kwargs) -> None:
        for k, v in kwargs.items():
            if k in self.__fields__:
                setattr(self, k, v)
        self.save()


def get_config() -> Dict[str, Any]:
    """获取当前配置字典"""
    return ModelConfig.load().dict()


def update_config(**kwargs) -> bool:
    """更新配置项"""
    try:
        config = ModelConfig.load()
        config.update(**kwargs)
        return True
    except Exception as e:
        logger.error(f"更新配置失败: {e}")
        return False


def load_model_config() -> Dict:
    if MODEL_CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(MODEL_CONFIG_FILE.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_model_config(cfg: Dict) -> None:
    MODEL_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")