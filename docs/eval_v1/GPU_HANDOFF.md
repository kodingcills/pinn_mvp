# GPU execution handoff: Campaign 0A, first model-in-the-loop validation

**Status: NOT AUTHORIZED.** Provisioning paid GPU compute needs explicit human
approval (see the approval request at the end). Nothing in this document has
been run on a GPU. Every GPU-side number is an estimate, and its assumptions are
stated.

## Why this run (the narrowest one that reduces the most uncertainty)

The LIBERO-Dynamic execution-strategy comparison holds the π0.5 PyTorch
checkpoint fixed and varies only the execution layer (sync hold / async queue /
RTC). That is the comparison a Haplous execution arm would join. The first GPU
job therefore checks that we can **reproduce upstream's own sync and async
numbers on a subset** before any Haplous arm exists. If that fails, no Haplous
comparison on this benchmark is meaningful.

## Requirements (source: pinned audit, `LIBERO_DYNAMIC_AUDIT.md`)

| Requirement | Value | Basis |
|---|---|---|
| Evaluator env | Python 3.10, robosuite 1.4.0, MuJoCo 2.3.7, EGL | read + CPU-measured |
| Policy server env | LeRobot revision exposing `lerobot.policies.pi05.modeling_pi05.PI05Policy` (0.5.x-compatible). Current `main` may need Python ≥3.12 | upstream `examples/README.md` |
| CUDA | upstream validated torch 2.13.0 + CUDA 13.0 user space. **Host driver R580+** if we keep that stack | lock file + reproducibility notes |
| Checkpoint | `pi05/static_official/` in `rsj2003/libero-dynamic-paper-models` (HF), or the official LeRobot π0.5 LIBERO checkpoint, plus a local PaliGemma tokenizer | upstream docs. **HF unreachable from this session; files/sizes unverified** |
| VRAM | Estimate: π0.5 is ≈3B parameters (public model descriptions, *not verified here*), so bf16 weights ≈ 6–7 GB. With activations and the CUDA context, **≥16 GB is probably enough; 24 GB gives margin** | estimate |
| GPU features | bf16 support (Ampere or newer). Blackwell not required | estimate |
| Latency | Use the **published calibrated latency 0.147199 s** with `--latency-source fixed`. The simulator then advances the same simulated time whatever the GPU speed, so a slower GPU changes throughput, not the benchmark condition | upstream `results/execution_strategy_best_points.csv`; `sim_latency_decoupled_from_wallclock: true` measured in our CPU manifests |

