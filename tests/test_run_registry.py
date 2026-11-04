"""Adversarial and property tests for the M0 run registry."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from evaluation.run_registry import (
    Registry,
    RegistryError,
    canonical_bytes,
    collect_provenance,
    record_run,
    strict_loads,
)


class StepClock:
    def __init__(self) -> None:
        self.t = datetime(2026, 11, 4, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


def manifest(run_id: str = "r1", **over):
    m = {
        "schema_version": "m0-run-manifest-v0",
        "run_id": run_id,
        "track": "cpu_contract",
        "created_at": "2026-11-04T00:00:00Z",
        "purpose": "test",
        "command": ["true"],
        "provenance": collect_provenance(()),
    }
    m.update(over)
    return m


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "reg", clock=StepClock())


def complete(reg: Registry, run_id: str, files: dict[str, bytes]) -> None:
    reg.register(manifest(run_id))
    reg.start(run_id)
    art = reg.run_dir(run_id) / "artifacts"
    art.mkdir()
    for name, data in files.items():
        (art / name).write_bytes(data)
    reg.finish(run_id, success=True, exit_code=0)


# ---------------- strict JSON / canonicalization ----------------


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1, "a": 2}',
        '{"x": {"b": 1, "b": 1}}',
        '{"a": NaN}',
        '{"a": Infinity}',
        '{"a": 1,}',
        "{'a': 1}",
        '{"a": 1} trailing',
        '{"a": 9007199254740993}',  # beyond IEEE-754 exact integer range
        '{"a": "\\ud800"}',  # lone surrogate
        "",
    ],
)
def test_strict_loads_rejects(text):
    with pytest.raises(RegistryError):
        strict_loads(text)


def test_strict_loads_rejects_bom_and_bad_utf8():
    with pytest.raises(RegistryError):
        strict_loads(b'\xef\xbb\xbf{"a":1}')
    with pytest.raises(RegistryError):
        strict_loads(b'{"a":"\xff"}')


@settings(max_examples=200, deadline=None)
@given(st.floats(allow_nan=False, allow_infinity=False))
def test_any_canonical_output_is_readable(value):
    # Regression: 2.0**53 used to be written, then refused on read-back.
    try:
        out = canonical_bytes(value)
    except RegistryError:
        return
    assert canonical_bytes(strict_loads(out)) == out


def test_unreadable_float_refused_at_register(reg):
    with pytest.raises(RegistryError):
        reg.register(manifest(config={"x": 2.0**53}))
    assert reg.events()[-1]["event"] == "rejected_input"


def test_rfc8785_known_vectors():
    # RFC 8785 §3.2.2/3.2.3: key order by UTF-16 code units, ES6 number formatting.
    assert canonical_bytes({"b": 1, "a": [1.0, 1e21, 0.000001]}) == b'{"a":[1,1e+21,0.000001],"b":1}'
    assert canonical_bytes({"€": 1, "\r": 2, "1": 3}) == '{"\\r":2,"1":3,"€":1}'.encode()


json_values = st.recursive(
    st.none() | st.booleans() | st.integers(-(2**53) + 1, 2**53 - 1)
    | st.floats(allow_nan=False, allow_infinity=False, min_value=-1e15, max_value=1e15) | st.text(),
    lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=5), c, max_size=4),
    max_leaves=20,
)


@settings(max_examples=200, deadline=None)
@given(json_values)
def test_canonicalization_is_idempotent_and_order_independent(value):
    once = canonical_bytes(value)
    assert canonical_bytes(strict_loads(once)) == once
    if isinstance(value, dict):
        reversed_dict = dict(reversed(list(value.items())))
        assert canonical_bytes(reversed_dict) == once


# ---------------- manifest validation / rejection ----------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(created_at="2026-13-01T00:00:00Z"),
        lambda m: m.update(created_at="2026-02-30T00:00:00Z"),
        lambda m: m.update(created_at="2026-11-04 00:00:00Z"),
        lambda m: m.update(created_at="2026-11-04T00:00:00+02:00"),  # non-UTC refused
        lambda m: m.update(created_at="yesterday"),
        lambda m: m.update(run_id="../escape"),
        lambda m: m.update(run_id=""),
        lambda m: m.update(track="gpu_magic"),
        lambda m: m.update(extra_field=1),
        lambda m: m.pop("provenance"),
        lambda m: m["provenance"].update(git_commit="not-a-sha"),
        lambda m: m.update(simulator={"name": "mujoco", "version": "x"}),  # cpu track: no sim
        lambda m: m.update(
            policy={"name": "p", "checkpoint_uri": "hf://x", "checkpoint_sha256": "0" * 64}
        ),
        lambda m: m.update(track="sim_model_in_loop"),  # missing simulator+policy
        lambda m: m.update(track="sim_policy_free"),  # missing simulator
    ],
)
def test_invalid_manifests_rejected_and_logged(reg, mutate):
    m = manifest()
    mutate(m)
    with pytest.raises(RegistryError):
        reg.register(m)
    events = reg.events()
    assert [e["event"] for e in events] == ["rejected_input"]
    assert events[0]["run_id"] is None and events[0]["payload"]["errors"]
    assert not (reg.root / "runs" / "r1").exists()


def test_duplicate_key_manifest_bytes_rejected(reg):
    text = json.dumps(manifest())[:-1] + ', "run_id": "r2"}'
    with pytest.raises(RegistryError, match="duplicate"):
        reg.register(text.encode())


def test_track_conditional_manifests_accepted(reg):
    reg.register(manifest("cpu"))
    reg.register(manifest("sim", track="sim_policy_free", simulator={"name": "mujoco", "version": "2.3.7"}))
    reg.register(
        manifest(
            "mil",
            track="sim_model_in_loop",
            simulator={"name": "mujoco", "version": "2.3.7"},
            policy={"name": "pi05", "checkpoint_uri": "hf://rsj2003/x", "checkpoint_sha256": "a" * 64},
            provenance={**collect_provenance(()), "gpu": [L4]},
        )
    )
    assert {reg.state(r) for r in ("cpu", "sim", "mil")} == {"REGISTERED"}


L4 = {"name": "NVIDIA L4", "driver_version": "580.126.18", "memory_total_mib": 23034}
MIL = {
    "track": "sim_model_in_loop",
    "simulator": {"name": "mujoco", "version": "2.3.7"},
    "policy": {"name": "pi05", "checkpoint_uri": "hf://rsj2003/x", "checkpoint_sha256": "a" * 64},
}


@pytest.mark.parametrize("gpu", [None, [], "missing"])
def test_model_in_loop_requires_measured_gpu(reg, gpu):
    prov = collect_provenance(())
    if gpu == "missing":
        prov.pop("gpu")
    else:
        prov["gpu"] = gpu
    with pytest.raises(RegistryError):
        reg.register(manifest(**MIL, provenance=prov))


def test_compare_flags_different_gpu_types(reg):
    for rid, name in (("a", "NVIDIA L4"), ("b", "NVIDIA L4"), ("c", "NVIDIA GeForce RTX 4090")):
        reg.register(manifest(rid, **MIL, provenance={**collect_provenance(()), "gpu": [{**L4, "name": name}]}))
    assert reg.compare("a", "b")["hardware_comparable"] is True
    assert reg.compare("a", "c")["hardware_comparable"] is False


def test_duplicate_run_id_rejected(reg):
    reg.register(manifest())
    with pytest.raises(RegistryError, match="already registered"):
        reg.register(manifest(purpose="different"))
    assert reg.manifest("r1")["purpose"] == "test"


# ---------------- lifecycle ----------------


def test_invalid_transitions_refused(reg):
    with pytest.raises(RegistryError):
        reg.start("ghost")
    reg.register(manifest())
    with pytest.raises(RegistryError):
        reg.finish("r1", success=True)  # not started
    reg.start("r1")
    with pytest.raises(RegistryError):
        reg.start("r1")
    reg.finish("r1", success=False, exit_code=3)
    with pytest.raises(RegistryError):
        reg.finish("r1", success=True)
    assert reg.state("r1") == "FAILED"
    assert reg.get("r1")["outcome"] == "failed"


@settings(max_examples=60, deadline=None)
@given(st.lists(st.sampled_from(["start", "ok", "fail", "verify"]), max_size=8))
def test_random_operation_sequences_never_corrupt_ledger(tmp_path_factory, ops):
    reg = Registry(tmp_path_factory.mktemp("p"), clock=StepClock())
    reg.register(manifest())
    allowed = {"start": {"REGISTERED"}, "ok": {"RUNNING"}, "fail": {"RUNNING"}}
    for op in ops:
        before = reg.state("r1")
        call = {
            "start": lambda: reg.start("r1"),
            "ok": lambda: reg.finish("r1", success=True),
            "fail": lambda: reg.finish("r1", success=False),
            "verify": lambda: reg.verify("r1"),
        }[op]
        if op in allowed and before not in allowed[op]:
            with pytest.raises(RegistryError):
                call()
            assert reg.state("r1") == before
        else:
            call()
    reg.events()  # chain still valid


# ---------------- integrity ----------------


def test_verify_detects_corrupted_missing_and_extra_artifacts(reg):
    complete(reg, "r1", {"a.txt": b"hello", "b.txt": b"world"})
    assert reg.verify("r1") == []
    assert reg.state("r1") == "VERIFIED"
    art = reg.run_dir("r1") / "artifacts"
    (art / "a.txt").write_bytes(b"hellO")
    (art / "b.txt").unlink()
    (art / "c.txt").write_bytes(b"new")
    problems = reg.verify("r1")
    assert problems == [
        "artifact hash mismatch: a.txt",
        "artifact missing: b.txt",
        "artifact not recorded: c.txt",
    ]
    assert reg.state("r1") == "INTEGRITY_FAILED"


def test_verify_detects_manifest_tamper(reg):
    complete(reg, "r1", {})
    path = reg.run_dir("r1") / "manifest.json"
    m = json.loads(path.read_bytes())
    m["purpose"] = "rewritten"
    path.write_bytes(canonical_bytes(m))
    assert "manifest bytes do not match registered digest" in reg.check("r1")


@pytest.mark.parametrize("attack", ["edit", "delete_line", "reorder", "append_forged"])
def test_ledger_tampering_detected(reg, attack):
    complete(reg, "r1", {"a": b"1"})
    lines = reg.ledger_path.read_bytes().splitlines()
    if attack == "edit":
        e = json.loads(lines[2])
        e["payload"]["exit_code"] = 1
        lines[2] = canonical_bytes(e)
    elif attack == "delete_line":
        del lines[1]
    elif attack == "reorder":
        lines[1], lines[2] = lines[2], lines[1]
    else:
        forged = json.loads(lines[-1])
        forged["seq"] += 1
        lines.append(canonical_bytes(forged))
    reg.ledger_path.write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(RegistryError):
        reg.events()


def test_clock_skew_backwards_refused(tmp_path):
    times = iter([datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)])
    reg = Registry(tmp_path, clock=lambda: next(times))
    reg.register(manifest())
    with pytest.raises(RegistryError, match="backwards"):
        reg.start("r1")
    assert reg.state("r1") == "REGISTERED"


# ---------------- end-to-end: record, retrieve, compare ----------------


def test_record_run_reproducible_and_comparable(tmp_path):
    reg = Registry(tmp_path, clock=StepClock())
    cmd = [sys.executable, "-c", "print('deterministic output')"]
    a = record_run(reg, run_id="a", track="cpu_contract", purpose="repro", command=cmd)
    b = record_run(reg, run_id="b", track="cpu_contract", purpose="repro", command=cmd)
    bad = record_run(reg, run_id="c", track="cpu_contract", purpose="repro",
                     command=[sys.executable, "-c", "import sys; print('x'); sys.exit(2)"])
    assert a["state"] == b["state"] == "VERIFIED" and a["outcome"] == "completed"
    assert bad["outcome"] == "failed" and bad["state"] == "VERIFIED"
    cmp_ab = reg.compare("a", "b")
    assert set(cmp_ab["manifest_differences"]) == {"/run_id", "/created_at"}
    assert cmp_ab["artifacts_identical"] == ["stderr.txt", "stdout.txt"]
    cmp_ac = reg.compare("a", "c")
    assert "stdout.txt" in cmp_ac["artifacts_differ"] and cmp_ac["outcomes"] == ["completed", "failed"]


def test_committed_registry_evidence_is_intact():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "results" / "m0_registry"
    if not root.exists():
        pytest.skip("no committed registry")
    reg = Registry(root)
    run_ids = {e["run_id"] for e in reg.events() if e["event"] == "registered"}
    assert run_ids
    for run_id in sorted(run_ids):
        assert reg.check(run_id) == [], run_id


def test_registry_writes_do_not_mark_checkout_dirty(tmp_path, monkeypatch):
    import subprocess

    import evaluation.run_registry as rr

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    monkeypatch.setattr(rr, "REPO_ROOT", repo)
    reg = Registry(repo / "results" / "reg", clock=StepClock())
    assert rr.collect_provenance((), (), reg.root)["git_dirty"] is False
    assert rr.collect_provenance((), (), tmp_path / "elsewhere")["git_dirty"] is True  # reg dir counts
    (repo / "edited.py").write_text("x")
    assert rr.collect_provenance((), (), reg.root)["git_dirty"] is True


# ---------------- official RFC 8785 vectors ----------------

_JCS = __import__("pathlib").Path(__file__).parent / "data" / "rfc8785"


@pytest.mark.parametrize("name", sorted(p.name for p in (_JCS / "input").glob("*.json")))
def test_official_rfc8785_vectors(name):
    # cyberphone/json-canonicalization testdata (reference implementation of RFC 8785).
    got = canonical_bytes(strict_loads((_JCS / "input" / name).read_bytes()))
    assert got == (_JCS / "output" / name).read_bytes()


def test_official_vector_set_is_complete():
    assert len(list((_JCS / "input").glob("*.json"))) == 6


# ---------------- concurrency ----------------


class _SlowReadRegistry(Registry):
    """Widens any check-then-act window: every ledger read sleeps after reading."""

    def events(self):
        import time

        out = super().events()
        time.sleep(0.02)
        return out


def _worker(args):
    root, run_id, barrier = args
    reg = _SlowReadRegistry(root)
    barrier.wait()
    try:
        reg.start(run_id)
        return "start"
    except RegistryError:
        return "refused"


def test_concurrent_processes_cannot_double_transition(tmp_path):
    import multiprocessing as mp

    reg = Registry(tmp_path)
    reg.register(manifest("r0"))
    ctx = mp.get_context("spawn")
    with ctx.Manager() as manager:
        barrier = manager.Barrier(6)
        with ctx.Pool(6) as pool:
            results = pool.map(_worker, [(str(tmp_path), "r0", barrier)] * 6)
    events = reg.events()  # chain intact after racing writers
    assert [e["event"] for e in events if e["run_id"] == "r0"] == ["registered", "started"]
    assert sorted(results) == ["refused"] * 5 + ["start"]


# ---------------- subject environment provenance ----------------


def test_subject_environment_is_measured_not_claimed(tmp_path):
    reg = Registry(tmp_path, clock=StepClock())
    r = record_run(reg, run_id="s", track="cpu_contract", purpose="p", command=[sys.executable, "-c", "pass"],
                   config={"subject_environment": "forged"}, subject_python=sys.executable,
                   subject_dependencies=("pytest", "no-such-dist"))
    env = r["manifest"]["config"]["subject_environment"]
    assert env["python"] == sys.version.split()[0]
    assert env["dependencies"]["no-such-dist"] is None and env["dependencies"]["pytest"]
