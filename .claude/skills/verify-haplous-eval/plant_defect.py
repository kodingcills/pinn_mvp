#!/usr/bin/env python3
"""Copy one M0 recording to a scratch registry and plant one defect in the copy.

  plant_defect.py SRC_ROOT RUN_ID DEST_ROOT corrupt FILE
  plant_defect.py SRC_ROOT RUN_ID DEST_ROOT shift-pose TICK DZ_M
  plant_defect.py SRC_ROOT RUN_ID DEST_ROOT shift-state TICK DZ_M

corrupt     flips one byte in artifacts/FILE and keeps the original ledger.
shift-pose  adds DZ_M to telemetry observation pose_m[2] at TICK, then re-seals M0.
shift-state adds DZ_M to state.arrow qpos z (state[3]) at TICK, then re-seals M0.

Re-sealing writes a fresh ledger over the tampered artifacts, so
`run_registry verify` reports no problems and only content checks remain.
The source registry is only read.
"""
import json
import shutil
import sys
from pathlib import Path

import pyarrow as pa

from evaluation.run_registry import Registry


def _flip_byte(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))


def _shift_pose(art: Path, tick: int, dz: float) -> None:
    path = art / "telemetry.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    hits = [r for r in rows if r["kind"] == "observation" and r["tick"] == tick and r["observation"]]
    assert hits, f"no observation at tick {tick} (dropout tick?)"
    hits[0]["observation"]["pose_m"][2] += dz
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def _shift_state(art: Path, tick: int, dz: float) -> None:
    path = art / "state.arrow"
    reader = pa.ipc.open_file(str(path))
    schema = reader.schema
    table = reader.read_all()
    rows = table.to_pylist()
    assert rows[tick]["tick"] == tick
    rows[tick]["state"][3] += dz
    batches = pa.Table.from_pylist(rows, schema=schema).to_batches(max_chunksize=reader.get_batch(0).num_rows)
    with pa.ipc.new_file(str(path), schema) as writer:
        for batch in batches:
            writer.write_batch(batch)


def main(argv: list[str]) -> int:
    src_root, run_id, dest_root, mode, *rest = argv
    src, dest = Registry(src_root), Path(dest_root)
    assert not dest.exists(), f"{dest} exists; pick an empty destination"
    if mode == "corrupt":
        shutil.copytree(src_root, dest)
        _flip_byte(dest / "runs" / run_id / "artifacts" / rest[0])
    elif mode in ("shift-pose", "shift-state"):
        tick, dz = int(rest[0]), float(rest[1])
        done = [e for e in src.events() if e["run_id"] == run_id and e["event"] == "completed"][-1]["payload"]
        reg = Registry(dest)
        reg.register((src.run_dir(run_id) / "manifest.json").read_bytes())
        reg.start(run_id)
        art = reg.run_dir(run_id) / "artifacts"
        shutil.copytree(src.run_dir(run_id) / "artifacts", art)
        (_shift_pose if mode == "shift-pose" else _shift_state)(art, tick, dz)
        reg.finish(run_id, success=True, exit_code=done["exit_code"], summary=done["summary"])
        assert reg.verify(run_id) == [] and reg.state(run_id) == "VERIFIED"
    else:
        raise SystemExit(f"unknown mode {mode!r}")
    print(json.dumps({"planted": mode, "args": rest, "root": str(dest), "run_id": run_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
