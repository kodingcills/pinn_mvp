# GPU execution handoff: Campaign 0A, G1 (one π0.5 episode)

**Status (iteration 3): ACCELERATOR_DEPENDENT, not requested.** No GPU is
available and no approval is requested this milestone. Nothing here has run on a
GPU. Checkpoint step 0 **is now done** (2026-11-04 13:52Z; `huggingface.co`
became reachable).

LIBERO-Dynamic is pinned at `ef12443b7c1f0699356eaf2a4e1676e034f2d806`.

## What is already established on CPU (no GPU needed)

| Fact | Evidence |
|---|---|
| Formal evaluator runs, is deterministic, and passes upstream acceptance | runs `c0a-smoke-l1-n4-s40-001/002/003`; traces byte-identical |
| Fixed sync latency advances simulated time by exactly L (0.03 s, 0.147199 s; not rounded to 0.05 s control periods) | runs `c0a-latency-0.03-n1-s40-001`, `c0a-latency-0.147199-n1-s40-001`; `tests/test_libero_dynamic_evidence.py` |
| Simulated latency is independent of wall-clock inference time | manifests `sim_latency_decoupled_from_wallclock: true`, `physics_time_quantized_to_control_steps: false` |
| The zero policy emits 1-action chunks, so it pays latency every step. A real K=10 policy should pay it once per 10 steps | trace `merge_appended: 1`; G1 verifies the real-policy case |

So GPU speed changes throughput, not the benchmark condition. **GPU numerics
are a different matter.** bf16 kernels differ across GPU types, so actions (and
therefore outcomes) can differ.

## Hardware-consistency rule (binding for every comparison)

- `provenance.gpu` is measured with `nvidia-smi` and is **required** for
  `sim_model_in_loop` manifests (schema-enforced).
- Every arm of a comparison runs on the **same GPU type**.
  `Registry.compare()` reports `hardware_comparable`. Results with
  `false` are not compared unless an explicit cross-hardware A/A check
  (same arm, both GPU types, same seeds) has been run and recorded.

## Step 0 (done): pinned and measured checkpoint

Command (one attempt, as required):

```bash
.venv/bin/python scripts/verify_hf_checkpoint.py --repo rsj2003/libero-dynamic-paper-models --path pi05/static_official --out results/checkpoints/pi05_static_official.json
```

| Item | Value | Source |
|---|---|---|
| Repo / path | `rsj2003/libero-dynamic-paper-models` / `pi05/static_official` | |
| Pinned revision | `ffd700087b34b734805ca5148e278a6a61fa5a8a` | HF API `revision/main` |
| Format | LeRobot PyTorch `safetensors` (`config.json` `"type": "pi05"`), so the **PyTorch path applies** (not JAX) | file list + config |
| Architecture | `paligemma_variant: gemma_2b`, `action_expert_variant: gemma_300m`, config `dtype: bfloat16` | config.json |
| Action interface | `chunk_size: 50`, `n_action_steps: 50`, `num_inference_steps: 10`, 7-D action, 8-D state, 2 images at 256×256 (+1 empty 224 camera) | config.json |
| Parameters | **3,616,758,006** = 3,497,036,912 BF16 + 119,720,608 F32 | safetensors header (byte-range read) |
| Weight bytes | **7,472,958,200 B = 6.96 GiB** (6,994,073,824 BF16 + 478,882,432 F32) | header dtype × shape, offsets cross-checked |
| `model.safetensors` | 7,473,096,344 B, SHA-256 `877b3ec1130548b69af7f8aeef3ec9d3fc7738040f0b9beb490857ec970997ae` | HF LFS oid; local re-hash: see below |
| `config.json` | SHA-256 `2f6d4b96b032593e1de65b391ee7252a70227bff398ef1b94658fea17818142f` | fetched + hashed locally |
| normalizer / unnormalizer stats | both `a002c0df7f79c5b169c5a899ad151d4ea1bed246c7d82bd93ed1556558d517a9` (identical bytes) | LFS oid |
| Provenance (config) | `pretrained_path: lerobot/pi05_libero`, `repo_id: pepijn223/pi05_libero_31_9_non_q` | config.json (stated by uploader, not verified) |

All hashes and the full file list are in `results/checkpoints/pi05_static_official.json`.
The full local re-hash (streamed, `--download`, 2026-11-04) **completed: all 9 files
match**, including `model.safetensors` against its LFS SHA-256
(`results/checkpoints/pi05_static_official_rehashed.json`).

**Still unknown:** whether this artifact is the one behind upstream's 147.199 ms
π0.5-PyTorch result (upstream does not say). The config's `n_action_steps: 50`
differs from the evaluation's `--replan-steps 10`. The launcher sets the executed
horizon, and G1 must confirm which value takes effect.

**GPU sizing (computed, not from secondary sources):** weights are 6.96 GiB, a
hard lower bound. Using the stated heuristic (1.5 × weights + 4 GiB) gives
≈ 14.4 GiB, so **a 16 GB card is the minimum candidate and 24 GB gives
margin**. Activation memory for 10 flow steps over a 50-step chunk with three
images has **not been measured**. G1's load-only step records
`torch.cuda.max_memory_allocated`, and the GPU choice stays provisional until
then.

