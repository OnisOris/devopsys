
from __future__ import annotations
import re
import json, os
from ..system.analyzer import SystemAnalyzer
from ..config import Config
from ..logutil import log_event
import logging, os

try:
    from langchain_core.prompts import ChatPromptTemplate  # type: ignore
    try:
        from langchain_openai import ChatOpenAI  # type: ignore
    except Exception:
        from langchain_community.chat_models import ChatOpenAI  # type: ignore
    _LC = True
except Exception:
    _LC = False

RULES = """Ты опытный DevOps-инженер (нейро-девопсер). Работай на Linux.
Правила:
- Используй пакетный менеджер дистрибутива (apt/dnf/yum/pacman/zypper), неинтерактивные флаги (-y), проверяй установленность перед установкой.
- Не выполняй опасные команды (rm -rf /*, dd на устройства, :(){ :|: & };:) и перезапуски критичных сервисов без причины.
- Для systemd: systemctl enable --now <svc>; проверяй статус через systemctl is-active/is-enabled.
- Для Docker: проверяй docker --version, sudo потребности; 'docker run -d --name ... --restart unless-stopped'.
- Для Kubernetes: kubectl get nodes/context/namespace; изменения — через kubectl apply -f.
- Для журналов: journalctl -u <svc> --no-pager --since "1 hour ago".
- Для мониторинга: top/df/free/vmstat/uptime.
- Ответ строго JSON массива: [{"description","command","reason"}], без пояснений.
"""

def _system_context_text() -> str:
    rep = SystemAnalyzer().analyze(simulate=False)
    ctx = [
        "OS: " + str(rep['platform'].get('system')) + " " + str(rep['platform'].get('release')) + " Python " + str(rep['platform'].get('python')),
        "DISKS:\n" + rep['disks'][:2000],
        "TOP:\n" + rep['processes'][:2000],
    ]
    return "\n".join(ctx)


def _heuristic_plan(goal: str) -> list[dict]:
    g = goal.lower().strip()
    steps: list[dict] = []
    def add(cmd: str, desc: str | None = None):
        steps.append({"description": desc or cmd, "command": cmd, "reason": "heuristic"})
    # verbs / intent
    verbs_install = ["установить", "установи", "поставь", "install", "setup", "инсталлируй"]
    verbs_download = ["скачать", "скачай", "загрузить", "загрузи", "download"]
    # common installs
    pkgs = ["htop","git","curl","tmux","docker.io","docker","aria2","wget","btop","bashtop","neofetch"]
    if any(v in g for v in verbs_install):
        for pkg in pkgs:
            if re.search(rf"(?:^|\W){re.escape(pkg)}(?:$|\W)", g):
                add("sudo apt-get update && sudo apt-get install -y " + pkg, "Установка " + pkg)
    # system state
    if any(k in g for k in ["состояние","анализ","дай состояние","diagnos","diagnostic","status","state"]):
        add("echo 'use /analyze or API /api/analyze'; true", "Показать отчёт через /analyze")
    # download Windows ISO
    if any(v in g for v in verbs_download) and (("windows" in g) or ("виндовс" in g)):
        if any(k in g for k in ["11","10","iso","образ","image","installer"]):
            add("sudo apt-get update && sudo apt-get install -y aria2 xdg-user-dirs", "Подготовка инструментов загрузки")
            add("DOWNLOADS=$(xdg-user-dir DOWNLOAD || echo \"$HOME/Downloads\"); mkdir -p \"$DOWNLOADS\"; echo \"$DOWNLOADS\"", "Определить папку загрузок")
            add("xdg-open 'https://www.microsoft.com/software-download/windows11' || echo 'Откройте ссылку вручную в браузере'", "Открыть страницу загрузки Windows 11 (ручное подтверждение)")
            add("echo 'После получения прямой ссылки на ISO, выполните:' && echo 'aria2c -x16 -s16 -o Win11.iso -d \"$HOME/Downloads\" \"<DIRECT_ISO_URL>\"'", "Подсказка по прямой загрузке через aria2c")
            add("DOWNLOADS=$(xdg-user-dir DOWNLOAD || echo \"$HOME/Downloads\"); ls -lh \"$DOWNLOADS\"/Win11*.iso 2>/dev/null || echo 'ISO пока не найдено'", "Проверка наличия ISO")
        # Windows wallpaper/images
        if any(k in g for k in ["картинк","wallpaper","обои","фон"]):
            add("sudo apt-get update && sudo apt-get install -y wget xdg-user-dirs xdg-utils", "Подготовка инструментов")
            add("DOWNLOADS=$(xdg-user-dir DOWNLOAD || echo \"$HOME/Downloads\"); mkdir -p \"$DOWNLOADS\"; echo \"$DOWNLOADS\"", "Определить папку загрузок")
            add("xdg-open 'https://www.bing.com/images/search?q=Windows+11+wallpaper+4k' || echo 'Откройте ссылку вручную и сохраните картинку в Загрузки'", "Открыть поиск обоев Windows 11")
            add("echo 'Либо скачайте напрямую: wget -O \"$HOME/Downloads/win11_wallpaper.jpg\" \"<DIRECT_IMAGE_URL>\"'", "Подсказка для прямой загрузки")
            add("ls -lh \"$HOME/Downloads\"/win11* 2>/dev/null || echo 'Файл пока не найден'", "Проверка наличия картинки")
    if not steps:
        # fallback: if user asked plain '/ask' or unclear goal -> suggest analyze
        add("echo 'no-op (heuristic)'; true", "Нет явных команд для цели")
    return steps


