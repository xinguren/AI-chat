import logging, pathlib, datetime, sys
import os

LOG_DIR = pathlib.Path("LOG")
LOG_DIR.mkdir(exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    """改进的日志系统，解决级别冲突和权限问题"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    # 确保日志目录存在
    LOG_DIR.mkdir(mode=0o755, exist_ok=True)

    # 唯一日志文件名
    log_file = LOG_DIR / f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}.log"

    # 文件处理器
    file_handler = logging.FileHandler(
        log_file,
        encoding='utf-8',
        mode='a'
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
    )

    # 根据环境设置级别
    log_level = logging.DEBUG if os.getenv('DEBUG') else logging.INFO
    logger.setLevel(log_level)

    # 添加处理器
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # 防止日志重复
    logger.propagate = False

    return logger