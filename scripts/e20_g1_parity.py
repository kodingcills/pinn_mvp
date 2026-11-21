"""E20 G phase 1 parity: G1 on every HEB scenario, digested per scenario.

    python scripts/e20_g1_parity.py OUT.json [BEFORE_DUMP.jsonl]
Each scenario's record (disposition, reason, refusal codes, every observation, detail; wall time
excluded) is serialised as one JSON line and hashed. BEFORE_DUMP is the same dump made at the
pre-extraction commit; OUT gets both digest maps and whether they match.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.heb import mujoco_backend  # noqa: E402
from benchmarks.heb.insertion import insertion_scenarios  # noqa: E402
from benchmarks.heb.sliding import sliding_scenarios  # noqa: E402
from benchmarks.incumbent import G1IncumbentSystem  # noqa: E402


def line(s) -> str:
    r = G1IncumbentSystem().run(mujoco_backend.build_realization(s), s)
    return json.dumps({"id": s.id, "disp": r.disposition, "reason": r.reason, "codes": list(r.refusal_codes),
                       "n_obs": len(r.observations), "obs": repr(r.observations),
                       "detail": repr(sorted(r.detail.items()))})


def digests(lines) -> dict:
    out = {}
    for ln in lines:
        d = json.loads(ln)
        out[d["id"]] = {"disposition": d["disp"], "reason": d["reason"], "n_obs": d["n_obs"],
                        "sha256": hashlib.sha256(ln.strip().encode()).hexdigest()}
    return out


after = digests(line(s) for s in insertion_scenarios() + sliding_scenarios())
result = {"after": after}
if len(sys.argv) > 2:
    result["before"] = digests(Path(sys.argv[2]).read_text().splitlines())
    result["identical"] = result["before"] == after
Path(sys.argv[1]).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
print(f"{len(after)} scenarios; identical={result.get('identical')}")
