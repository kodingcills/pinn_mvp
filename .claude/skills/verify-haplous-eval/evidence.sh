#!/usr/bin/env bash
# Usage: evidence.sh REGISTRY_ROOT RUN_ID OUT_DIR [TRUSTED_ROOT]
# Runs the independent archive checks on one M0 recording and writes every
# output under OUT_DIR. TRUSTED_ROOT (e.g. the committed results/recordings/registry)
# adds a trust_root check of artifact hashes. Exit 0 only if every check passes.
set -u
[ $# -ge 3 ] || { sed -n 2,5p "$0"; exit 2; }
root=$(realpath -e "$1") || exit 2
run=$2; out=$(realpath -m "$3")
repo=$(cd "$(dirname "$0")/../../.." && pwd)
venv=${VENV:-$repo/.venv}
export PYTHONPATH=$repo MUJOCO_GL=${MUJOCO_GL:-osmesa}
mkdir -p "$out"
cd "$repo" || exit 2
fail=0
verdict() { printf '%-14s %s  %s\n' "$1" "$2" "$3"; [ "$2" = PASS ] || fail=1; }

"$venv/bin/python" -m evaluation.run_registry verify --root "$root" "$run" >"$out/m0_verify.json" 2>&1
probs=$("$venv/bin/python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["problems"])' "$out/m0_verify.json" 2>/dev/null || echo unreadable)
[ "$probs" = "[]" ] && verdict m0_verify PASS "problems: []" || verdict m0_verify FAIL "$probs"

"$venv/bin/haplous" replay "$run" --recordings-root "$root" --ui \
  --rrd "$out/$run.rrd" --mp4 "$out/$run.mp4" --report "$out/replay_report.json" >"$out/replay.out" 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  verdict replay PASS "$("$venv/bin/python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["pose_check"])' "$out/replay_report.json")"
else
  verdict replay FAIL "rc=$rc $(grep -iE 'refused|max_abs_error_m|"pass"' "$out/replay.out" | tail -2 | tr -s ' \n' ' ')"
fi

if [ -f "$out/$run.rrd" ]; then
  r=$("$venv/bin/rerun" rrd verify "$out/$run.rrd" 2>&1); rc=$?
  [ $rc -eq 0 ] && verdict rrd_verify PASS "$r" || verdict rrd_verify FAIL "$r"
else
  verdict rrd_verify FAIL "no .rrd written"
fi

if [ -f "$out/$run.mp4" ]; then
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$out/$run.mp4")
  distinct=$(ffmpeg -loglevel error -i "$out/$run.mp4" -f framemd5 - | grep -v '^#' | awk '{print $NF}' | sort -u | wc -l)
  sim=$("$venv/bin/python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["sim_duration_s"])' "$out/replay_report.json")
  ok=$(python3 -c "print(abs($dur-$sim) <= 1/30 + 1e-6 and $distinct > 1)")
  [ "$ok" = True ] && verdict mp4 PASS "duration=${dur}s sim=${sim}s distinct_frames=$distinct" \
                   || verdict mp4 FAIL "duration=${dur}s sim=${sim}s distinct_frames=$distinct"
else
  verdict mp4 FAIL "no .mp4 written"
fi

if [ -n "${4:-}" ]; then
  d=$("$venv/bin/python" - "$root" "$(realpath "$4")" "$run" <<'EOF'
import sys
from evaluation.run_registry import Registry
a, b = ({x["path"]: x["sha256"] for x in Registry(r).get(sys.argv[3])["artifacts"]} for r in sys.argv[1:3])
print(sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p)))
EOF
)
  [ "$d" = "[]" ] && verdict trust_root PASS "artifact hashes equal the trusted ledger" \
                  || verdict trust_root FAIL "differs from trusted ledger: $d"
fi
exit $fail
