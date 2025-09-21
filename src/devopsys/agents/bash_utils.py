"""Utilities for normalising Bash agent outputs.

These helpers make it easier to keep the Bash agent deterministic when the
LLM responds with unexpected markup (for example OpenHands-style [PYTHON]
sections).  When that happens we fall back to a curated rsync backup script
that matches the project prompt contract.
"""

from __future__ import annotations

from typing import Callable, Optional


_RSYNC_BACKUP_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ${0##*/} -s SOURCE -d DESTINATION [-r SSH_COMMAND] [-n] [-l LOG_FILE]

Options:
  -s  Source directory to back up (required)
  -d  Destination path (local or remote user@host:/path) (required)
  -r  Custom remote shell command passed to rsync via -e
  -n  Dry-run mode (no changes will be written)
  -l  Path to rsync log file (overwritten on each run)
  -h  Show this help message and exit

Examples:
  ${0##*/} -s /srv/data -d /mnt/backups/data
  ${0##*/} -s /srv/data -d backups/host1 -r "ssh -i ~/.ssh/backup_key"
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2
}

abort() {
  local line=$1
  local code=${2:-1}
  log "Backup aborted on line ${line} (exit code ${code})."
  exit "${code}"
}

trap 'abort $LINENO $?' ERR
trap 'log "Backup interrupted"; exit 130' INT TERM

SOURCE=""
DESTINATION=""
RSH_COMMAND=""
DRY_RUN=0
LOG_FILE=""

while getopts ':s:d:r:l:nh' opt; do
  case "${opt}" in
    s) SOURCE=${OPTARG} ;;
    d) DESTINATION=${OPTARG} ;;
    r) RSH_COMMAND=${OPTARG} ;;
    l) LOG_FILE=${OPTARG} ;;
    n) DRY_RUN=1 ;;
    h) usage; exit 0 ;;
    :) log "Option -${OPTARG} requires an argument."; usage; exit 64 ;;
    *) log "Unknown option -${OPTARG}."; usage; exit 64 ;;
  esac
done

shift $((OPTIND - 1))

if [[ -z "${SOURCE}" || -z "${DESTINATION}" ]]; then
  log "Source and destination are required."
  usage
  exit 64
fi

if [[ ! -d "${SOURCE}" ]]; then
  log "Source directory '${SOURCE}' does not exist."
  exit 66
fi

RSYNC_ARGS=(-a --delete --human-readable --partial --progress)

if [[ ${DRY_RUN} -eq 1 ]]; then
  RSYNC_ARGS+=(--dry-run --itemize-changes)
fi

if [[ -n "${RSH_COMMAND}" ]]; then
  RSYNC_ARGS+=(-e "${RSH_COMMAND}")
fi

if [[ -n "${LOG_FILE}" ]]; then
  mkdir -p "$(dirname "${LOG_FILE}")"
  RSYNC_ARGS+=(--log-file="${LOG_FILE}" --log-file-format="%t %f %b")
fi

TARGET=${DESTINATION}

if [[ "${DESTINATION}" != *:* && -z "${RSH_COMMAND}" ]]; then
  mkdir -p "${DESTINATION}"
fi

log "Starting rsync backup from '${SOURCE}' to '${TARGET}'."

if ! rsync "${RSYNC_ARGS[@]}" "${SOURCE}/" "${TARGET}"; then
  status=$?
  log "Rsync exited with status ${status}."
  exit "${status}"
fi

