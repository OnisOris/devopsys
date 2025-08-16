
from __future__ import annotations
import logging, sys
from .telegram.bot import TelegramClient
from .config import Config

_LOGGER_INITIALIZED = False

def init_logging(cfg: Config) -> logging.Logger:
    global _LOGGER_INITIALIZED
    logger = logging.getLogger("devopsys")
    if _LOGGER_INITIALIZED:
        return logger
    logger.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))

    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    if cfg.log_console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        ch.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))
        logger.addHandler(ch)
    if cfg.log_file:
        try:
            fh = logging.FileHandler(cfg.log_file, encoding="utf-8")
            fh.setFormatter(fmt)
            fh.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))
            logger.addHandler(fh)
        except Exception as e:
            logger.warning("Failed to open log file %s: %s", cfg.log_file, e)
    _LOGGER_INITIALIZED = True
    logger.debug("Logging initialized (level=%s, file=%s, console=%s, to_telegram=%s)",
                 cfg.log_level, cfg.log_file, cfg.log_console, cfg.log_to_telegram)
    return logger

def log_event(cfg: Config, level: int, msg: str):
    logger = logging.getLogger("devopsys")
    logger.log(level, msg)
    if cfg.log_to_telegram:
        try:
            tg = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)
            tg.send_message(f"[log] {msg}")
        except Exception:
            pass
