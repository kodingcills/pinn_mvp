# Reproduce: iteration 1

## Tool versions used

| Tool | Version |
|---|---|
| OS | Linux 6.18 x86_64, Ubuntu 24.04 userland, 4 vCPU, no GPU |
| uv | 0.11.32 |
| Haplous venv Python | 3.12.3 |
| jsonschema / rfc8785 / cryptography / hypothesis / pytest | 4.26.0 / 0.1.4 / 50.0.2 / 6.170.14 / 9.1.1 |
| numpy / mujoco (Haplous side) | 2.5.1 / 3.11.0 |
| LIBERO-Dynamic | `ef12443b7c1f0699356eaf2a4e1676e034f2d806` |
| LIBERO venv | Python 3.10.20, numpy 1.22.4, mujoco 2.3.7, robosuite 1.4.0 |
| Mesa (CPU GL) | libosmesa6 25.1.7 (apt) |

## Haplous side

```bash
uv sync --extra all --extra dev
.venv/bin/python -m pytest tests/test_run_registry.py -q    # 47 passed
.venv/bin/python -m pytest tests -q                          # 459 passed, 4 failed (pre-existing), 1 xfailed
```

## LIBERO-Dynamic CPU smoke

```bash
sudo apt-get install -y libosmesa6 libegl1 libegl-mesa0 libgl1-mesa-dri
git clone https://github.com/renshaojie233/LIBERO-Dynamic /path/libero/src
git -C /path/libero/src checkout ef12443b7c1f0699356eaf2a4e1676e034f2d806
uv venv -p 3.10 /path/libero/venv
VIRTUAL_ENV=/path/libero/venv uv pip install -r /path/libero/src/requirements.txt pytest
VIRTUAL_ENV=/path/libero/venv uv pip install -e /path/libero/src --no-deps
```

```bash
.venv/bin/python scripts/run_libero_dynamic_cpu_smoke.py --libero-src /path/libero/src --libero-python /path/libero/venv/bin/python --run-id <new-id>
```

```bash
.venv/bin/python scripts/compare_libero_smoke_runs.py c0a-smoke-l1-n4-s40-001 <new-id>
```

Expected: acceptance `pass: true`, `simulation_reproducible: true`, about
4 minutes on the 4-vCPU host used here. Run IDs are unique, so pick a new one. The
driver re-derives the verdict, without re-running, if the ID already exists.

Upstream unit tests (recorded as `c0a-upstream-unit-tests-001`): from
`/path/libero/src`, with `MUJOCO_GL=osmesa PYOPENGL_PLATFORM=osmesa
PYTHONPATH=$PWD LIBERO_CONFIG_PATH=$PWD/libero/libero/configs`, run
`/path/libero/venv/bin/python -m pytest tests -q` → 42 passed.

## Inspect / verify committed runs

```bash
.venv/bin/python -m evaluation.run_registry show   --root results/m0_registry c0a-smoke-l1-n4-s40-003
```

```bash
.venv/bin/python -m evaluation.run_registry verify --root results/m0_registry c0a-smoke-l1-n4-s40-003
```

```bash
.venv/bin/python -m evaluation.run_registry compare --root results/m0_registry c0a-smoke-l1-n4-s40-001 c0a-smoke-l1-n4-s40-003
```

## Artifact hashes (SHA-256)

| Artifact | SHA-256 |
|---|---|
| `results/m0_registry/runs/c0a-smoke-l1-n4-s40-001/manifest.json` | `e484a873f093a51ec0640e3cb40e90a23f73c6f5d3007e2f7c75981371b01df3` |
| `results/m0_registry/runs/c0a-smoke-l1-n4-s40-002/manifest.json` | `81bcdc195bccc982420e6b7c5d0fba2197c17fb5001c4be11f513dfcf7d7e180` |
| `results/m0_registry/runs/c0a-smoke-l1-n4-s40-003/manifest.json` | `2f69e73a7000b7dddca84a3b6b636bb9fe1ede99d32c2f42ec36014f8d59f76e` |
| `results/m0_registry/runs/c0a-upstream-unit-tests-001/manifest.json` | `508692f00dd641eff008bf948e65b1baedc9814220be5c4cd42ab2165f10c1df` |
| `results/m0_registry/ledger.jsonl` (16 events at time of writing) | `8ec0243bd4c48f500fbea8c0b9bc6a34246b9d219ae9183741dec914acac8068` |
| `evaluation/schemas/run_manifest.schema.json` | `6c7f90cd0fea668869792df0ecd3cd3c135878a1ec489ccbc4aeea2293677cbb` |
| `evaluation/schemas/ledger_event.schema.json` | `79d2b3459b3494d5659c5125609585448d64c62014d9a2a0998cb21ff84bba17` |

Per-artifact hashes for every run are in the ledger's `completed` event and are
re-checked by `verify`. The ledger hash changes whenever a run is appended; that
is expected.

## Iteration 2 additions

