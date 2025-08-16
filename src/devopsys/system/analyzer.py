
from __future__ import annotations

from typing import TypedDict

class Report(TypedDict):
    platform: dict
    disks: str
    processes: str

import platform, subprocess, shutil

class SystemAnalyzer:
    @staticmethod
    def _cap(cmd: list[str]) -> str:
        try:
            return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, timeout=10)
        except Exception as e:
            return f"<error: {e}>"

    def analyze(self, simulate: bool = False) -> dict:
        rep = {
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "python": platform.python_version(),
            },
            "disks": "",
            "processes": "",
        }
        rep["disks"] = self._cap(["df","-h"])
        if shutil.which("top"):
            try:
                out = subprocess.check_output(["bash","-lc","COLUMNS=120 top -b -n1 | head -n 40"], text=True, stderr=subprocess.STDOUT, timeout=10)
            except Exception as e:
                out = f"<error: {e}>"
        else:
            out = self._cap(["ps","aux"])
        rep["processes"] = out
        return rep

    @staticmethod
    def render_markdown(rep: dict) -> str:
        md = []
        md.append(f"**Platform**: {{'system': '{rep['platform']['system']}', 'release': '{rep['platform']['release']}', 'python': '{rep['platform']['python']}'}}\\n")
        md.append("## Disks\\n```\\n" + rep.get("disks","").strip() + "\\n```")
        md.append("## Top processes\\n```\\n" + rep.get("processes","").strip()[:4000] + "\\n```")
        return "\\n".join(md)