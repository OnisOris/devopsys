from devopsys.models.dummy import DummyModel
from devopsys.orchestrator import MultiAgentOrchestrator, LeadAgent, PlanStep


def _dummy_factory():
    return DummyModel()


def test_orchestrator_chooses_docker_for_dockerfile():
    orchestrator = MultiAgentOrchestrator(_dummy_factory)
    result = orchestrator.execute("Собери Dockerfile под Python 3.11 c poetry")

    assert result.steps
    assert result.steps[0].step.agent == "docker"
    assert result.final.filename == "Dockerfile"


def test_orchestrator_prefers_first_file_step(monkeypatch):
    orchestrator = MultiAgentOrchestrator(_dummy_factory)

    def fake_plan(self, task, workspace):
        return [
            PlanStep(agent="docker", instruction=task, reason="primary docker"),
            PlanStep(agent="python", instruction="setup script", reason="helper"),
        ]

    monkeypatch.setattr(LeadAgent, "plan", fake_plan, raising=False)

    result = orchestrator.execute("Собери Dockerfile под Python 3.11 c poetry")

    assert result.final.filename == "Dockerfile"
    assert "Generated (dummy backend)" in result.final.text


def test_orchestrator_reorders_to_router_agent(monkeypatch):
    orchestrator = MultiAgentOrchestrator(_dummy_factory)

    def fake_plan(self, task, workspace):
        return [
            PlanStep(agent="python", instruction="write helper", reason="prep"),
            PlanStep(agent="docker", instruction=task, reason="actual dockerfile"),
        ]

    monkeypatch.setattr(LeadAgent, "plan", fake_plan, raising=False)

    result = orchestrator.execute("Собери Dockerfile под Python 3.11 c poetry")

    assert result.steps[0].step.agent == "docker"
    assert result.final.filename == "Dockerfile"
