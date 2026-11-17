#!/usr/bin/env bash
# Build the haplous wheel and test it in fresh venvs: bare, [sim], [studio].
# Usage: scripts/wheel_smoke.sh [WORKDIR]   (needs uv; WORKDIR defaults to a new temp dir)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${1:-$(mktemp -d)}"
mkdir -p "$WORK"
WORK="$(cd "$WORK" && pwd)"
cd "$WORK"  # never run from the checkout, so imports resolve to the installed wheel

step() { printf '\n=== %s\n' "$*"; }
fail() { echo "FAIL: $*"; exit 1; }

step "build wheel"
rm -rf "$WORK/dist"
uv build --wheel --out-dir "$WORK/dist" "$REPO"
WHEEL="$(ls "$WORK"/dist/haplous-*.whl)"
echo "$WHEEL"

fresh_venv() {  # fresh_venv NAME REQUIREMENT
  rm -rf "$WORK/$1"
  uv venv --quiet --python 3.12 "$WORK/$1"
  uv pip install --quiet --python "$WORK/$1/bin/python" "$2"
}

step "bare: install"
fresh_venv bare "$WHEEL"
B="$WORK/bare/bin"
step "bare: import haplous from site-packages, no extra modules loaded"
"$B/python" -c "
import sys, haplous, haplous.cli
assert 'site-packages' in haplous.__file__, haplous.__file__
loaded = [m for m in ('mujoco', 'numpy', 'rerun', 'fastapi') if m in sys.modules]
assert not loaded, loaded
print('haplous', haplous.__version__, 'from', haplous.__file__)"
step "bare: stub-machine contract"
"$B/python" "$REPO/examples/stub_machine_contract.py"
step "bare: haplous --version / --help"
"$B/haplous" --version
"$B/haplous" --help
step "bare: haplous doctor"
"$B/haplous" doctor
step "bare: haplous demo names the missing extra"
set +e; out="$("$B/haplous" demo 2>&1)"; rc=$?; set -e
echo "$out (rc=$rc)"
[ "$rc" = 2 ] && grep -q "'sim' extra" <<<"$out" || fail "demo did not name the sim extra"
step "bare: package list"
uv pip list --python "$B/python"
if uv pip list --python "$B/python" | grep -Eiq '^(mujoco|numpy|rerun-sdk|fastapi) '; then
  fail "bare venv has a simulator/UI dependency"
fi
echo "bare: no mujoco/numpy/rerun-sdk/fastapi"

step "[sim]: install"
fresh_venv sim "$WHEEL[sim]"
step "[sim]: haplous demo (MuJoCo episodes through the runtime)"
HAPLOUS_RESULTS_DIR="$WORK/sim/results" HAPLOUS_ARTIFACTS_DIR="$WORK/sim/artifacts" "$WORK/sim/bin/haplous" demo

step "[studio]: install"
fresh_venv studio "$WHEEL[studio]"
PORT="${STUDIO_PORT:-18765}"
"$WORK/studio/bin/haplous" studio --no-browser --port "$PORT" --root "$WORK/studio/root" >"$WORK/studio/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
code=000
for _ in $(seq 60); do
  code="$(curl -s -o "$WORK/studio/runs.json" -w '%{http_code}' "http://127.0.0.1:$PORT/api/runs" || true)"
  [ "$code" = 200 ] && break
  sleep 1
done
step "[studio]: GET /api/runs -> $code"
head -c 400 "$WORK/studio/runs.json"; echo
[ "$code" = 200 ] || { cat "$WORK/studio/server.log"; fail "studio /api/runs"; }

step "wheel smoke: PASS ($WORK)"