def plan_actions(goal: str, safe_mode: bool = True, model: str = "gpt-4o-mini") -> list[dict]:
    cfg = Config.from_env()
    logger = logging.getLogger('devopsys')
    log_event(cfg, logging.INFO, f"agent.plan goal='{goal}' safe={safe_mode}")
    # If LC missing or API key absent — fallback to heuristics
    if not _LC or not os.environ.get("OPENAI_API_KEY"):
        log_event(cfg, logging.WARNING, "LangChain/OpenAI missing -> heuristic plan")
        return _heuristic_plan(goal)

    # Build prompt & ask LLM, but be defensive: any failure -> heuristics
    try:
        ctx = _system_context_text()
        guard = "SAFE MODE: Требуется подтверждение каждого шага." if safe_mode else "UNSAFE MODE: Можно выполнять автоматически, но всё равно предлагай пошагово."
        system_messages = [
            ("system", RULES),
            ("system", "Контекст системы:\n" + ctx + "\n\nРабочий режим: " + guard + "\nЦель пользователя: {goal}"),
        ]
        prompt = ChatPromptTemplate.from_messages(system_messages + [("human", "Сформируй шаги. Только JSON-массив объектов.")])
        llm = ChatOpenAI(model=model, temperature=0.1)
        out = llm.invoke(prompt.format(goal=goal)).content
        try:
            steps = json.loads(out)
            if isinstance(steps, dict):
                steps = [steps]
            norm: list[dict] = []
            for s in steps:
                if not isinstance(s, dict): 
                    continue
                d = {"description": s.get("description") or s.get("step") or "step",
                     "command": s.get("command") or s.get("cmd") or "",
                     "reason": s.get("reason","")}
                if d["command"]:
                    norm.append(d)
            if norm:
                log_event(cfg, logging.INFO, f"agent.plan produced {len(norm)} steps via LLM")
                return norm[:8]
            else:
                log_event(cfg, logging.WARNING, "LLM returned empty -> heuristic fallback")
                return _heuristic_plan(goal)
        except Exception as e:
            log_event(cfg, logging.ERROR, f"parse error: {e}")
            return _heuristic_plan(goal)
    except Exception as e:
        log_event(cfg, logging.ERROR, f"agent error: {e}")
        return _heuristic_plan(goal)
