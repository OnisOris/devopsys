
from __future__ import annotations
import time, subprocess
from typing import Iterable
from ..telegram.bot import TelegramClient
from ..config import Config

def _capture(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, timeout=10)
    except Exception as e:
        return f"<error: {e}>"

def snapshot(sources: Iterable[str]) -> str:
    parts = []
    for s in sources:
        s = s.strip()
        if s == "top":
            parts.append("### top -b -n1\n```\n" + _capture(["bash","-lc","COLUMNS=120 top -b -n1 | head -n 30"]) + "\n```")
        elif s == "df":
            parts.append("### df -h\n```\n" + _capture(["df", "-h"]) + "\n```")
        elif s == "vmstat":
            parts.append("### vmstat 1 5\n```\n" + _capture(["vmstat","1","5"]) + "\n```")
        elif s == "free":
            parts.append("### free -h\n```\n" + _capture(["free","-h"]) + "\n```")
        elif s == "uptime":
            parts.append("### uptime\n```\n" + _capture(["uptime"]) + "\n```")
    return "\n\n".join(parts) if parts else "<no sources>"

def monitor_loop(cfg: Config) -> None:
    tg = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)
    while True:
        msg = snapshot(cfg.monitor_sources)
        tg.send_message("Мониторинг:\n" + msg)
        time.sleep(cfg.monitor_interval_sec)
