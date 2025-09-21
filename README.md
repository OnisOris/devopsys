# devopsys

Локальный мультиагентный DevOps-ассистент с CLI на базе LangChain. Лидер-агент составляет план, далее специализированные агенты (Docker/Python/Rust/Bash/Linux) взаимодействуют и готовят артефакты.

## Установка (через uv)

```bash
uv venv .venv
uv pip install -e .[dev]
```

## Запуск CLI

```bash
uv run devopsys --help
uv run devopsys ask "Собери Dockerfile под Python 3.11 c poetry"
uv run devopsys ask "Скрипт на bash для rsync бэкапа" --agent bash --out out/backup.sh
uv run devopsys ask --backend dummy "Быстрый тест без LLM"
uv run devopsys ask --backend ollama --model codellama:7b-instruct "Dockerfile для FastAPI"
```

Команда `ask` показывает пошаговый план (Step 1 → …) и выводит финальный артефакт. При необходимости можно указать конкретного агента (`--agent`) или ОС для Linux-агента (`--os`).

## Архитектура

- Lead-агент: строит план по запросу пользователя через LangChain, формирует JSON со списком подзадач.
- Рабочие агенты: Docker/Python/Rust/Bash/Linux получают инструкции от планировщика и генерируют артефакты с помощью собственных промптов.
- Оркестратор: запускает агентов последовательно, собирает результаты и при необходимости сохраняет файл (например, Dockerfile или script.py).
- Бэкенды: `dummy` (для тестов) и `ollama` (локальные LLM). Для каждого шага создаётся отдельный экземпляр модели, что позволяет подключать различные LLM.

## Ollama и модели

1. Установите Ollama, следуя [официальной инструкции](https://ollama.com/download) (Linux/macOS/Windows).
2. Поднимите сервис: `ollama serve` (по умолчанию http://127.0.0.1:11434).
3. Скачайте нужную модель через CLI devopsys:

```bash
uv run devopsys ollama pull codellama:7b-instruct
```

Команда использует HTTP API Ollama и работает даже если бинарь `ollama` недоступен в PATH. Хост можно переопределить: `uv run devopsys ollama --host http://ollama:11434 pull llama3`.

4. Посмотрите, какие модели уже загружены локально:

```bash
uv run devopsys ollama list
```

Для постоянного переопределения адреса Ollama используйте глобальный флаг `--ollama-host` или переменную окружения `DEVOPSYS_OLLAMA_HOST`. Например, если сервис доступен в Docker-сети как `http://ollama:11434`:

```bash
uv run devopsys --ollama-host http://ollama:11434 ollama list
uv run devopsys ask --ollama-host http://ollama:11434 --backend ollama --model codellama:7b-instruct "Dockerfile для FastAPI"
```

## Тесты

```bash
uv pip install -e .[dev]
uv run pytest -q
```
