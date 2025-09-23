from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Literal, Optional

AgentName = Literal["docker", "python", "rust", "bash", "linux", "project_architect"]

KEYWORDS = {
    "docker": [r"\bdockerfile\b", r"\bdocker\b", r"container\b"],
    "python": [
        r"\bpython\b",
        r"\.py\b",
        r"fastapi|uvicorn|poetry|pip|pyproject",
        r"draw|circle|plot|graph|visualise|visualize",
        r"рису(й|ет|ем)|круг|график|построй",
    ],
    "rust": [r"\brust\b", r"cargo\b", r"\.rs\b"],
    "bash": [
        r"\bbash\b",
        r"shell\b",
        r"\.sh\b",
        r"cron|rsync|grep|sed|awk",
        r"launch|start|bootstrap",
        r"запуск|запусти|старт",
    ],
    "linux": [r"\bubuntu\b", r"\barch\b", r"linux\b", r"apt\b|pacman\b|systemd\b"],
    "project_architect": [
        r"\bproject\b",
        r"\bscaffold\b",
        r"\bstructure\b",
        r"pyproject\.toml",
        r"readme\.md",
        r"\bmodule\b",
        r"\bsrc\b",
        r"проект",
        r"структур",
        r"каталог",
    ],
}

@dataclass
class Route:
    agent: AgentName
    score: float
    reason: str

class Router:
    def classify(self, text: str) -> Route:
        text_l = text.lower()
        best: Optional[Route] = None
        for agent, patterns in KEYWORDS.items():
            hits = sum(1 for p in patterns if re.search(p, text_l))
            if hits:
                extra = 0
                if agent == "docker" and "dockerfile" in text_l:
                    extra = 5
                score = hits + extra
                reason = f"matched {hits} keywords for {agent}"
                if (best is None) or (score > best.score):
                    best = Route(agent=agent, score=score, reason=reason)
        if best is None:
            best = Route(agent="python", score=0.0, reason="fallback to python")
        return best
