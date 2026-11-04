#!/usr/bin/env bash
# Campaign 0A, GPU step G1: load pi0.5 (PyTorch/LeRobot) and run EXACTLY ONE LIBERO-Dynamic episode.
# NOT AUTHORIZED TO RUN until a human approves docs/eval_v1/GPU_HANDOFF.md (paid compute).
#
# Required env:
#   HOURLY_USD, MAX_USD     price of THIS instance (read from the provider at launch) and the approved cap
#   PERSIST_DEST            rclone destination outside the instance, e.g. "r2:haplous-evidence/g1"
#   CKPT_DIR, TOKENIZER_DIR local pi0.5 PyTorch checkpoint + PaliGemma tokenizer (from the pinned HF revision)
#   CKPT_URI                hf://<repo>@<revision_sha>/<path> recorded by scripts/verify_hf_checkpoint.py
#   LEROBOT_PY, OPENPI_ROOT policy-server python and openpi checkout (websocket client)
# Optional: TERMINATE_CMD   command that destroys this instance (e.g. 'runpodctl remove pod $RUNPOD_POD_ID')
set -euo pipefail

: "${HOURLY_USD:?}" "${MAX_USD:?}" "${PERSIST_DEST:?}" "${CKPT_DIR:?}" "${TOKENIZER_DIR:?}" "${CKPT_URI:?}"
: "${LEROBOT_PY:?}" "${OPENPI_ROOT:?}"
HAPLOUS=${HAPLOUS:-$PWD}
LIBERO=${LIBERO:-$HAPLOUS/../LIBERO-Dynamic}
LIBERO_PY=${LIBERO_PY:-$HAPLOUS/../libero-env/bin/python}
REV=ef12443b7c1f0699356eaf2a4e1676e034f2d806
LATENCY=0.147199            # upstream published pi0.5-PyTorch calibrated latency (fixed protocol)
RUN_ID=${RUN_ID:-g1-pi05-sync-1ep-001}
OUT=$HAPLOUS/results/m0_registry

# Wall-clock cap = min(dollar cap / hourly price, 2 h), minus 10 min reserved for sync + teardown.
MAX_S=$(python3 -c "print(int(min(float('$MAX_USD')/float('$HOURLY_USD')*3600, 7200)) - 600)")
[ "$MAX_S" -gt 600 ] || { echo "STOP: budget allows < 20 min"; exit 2; }

finish() {
  rc=$?
  echo "[g1] exit=$rc; syncing evidence to $PERSIST_DEST"
  rclone copy "$OUT" "$PERSIST_DEST/m0_registry" --checksum || echo "[g1] WARNING: sync failed"
  rclone copy "$HAPLOUS/g1_logs" "$PERSIST_DEST/g1_logs" --checksum || true
  if [ -n "${TERMINATE_CMD:-}" ]; then eval "$TERMINATE_CMD"; fi
}
trap finish EXIT

mkdir -p "$HAPLOUS/g1_logs"
cd "$HAPLOUS"
[ "$(git -C "$LIBERO" rev-parse HEAD)" = "$REV" ] || { echo "STOP: LIBERO-Dynamic not at $REV"; exit 3; }
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv | tee g1_logs/nvidia-smi.csv

# Step 1: load-only check of the checkpoint on this GPU (OOM / load failure => hard stop).
timeout 900 "$LEROBOT_PY" - "$CKPT_DIR" > g1_logs/load_check.json 2> g1_logs/load_check.err <<'PY' \
  || { echo "STOP: model load failed (see g1_logs/load_check.err)"; exit 4; }
import json, sys, torch
from lerobot.policies.pi05.modeling_pi05 import PI05Policy
p = PI05Policy.from_pretrained(sys.argv[1]).to("cuda").eval()
torch.cuda.synchronize()
print(json.dumps({"params": sum(t.numel() for t in p.parameters()),
                  "dtypes": sorted({str(t.dtype) for t in p.parameters()}),
                  "max_memory_allocated_bytes": torch.cuda.max_memory_allocated(),
                  "device": torch.cuda.get_device_name(0)}))
PY
cat g1_logs/load_check.json

# Step 2: exactly one episode, sync_hold, fixed published latency, recorded as an M0 sim_model_in_loop run.
CKPT_SHA=$(sha256sum "$CKPT_DIR/model.safetensors" | cut -c1-64)
timeout "$MAX_S" .venv/bin/python - <<PY
from evaluation.run_registry import Registry, record_run
r = record_run(Registry("$OUT"), run_id="$RUN_ID", track="sim_model_in_loop",
    purpose="G1: one pi0.5 episode, sync_hold, fixed latency $LATENCY s",
    # bash -c so the output dir expands to the registry's per-run \$M0_ARTIFACT_DIR.
    command=["bash", "-c", 'exec "\$@" --output-dir "\$M0_ARTIFACT_DIR/out"', "_",
             "$LIBERO_PY", "examples/evaluate_pi05_pytorch.py", "--openpi-root", "$OPENPI_ROOT",
             "--server-python", "$LEROBOT_PY", "--eval-python", "$LIBERO_PY",
             "--checkpoint", "$CKPT_DIR", "--local-tokenizer", "$TOKENIZER_DIR",
             "--level", "l1", "--trajectory", "0", "--task-count", "1", "--replan-steps", "10",
             "--calibrated-latency-seconds", "$LATENCY", "--gpu", "0"],
    config={"latency_s": $LATENCY, "episodes": 1, "controller": "sync_hold"},
    simulator={"name": "mujoco+robosuite", "version": "mujoco 2.3.7; robosuite 1.4.0", "render_backend": "egl"},
    policy={"name": "pi05-pytorch", "checkpoint_uri": "$CKPT_URI", "checkpoint_sha256": "$CKPT_SHA"},
    external_sources=[{"url": "https://github.com/renshaojie233/LIBERO-Dynamic", "revision": "$REV"}],
    cwd="$LIBERO", env={"MUJOCO_GL": "egl", "LIBERO_CONFIG_PATH": "$LIBERO/libero/libero/configs", "PYTHONPATH": "$LIBERO"},
    subject_python="$LIBERO_PY", subject_dependencies=("mujoco", "robosuite", "numpy", "libero"),
    timeout_s=$MAX_S - 120)
print(r["state"], r["outcome"])
PY

# Step 3: hard stops on OOM and timing-semantics deviation; report and stop either way.
if grep -rqiE "out of memory|CUDA error" "$OUT/runs/$RUN_ID/artifacts"; then echo "STOP: OOM/CUDA error"; exit 5; fi
# The evidence tests glob out/<level>/<suite>/traces; a different launcher layout would make them vacuous.
ls "$OUT/runs/$RUN_ID"/artifacts/out/*/*/traces/*.jsonl >/dev/null 2>&1 \
  || { echo "STOP: no runtime-v2 trace in the expected layout; timing semantics unchecked"; exit 7; }
.venv/bin/python -m pytest tests/test_libero_dynamic_evidence.py tests/test_run_registry.py -q -k "latency or committed" \
  || { echo "STOP: timing semantics or evidence integrity deviated"; exit 6; }
echo "[g1] one episode complete; report before any further episodes."
