#!/usr/bin/env bash
# Thin wrapper that forwards to hooks/scripts/_migrate_v3.py.
# Lives under bin/ so it's discoverable next to doctor.sh + rollback-v3.sh.
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$HERE/hooks/scripts/_migrate_v3.py" "$@"
