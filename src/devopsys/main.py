
from __future__ import annotations
import argparse, sys, json, subprocess, shlex, uuid, threading, time
from .config import Config, save_to_env
from .system.analyzer import SystemAnalyzer
from .monitor.stream import monitor_loop
from .telegram.bot import TelegramClient
from .logutil import init_logging, log_event
import logging


def _get_agent(cfg):
    try:
        from .agent import DevOpSysAgent  # type: ignore
        return DevOpSysAgent(cfg)
    except Exception:
        class _Dummy:
            def __init__(self, _cfg): self.cfg=_cfg
            def analyze_and_send_report(self): 
                rep = SystemAnalyzer().analyze(simulate=False)
                md = SystemAnalyzer.render_markdown(rep)
                TelegramClient(self.cfg.telegram_bot_token, self.cfg.telegram_chat_id).send_message(md[:3500])
            def plan(self, goal): return []
            def execute(self, steps, confirm_cb=None): return []
        return _Dummy(cfg)
RUNTIME = {"monitor_enabled": False}

def _exec_command_stream(cmd: str, tg: TelegramClient, chat_id: str, message_id: int, cfg: Config, logger):
    import subprocess, shlex, time, threading, os, signal
    # SUDO: make non-interactive to avoid hanging
    if cfg.sudo_noninteractive and cmd.strip().startswith("sudo ") and " -n " not in cmd:
        cmd = cmd.replace("sudo ", "sudo -n ", 1)
    needs_shell = any(m in cmd for m in ["&&","||",";","|","*","<",">","$(","`"])
    header = f"▶️ Выполняю:\n`{cmd}`\n\n"
    tg.edit_message_text(chat_id, message_id, header + "…")
    logger.info("exec start: %s", cmd)

    try:
        proc = subprocess.Popen(
            cmd if needs_shell else shlex.split(cmd),
            shell=needs_shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as e:
        tg.edit_message_text(chat_id, message_id, f"⚠️ Не удалось запустить `{cmd}`: {e}")
        logger.error("exec spawn failed: %s -> %s", cmd, e)
        return

    buf_lines = []
    last_edit = 0.0
    MAX_LEN = 3500
    def flush(force=False):
        nonlocal last_edit
        now = time.time()
        if not force and (now - last_edit) * 1000 < cfg.exec_edit_ms:
            return
        text = header + "```\n" + "".join(buf_lines)[- (MAX_LEN-10) :] + "\n```"
        try:
            tg.edit_message_text(chat_id, message_id, text)
        except Exception:
            pass
        last_edit = now

    try:
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            buf_lines.append(line)
            logger.info("[exec] %s", line.rstrip())
            if len(buf_lines) % cfg.exec_chunk_lines == 0:
                flush()
        proc.wait(timeout=600)
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        buf_lines.append(f"\n[error] {e}\n")
    finally:
        rc = proc.returncode if proc and proc.poll() is not None else -1
        out = "".join(buf_lines).strip()
        hint = ""
        if "sudo: a password is required" in out or "sudo: no tty present" in out or "sudo: " in out and rc != 0:
            hint = "\n\nПодсказка: команда потребовала sudo-пароль. Включено `sudo -n` (неинтерактивно). " \
                   "Добавьте NOPASSWD для нужных команд в sudoers или запустите без sudo."
        text = header + f"код={rc}\n```\n{out[- (MAX_LEN-10) :]}\n```" + hint
        tg.edit_message_text(chat_id, message_id, text)
        logger.info("exec done: rc=%s", rc)

PENDING: dict[str, dict[str, str]] = {}

def _format_status(cfg: Config) -> str:
    safe = "✅ SAFE" if cfg.safe_mode else "⚠️ UNSAFE"
    mon = "📡 ВКЛ" if RUNTIME.get("monitor_enabled") else "⏹️ ВЫКЛ"
    sources = ", ".join(cfg.monitor_sources) if cfg.monitor_sources else "—"
    return ("*Статус devopsys*\n"
            f"- Режим: {safe}\n"
            f"- Мониторинг: {mon}\n"
            f"- Источники: `{sources}`\n"
            f"- Интервал: {cfg.monitor_interval_sec}с")

def _build_status_kb(cfg: Config) -> list[list[dict]]:
    return [
        [{"text": "SAFE: ON" if cfg.safe_mode else "SAFE: OFF", "callback_data": "safe:toggle"}],
        [{"text": "MONITOR: ON" if RUNTIME.get("monitor_enabled") else "MONITOR: OFF", "callback_data": "mon:toggle"}],
        [{"text": "🛠 Источники", "callback_data": "src:menu"}],
        [{"text": "💾 Сохранить в .env", "callback_data": "cfg:save"}],
    ]

def _run_server():
    from .web.app import run_server as _rs  # lazy import
    _rs()

def _start_bot_loop(cfg: Config) -> None:
    logger = logging.getLogger('devopsys')
    tg = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)
    def handle(update: dict) -> None:
        if "message" in update:
            msg = update["message"]
            text = msg.get("text","").strip()

            if text.startswith("/start") or text.startswith("/menu"):
                tg.send_reply_keyboard("*Команды*:", [["/analyze","/snapshot"], ["/ask установить htop"], ["/safe_on","/safe_off"], ["/enable_monitor","/disable_monitor"], ["/status"], ["/help"]])
                tg.send_message(_format_status(cfg))
                return

            if text.startswith("/status"):
                tg.send_message(_format_status(cfg))
                tg.send_inline_keyboard("Настройки:", _build_status_kb(cfg))
                return

            if text.startswith("/help"):
                tg.send_message("Доступные команды:\n/analyze — отчёт\n/snapshot — разовый снимок\n/ask <задача> — нейро-агент\n/status — состояние SAFE/мониторинга\n/safe_on|/safe_off — режим агента\n/enable_monitor|/disable_monitor — мониторинг")
                return

            if text.startswith("/safe_on"):
                cfg.safe_mode = True
                tg.send_message("SAFE режим включен")
                return

            if text.startswith("/safe_off"):
                cfg.safe_mode = False
                tg.send_message("SAFE режим выключен")
                return

            if text.startswith("/enable_monitor"):
                RUNTIME["monitor_enabled"] = True
                tg.send_message("Мониторинг: ВКЛ")
                return

            if text.startswith("/disable_monitor"):
                RUNTIME["monitor_enabled"] = False
                tg.send_message("Мониторинг: ВЫКЛ")
                return

            if text.startswith("/snapshot"):
                from .monitor.stream import snapshot
                tg.send_message(snapshot(cfg.monitor_sources))
                return
            if text.startswith("/ask"):
                logger.info("/ask received: %s", text)
                parts = text.split(" ", 1)
                if len(parts) == 1 or not parts[1].strip():
                    help_text = (
                        "Использование: `/ask <задача>`\n"
                        "Примеры:\n"
                        "• `/ask установить htop`\n"
                        "• `/ask скачать образ Windows 11`\n"
                        "• `/ask настроить docker`\n\n"
                        "Совет: включи SAFE через `/safe_on`, чтобы подтверждать шаги."
                    )
                    tg.send_message(help_text)
                    return
                goal = parts[1].strip()
                try:
                    from .agents.devops import plan_actions
                    steps = plan_actions(goal, safe_mode=cfg.safe_mode)
                except Exception as e:
                    goal_lc = goal.lower()
                    if any(k in goal_lc for k in ["состояние","анализ","state","status"]):
                        rep = SystemAnalyzer().analyze(simulate=False)
                        md = SystemAnalyzer.render_markdown(rep)
                        tg.send_message("*Отчёт (fallback)*\n" + md[:3500])
                        return
                    msg_err = str(e).strip() or "внутренняя ошибка планировщика"
                    hint = " Проверьте OPENAI_API_KEY или установите пакет langchain-openai."
                    tg.send_message(f"❗ Агент недоступен: {msg_err}.{hint}")
                    return
                if not steps:
                    tg.send_message("Не удалось построить план. Попробуйте переформулировать задачу или используйте `/анalyze`.")
                    return
                tg.send_message(f"*План из {len(steps)} шагов:*\n" + "\n".join([f"{i+1}. `{s['command']}`" for i,s in enumerate(steps)]))
                for st in steps:
                    cmd = st["command"]
                    tok = str(uuid.uuid4())
                    PENDING[tok] = {"cmd": cmd}
                    buttons = [[{"text":"✅ Выполнить","callback_data":f"ok:{tok}"},{"text":"⛔ Отменить","callback_data":f"no:{tok}"}]]
                    tg.send_inline_keyboard(f"[AGENT] {st.get('description','step')}\n`{cmd}`", buttons)
                return

            
            if text.startswith("/analyze"):
                rep = SystemAnalyzer().analyze(simulate=False)
                md = SystemAnalyzer.render_markdown(rep)
                tg.send_message("Отчёт devopsys:\n" + md[:3500])
                return

        if "callback_query" in update:
            logger.debug("callback_query: %s", update.get('callback_query',{}))
            cq = update["callback_query"]
            data = cq.get("data","")
            chat_id = str(cq["message"]["chat"]["id"]) if cq.get("message") else None
            message_id = cq["message"]["message_id"] if cq.get("message") else None
            if data.startswith(("ok:","no:")):
                action, tok = data.split(":",1)
                item = PENDING.pop(tok, None)
                if item:
                    if action == "ok":
                        cmd = item["cmd"]
                        needs_shell = any(m in cmd for m in ["&&","||",";","|","*","<",">","$(","`"])
                        try:
                            proc = subprocess.run(cmd if needs_shell else shlex.split(cmd), shell=needs_shell, capture_output=True, text=True, timeout=600)
                            out = (proc.stdout + "\n" + proc.stderr).strip()
                            text = ("✅ Выполнено:\n"
                                    f"`{cmd}`\n\n"
                                    f"код={proc.returncode}\n"
                                    "```\n"
                                    f"{out[:1500]}\n"
                                    "```")
                        except Exception as e:
                            text = f"⚠️ Ошибка при выполнении `{cmd}`: {e}"
                    else:
                        text = "Операция отменена."
                else:
                    text = "Задача не найдена или уже обработана."
                if chat_id and message_id:
                    tg.edit_message_text(chat_id, message_id, text)
                tg.answer_callback(cq.get("id",""), "OK")
                return
            if data == "safe:toggle":
                cfg.safe_mode = not cfg.safe_mode
                tg.edit_message_text(chat_id, message_id, _format_status(cfg))
                tg.send_inline_keyboard("Настройки:", _build_status_kb(cfg))
                return
            if data == "mon:toggle":
                RUNTIME["monitor_enabled"] = not RUNTIME.get("monitor_enabled")
                tg.edit_message_text(chat_id, message_id, _format_status(cfg))
                tg.send_inline_keyboard("Настройки:", _build_status_kb(cfg))
                return
            if data == "cfg:save":
                save_to_env(cfg)
                tg.edit_message_text(chat_id, message_id, _format_status(cfg) + "\n\n*Сохранено в .env*")
                tg.send_inline_keyboard("Настройки:", _build_status_kb(cfg))
                return
            if data == "src:menu":
                options = ["top","df","vmstat","free","uptime"]
                rows = []
                for name in options:
                    on = name in cfg.monitor_sources
                    rows.append([{"text": ("✅ "+name) if on else ("⬜ "+name), "callback_data": f"src:toggle:{name}"}])
                rows.append([{"text": "⬅️ Назад", "callback_data": "status:back"}])
                tg.edit_message_text(chat_id, message_id, "Источники мониторинга:")
                tg.send_inline_keyboard("Выберите:", rows)
                return
            if data.startswith("src:toggle:"):
                name = data.split(":",2)[2]
                if name in cfg.monitor_sources:
                    cfg.monitor_sources = [x for x in cfg.monitor_sources if x != name]
                else:
                    cfg.monitor_sources = cfg.monitor_sources + [name]
                options = ["top","df","vmstat","free","uptime"]
                rows = []
                for n in options:
                    on = n in cfg.monitor_sources
                    rows.append([{"text": ("✅ "+n) if on else ("⬜ "+n), "callback_data": f"src:toggle:{n}"}])
                rows.append([{"text": "⬅️ Назад", "callback_data": "status:back"}])
                tg.edit_message_text(chat_id, message_id, "Источники обновлены")
                tg.send_inline_keyboard("Выберите:", rows)
                return
            if data == "status:back":
                tg.edit_message_text(chat_id, message_id, _format_status(cfg))
                tg.send_inline_keyboard("Настройки:", _build_status_kb(cfg))
                return

    tg.set_my_commands([
        {"command":"start","description":"Показать меню"},
        {"command":"menu","description":"Клавиатура"},
        {"command":"analyze","description":"Системный отчёт"},
        {"command":"snapshot","description":"Разовый снимок"},
        {"command":"ask","description":"Нейро-агент"},
        {"command":"safe_on","description":"Включить SAFE"},
        {"command":"safe_off","description":"Выключить SAFE"},
        {"command":"status","description":"Статус и настройки"},
    ])
    tg.poll_loop(handle)

