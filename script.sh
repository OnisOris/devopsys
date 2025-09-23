#!/usr/bin/env bash
# list_files.sh – List all files (including hidden) in a directory.
#
# Usage:
#   ./list_files.sh <directory>
#
# Exit codes:
#   0  – Success, files listed.
#   1  – Directory does not exist or is not readable.
#   2  – Wrong number of arguments.

set -euo pipefail

# ------------------------------------------------------------------
# Print usage information
usage() {
    cat <<EOF
Usage: $(basename "$0") <directory>

List all files (including hidden ones) in the specified directory.
The list includes regular files, directories, and other file types,
but excludes '.' and '..'.

Exit codes:
  0 – Success
  1 – Directory does not exist or is unreadable
  2 – Incorrect number of arguments
EOF
}

# ------------------------------------------------------------------
# Argument validation
if [[ $# -ne 1 ]]; then
    echo "Error: Exactly one argument required." >&2
    usage
    exit 2
fi

DIR="$1"

# ------------------------------------------------------------------
# Check that the directory exists and is readable
if [[ ! -d "$DIR" ]]; then
    echo "Error: '$DIR' is not a directory or does not exist." >&2
    exit 1
elif [[ ! -r "$DIR" ]]; then
    echo "Error: Directory '$DIR' is not readable." >&2
    exit 1
fi

# ------------------------------------------------------------------
# List files, including hidden ones, but excluding '.' and '..'
# Use find with -mindepth 1 to skip the directory itself.
find "$DIR" -mindepth 1 -print0 | xargs -0 printf '%s\n' | sort

exit 0
