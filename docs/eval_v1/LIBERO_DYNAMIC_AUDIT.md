# LIBERO-Dynamic source audit (Campaign 0A, CPU only)

**Pinned revision:** `ef12443b7c1f0699356eaf2a4e1676e034f2d806`
(https://github.com/renshaojie233/LIBERO-Dynamic, `main`, committed 2026-11-01).
All statements below are about this revision. Upstream's own "validated
baseline" is an *older* commit (`f1a86422…`, tag `formal-eval-final-20260622`,
per `docs/reproducibility_notes.md`); results here are not claims about that tag.

Audit method: shallow clone, reading the files named below, and running the
CPU-feasible parts. Measured items are marked **[measured]**; everything else is
read from source/docs and is **[read]**.

## Installation / dependencies

| Item | Finding |
|---|---|
| Python | 3.10 (README, lock file `requirements-lock-linux-py310-cu130.txt`) **[read]** |
| Simulator | robosuite 1.4.0, MuJoCo **2.3.7 pinned** (newer MuJoCo breaks robosuite 1.4.0 per upstream notes) **[read]**, installed and imported **[measured]** |
| numpy | 1.22.4 **[measured]** |
| `pip install -r requirements.txt` | succeeds on CPU-only Linux with `uv` **[measured]** (`setup.py` declares no deps; requirements.txt is the real list) |
| Rendering | upstream assumes EGL on GPU. On this CPU host neither EGL nor OSMesa libs existed; after `apt-get install libosmesa6 libegl1 libegl-mesa0 libgl1-mesa-dri`, `MUJOCO_GL=osmesa` works **[measured]** |
| First import | `import libero.libero` **blocks on an interactive `input()`** unless `LIBERO_CONFIG_PATH` points at a directory with `config.yaml` (`libero/libero/configs` works) **[measured]** |
| Policy envs | Each policy (openpi JAX, LeRobot PyTorch, FastWAM, VLASH) lives in its own env and talks to the evaluator over an OpenPI-compatible websocket (`websockets`, `msgpack-numpy`) **[read]** |
| GPU stack for models | torch 2.13.0, CUDA 13.0 user space (`nvidia-*-cu13`), validated on RTX 5090 / driver 580.126.18 **[read]** → host driver must be R580+ |

## Policy-free preflight: findings

1. **README's documented preflight is vacuous at this revision [measured].**
   `python scripts/preflight_dynamic_configs.py` loads the default
   `libero/libero/dynamic_config.yaml` (`enabled: false`, entries under
   `targets:`), but the script iterates `config["tasks"]`. Result:
   `num_tasks: 0`, exit code 0. It checks nothing and still reports success.
2. **The same script cannot load the released configs [measured].** With
   `--config libero/libero/dynamic_configs/libero_dynamic_l1.yaml` it fails with
   `ValueError('Unsupported dynamic carrier.type: physical_conveyor_patch')`.
   Upstream's notes say formal and zero-policy evaluation must use
   `scripts/eval_dynamic_formal.py`. So preflight is legacy, and the README
   is out of date relative to that.
3. **The documented formal zero-policy smoke passes on CPU [measured]**
   (`docs/formal_dynamic_eval.md` "Small Smoke Test", without videos). See
   EVIDENCE_REPORT.md §C.
4. **Upstream unit tests pass on CPU [measured]**: `pytest tests` →
   42 passed (M0 run `c0a-upstream-unit-tests-001`).

## Interfaces (read from source)

- **Execution loop** (`scripts/realtime_eval/`): `runtime.py` is the only code
  that calls `env.step()`. Controllers in `strategies/` (`sync_hold`,
  `async_queue`, `rtc_queue`, `vlash_future_state`) receive observation packets
  and enqueue action chunks. They never step the world. On an empty queue the
  runtime applies `hold`/`zero` and records an *underflow*.
