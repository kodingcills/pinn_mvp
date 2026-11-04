"""M0 experiment registry: immutable run manifests + append-only, hash-chained ledger.

Layout under a registry root::

    ledger.jsonl                      append-only events, one JSON object per line
    runs/<run_id>/manifest.json       RFC 8785 canonical bytes, written once (O_EXCL)
    runs/<run_id>/artifacts/...       files hashed (SHA-256) at completion

Lifecycle (explicit; anything else is refused)::

    (none) --registered--> REGISTERED --started--> RUNNING --completed--> COMPLETED
                                                          \\--failed----> FAILED
    COMPLETED|FAILED --verified--> VERIFIED   |  --integrity_failed--> INTEGRITY_FAILED
    invalid input --rejected_input--> REJECTED  (no manifest stored; input digest logged)

The ledger hash chain detects edits/reordering/truncation-in-the-middle of the
ledger, not wholesale rewrites by someone with write access; Ed25519 approval
signatures here are TEST-ONLY (keys are caller-supplied, no key management) and
are not a substitute for an environment-enforced approval gate.

Schemas are provisional (evaluation/schemas/): the v1.1 package schemas were not
present in this repository when M0 was implemented.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
REPO_ROOT = Path(__file__).resolve().parents[1]
GENESIS_HASH = "0" * 64

TRANSITIONS: dict[str, tuple[tuple[str | None, ...], str]] = {
    "registered": ((None,), "REGISTERED"),
    "started": (("REGISTERED",), "RUNNING"),
    "completed": (("RUNNING",), "COMPLETED"),
    "failed": (("RUNNING",), "FAILED"),
    "verified": (("COMPLETED", "FAILED"), "VERIFIED"),
    "integrity_failed": (("COMPLETED", "FAILED", "VERIFIED"), "INTEGRITY_FAILED"),
}


class RegistryError(ValueError):
    """Invalid input, refused transition, or failed integrity check."""


# --------------------------------------------------------------------------
# Strict JSON + canonicalization
# --------------------------------------------------------------------------


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise RegistryError(f"duplicate JSON key: {key!r}")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise RegistryError(f"non-finite JSON number not allowed: {name}")


def strict_loads(data: bytes | str) -> Any:
    """Parse JSON rejecting duplicate keys, NaN/Infinity, BOM, invalid UTF-8,
    and anything RFC 8785 cannot canonicalize (e.g. integers beyond 2**53, lone
    surrogates)."""
    if isinstance(data, bytes):
        if data.startswith(b"\xef\xbb\xbf"):
            raise RegistryError("byte-order mark not allowed")
        try:
            data = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RegistryError(f"invalid UTF-8: {exc}") from exc
    try:
        value = json.loads(
            data, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant
        )
    except json.JSONDecodeError as exc:
        raise RegistryError(f"malformed JSON: {exc}") from exc
    canonical_bytes(value)  # domain check
    return value


def canonical_bytes(value: Any) -> bytes:
    try:
        out = rfc8785.dumps(value)
        # Output must re-read under the same domain: a float like 2.0**53 serializes as an
        # integer outside the I-JSON safe range and would otherwise be unreadable later.
        rfc8785.dumps(json.loads(out))
        return out
    except Exception as exc:  # rfc8785 raises several domain-specific types
        raise RegistryError(f"not canonicalizable under RFC 8785: {exc}") from exc


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validator(name: str) -> Draft202012Validator:
    schema = strict_loads((SCHEMA_DIR / name).read_bytes())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


MANIFEST_VALIDATOR = _validator("run_manifest.schema.json")
EVENT_VALIDATOR = _validator("ledger_event.schema.json")


def schema_errors(validator: Draft202012Validator, value: Any) -> list[str]:
    return sorted(
        f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
        for e in validator.iter_errors(value)
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_ts(ts: datetime) -> str:
    if ts.tzinfo is None:
        raise RegistryError("timestamps must be timezone-aware")
    return ts.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_ts(text: str) -> datetime:
    if not isinstance(text, str) or not text.endswith("Z"):
        raise RegistryError(f"timestamp must be RFC 3339 UTC ending in 'Z': {text!r}")
    try:
        return datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise RegistryError(f"invalid timestamp {text!r}: {exc}") from exc


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def _dist_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _git_state(exclude: Path | None) -> tuple[str | None, bool | None]:
    """HEAD and dirtiness of the recorder's checkout, ignoring the registry's own writes."""
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()

    try:
        commit = git("rev-parse", "HEAD") or None
        top = Path(git("rev-parse", "--show-toplevel"))
        target = exclude.resolve() if exclude is not None else None
        inside = target is not None and target.is_relative_to(top)
        spec = ["--", ".", f":(exclude){target.relative_to(top)}"] if target and inside else []
        return commit, bool(git("status", "--porcelain", *spec))
    except (OSError, subprocess.SubprocessError):
        return None, None


def collect_provenance(
    dependencies: Sequence[str] = ("numpy", "mujoco", "jsonschema", "cryptography"),
    external_sources: Sequence[Mapping[str, str]] = (),
    exclude_from_dirty: Path | None = None,
) -> dict[str, Any]:
    """Actual provenance of the recording process (python/platform/deps are the
    recorder's; a subprocess under test reports its own in `config`)."""
    commit, dirty = _git_state(exclude_from_dirty)
    prov: dict[str, Any] = {
        "git_commit": commit,
        "git_dirty": dirty,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "dependencies": {name: _dist_version(name) for name in dependencies},
    }
    if external_sources:
        prov["external_sources"] = [dict(s) for s in external_sources]
    return prov


# --------------------------------------------------------------------------
# Test-only Ed25519 approvals
# --------------------------------------------------------------------------


def public_key_hex(key: Ed25519PublicKey) -> str:
    return key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def sign_approval(private_key: Ed25519PrivateKey, record: Mapping[str, Any]) -> dict[str, Any]:
    """TEST-ONLY. Sign {run_id, manifest_digest, decision, approver, decided_at}."""
    body = {k: record[k] for k in ("run_id", "manifest_digest", "decision", "approver", "decided_at")}
    if body["decision"] not in ("approved", "denied"):
        raise RegistryError("decision must be 'approved' or 'denied'")
    parse_ts(body["decided_at"])
    body["public_key"] = public_key_hex(private_key.public_key())
    return {**body, "signature": private_key.sign(canonical_bytes(body)).hex()}


def _decision_digest(signed: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_bytes({k: v for k, v in signed.items() if k != "signature"}))


def verify_approval(signed: Mapping[str, Any], trusted_public_keys: set[str]) -> None:
    body = {k: v for k, v in signed.items() if k != "signature"}
    if set(body) != {"run_id", "manifest_digest", "decision", "approver", "decided_at", "public_key"}:
        raise RegistryError(f"approval has unexpected fields: {sorted(body)}")
    if body["public_key"] not in trusted_public_keys:
        raise RegistryError("approval signed by an untrusted key")
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(body["public_key"]))
        key.verify(bytes.fromhex(signed["signature"]), canonical_bytes(body))
    except (InvalidSignature, ValueError, KeyError) as exc:
        raise RegistryError("approval signature invalid") from exc


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