**Smallest viable configuration (recommendation):** 1× NVIDIA L4 24 GB, or
1× RTX 4090 24 GB, with ≥8 vCPU, ≥32 GB RAM and ≥100 GB persistent disk. An
RTX 5090 (the paper's GPU) is not needed, because latency is fixed rather than
measured.

## Cost estimate (unverified prices)

Vendor pricing pages (runpod.io, lambda.ai) and huggingface.co were **blocked by
this session's network policy**. The prices below come from third-party
summaries found by web search (Oct 2026) and **must be re-checked on the vendor
page at launch**:

| GPU | Reported $/h (range across sources) |
|---|---|
| L4 24 GB (RunPod) | 0.39 – 0.49 |
| RTX 4090 24 GB (RunPod) | 0.34 (community) – 0.89 (secure) |

Time assumptions (not measured on GPU): setup and downloads 1.0–2.0 h. Per
600-step episode: inference ≈ 60 requests (K=10) × ~0.15 s ≈ 9 s GPU, plus
simulation/rendering with EGL, *assumed* 20–60 s. On CPU/OSMesa we measured
44–91 s per **40-step** item, so CPU timings cannot stand in for GPU ones.

| Phase | Episodes | Est. wall time | Est. cost @ $0.35–0.90/h |
|---|---|---|---|
| G1 smoke (1 episode per mode, 3 modes) | 3 | 1.5–2.5 h incl. setup | $0.5 – $2.3 |
| G2 subset (`--levels l1 --traj-indices 0`, 40 items, sync + async) | 80 | 0.5–1.5 h | $0.2 – $1.4 |
| **Hard budget cap for G1+G2** | — | **5 h** | **≤ $5** |
| Full population, 2 modes (later, separate approval) | 3,600 | 20–60 h (1 worker) | $7 – $54 |

The uncertainty is dominated by per-episode simulation time on the GPU host.
G1 measures it, and G2/full costs should be re-estimated from that value before
continuing.

## Exact commands (to run on the approved instance)

```bash
# 0. system
sudo apt-get install -y git libegl1 libgl1 ffmpeg
nvidia-smi   # record driver version; must be >= 580 for the cu130 stack
# 1. evaluator (pinned)
git clone https://github.com/renshaojie233/LIBERO-Dynamic.git && cd LIBERO-Dynamic
git checkout ef12443b7c1f0699356eaf2a4e1676e034f2d806
uv venv -p 3.10 ../libero-env && VIRTUAL_ENV=../libero-env uv pip install -r requirements.txt && VIRTUAL_ENV=../libero-env uv pip install -e . --no-deps
export LIBERO_CONFIG_PATH=$PWD/libero/libero/configs MUJOCO_GL=egl
# 2. CPU-parity check first: must reproduce our committed smoke result
#    (from a Haplous checkout on branch claude/magical-curie-si1vjr)
python scripts/run_libero_dynamic_cpu_smoke.py --libero-src LIBERO-Dynamic --libero-python libero-env/bin/python --run-id g1-parity-001
# 3. policy server env (separate), per upstream examples/README.md §2
git clone https://github.com/huggingface/lerobot.git   # pin the revision you check out; record it
#    pip install -e ".[pi]" websockets msgpack-numpy huggingface_hub transformers sentencepiece protobuf tiktoken
#    download checkpoint + PaliGemma tokenizer to /persist/ckpt (record sha256 of model.safetensors)
# 4. G1 smoke, one per mode, published latency
python examples/evaluate_pi05_pytorch.py       --openpi-root ... --server-python ... --eval-python ../libero-env/bin/python --checkpoint /persist/ckpt/pi05 --local-tokenizer /persist/ckpt/paligemma_tokenizer --output-dir /persist/out/g1-sync  --gpu 0 --calibrated-latency-seconds 0.147199
python examples/evaluate_pi05_pytorch_async.py (same args) --output-dir /persist/out/g1-async
python examples/evaluate_pi05_pytorch_rtc.py   (same args) --output-dir /persist/out/g1-rtc
```

Run every step through the M0 registry (`python -m evaluation.run_registry run
--track sim_model_in_loop ...`). A `sim_model_in_loop` manifest is refused
unless it has a `policy` block with a checkpoint URI and a real `checkpoint_sha256`.
**Do not fill these in by hand from documentation.** Hash the file you actually
loaded.

`--calibrated-latency-seconds` is confirmed in `examples/_common.py:55`.

## Persistence

Containers are ephemeral. Before termination:
1. Put checkpoints and outputs on a persistent volume (`/persist`) or sync them
   to object storage (`rclone`/`gsutil`) after every phase.
2. Commit `results/m0_registry/` (ledger + manifests + small artifacts) to the
   branch. Store large artifacts (videos, full traces) in object storage, keeping
   only their SHA-256 in the M0 artifact list.
3. Run `python -m evaluation.run_registry verify --root results/m0_registry <run>`
   after copying, and confirm `problems: []`.

## Stopping criteria

Stop and report (do not continue to the next phase) if any of these happens:
- the G1 parity smoke does not reproduce the committed CPU smoke (any non-wall-clock field differs in `scripts/compare_libero_smoke_runs.py`);
- any G1 episode has `status != ok` or an infrastructure error;
- the RTC run lacks `rtc_conditioning_supported=True`. That run is then invalid as RTC evidence; record it and continue with sync/async only;
- G2 sync success on the 40-item subset falls outside the Wilson 95% interval
  around upstream's sync rate at the speed used. Patch `alpha_mps` the way
  `scripts/launch_pi05_sync_speed_sweep_20260606.sh` does, e.g. 0.06 →
  upstream 0.425 over 600 episodes. This is only a crude sanity bound: one
  motion family × one trajectory is not a random sample of the 600. A miss is a
  reproduction failure to diagnose. Do not use it as a finding;
- cumulative spend reaches the approved cap.

## Approval request (structured)

```yaml
request: provision paid GPU compute
scope: Campaign 0A phases G1 + G2 only (Tier 1 development; no holdouts, no hidden seeds)
resource: 1x L4 24GB or 1x RTX 4090 24GB, on-demand, no auto-renew, no reserved billing
budget_cap_usd: 5
max_wall_hours: 5
data_leaving_instance: results/m0_registry + summaries committed to branch claude/magical-curie-si1vjr
needs_human_decision:
  - provider/account to use (none configured in this session)
  - whether to allow huggingface.co and the provider's domains in this environment's network policy
  - acceptance of the unverified price figures above (re-check at launch)
not_requested: Tier 3 holdout execution, physical robot deployment, external publication
```
