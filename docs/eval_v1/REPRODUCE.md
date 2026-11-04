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