class Registry:
    def __init__(self, root: str | Path, clock: Callable[[], datetime] = utc_now) -> None:
        self.root = Path(root)
        self.ledger_path = self.root / "ledger.jsonl"
        self.clock = clock
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        self.ledger_path.touch(exist_ok=True)

    # ----- ledger -----

    def events(self) -> list[dict[str, Any]]:
        """Read and fully verify the ledger (schema, chain, seq, time order)."""
        out: list[dict[str, Any]] = []
        prev_hash, prev_at = GENESIS_HASH, None
        for lineno, line in enumerate(self.ledger_path.read_bytes().splitlines(), 1):
            event = strict_loads(line)
            errors = schema_errors(EVENT_VALIDATOR, event)
            if errors:
                raise RegistryError(f"ledger line {lineno}: {errors}")
            body = {k: v for k, v in event.items() if k != "hash"}
            if event["seq"] != len(out):
                raise RegistryError(f"ledger line {lineno}: seq {event['seq']} != {len(out)}")
            if event["prev_hash"] != prev_hash or sha256_hex(canonical_bytes(body)) != event["hash"]:
                raise RegistryError(f"ledger line {lineno}: hash chain broken")
            at = parse_ts(event["at"])
            if prev_at is not None and at < prev_at:
                raise RegistryError(f"ledger line {lineno}: timestamp goes backwards")
            prev_hash, prev_at = event["hash"], at
            out.append(event)
        return out

    def _append(
        self,
        run_id: str | None,
        event: str,
        payload: dict[str, Any],
        decide: Callable[[list[dict[str, Any]]], str],
    ) -> dict[str, Any]:
        """Append one event. `decide(history)` runs while the lock is held and returns
        the resulting state or raises, so check-then-append cannot race."""
        # shortcut: single-host flock; a multi-host registry needs a real transactional store.
        with open(self.ledger_path, "ab") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                history = self.events()
                state = decide(history)
                prev = history[-1] if history else None
                at = self.clock()
                if prev is not None and at < parse_ts(prev["at"]):
                    raise RegistryError("clock went backwards relative to ledger")
                body = {
                    "seq": len(history),
                    "at": format_ts(at),
                    "run_id": run_id,
                    "event": event,
                    "state": state,
                    "payload": payload,
                    "prev_hash": prev["hash"] if prev else GENESIS_HASH,
                }
                record = {**body, "hash": sha256_hex(canonical_bytes(body))}
                errors = schema_errors(EVENT_VALIDATOR, record)
                if errors:
                    raise RegistryError(f"event invalid: {errors}")
                fh.write(canonical_bytes(record) + b"\n")
                fh.flush()
                os.fsync(fh.fileno())
                return record
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _run_events(self, run_id: str) -> list[dict[str, Any]]:
        return [e for e in self.events() if e["run_id"] == run_id]

    def state(self, run_id: str) -> str | None:
        return _state_in(self.events(), run_id)

    def _transition(self, run_id: str, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        allowed_from, to_state = TRANSITIONS[event]

        def decide(history: list[dict[str, Any]]) -> str:
            current = _state_in(history, run_id)
            if current not in allowed_from:
                raise RegistryError(f"transition {event!r} refused from state {current!r} for {run_id}")
            return to_state

        return self._append(run_id, event, payload, decide)

    def _reject(self, raw: bytes, errors: list[str]) -> None:
        payload = {"input_sha256": sha256_hex(raw), "errors": errors}
        self._append(None, "rejected_input", payload, lambda _h: "REJECTED")
        raise RegistryError(f"manifest rejected: {errors}")

    # ----- lifecycle -----

    def run_dir(self, run_id: str) -> Path:
        return self.root / "runs" / run_id

    def register(self, manifest: Mapping[str, Any] | bytes | str) -> str:
        """Validate and store an immutable manifest. Returns its digest."""
        raw = manifest if isinstance(manifest, bytes) else (
            manifest.encode() if isinstance(manifest, str) else json.dumps(manifest).encode()
        )
        try:
            value = strict_loads(raw)
        except RegistryError as exc:
            self._reject(raw, [str(exc)])
        errors = schema_errors(MANIFEST_VALIDATOR, value)
        if errors:
            self._reject(raw, errors)
        run_id = value["run_id"]
        if self.state(run_id) is not None or self.run_dir(run_id).exists():
            self._reject(raw, [f"run_id {run_id!r} already registered"])
        data = canonical_bytes(value)
        try:
            self.run_dir(run_id).mkdir(parents=True)
        except FileExistsError:  # lost a race with a concurrent register of the same id
            self._reject(raw, [f"run_id {run_id!r} already registered"])
        with open(self.run_dir(run_id) / "manifest.json", "xb") as fh:
            fh.write(data)
        digest = sha256_hex(data)
        self._transition(run_id, "registered", {"manifest_sha256": digest})
        return digest

    def start(self, run_id: str) -> None:
        self._transition(run_id, "started", {})

    def _hash_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        base = self.run_dir(run_id) / "artifacts"
        files = sorted(p for p in base.rglob("*") if p.is_file()) if base.exists() else []
        return [
            {"path": p.relative_to(base).as_posix(), "sha256": sha256_hex(p.read_bytes()), "bytes": p.stat().st_size}
            for p in files
        ]

    def finish(self, run_id: str, *, success: bool, exit_code: int | None = None, summary: Mapping[str, Any] | None = None) -> None:
        payload = {"artifacts": self._hash_artifacts(run_id), "exit_code": exit_code, "summary": dict(summary or {})}
        self._transition(run_id, "completed" if success else "failed", payload)

    def record_approval(self, run_id: str, signed: Mapping[str, Any], trusted_public_keys: set[str]) -> None:
        verify_approval(signed, trusted_public_keys)
        if signed["run_id"] != run_id or signed["manifest_digest"] != self.manifest_digest(run_id):
            raise RegistryError("approval does not bind to this run's manifest")

        decision = _decision_digest(signed)

        def decide(history: list[dict[str, Any]]) -> str:
            # Replay: one decision is recorded at most once, anywhere in the ledger. Keyed on the
            # signed body, not the signature bytes, so it does not rely on signature non-malleability.
            if any(e["event"] == "approval_recorded" and _decision_digest(e["payload"]["approval"]) == decision
                   for e in history):
                raise RegistryError("approval replay: this signed decision is already recorded")
            state = _state_in(history, run_id)
            if state is None:
                raise RegistryError(f"unknown run {run_id!r}")
            return state

        self._append(run_id, "approval_recorded", {"approval": dict(signed)}, decide)

    # ----- retrieval / verification / comparison -----

    def manifest(self, run_id: str) -> dict[str, Any]:
        return strict_loads((self.run_dir(run_id) / "manifest.json").read_bytes())

    def manifest_digest(self, run_id: str) -> str:
        registered = [e for e in self._run_events(run_id) if e["event"] == "registered"]
        if not registered:
            raise RegistryError(f"unknown run {run_id!r}")
        return registered[0]["payload"]["manifest_sha256"]

    def get(self, run_id: str) -> dict[str, Any]:
        events = self._run_events(run_id)
        terminal = [e for e in events if e["event"] in ("completed", "failed")]
        return {
            "run_id": run_id,
            "state": self.state(run_id),
            "manifest": self.manifest(run_id),
            "manifest_sha256": self.manifest_digest(run_id),
            "outcome": terminal[-1]["event"] if terminal else None,
            "artifacts": terminal[-1]["payload"]["artifacts"] if terminal else [],
            "approvals": [e["payload"]["approval"] for e in events if e["event"] == "approval_recorded"],
        }

    def check(self, run_id: str, trusted_public_keys: set[str] | None = None) -> list[str]:
        """Read-only integrity check; returns a list of problems (empty == intact)."""
        problems: list[str] = []
        try:
            record = self.get(run_id)
        except (RegistryError, OSError) as exc:
            return [str(exc)]
        data = (self.run_dir(run_id) / "manifest.json").read_bytes()
        if sha256_hex(data) != record["manifest_sha256"]:
            problems.append("manifest bytes do not match registered digest")
        if canonical_bytes(record["manifest"]) != data:
            problems.append("manifest file is not canonical")
        if record["outcome"] is None:
            problems.append("run has no completion record")
        recorded = {a["path"]: a for a in record["artifacts"]}
        actual = {a["path"]: a for a in self._hash_artifacts(run_id)}
        for path in sorted(recorded.keys() | actual.keys()):
            if path not in actual:
                problems.append(f"artifact missing: {path}")
            elif path not in recorded:
                problems.append(f"artifact not recorded: {path}")
            elif recorded[path]["sha256"] != actual[path]["sha256"]:
                problems.append(f"artifact hash mismatch: {path}")
        for approval in record["approvals"]:
            try:
                verify_approval(approval, trusted_public_keys or {approval.get("public_key", "")})
            except RegistryError as exc:
                problems.append(str(exc))
        return problems

    def verify(self, run_id: str, trusted_public_keys: set[str] | None = None) -> list[str]:
        """check() and record the verdict as a lifecycle event."""
        problems = self.check(run_id, trusted_public_keys)
        if self.state(run_id) in ("COMPLETED", "FAILED", "VERIFIED"):
            if problems:
                self._transition(run_id, "integrity_failed", {"problems": problems})
            elif self.state(run_id) != "VERIFIED":
                self._transition(run_id, "verified", {})
        return problems

    def compare(self, run_a: str, run_b: str) -> dict[str, Any]:
        a, b = self.get(run_a), self.get(run_b)
        flat_a, flat_b = _flatten(a["manifest"]), _flatten(b["manifest"])
        arts_a = {x["path"]: x["sha256"] for x in a["artifacts"]}
        arts_b = {x["path"]: x["sha256"] for x in b["artifacts"]}
        return {
            "runs": [run_a, run_b],
            "outcomes": [a["outcome"], b["outcome"]],
            "manifest_differences": {
                k: [flat_a.get(k), flat_b.get(k)]
                for k in sorted(flat_a.keys() | flat_b.keys())
                if flat_a.get(k) != flat_b.get(k)
            },
            "artifacts_identical": sorted(k for k in arts_a.keys() & arts_b.keys() if arts_a[k] == arts_b[k]),
            "artifacts_differ": sorted(k for k in arts_a.keys() & arts_b.keys() if arts_a[k] != arts_b[k]),
            "artifacts_only_a": sorted(arts_a.keys() - arts_b.keys()),
            "artifacts_only_b": sorted(arts_b.keys() - arts_a.keys()),
        }


def _state_in(history: Sequence[Mapping[str, Any]], run_id: str) -> str | None:
    lifecycle = [e for e in history if e["run_id"] == run_id and e["event"] in TRANSITIONS]
    return lifecycle[-1]["state"] if lifecycle else None


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out.update(_flatten(v, f"{prefix}/{k}"))
        return out
    return {prefix or "/": value}


# --------------------------------------------------------------------------
# Recording an actual development run
# --------------------------------------------------------------------------


_PROBE = """
import importlib.metadata as m, json, platform, sys
def v(n):
    try: return m.version(n)
    except m.PackageNotFoundError: return None
print(json.dumps({"python": sys.version.split()[0], "executable": sys.executable,
                  "platform": platform.platform(), "dependencies": {n: v(n) for n in sys.argv[1:]}}))
"""


def probe_environment(python: str, dependencies: Sequence[str]) -> dict[str, Any]:
    """Measured versions of a *different* interpreter (e.g. the simulator's venv)."""
    proc = subprocess.run([python, "-I", "-c", _PROBE, *dependencies], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RegistryError(f"cannot probe {python}: {proc.stderr.strip()[-200:]}")
    return strict_loads(proc.stdout)


def record_run(
    registry: Registry,
    *,
    run_id: str,
    track: str,
    purpose: str,
    command: Sequence[str],
    config: Mapping[str, Any] | None = None,
    simulator: Mapping[str, str] | None = None,
    external_sources: Sequence[Mapping[str, str]] = (),
    dependencies: Sequence[str] = ("numpy", "mujoco", "jsonschema", "cryptography"),
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
    subject_python: str | None = None,
    subject_dependencies: Sequence[str] = (),
) -> dict[str, Any]:
    """Register, execute `command`, capture stdout/stderr as artifacts, finish, verify.

    The command may write extra artifacts to $M0_ARTIFACT_DIR. `provenance` describes
    the recording process; when the subject runs under another interpreter, pass
    `subject_python` and its measured environment is stored in config.subject_environment.
    """
    config = dict(config or {})
    if subject_python:
        config["subject_environment"] = probe_environment(subject_python, subject_dependencies)
    manifest: dict[str, Any] = {
        "schema_version": "m0-run-manifest-v0",
        "run_id": run_id,
        "track": track,
        "tier": "tier1_development",
        "created_at": format_ts(registry.clock()),
        "purpose": purpose,
        "command": list(command),
        "config": config,
        "provenance": collect_provenance(dependencies, external_sources, registry.root),
    }
    if simulator:
        manifest["simulator"] = dict(simulator)
    registry.register(manifest)
    art = registry.run_dir(run_id) / "artifacts"
    art.mkdir()
    registry.start(run_id)
    run_env = {**os.environ, **(env or {}), "M0_ARTIFACT_DIR": str(art)}
    try:
        proc = subprocess.run(list(command), cwd=cwd, env=run_env, capture_output=True, timeout=timeout_s)
        code: int | None = proc.returncode
        out, err = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        code, out, err = None, exc.stdout or b"", (exc.stderr or b"") + b"\n[m0] TIMEOUT\n"
    (art / "stdout.txt").write_bytes(out)
    (art / "stderr.txt").write_bytes(err)
    registry.finish(run_id, success=code == 0, exit_code=code)
    registry.verify(run_id)
    return registry.get(run_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation.run_registry")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="record a development run: ... run --root R --run-id X -- cmd args")
    run.add_argument("--root", required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--track", default="cpu_contract")
    run.add_argument("--purpose", required=True)
    run.add_argument("command", nargs=argparse.REMAINDER)
    for name in ("show", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True)
        p.add_argument("run_id")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("--root", required=True)
    cmp_.add_argument("run_a")
    cmp_.add_argument("run_b")
    args = parser.parse_args(argv)
    reg = Registry(args.root)
    if args.cmd == "run":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        result = record_run(reg, run_id=args.run_id, track=args.track, purpose=args.purpose, command=command)
        print(json.dumps({k: result[k] for k in ("run_id", "state", "outcome", "manifest_sha256")}, indent=2))
        return 0 if result["state"] == "VERIFIED" and result["outcome"] == "completed" else 1
    if args.cmd == "show":
        print(json.dumps(reg.get(args.run_id), indent=2))
        return 0
    if args.cmd == "verify":
        problems = reg.check(args.run_id)
        print(json.dumps({"run_id": args.run_id, "problems": problems}, indent=2))
        return 1 if problems else 0
    print(json.dumps(reg.compare(args.run_a, args.run_b), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