def cli_main(argv=None):
    argv = argv or sys.argv[1:]
    p = argparse.ArgumentParser("devopsys")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_an = sub.add_parser("analyze")
    p_an.add_argument("--report", choices=["md","json"], default="md")
    p_an.add_argument("--to", choices=["stdout","telegram","both"], default="stdout")

    p_pi = sub.add_parser("plan-install")
    p_pi.add_argument("goal", help="Цель в виде shell-команды или задачи")

    sub.add_parser("web")
    sub.add_parser("bot")

    p_mon = sub.add_parser("monitor")
    p_mon.add_argument("action", choices=["start","once","render-unit"])

    p_tg = sub.add_parser("telegram-test")
    p_tg.add_argument("text", nargs="?", default="ping from devopsys")

    p_agent = sub.add_parser("agent")
    p_agent.add_argument("prompt", nargs="+", help="Задача для DevOps-агента (LangChain)")

    sub.add_parser("up")

    args = p.parse_args(argv)
    cfg = Config.from_env()
    init_logging(cfg)
    logger = logging.getLogger('devopsys')
    logger.debug('CLI started with args: %s', argv)
    agent = _get_agent(cfg)

    if args.cmd == "analyze":
        log_event(cfg, logging.INFO, "run analyze")
        rep = SystemAnalyzer().analyze(simulate=False)
        if args.report == "md":
            out = SystemAnalyzer.render_markdown(rep)
        else:
            out = json.dumps(rep, ensure_ascii=False, indent=2)
        if args.to in ("stdout","both"):
            print(out)
        if args.to in ("telegram","both"):
            agent.analyze_and_send_report()
        return 0

    if args.cmd == "plan-install":
        log_event(cfg, logging.INFO, f"plan-install: {args.goal}")
        steps = agent.plan(args.goal)
        if cfg.safe_mode:
            for st in steps:
                ans = input(f"[SAFE] {st.description} :: {st.command}\nRun? [y/N] ").strip().lower()
                if ans not in {"y","yes"}:
                    st.command = None
            res = agent.execute(steps, confirm_cb=lambda s: True)
        else:
            res = agent.execute(steps)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "web":
        log_event(cfg, logging.INFO, "start web")
        _run_server()
        return 0

    if args.cmd == "bot":
        log_event(cfg, logging.INFO, "start bot")
        tg = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)
        if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
            print("TELEGRAM_* не заданы в .env")
            return 1
        print("Telegram bot: long polling... (Ctrl+C to stop)")
        _start_bot_loop(cfg)
        return 0

    if args.cmd == "monitor":
        log_event(cfg, logging.INFO, f"monitor {args.action}")
        if args.action == "start":
            monitor_loop(cfg)
        elif args.action == "once":
            from .monitor.stream import snapshot
            print(snapshot(cfg.monitor_sources))
        elif args.action == "render-unit":
            from .system.services import render_systemd_unit
            import getpass, os as _os
            workdir = _os.getcwd()
            exec_start = f"{sys.executable} -m devopsys monitor start"
            unit = render_systemd_unit("devopsys monitor", getpass.getuser(), workdir, exec_start)
            print(unit)
        return 0

    if args.cmd == "telegram-test":
        tg = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)
        tg.send_message(args.text)
        print("Sent.")
        return 0

    if args.cmd == "agent":
        log_event(cfg, logging.INFO, f"agent: {' '.join(args.prompt)}")
        try:
            from .agents.devops import plan_actions
        except Exception as e:
            print("Агенты недоступны:", e)
            return 1
        prompt = " ".join(args.prompt)
        steps = plan_actions(prompt, safe_mode=cfg.safe_mode)
        print(json.dumps(steps, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "up":
        log_event(cfg, logging.INFO, "devopsys up (web+bot+monitor)")
        def web_thread():
            try:
                _run_server()
            except Exception as e:
                print(f"[web] {e}")

        def bot_thread():
            print("[bot] long polling...")
            _start_bot_loop(cfg)

        def monitor_thread():
            from .monitor.stream import snapshot
            tg = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)
            print("[monitor] loop started")
            while True:
                if RUNTIME.get("monitor_enabled"):
                    msg = snapshot(cfg.monitor_sources)
                    tg.send_message("Мониторинг:\n" + msg)
                time.sleep(cfg.monitor_interval_sec)

        t_bot = threading.Thread(target=bot_thread, daemon=True); t_bot.start()
        try:
            t_web = threading.Thread(target=web_thread, daemon=True); t_web.start()
        except Exception as e:
            print(f"[web] {e}")
        t_mon = threading.Thread(target=monitor_thread, daemon=True); t_mon.start()

        print("devopsys up: running. Use Telegram /menu to control SAFE & monitor. Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Stopping...")
        return 0

    return 0

if __name__ == "__main__":
    raise SystemExit(cli_main())