## G1 job: exactly one episode, then report

`scripts/gpu/g1_one_episode.sh` (syntax-checked; never executed). On the
approved instance it:

| Step | Action | Expected output | Hard stop |
|---|---|---|---|
| 0 | refuse unless LIBERO-Dynamic is at `ef12443`; record `nvidia-smi` | `g1_logs/nvidia-smi.csv` | wrong revision (exit 3) |
| 1 | load π0.5 on the GPU, no episode | `g1_logs/load_check.json`: params, dtypes, `max_memory_allocated_bytes`, device | load failure / OOM / >15 min (exit 4) |
| 2 | one episode: `--level l1 --trajectory 0 --task-count 1 --replan-steps 10 --max-steps 600` (default), `sync_hold`, fixed latency 0.147199 s, recorded as an M0 `sim_model_in_loop` run with the loaded file's SHA-256 | M0 run `g1-pi05-sync-1ep-001` in state `VERIFIED`; per-item manifest + `runtime-v2` trace | wall-clock cap (`timeout`) |
| 3 | scan artifacts for OOM/CUDA errors | none | exit 5 |
| 3 | require a trace in the expected layout | trace exists | exit 7 (else the timing tests would pass vacuously) |
| 3 | rerun `tests/test_libero_dynamic_evidence.py` + registry integrity on the new run | latency held exactly 0.147199 s at replan boundaries only, sim time = 0.05·steps + Σlatency | exit 6 |
| — | **stop and report** | — | no further episodes without a new approval |

Task success of one episode is **not** a result. It is reported, not interpreted.

**Instance termination (checked in iteration 3):** process exit alone does NOT
stop billing. `TERMINATE_CMD` is now **required**: the script refuses to start
without it, and the `EXIT` trap runs it with 3 attempts, logging success or
`INSTANCE MAY STILL BE BILLING`. A test confirmed that bash runs the `EXIT` trap on
SIGTERM, SIGINT and SIGHUP. SIGKILL and host hangs cannot be trapped, so the
provider-side spending limit / auto-terminate remains mandatory.

**Budget enforcement (automatic):** the script computes `min(MAX_USD /
HOURLY_USD, 2 h) − 10 min` and runs the episode under `timeout`. On *any* exit
(success, stop, crash, timeout), an `EXIT` trap copies `results/m0_registry` and
`g1_logs` to `PERSIST_DEST` with `rclone --checksum`, then runs `TERMINATE_CMD`
(for example `runpodctl remove pod $RUNPOD_POD_ID`). Also set the provider's
own spending limit / auto-terminate where it has one. The in-instance trap does
not protect against a hung host.

**Persistence:** `PERSIST_DEST` must be object storage outside the instance
(any rclone remote). Then commit `results/m0_registry/runs/<g1 run>` to the
branch and run `python -m evaluation.run_registry verify` on it.

## Environment (from the pinned audit)

| Item | Value |
|---|---|
| Evaluator env | Python 3.10, robosuite 1.4.0, MuJoCo 2.3.7, `MUJOCO_GL=egl` |
| Policy server env | LeRobot revision exposing `lerobot.policies.pi05.modeling_pi05.PI05Policy` (0.5.x-compatible). Pin it and record the revision |
| CUDA | upstream validated torch 2.13.0 + CUDA 13.0 user space, so host driver **R580+** |
| Tokenizer | local PaliGemma tokenizer directory (avoids implicit download) |

## Cost (unverified prices)

Vendor pricing pages were blocked. Third-party figures (Oct 2026), to be
re-checked at launch: L4 24 GB about $0.39–0.49/h, RTX 4090 24 GB about
$0.34–0.89/h. G1 (setup + load + one 600-step episode): an estimated 1–2.5 h, so
**$0.4–$2.3**. The approval cap below bounds it regardless of these estimates.

## Later phases (each needs its own approval)

G2: a 40-item subset, sync + async, same GPU type. Compare against upstream's
per-speed rate only as a sanity bound (see LIBERO_DYNAMIC_AUDIT.md for how speed
is set via `alpha_mps`). G3: the full 1,800-episode population.

## Approval request (structured)

```yaml
# NOT SUBMITTED in iteration 3 (no GPU available; ACCELERATOR_DEPENDENT). Kept for when one is.
request: provision paid GPU compute for G1 only
scope: load pi0.5 PyTorch + run exactly 1 LIBERO-Dynamic episode (Tier 1 development; no holdouts, no hidden seeds)
prerequisite: step 0 completed (checkpoint revision, format, weight bytes and SHA-256 recorded)
resource: 1 on-demand GPU sized by the step-0 rule; no reserved billing, no auto-renew
budget_cap_usd: 5
max_wall_hours: 2
auto_termination: in-instance timeout + EXIT trap (sync, then TERMINATE_CMD) + provider spending limit
artifact_destination: rclone remote outside the instance (to be named by the approver)
needs_human_decision:
  - provider/account and rclone destination
  - (resolved) huggingface.co reachable; step 0 done, see table above
not_requested: further episodes, Tier 3 holdouts, physical robots, external claims
```
