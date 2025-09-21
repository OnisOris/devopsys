import json
from pathlib import Path

from click.testing import CliRunner

from devopsys.__main__ import cli_main
from devopsys import ollama as ollama_module


def test_cli_ask_allows_backend_override():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli_main,
            [
                "ask",
                "--no-trace",
                "--backend",
                "dummy",
                "--agent",
                "python",
                "--out",
                "result.py",
                "Пример задания",
            ],
        )

        assert result.exit_code == 0
        assert "Step 1" in result.output
        assert "Saved → result.py" in result.output
        saved = Path("result.py").read_text(encoding="utf-8")
        assert "Generated (dummy backend)" in saved


class _DummyStream:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_lines(self):
        yield from self._lines

    def raise_for_status(self):
        return None


class _DummyClient:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.stream_args = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url, json):
        self.stream_args = (method, url, json)
        lines = [
            json_dumps({"status": "pulling manifest"}),
            json_dumps({"status": "success"}),
        ]
        return _DummyStream(lines)


class _DummyListResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _DummyListClient:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.called_urls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url):
        self.called_urls.append(url)
        return _DummyListResponse(
            {
                "models": [
                    {
                        "name": "codellama:7b-instruct",
                        "size": 10_000_000,
                        "details": {
                            "families": ["llama"],
                            "parameter_size": "7B",
                        },
                    },
                    {
                        "name": "qwen2",
                        "details": {"family": "qwen2"},
                    },
                ]
            }
        )


def json_dumps(data):
    return json.dumps(data)


def test_ollama_pull_command(monkeypatch):
    client_holder = {}

    def _client_factory(*args, **kwargs):
        client = _DummyClient(*args, **kwargs)
        client_holder["instance"] = client
        return client

    monkeypatch.setattr(ollama_module.httpx, "Client", _client_factory)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["ollama", "pull", "codellama:7b-instruct"])

    assert result.exit_code == 0
    assert "Model ready" in result.output
    client = client_holder["instance"]
    assert client.stream_args is not None
    method, url, payload = client.stream_args
    assert method == "POST"
    assert payload["name"] == "codellama:7b-instruct"
    assert payload["stream"] is True
    assert url.endswith("/api/pull")


def test_ollama_list_command(monkeypatch):
    def _client_factory(*args, **kwargs):
        return _DummyListClient(*args, **kwargs)

    monkeypatch.setattr(ollama_module.httpx, "Client", _client_factory)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["ollama", "list"])

    assert result.exit_code == 0
    assert "Models on" in result.output
    assert "codellama:7b-instruct" in result.output
    assert "qwen2" in result.output
