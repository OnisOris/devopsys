from __future__ import annotations

from devopsys.orchestrator import MultiAgentOrchestrator
from devopsys.models.dummy import DummyModel


def _factory():
    return DummyModel()


def test_static_audit_only_checks_syntax():
    orch = MultiAgentOrchestrator(_factory)
    task = "Напиши скрипт для рисования солнца с помощью matplotlib"
    code = "import httpx\nprint('hello')\n"
    ok, reason, missing, meta = orch._static_python_audit(task, code)
    assert ok is True
    assert reason == ""
    assert missing == []
    assert meta == {}


def test_mpl_audit_accepts_minimal_matplotlib_script():
    orch = MultiAgentOrchestrator(_factory)
    task = "Напиши скрипт для рисования солнца с помощью matplotlib"
    code = (
        "import matplotlib.pyplot as plt\n"
        "def main():\n"
        "    fig, ax = plt.subplots()\n"
        "    ax.plot([0,1],[0,1])\n"
        "    plt.show()\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    ok, reason, missing, meta = orch._static_python_audit(task, code)
    assert ok is True
    assert reason == ""
    assert missing == []
    assert meta == {}