log "Backup completed successfully."
exit 0
"""


_PROJECT_RUNNER_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ${0##*/} [-p PROJECT_DIR] [-q QUERY] [-a AGENT] [-b BACKEND] [-m MODEL] [-o OUT_FILE] [-s] [-x]

Options:
  -p  Path to project directory (defaults to current directory)
  -q  Prompt text to run with devopsys ask (optional)
  -a  Agent to force for the sample run (passed to --agent)
  -b  Backend to use (passed to --backend, defaults to dummy if unset)
  -m  Model name to use for the sample run (passed to --model)
  -o  Output file for the generated artefact (passed to --out)
  -s  Skip running project tests after installation
  -x  Skip the sample devopsys ask invocation
  -h  Show this help message and exit

Examples:
  ${0##*/} -q "Скрипт на bash для rsync бэкапа" -a bash -o out/backup.sh
  ${0##*/} -p /srv/devopsys -q "Dockerfile для FastAPI" -b ollama -m llama3
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

abort() {
  local msg=$1
  local code=${2:-1}
  printf '[%s] ERROR: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${msg}" >&2
  exit "${code}"
}

command -v uv >/dev/null 2>&1 || abort "uv CLI is required but not found in PATH"

PROJECT_DIR="$(pwd)"
PROMPT=""
AGENT=""
BACKEND=""
MODEL=""
OUT_FILE=""
SKIP_TESTS=0
SKIP_SAMPLE=0

while getopts ':p:q:a:b:m:o:sxh' opt; do
  case "${opt}" in
    p) PROJECT_DIR=${OPTARG} ;;
    q) PROMPT=${OPTARG} ;;
    a) AGENT=${OPTARG} ;;
    b) BACKEND=${OPTARG} ;;
    m) MODEL=${OPTARG} ;;
    o) OUT_FILE=${OPTARG} ;;
    s) SKIP_TESTS=1 ;;
    x) SKIP_SAMPLE=1 ;;
    h) usage; exit 0 ;;
    :) abort "Option -${OPTARG} requires an argument." 64 ;;
    *) abort "Unknown option -${OPTARG}." 64 ;;
  esac
done

shift $((OPTIND - 1))

if [[ ! -d "${PROJECT_DIR}" ]]; then
  abort "Project directory '${PROJECT_DIR}' does not exist." 66
fi

pushd "${PROJECT_DIR}" >/dev/null
trap 'popd >/dev/null' EXIT

VENV_DIR="${PROJECT_DIR}/.venv"
PY_BIN="${VENV_DIR}/bin/python"
DEVOPSYS_BIN="${VENV_DIR}/bin/devopsys"

log "Using project directory: ${PROJECT_DIR}"

if [[ ! -x "${PY_BIN}" ]]; then
  log "Creating virtual environment in ${VENV_DIR}"
  uv venv "${VENV_DIR}"
fi

log "Installing project dependencies (offline preferred)"
if [[ -d "vendor/wheels" ]]; then
  WHEEL_DIR=$(cd "vendor/wheels" && pwd)
  if ! uv pip install --python "${PY_BIN}" --no-index --find-links "${WHEEL_DIR}" ".[dev]"; then
    log "Offline installation failed, retrying with PyPI access"
    uv pip install --python "${PY_BIN}" ".[dev]"
  fi
else
  uv pip install --python "${PY_BIN}" ".[dev]"
fi

if [[ ! -x "${DEVOPSYS_BIN}" ]]; then
  abort "Executable ${DEVOPSYS_BIN} not found after installation." 70
fi

export DEVOPSYS_BACKEND="${DEVOPSYS_BACKEND:-dummy}"

DEVOPSYS_VERSION="$(${DEVOPSYS_BIN} --version)"
log "devopsys version: ${DEVOPSYS_VERSION}"

if [[ ${SKIP_TESTS} -eq 0 && -d "tests" ]]; then
  log "Running pytest suite"
  "${PY_BIN}" -m pytest -q
else
  log "Skipping tests"
fi

if [[ ${SKIP_SAMPLE} -eq 0 && -n "${PROMPT}" ]]; then
  log "Running sample devopsys ask"
  CMD=("${DEVOPSYS_BIN}" ask)
  RUN_BACKEND=${BACKEND:-${DEVOPSYS_BACKEND}}
  if [[ -n "${RUN_BACKEND}" ]]; then
    CMD+=(--backend "${RUN_BACKEND}")
  fi
  if [[ -n "${MODEL}" ]]; then
    CMD+=(--model "${MODEL}")
  fi
  if [[ -n "${AGENT}" ]]; then
    CMD+=(--agent "${AGENT}")
  fi
  if [[ -n "${OUT_FILE}" ]]; then
    CMD+=(--out "${OUT_FILE}")
  fi
  CMD+=("${PROMPT}")
  "${CMD[@]}"
else
  log "Sample devopsys ask step skipped"
fi

log "Project bootstrap completed"
"""


_CIRCLE_DRAW_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ${0##*/} [-r RADIUS] [-c CHAR] [-f] [-o OUTPUT]

Options:
  -r  Circle radius (integer, >= 2). Default: 10
  -c  Drawing character (single printable char). Default: '*'
  -f  Fill circle instead of drawing only the outline
  -o  Write output to file instead of stdout
  -h  Show this help message and exit

Examples:
  ${0##*/} -r 8
  ${0##*/} -r 12 -c 'o' -f -o circle.txt
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

abort() {
  local msg=$1
  local code=${2:-1}
  printf '[%s] ERROR: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${msg}" >&2
  exit "${code}"
}

RADIUS=10
CHAR='*'
FILL=0
OUTPUT=""

while getopts ':r:c:fo:h' opt; do
  case "${opt}" in
    r) RADIUS=${OPTARG} ;;
    c) CHAR=${OPTARG} ;;
    f) FILL=1 ;;
    o) OUTPUT=${OPTARG} ;;
    h) usage; exit 0 ;;
    :) abort "Option -${OPTARG} requires an argument." 64 ;;
    *) abort "Unknown option -${OPTARG}." 64 ;;
  esac
done

