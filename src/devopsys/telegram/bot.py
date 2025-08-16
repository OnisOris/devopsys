
from __future__ import annotations
from typing import Optional
import json, urllib.request, urllib.parse

class TelegramClient:
    def __init__(self, token: Optional[str], chat_ids: Optional[str]):
        self.token = token
        self.chat_ids = [c.strip() for c in chat_ids.split(",")] if chat_ids else []

    def _api(self, method: str, data: dict, is_json: bool = True) -> None:
        if not (self.token and self.chat_ids):
            return
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        for chat_id in self.chat_ids:
            payload = dict(chat_id=chat_id, **data)
            body = json.dumps(payload).encode("utf-8") if is_json else urllib.parse.urlencode(payload).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json" if is_json else "application/x-www-form-urlencoded"})
            try:
                with urllib.request.urlopen(req, timeout=10) as _:
                    pass
            except Exception:
                pass

    def _http_get(self, method: str, params: dict) -> dict | None:
        if not self.token:
            return None
        q = urllib.parse.urlencode(params)
        url = f"https://api.telegram.org/bot{self.token}/{method}?{q}"
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                data = resp.read().decode("utf-8")
                return json.loads(data)
        except Exception:
            return None

    def send_message(self, text: str) -> None:
        self._api("sendMessage", {"text": text, "disable_web_page_preview": True, "parse_mode": "Markdown"})

    def send_document_bytes(self, filename: str, content: bytes) -> None:
        try:
            text = content.decode("utf-8")
        except Exception:
            text = "<binary>"
        self.send_message(f"Документ {filename} (превью):\n" + text[:3500])

    def send_inline_keyboard(self, text: str, buttons: list[list[dict]]) -> None:
        self._api("sendMessage", {"text": text, "reply_markup": {"inline_keyboard": buttons}, "parse_mode": "Markdown"})

    def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        self._api("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

    def edit_message_text(self, chat_id: str, message_id: int, text: str) -> None:
        self._api("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"})

    def set_webhook(self, url: str) -> None:
        if not self.token:
            return
        api = f"https://api.telegram.org/bot{self.token}/setWebhook"
        data = {"url": url}
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(api, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as _:
                pass
        except Exception:
            pass

    def set_my_commands(self, commands: list[dict]) -> None:
        self._api("setMyCommands", {"commands": commands})

    def send_reply_keyboard(self, text: str, buttons: list[list[str]]) -> None:
        kb = {"keyboard": [[{"text": b} for b in row] for row in buttons], "resize_keyboard": True, "one_time_keyboard": False}
        self._api("sendMessage", {"text": text, "reply_markup": kb, "parse_mode": "Markdown"})

    def get_updates(self, offset: int | None = None, timeout: int = 20) -> list[dict]:
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        data = self._http_get("getUpdates", params) or {}
        return data.get("result", [])

    def poll_loop(self, handler):
        offset = None
        while True:
            updates = self.get_updates(offset=offset, timeout=20) or []
            for u in updates:
                offset = (u["update_id"] + 1) if "update_id" in u else offset
                try:
                    handler(u)
                except Exception:
                    pass
