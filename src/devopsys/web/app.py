from __future__ import annotations
import os, json, uuid, subprocess, shlex
from typing import Any, Dict, List
from fastapi import FastAPI, Body
from fastapi.responses import HTMLResponse, JSONResponse
from ..config import Config, save_to_env
from ..system.analyzer import SystemAnalyzer
from ..telegram.bot import TelegramClient

PENDING: Dict[str, Dict[str, str]] = {}

def get_app() -> FastAPI:
    app = FastAPI(title='devopsys')
    cfg = Config.from_env()

    def _format_status(cfg: Config) -> str:
        safe = '✅ SAFE' if cfg.safe_mode else '⚠️ UNSAFE'
        sources = ', '.join(cfg.monitor_sources) if cfg.monitor_sources else '—'
        return ('*Статус devopsys*\n'
                f'- Режим: {safe}\n'
                f'- Источники: `{sources}`\n'
                f'- Интервал: {cfg.monitor_interval_sec}с')

    def _build_status_kb(cfg: Config) -> list[list[dict]]:
        return [
            [{'text': 'SAFE: ON' if cfg.safe_mode else 'SAFE: OFF', 'callback_data': 'safe:toggle'}],
            [{'text': '🛠 Источники', 'callback_data': 'src:menu'}],
            [{'text': '💾 Сохранить в .env', 'callback_data': 'cfg:save'}],
        ]

    @app.get('/', response_class=HTMLResponse)
    def index() -> str:
        return "<html><body><h2>devopsys</h2><button onclick=\"fetch('/api/analyze').then(r=>r.json()).then(t=>alert('ok'))\">Анализ</button><p>Docs: <a href='/docs'>/docs</a></p></body></html>"

    @app.get('/api/analyze', response_class=JSONResponse)
    def api_analyze():
        rep = SystemAnalyzer().analyze(simulate=False)
        return rep

    @app.get('/api/config', response_class=JSONResponse)
    def api_get_cfg():
        return cfg.model_dump()

    @app.post('/api/config', response_class=JSONResponse)
    def api_set_cfg(data: Dict[str, Any] = Body(...)):
        if 'safe_mode' in data: cfg.safe_mode = bool(data['safe_mode'])
        if 'monitor_sources' in data: cfg.monitor_sources = list(map(str, data['monitor_sources']))
        if 'monitor_interval_sec' in data: cfg.monitor_interval_sec = int(data['monitor_interval_sec'])
        save_to_env(cfg)
        return {'ok': True, **cfg.model_dump()}

    @app.get('/api/monitor/sources', response_class=JSONResponse)
    def api_sources_get():
        return {'sources': cfg.monitor_sources}

    @app.post('/api/monitor/sources', response_class=JSONResponse)
    def api_sources_set(data: Dict[str, Any] = Body(...)):
        if 'sources' in data:
            cfg.monitor_sources = list(map(str, data['sources']))
            save_to_env(cfg)
        return {'ok': True, 'sources': cfg.monitor_sources}

    @app.post('/api/agent/plan', response_class=JSONResponse)
    def api_agent_plan(data: Dict[str, Any] = Body(...)):
        goal = data.get('goal') or ''
        dispatch = bool(data.get('dispatch_to_telegram', False))
        steps: List[Dict[str,str]] = []
        try:
            from ..agents.devops import plan_actions
            steps = plan_actions(goal or 'проанализировать систему', safe_mode=cfg.safe_mode)
        except Exception as e:
            steps = [{'description':'fallback','command':'echo \'use /analyze\'; true','reason':str(e)}]
        if dispatch:
            tg = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)
            tg.send_message(f"*План из {len(steps)} шагов:*\n" + "\n".join([f"{i+1}. `{s['command']}`" for i,s in enumerate(steps)]))
            for st in steps:
                cmd = st['command']
                tok = str(uuid.uuid4())
                PENDING[tok] = {'cmd': cmd}
                buttons = [[{'text':'✅ Выполнить','callback_data':f'ok:{tok}'},{'text':'⛔ Отменить','callback_data':f'no:{tok}'}]]
                tg.send_inline_keyboard(f"[AGENT] {st.get('description','step')}\n`{cmd}`", buttons)
        return {'steps': steps}

    @app.post('/tg/webhook')
    def tg_webhook(payload: Dict[str, Any]) -> Dict[str, Any]:
        tg = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)
        if 'callback_query' in payload:
            cq = payload['callback_query']
            data = cq.get('data','')
            chat_id = str(cq['message']['chat']['id']) if cq.get('message') else None
            message_id = cq['message']['message_id'] if cq.get('message') else None
            if data.startswith('ok:') or data.startswith('no:'):
                action, tok = data.split(':',1)
                item = PENDING.pop(tok, None)
                if item:
                    if action == 'ok':
                        cmd = item['cmd']
                        needs_shell = any(m in cmd for m in ['&&','||',';','|','*','<','>','$(','`'])
                        try:
                            proc = subprocess.run(cmd if needs_shell else shlex.split(cmd), shell=needs_shell, capture_output=True, text=True, timeout=300)
                            out = (proc.stdout + '\n' + proc.stderr).strip()
                            text = ('✅ Выполнено:\n'
                                    f'`{cmd}`\n\n'
                                    f'код={proc.returncode}\n'
                                    '```\n'
                                    f'{out[:1500]}\n'
                                    '```')
                        except Exception as e:
                            text = f'⚠️ Ошибка при выполнении `{cmd}`: {e}'
                    else:
                        text = 'Операция отменена.'
                else:
                    text = 'Задача не найдена или уже обработана.'
                if chat_id and message_id:
                    tg.edit_message_text(chat_id, message_id, text)
                if 'id' in cq:
                    tg.answer_callback(cq['id'], 'OK')
                return {'status': 'ok'}
        if 'message' in payload:
            msg = payload['message']
            txt = msg.get('text','')
            if txt.startswith('/start') or txt.startswith('/menu'):
                kb = [['/analyze','/snapshot'], ['/ask установить htop'], ['/status'], ['/help']]
                tg.send_reply_keyboard('*Команды*:', kb)
                tg.send_message(_format_status(cfg))
                return {'status':'ok'}
            if txt.startswith('/status'):
                tg.send_message(_format_status(cfg))
                tg.send_inline_keyboard('Настройки:', _build_status_kb(cfg))
                return {'status':'ok'}
            if txt.startswith('/help'):
                tg.send_message('Доступные команды:\n/analyze — отчёт\n/snapshot — разовый снимок\n/ask <задача> — нейро-агент\n/status — состояние SAFE/мониторинга')
                return {'status':'ok'}
            if txt.startswith('/snapshot'):
                from ..monitor.stream import snapshot
                tg.send_message(snapshot(cfg.monitor_sources))
                return {'status':'ok'}
            if txt.startswith('/ask'):
                goal = txt.split(' ',1)[1] if ' ' in txt else 'проанализировать систему'
                try:
                    from ..agents.devops import plan_actions
                    steps = plan_actions(goal, safe_mode=cfg.safe_mode)
                except Exception as e:
                    goal_lc = goal.lower()
                    if any(k in goal_lc for k in ['состояние','анализ','state','status']):
                        rep = SystemAnalyzer().analyze(simulate=False)
                        md = SystemAnalyzer.render_markdown(rep)
                        tg.send_message('*Отчёт (fallback)*\n' + md[:3500])
                        return {'status':'ok'}
                    tg.send_message(f'❗ Агент недоступен: {e}. Установите пакет *langchain-openai* или используйте /analyze.')
                    return {'status':'ok'}
                tg.send_message(f"*План из {len(steps)} шагов:*\n" + "\n".join([f"{i+1}. `{s['command']}`" for i,s in enumerate(steps)]))
                for st in steps:
                    cmd = st['command']
                    tok = str(uuid.uuid4())
                    PENDING[tok] = {'cmd': cmd}
                    buttons = [[{'text':'✅ Выполнить','callback_data':f'ok:{tok}'},{'text':'⛔ Отменить','callback_data':f'no:{tok}'}]]
                    tg.send_inline_keyboard(f"[AGENT] {st.get('description','step')}\n`{cmd}`", buttons)
                return {'status':'ok'}
            if txt.startswith('/analyze'):
                rep = SystemAnalyzer().analyze(simulate=False)
                md = SystemAnalyzer.render_markdown(rep)
                tg.send_message('Отчёт devopsys:\n' + md[:3500])
                return {'status':'ok'}
        return {'status':'ignored'}

    @app.get('/tg/set_webhook')
    def tg_set_webhook(url: str):
        tg = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)
        tg.set_webhook(url)
        return {'ok': True, 'url': url}

    return app

def run_server(host: str = '0.0.0.0', port: int = 8000):
    try:
        import uvicorn
    except Exception as e:
        raise RuntimeError("Uvicorn не установлен. Установите extra 'web'") from e
    app = get_app()
    uvicorn.run(app, host=host, port=port)