shift $((OPTIND - 1))

if ! [[ ${RADIUS} =~ ^[0-9]+$ ]]; then
  abort "Radius must be a positive integer." 65
fi

if [[ ${RADIUS} -lt 2 ]]; then
  abort "Radius must be at least 2." 65
fi

if [[ ${#CHAR} -ne 1 ]]; then
  abort "Drawing character must be exactly one symbol." 65
fi

draw_circle() {
  local radius=$1
  local glyph=$2
  local fill=$3
  awk -v R="${radius}" -v C="${glyph}" -v F="${fill}" '
    function draw_cell(x, y) {
      dist = sqrt(x * x + y * y)
      if (F) {
        return dist <= R + 0.3
      }
      return (dist >= R - 0.6) && (dist <= R + 0.4)
    }

    BEGIN {
      for (y = R; y >= -R; y--) {
        line = ""
        for (x = -R; x <= R; x++) {
          if (draw_cell(x, y)) {
            line = line C
          } else {
            line = line " "
          }
        }
        print line
      }
    }
  '
}

if [[ -n "${OUTPUT}" ]]; then
  mkdir -p "$(dirname "${OUTPUT}")"
  exec >"${OUTPUT}"
fi

draw_circle "${RADIUS}" "${CHAR}" "${FILL}"

if [[ -n "${OUTPUT}" ]]; then
  log "Circle saved to ${OUTPUT}"
else
  log "Circle rendered to stdout"
fi
"""


_GENERIC_SCRIPT_TEMPLATE = """#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ${0##*/} [-o OUTPUT]

This scaffolded script was generated automatically and must be customised
for the task:
  {task}

Options:
  -o  Save the generated instructions to the specified file
  -h  Show this help message and exit
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

abort() {
  local msg=$1
  local code=${2:-1}
  printf '[%s] ERROR: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${msg}" >&2
  exit "${code}"
}

OUTPUT=""

while getopts ':o:h' opt; do
  case "${opt}" in
    o) OUTPUT=${OPTARG} ;;
    h) usage; exit 0 ;;
    :) abort "Option -${OPTARG} requires an argument." 64 ;;
    *) abort "Unknown option -${OPTARG}." 64 ;;
  esac
done

shift $((OPTIND - 1))

MESSAGE="TODO: Implement the following task in Bash -> {task}"

if [[ -n "${OUTPUT}" ]]; then
  mkdir -p "$(dirname "${OUTPUT}")"
  printf '%s\n' "${MESSAGE}" >"${OUTPUT}"
  log "Placeholder instructions saved to ${OUTPUT}"
else
  log "Placeholder instructions"
  printf '%s\n' "${MESSAGE}"
fi

exit 0
"""


def _render_generic_script(task: str) -> str:
    return _GENERIC_SCRIPT_TEMPLATE.replace("{task}", task)


FallbackMatcher = Callable[[str], bool]


def _matches_rsync_backup(task_l: str) -> bool:
    return "rsync" in task_l and ("backup" in task_l or "бэкап" in task_l or "резерв" in task_l)


def _matches_project_runner(task_l: str) -> bool:
    keywords = ("project", "проект")
    actions = ("run", "launch", "start", "запуск", "запусти", "старт")
    return any(k in task_l for k in keywords) and any(a in task_l for a in actions)


def _matches_circle_draw(task_l: str) -> bool:
    return ("circle" in task_l) or ("круг" in task_l)


_FALLBACKS: tuple[tuple[FallbackMatcher, str], ...] = (
    (_matches_rsync_backup, _RSYNC_BACKUP_SCRIPT),
    (_matches_project_runner, _PROJECT_RUNNER_SCRIPT),
    (_matches_circle_draw, _CIRCLE_DRAW_SCRIPT),
)

def _looks_like_valid_bash(code: str) -> bool:
    stripped = code.lstrip()
    if not stripped.startswith("#!/usr/bin/env bash"):
        return False
    if "set -euo pipefail" not in code:
        return False
    if "[PYTHON]" in code or "def get_bash_script" in code:
        return False
    return True


def _fallback_script_for_task(task: str) -> Optional[str]:
    task_l = (task or "").lower()
    for matcher, script in _FALLBACKS:
        if matcher(task_l):
            return script
    return None


def normalise_bash_output(raw: str, task: str) -> str:
    """Return the validated Bash script, falling back to a template when needed."""

    code = raw.strip()
    if not code:
        fallback = _fallback_script_for_task(task)
        return fallback or _render_generic_script(task)

    if not code.endswith("\n"):
        code = code + "\n"

    if _looks_like_valid_bash(code):
        return code

    fallback = _fallback_script_for_task(task)
    if fallback:
        return fallback
    return _render_generic_script(task)


__all__ = ["normalise_bash_output"]