- **Latency model, measured (iteration 2):** runs at fixed 0.03 s and
  0.147199 s show each synchronous hold advancing simulated time by exactly L
  (whole 2 ms substeps plus one residual substep:
  `render_dynamic_config_previews.py::advance_physical_dynamic_time`), not
  `ceil(L·20 Hz)`. Every step advances 0.05 + L s, to 1e-6. The **zero policy
  returns 1-action chunks**, so `sync_hold` requests (and pays latency) every
  step. Behaviour with real K-action chunks is covered only by upstream unit
  tests until G1. Checked by `tests/test_libero_dynamic_evidence.py`.
- **Latency model** (`docs/realtime_runtime_v2.md`): synchronous inference
  latency advances MuJoCo time by `ceil(latency·control_freq)` control steps
  with robot joints held while the scene moves. `--latency-source fixed
  --calibrated-latency-seconds S` is the formal protocol. Manifests record
  `sim_latency_decoupled_from_wallclock: true` **[measured in artifacts]**.
- **Async**: LeRobot-style queue. Triggers when `queue_len/chunk_size <=
  threshold`, skips stale timesteps, merges overlap as `0.3·old + 0.7·new`.
- **RTC**: passes `prev_chunk_left_over` + delay to an RTC-enabled policy, then
  drops the delay prefix and replaces the queue. A run is valid RTC evidence only
  if `rtc_conditioning_supported=True` appears in the server log/manifests.
  Upstream examples support RTC only on the π0.5 PyTorch (LeRobot) path.
- **Observations/actions**: 20 Hz action clock; 7-D executed action
  (`executed_action` length 7 in traces **[measured]**); camera inputs
  `agentview_image` + `robot0_eye_in_hand_image` (`control.py`). The exact
  robot-state vector and controller type were not verified in this audit.
  Transport is the msgpack websocket.
- **Episode scoring / storage**: per-item `manifests/*.json`, per-step
  `traces/*.jsonl`, aggregate `summary.json` (`success_count`, `ok_status`,
  `diagnostic_ok`, underflow, action L2 / delta-L2, latency fields). Upstream
  distinguishes infrastructure health, task success and strict QA pass.
- **Reset / state**: `OffScreenRenderEnv.reset()` + `set_init_state(init_states[i])`.
  Init states come from `libero/libero/init_files`.
- **Formal population**: 40 tasks × 3 motion families (l1–l3) × 5 trajectories
  × 3 speeds = 1,800 episodes. Full episodes use `--max-steps 600`.
  Levels `l1/l2/l3` are the motion families (each YAML: 40 tasks × 5
  trajectories, all described as "speed medium"). The formal evaluator has
  **no speed flag**. Upstream speed sweeps copy the YAMLs, rewrite every
  `alpha_mps` field, and pass `--config-dir`
  (`scripts/launch_pi05_sync_speed_sweep_20260606.sh`).
- **Published calibrated latency** (`results/execution_strategy_best_points.csv`):
  π0.5 PyTorch Sync K=10 and Async K=6 both use **147.199 ms**. Published success:
  Sync 775/1800, Async 871/1800 (copied from upstream, **not measured here**).

## Where a Haplous arm would attach (read; nothing implemented)

The clean seam is between controller output (action chunks) and the runtime's
per-tick action application. A Haplous arm would be one more `strategies/`
controller, or a wrapper around `ActionRuntime`, that can admit, hold or refuse
chunks. It must not touch `runtime.py`'s world stepping. That keeps the
task/world, policy and execution stack separate, as Workstream D requires.

## Not audited / blocked

- Hugging Face checkpoints (`rsj2003/libero-dynamic-paper-models`) and dataset:
  **`huggingface.co` is blocked by this session's network policy.** File list,
  sizes and HF revision are unverified.
- LeRobot / openpi / FastWAM / VLASH policy environments: not installed (GPU).
- `examples/render_pure_simulation.py` full sweep: not run (EGL and parallel
  workers assumed; CPU cost is large, see EVIDENCE_REPORT.md).
- Dynamic demonstration generation (`supplementary/data_generation/`): needs
  LIBERO datasets (HF-hosted). Not run.
