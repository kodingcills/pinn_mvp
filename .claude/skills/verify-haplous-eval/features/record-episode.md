# Record an episode

A user runs one real HEB peg-insertion episode in MuJoCo with recording on. The process exits and leaves an M0 run in state `VERIFIED` whose artifacts hold the model, the full state stream, telemetry, and Haplous and fault events.

## Sub-features

- `record-run` records one scenario into a registry root and exits.
- `record-seal` leaves the run `VERIFIED` in the M0 ledger with every artifact hashed.
- `record-show` displays the manifest and provenance, including the git commit that produced it.

## How to get to it (user POV)

- Run `scripts/record_episode.py --root <registry> --run-id <id> [--scenario <id>] [--chunk-ticks N]`.
- Run `python -m evaluation.run_registry verify|show --root <registry> <id>` to check the result.
- `scripts/m2_gate.py` also records one episode as its item 1 (see `m2-gate.md`).

## Driving it with the CLI

Preconditions:

- Launch and Doctor from `../SKILL.md` passed.
- `$W/registry` does not exist, or has no run named `j1`.

- **Record.** Run `$VENV/bin/python scripts/record_episode.py --root $W/registry --run-id j1`. Exit 0 in about 3 s. Stdout is JSON with `"run_id": "j1"`, `"state": "VERIFIED"`, `"outcome": "completed"` and a `manifest_sha256`.
- **Verify.** Run `$VENV/bin/python -m evaluation.run_registry verify --root $W/registry j1`. Exit 0 with `"problems": []`.
- **Show.** Run `$VENV/bin/python -m evaluation.run_registry show --root $W/registry j1`. `manifest.config` is `{"chunk_ticks": 1024, "scenario": "insert_dropout_large", "system": "haplous_mediated"}`. `manifest.provenance.git_commit` equals `git rev-parse HEAD` and `git_dirty` reflects your tree.
- **Inspect artifacts.** Run `ls $W/registry/runs/j1/artifacts`. Expect `events.jsonl`, `metadata.json`, `model.mjb`, `model.xml`, `state.arrow`, `stderr.txt`, `stdout.txt` and `telemetry.jsonl`. `metadata.json` has `"format": "haplous-mujoco-recording-v1"`, `n_ticks` 3651 and `n_observations` 3251 for the default scenario.

## Gotchas

- Re-using a run id in the same root raises `RegistryError: manifest rejected: ["run_id 'j1' already registered"]` and appends a `rejected_input` event to that ledger. Use a new id or a new root, and never retry against a committed registry.
- `git_dirty: true` in provenance is expected while you have uncommitted changes. Say so in the proof.
- `--scenario` takes HEB scenario ids. Only the default `insert_dropout_large` is covered by the replay oracle numbers above. `m2_gate.py` also records `insert_machine_fault` and `insert_nominal` for its memory check.
