from __future__ import annotations

from typing import Dict, Type

from .base import Agent
from .bash import BashAgent
from .docker import DockerAgent
from .linux import LinuxAgent
from .python import PythonAgent
from .rust import RustAgent
from .verifier import VerifierAgent


AgentName = str


AGENT_REGISTRY: Dict[AgentName, Type[Agent]] = {
    "docker": DockerAgent,
    "python": PythonAgent,
    "rust": RustAgent,
    "bash": BashAgent,
    "linux": LinuxAgent,
    "verifier": VerifierAgent,
}