Fixed-latency timing runs (about 2–2.5 min each on 4 vCPU):

```bash
.venv/bin/python scripts/run_libero_dynamic_cpu_smoke.py --libero-src /path/libero/src --libero-python /path/libero/venv/bin/python --run-id <new-id> --limit 1 --latency-s 0.147199
```

```bash
.venv/bin/python -m pytest tests/test_libero_dynamic_evidence.py tests/test_run_registry.py tests/test_verify_hf_checkpoint.py -q
```

Checkpoint step 0 (needs `huggingface.co` reachable; see GPU_HANDOFF.md):

```bash
.venv/bin/python scripts/verify_hf_checkpoint.py --repo rsj2003/libero-dynamic-paper-models --path pi05/static_official --out results/checkpoints/pi05_static_official.json
```

| Artifact | SHA-256 |
|---|---|
| `results/m0_registry/runs/c0a-latency-0.03-n1-s40-001/manifest.json` | `e242b5177da88b1d2031ffd0acc95a2840409547e03aa6202f84dcf9610f5c21` |
| `results/m0_registry/runs/c0a-latency-0.147199-n1-s40-001/manifest.json` | `c42d2eb3b06a9be31df886dc82509480f4a2499bbbb785d934110c4900404019` |
| `results/m0_registry/ledger.jsonl` (24 events) | `45c1194a452613b06b75a463e9ddce89ee3d9150c2200dac9a107e83d45ad927` |
| `evaluation/schemas/run_manifest.schema.json` (adds `provenance.gpu`) | `f144167878f49fc3946b81001d69faa60e4b63368eafb43173718826abbfbbb3` |
| RFC 8785 vectors: `tests/data/rfc8785/` | cyberphone/json-canonicalization @ `32a48195b9b20fe67b1795e39e4509f14d860b45` |

## Iteration 3: v1.1 package, M0 reconciliation, Track B

```bash
sha256sum evaluation/spec/haplous_eval_v1_1_starter.zip
```

Expected: `9c4130709feb80684b34d8a291ca95cd37ef6522cf6629bf59788cb3db41390d`. Package self-validation (from the package directory):

```bash
cd evaluation/spec/haplous_eval_v1_1 && ../../../.venv/bin/python -I tools/validate_package.py && ../../../.venv/bin/python -m pytest tests -q -p no:cacheprovider
```

Expected: `PASS ...` and `18 passed`. Track B engine directly (no registry):

```bash
.venv/bin/python -m evaluation.track_b --fixtures evaluation/spec/haplous_eval_v1_1/fixtures/track_b_scenarios.json --out /tmp/track_b_out
```

Recorded run (canonical v1.1 manifest, M0 lifecycle, ledger projection, report copy). Use a new run id:

```bash
.venv/bin/python scripts/run_track_b.py --run-id track-b-v1.1-003
```

```bash
.venv/bin/python -m evaluation.run_registry compare --root results/m0_registry track-b-v1.1-001 track-b-v1.1-003
```

Expected: 38/38, `VERIFIED`, and all artifacts identical with the same engine,
fixtures and Python. Targeted tests:

```bash
.venv/bin/python -m pytest tests/test_track_b.py tests/test_m0_v11.py tests/test_run_registry.py tests/test_verify_hf_checkpoint.py -q
```

Checkpoint record (needs huggingface.co):

```bash
.venv/bin/python scripts/verify_hf_checkpoint.py --repo rsj2003/libero-dynamic-paper-models --path pi05/static_official --revision ffd700087b34b734805ca5148e278a6a61fa5a8a --out results/checkpoints/pi05_static_official.json
```

| Artifact | SHA-256 |
|---|---|
| `fixtures/track_b_scenarios.json` | `1474f97cd5fa392c7e080e7f021b6d8afc8bef2aacfde65416e5f6f6f580ea6d` |
| `evaluation/track_b.py` (engine at run time) | `eb6fdafd5a635d155f04eddc7e134009bfc256dc1885d2cee49b35a01e3543d8` |
| `runs/track-b-v1.1-001/manifest.json` | `2e6376f8e99aa04557329e064e45f937b6b6e60b92ee01499c520888fa26852a` |
| `runs/track-b-v1.1-002/manifest.json` | `6f0f6989fd25a8e02297ed301241d4c286717e88c1c196fea24b1121b7d5c81f` |
| `results.jsonl` (both runs) | `243bee3221e5e83ac33952127a59af710a9eeb2adda69b385975e905b07aa720` |
| `summary.json` (both runs) | `e82e2d7397262725300627b97e7603ab8590210312dfdc20d27ef23d1e431a59` |
| `report.md` (both runs) | `ce834fea6797c014b7e4f9c22bf4f13686c11bdb4d2dc607fe70f7aaa4bc28dd` |
| π0.5 `model.safetensors` @ `ffd70008` | `877b3ec1130548b69af7f8aeef3ec9d3fc7738040f0b9beb490857ec970997ae` |
