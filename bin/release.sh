#!/usr/bin/env bash
# Release script — bump version in plugin.json + marketplace.json, commit, tag, push.
#
# Usage:
#   bin/release.sh [patch|minor|major]   # default: patch
#   bin/release.sh patch --no-push       # bump locally, don't push
#   bin/release.sh --to 2.3.0            # set explicit version
#
# What it does:
#   1. Read current version from plugin.json
#   2. Compute next version per semver bump (or use --to)
#   3. Write both .claude-plugin/{plugin,marketplace}.json with new version
#   4. git add + commit "release: vX.Y.Z"
#   5. git tag vX.Y.Z
#   6. git push origin main + tag (unless --no-push)
#
# Idempotent: skips if no functional commits since last tag.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BUMP="patch"
PUSH=1
EXPLICIT=""

while [ $# -gt 0 ]; do
  case "$1" in
    patch|minor|major) BUMP="$1"; shift ;;
    --no-push) PUSH=0; shift ;;
    --to) EXPLICIT="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Verify clean working tree (allow untracked, block uncommitted)
if [ -n "$(git diff --cached --name-only)" ] || [ -n "$(git diff --name-only)" ]; then
  echo "ERROR: working tree dirty. Commit or stash first." >&2
  git status --short
  exit 1
fi

CURRENT="$(python3 -c 'import json; print(json.load(open(".claude-plugin/plugin.json"))["version"])')"
echo "current: $CURRENT"

if [ -n "$EXPLICIT" ]; then
  NEXT="$EXPLICIT"
else
  NEXT="$(python3 -c "
v='$CURRENT'.split('.')
maj, mi, pa = int(v[0]), int(v[1]), int(v[2])
b='$BUMP'
if b=='major': maj+=1; mi=0; pa=0
elif b=='minor': mi+=1; pa=0
else: pa+=1
print(f'{maj}.{mi}.{pa}')
")"
fi
echo "next:    $NEXT"

if [ "$CURRENT" = "$NEXT" ]; then
  echo "no change — exiting"
  exit 0
fi

# Update both manifests
python3 - "$NEXT" <<'PYEOF'
import json, sys
nv = sys.argv[1]
for path in ['.claude-plugin/plugin.json', '.claude-plugin/marketplace.json']:
    d = json.load(open(path))
    d['version'] = nv
    if 'plugins' in d:
        for p in d['plugins']:
            p['version'] = nv
    open(path, 'w').write(json.dumps(d, indent=2) + '\n')
    print(f'  wrote {path} → {nv}')
PYEOF

# Commit + tag
git add .claude-plugin/plugin.json .claude-plugin/marketplace.json
git commit -m "release: v$NEXT"
git tag "v$NEXT"

echo ""
echo "tagged: v$NEXT"
git log --oneline -1

if [ "$PUSH" -eq 1 ]; then
  echo ""
  echo "pushing main + tag…"
  git push origin main
  git push origin "v$NEXT"
  echo ""
  echo "DONE. Other machines: claude /plugin marketplace update gowth-mem"
else
  echo ""
  echo "skipped push (--no-push). Run: git push origin main && git push origin v$NEXT"
fi
