"""Utilities to post-process Python agent outputs and provide fallbacks.

Enhancements:
- Robustly extract Python code from Markdown-fenced blocks, ignoring trailing
  prose like "```\nThis script ..." that some models append.
- Iteratively try fixes and validate with a syntax checker (``ast.parse``).
- Ensure a minimal ``main()`` and ``if __name__ == "__main__":`` guard exist.
Note: This module intentionally avoids task-specific templates or canned
solutions; it only performs generic cleanup and validation.
"""

from __future__ import annotations

import re
from typing import Optional, List, Tuple
import ast


_GENERIC_PLACEHOLDER = (
    "\n".join(
        [
            "Python scaffold for task: {task}",
            "",
            "This placeholder was generated automatically because the LLM response did not",
            "produce valid Python code. Implement the solution manually in this file.",
        ]
    )
    + "\n"
)


def _looks_like_valid_python(code: str) -> bool:
    snippet = code.strip()
    if not snippet:
        return False
    if "if __name__ == \"__main__\":" not in snippet:
        return False
    if "def main" not in snippet:
        return False
    return True


def _fallback_script_for_task(task: str) -> Optional[str]:
    # Intentionally no task-specific implementations.
    return None


def normalise_python_output(raw: str, task: str) -> str:
    """Ensure the Python agent output is executable, falling back if needed.

    Strategy:
    1) Prefer content inside ``` fenced blocks (first Python-looking block).
    2) Strip any remaining fence markers or language hints.
    3) Validate via ``ast.parse``; if it fails, try a couple of cleanup passes.
    4) Ensure a minimal main() and __main__ guard are present (append if missing).
    5) If all fails, return a task-specific fallback or the generic placeholder.
    """

    text = (raw or "").strip()
    if not text:
        return _fallback_script_for_task(task) or _GENERIC_PLACEHOLDER.format(task=task)

    # Direct pass-through for dummy backend test content.
    if "Generated (dummy backend)" in text:
        return text if text.endswith("\n") else text + "\n"

    def _extract_fenced_blocks(s: str) -> List[Tuple[str, str]]:
        # Returns list of (language_hint, content)
        blocks: List[Tuple[str, str]] = []
        for m in re.finditer(r"```(?P<lang>\w+)?\n(?P<body>[\s\S]*?)```", s):
            lang = (m.group("lang") or "").strip().lower()
            body = m.group("body")
            blocks.append((lang, body))
        return blocks

    def _syntax_ok(src: str) -> bool:
        # Treat empty strings as invalid for our purposes.
        if not (src or "").strip():
            return False
        try:
            ast.parse(src)
            return True
        except SyntaxError:
            return False

    def _pick_best_block(blocks: List[Tuple[str, str]]) -> Optional[str]:
        if not blocks:
            return None
        # Prefer blocks explicitly marked as python, otherwise the longest that parses.
        py_blocks = [(lang, body) for lang, body in blocks if lang in ("py", "python")]
        candidates = py_blocks or blocks
        # First try any that parse successfully, choose the longest among valid ones.
        valid = [body for _, body in candidates if _syntax_ok(body)]
        if valid:
            return max(valid, key=len)
        # Otherwise, return the longest candidate; later cleanup may help.
        return max((body for _, body in candidates), key=len)

    def _strip_fence_noise(s: str) -> str:
        # Remove stray fence markers and language lines left behind.
        lines = [ln for ln in s.splitlines() if ln.strip() != "```" and not ln.strip().lower().startswith("```python") and not ln.strip().lower().startswith("```py")]
        # Also drop solitary "python" language hints at the start of fenced content
        # that some models include when fences were already removed upstream.
        if lines and lines[0].strip().lower() in ("python", "py"):
            lines = lines[1:]
        return "\n".join(lines).strip()

    # 1) Prefer fenced code blocks
    blocks = _extract_fenced_blocks(text)
    candidate = _pick_best_block(blocks) if blocks else text
    candidate = _strip_fence_noise(candidate)

    # 2) Try to remove any prose that follows a closing fence in the raw text
    if not blocks and "```" in text:
        # Keep everything before the last closing fence if present
        try:
            before_last_fence = text[: text.rindex("```")]
            maybe_body = before_last_fence
            # If there was an opening fence too, try to grab content between the first pair
            if "```" in maybe_body:
                first_open = maybe_body.index("```")
                candidate2 = maybe_body[first_open + 3 :].lstrip("\n")
                candidate = _strip_fence_noise(candidate2)
        except ValueError:
            pass

    # Iterative cleanup + syntax validation
    attempts: List[str] = []
    attempts.append(candidate)

    # Heuristic trims if syntax still fails: drop trailing non-code paragraphs.
    def _trim_trailing_prose(src: str) -> str:
        lines = src.splitlines()
        # Drop trailing lines that look like English/Russian prose (no typical code tokens)
        code_tokens = ("def ", "class ", "import ", "from ", "return", "=", "):", "]:", "}:")
        while lines:
            tail = lines[-1].strip()
            if not tail:
                lines.pop()
                continue
            if tail.startswith("#"):
                # comments are fine
                break
            if not any(tok in tail for tok in code_tokens) and not re.match(r"[\w_]+\(.*\)", tail):
                lines.pop()
                continue
            break
        return "\n".join(lines).strip()

    if not _syntax_ok(attempts[-1]):
        trimmed = _trim_trailing_prose(attempts[-1])
        if trimmed != attempts[-1]:
            attempts.append(trimmed)

    # If still not ok and we have blocks, try the longest raw block content as-is.
    if not _syntax_ok(attempts[-1]) and blocks:
        longest_raw = max((body for _, body in blocks), key=len)
        attempts.append(_strip_fence_noise(longest_raw))

    # Choose the first syntactically valid attempt, or keep the last
    code = attempts[-1]
    for variant in attempts:
        if _syntax_ok(variant):
            code = variant
            break

    # If not valid at this point, fall back if we can (do NOT auto-salvage
    # with a stub main for non-Python content).
    if not _syntax_ok(code):
        fallback = _fallback_script_for_task(task)
        if fallback:
            return fallback
        return _GENERIC_PLACEHOLDER.format(task=task)

    # As a final touch, ensure a minimal main/guard structure exists for
    # already-valid Python code.
    needs_main = "def main" not in code
    needs_guard = "if __name__ == \"__main__\":" not in code
    if needs_main or needs_guard:
        parts = [code.rstrip()]
        if needs_main:
            parts.append(
                """
def main() -> None:
    pass
                """.strip()
            )
        if needs_guard:
            parts.append(
                """
if __name__ == "__main__":
    main()
                """.strip()
            )
        code = "\n\n".join(parts).strip()

    return code if code.endswith("\n") else code + "\n"


__all__ = ["normalise_python_output"]
