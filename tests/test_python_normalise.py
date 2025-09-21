from __future__ import annotations

import ast

from devopsys.agents.python_utils import normalise_python_output


def test_normaliser_strips_markdown_and_prose():
    raw = (
        """```python
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import List


@dataclass
class Step:
    agent: str
    instruction: str
    reason: str


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    print(args.task)


if __name__ == "__main__":
    main()
```

This script provides a basic structure... and more prose here.
"""
    )

    code = normalise_python_output(raw, task="test task")
    assert "```" not in code
    assert "This script provides" not in code
    # must contain structure
    assert "def main" in code
    assert "if __name__ == \"__main__\":" in code
    # syntax should be valid
    ast.parse(code)


def test_placeholder_used_when_invalid_even_if_task_specific():
    task = "Напиши скрипт для рисования круга на python"
    code = normalise_python_output("nonsense text", task=task)
    assert "Python scaffold for task" in code
