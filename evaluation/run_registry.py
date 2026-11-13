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

Manifests: new runs use the canonical v1.1 schema (`evaluation/spec/haplous_eval_v1_1/
schemas/manifest.schema.json`, schema_version "1.1"). Runs recorded before the package
arrived use the provisional `m0-run-manifest-v0` schema (evaluation/schemas/) and stay
verifiable. Approvals follow SIGNING_PROTOCOL.md v1.1. The registry only VERIFIES;
private signing keys are refused. Tier 3 manifests are refused (Tier 3 disabled).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import fcntl
import hashlib
import importlib.metadata
import json
import os
import platform
import re
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
from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
SPEC_SCHEMA_DIR = Path(__file__).resolve().parent / "spec" / "haplous_eval_v1_1" / "schemas"
# Canonical run_id allows any 1-128 chars; it is also a directory name here, so keep it path-safe.
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PLACEHOLDER_RE = re.compile(r"^(sha256:)?0{64}$|^0{40}$")
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


def _validator(name: str, directory: Path = SCHEMA_DIR) -> Draft202012Validator:
    schema = strict_loads((directory / name).read_bytes())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


MANIFEST_VALIDATOR = _validator("run_manifest.schema.json")
EVENT_VALIDATOR = _validator("ledger_event.schema.json")
V11 = {
    name: _validator(f"{name}.schema.json", SPEC_SCHEMA_DIR)
    for name in ("manifest", "ledger-event", "approval-envelope", "authorization-payload")
}


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


def _gpus() -> list[dict[str, Any]] | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    rows = [[c.strip() for c in line.split(",")] for line in out.splitlines() if line.strip()]
    return [{"name": n, "driver_version": d, "memory_total_mib": int(m)} for n, d, m in rows] or None


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
        "gpu": _gpus(),
    }
    if external_sources:
        prov["external_sources"] = [dict(s) for s in external_sources]
    return prov


# --------------------------------------------------------------------------
# Approvals: SIGNING_PROTOCOL.md v1.1 verification (no signing in this process)
# --------------------------------------------------------------------------


def payload_digest(payload: Mapping[str, Any]) -> str:
    """sha256 over JCS of the ENTIRE immutable authorization payload (protocol step 2)."""
    return "sha256:" + sha256_hex(canonical_bytes(payload))


def signing_message(envelope: Mapping[str, Any]) -> bytes:
    """Exact signed bytes: JCS of the envelope with ONLY signature_ed25519_base64 removed."""
    return canonical_bytes({k: v for k, v in envelope.items() if k != "signature_ed25519_base64"})


def _parse_rfc3339(text: str) -> datetime:
    value = datetime.fromisoformat(text.replace("z", "Z"))
    if value.tzinfo is None:
        raise RegistryError(f"timestamp without offset: {text!r}")
    return value


def _public_key(material: Any) -> Ed25519PublicKey:
    # The registry is an agent-side process: it must never hold signing keys.
    if isinstance(material, Ed25519PrivateKey) or (isinstance(material, bytes) and len(material) != 32):
        raise RegistryError("private or malformed key material refused; supply a 32-byte Ed25519 public key")
    if isinstance(material, Ed25519PublicKey):
        return material
    if isinstance(material, bytes):
        return Ed25519PublicKey.from_public_bytes(material)
    raise RegistryError(f"unsupported key material type {type(material).__name__}")


def verify_authorization(
    payload: Mapping[str, Any],
    envelope: Mapping[str, Any],
    trusted_keys: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Protocol steps 1, 5 and 6(b)-(e). Raises on any failure; returns the verified decision.

    trusted_keys: signer_key_id -> {"public_key": 32 raw bytes, "approver_id": str}.
    Nonce one-time use (f) and run binding (g) need ledger state: see Registry.record_approval.
    """
    payload = strict_loads(canonical_bytes(payload))  # I-JSON domain (lone surrogates, big ints)
    envelope = strict_loads(canonical_bytes(envelope))
    for name, value in (("authorization-payload", payload), ("approval-envelope", envelope)):
        errors = schema_errors(V11[name], value)
        if errors:
            raise RegistryError(f"{name} invalid: {errors}")
    key_entry = trusted_keys.get(envelope["signer_key_id"])
    if key_entry is None:
        raise RegistryError(f"untrusted signer_key_id {envelope['signer_key_id']!r}")
    key = _public_key(key_entry["public_key"])
    try:
        signature = base64.b64decode(envelope["signature_ed25519_base64"], validate=True)
        key.verify(signature, signing_message(envelope))
    except (InvalidSignature, binascii.Error, ValueError) as exc:
        raise RegistryError("approval signature invalid") from exc
    if envelope["approver_id"] != key_entry["approver_id"]:
        raise RegistryError("approver_id is not the identity bound to signer_key_id")
    if envelope["request_id"] != payload["request_id"]:
        raise RegistryError("request_id does not match the authorization payload")
    if envelope["payload_digest"] != payload_digest(payload):
        raise RegistryError("payload_digest does not match the recomputed payload digest")
    issued, expires = _parse_rfc3339(payload["issued_at"]), _parse_rfc3339(payload["expires_at"])
    signed_at = _parse_rfc3339(envelope["signed_at"])
    if not issued <= signed_at <= expires:
        raise RegistryError("approval signed outside the payload validity window")
    if now > expires:
        raise RegistryError("authorization expired")
    return {"decision": envelope["decision"], "payload_digest": envelope["payload_digest"],
            "nonce": payload["nonce"], "analysis_code_digest": payload["analysis_code_digest"]}


# Payload digests M0 has no artifact to compare against; recorded, never reported as checked.
UNCHECKED_AUTHORIZATION_FIELDS = (
    "candidate_digest", "incumbent_digest", "policy_checkpoint_digest", "benchmark_digest",
    "trusted_runner_digest", "scorer_digest", "preflight_report_digest", "variant_registry_digest",
)


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
        if isinstance(value, dict) and value.get("schema_version") == "1.1":
            errors = schema_errors(V11["manifest"], value) + _v11_manifest_policy_errors(value)
        else:
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

    def record_approval(
        self,
        run_id: str,
        payload: Mapping[str, Any],
        envelope: Mapping[str, Any],
        trusted_keys: Mapping[str, Mapping[str, Any]],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Verify per SIGNING_PROTOCOL v1.1 and append the decision (APPROVED or REJECTED).

        Recording is not authorization to run anything: Tier 3 stays disabled, and
        `authorized` is true only for a verified APPROVED decision.
        """
        verified = verify_authorization(payload, envelope, trusted_keys, now or self.clock())
        manifest = self.manifest(run_id)
        if manifest.get("schema_version") != "1.1":
            raise RegistryError("only v1.1 manifests can be bound to an authorization")
        if manifest["analysis_code_digest"] != verified["analysis_code_digest"]:  # protocol 6(g)
            raise RegistryError("authorization analysis_code_digest does not match the run manifest")
        record = {
            "payload": dict(payload),
            "envelope": dict(envelope),
            "payload_digest": verified["payload_digest"],
            "decision": verified["decision"],
            "authorized": verified["decision"] == "APPROVED",
            "unchecked": list(UNCHECKED_AUTHORIZATION_FIELDS),
        }

        def decide(history: list[dict[str, Any]]) -> str:
            # 6(f): one-time nonce, checked against the whole ledger under the lock.
            if any(e["event"] == "approval_recorded" and e["payload"]["approval"]["payload"]["nonce"] == verified["nonce"]
                   for e in history):
                raise RegistryError("replayed nonce: this authorization was already used")
            state = _state_in(history, run_id)
            if state is None:
                raise RegistryError(f"unknown run {run_id!r}")
            return state

        self._append(run_id, "approval_recorded", {"approval": record}, decide)
        return record

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

    def check(self, run_id: str, trusted_keys: Mapping[str, Mapping[str, Any]] | None = None) -> list[str]:
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
            if payload_digest(approval["payload"]) != approval["payload_digest"]:
                problems.append("recorded approval payload does not match its digest")
            if trusted_keys is not None:  # re-verify as of signing time; expiry was checked when recorded
                try:
                    signed_at = _parse_rfc3339(approval["envelope"]["signed_at"])
                    verify_authorization(approval["payload"], approval["envelope"], trusted_keys, signed_at)
                except RegistryError as exc:
                    problems.append(str(exc))
        return problems

    def verify(self, run_id: str, trusted_keys: Mapping[str, Mapping[str, Any]] | None = None) -> list[str]:
        """check() and record the verdict as a lifecycle event."""
        problems = self.check(run_id, trusted_keys)
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
            # Not comparable across GPU types without an explicit cross-hardware A/A check.
            "hardware_comparable": _gpu_names(a["manifest"]) == _gpu_names(b["manifest"]),
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


def _v11_manifest_policy_errors(m: Mapping[str, Any]) -> list[str]:
    """Rules the canonical schema states in prose but cannot express."""
    errors = []
    if not RUN_ID_RE.match(m["run_id"]):
        errors.append(f"run_id {m['run_id']!r} is not path-safe")
    if m["tier"] == 3:
        errors.append("tier 3 is disabled in M0 (no protected runner)")
    placeholders = sorted(k for k, v in _flatten(m).items() if isinstance(v, str) and PLACEHOLDER_RE.match(v))
    if placeholders:  # "All supplied digests must identify real bytes, not placeholders."
        errors.append(f"placeholder digests/commits: {placeholders}")
    return errors


def _gpu_names(m: Mapping[str, Any]) -> list[str]:
    if m.get("schema_version") == "1.1":
        gpu = m["environment"].get("gpu")
        return [gpu] if gpu else []
    return [g["name"] for g in m["provenance"].get("gpu") or []]


def _state_in(history: Sequence[Mapping[str, Any]], run_id: str) -> str | None:
    lifecycle = [e for e in history if e["run_id"] == run_id and e["event"] in TRANSITIONS]
    return lifecycle[-1]["state"] if lifecycle else None


_V11_EVENT_TYPES = {"registered": "RUN_REGISTERED", "started": "RUN_STARTED",
                    "completed": "RUN_FINISHED", "failed": "RUN_FINISHED"}


def export_v11_events(registry: Registry, run_id: str) -> dict[str, Any]:
    """Project one run's M0 events onto ledger-event.schema.json v1.1, each schema-validated.

    M0 stores one global hash-chained ledger; v1.1 events are per run with their own
    sequence and previous_event_digest chain. M0 events with no v1.1 event_type
    (verified, integrity_failed) are listed in `unmapped_m0_events`, not relabelled.
    """
    events: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    prev: str | None = None
    manifest_digest = "sha256:" + registry.manifest_digest(run_id)
    for e in registry._run_events(run_id):
        details: dict[str, Any] = {"m0_event": e["event"], "m0_seq": e["seq"], "m0_hash": e["hash"]}
        evidence = None
        if e["event"] == "approval_recorded":
            approval = e["payload"]["approval"]
            event_type, actor_type, actor_id = approval["decision"], "human", approval["envelope"]["approver_id"]
            details["payload_digest"] = approval["payload_digest"]
        elif e["event"] in _V11_EVENT_TYPES:
            event_type, actor_type, actor_id = _V11_EVENT_TYPES[e["event"]], "system", "m0-registry"
            if e["event"] in ("completed", "failed"):
                details.update(outcome=e["event"], exit_code=e["payload"]["exit_code"])
                evidence = "sha256:" + sha256_hex(canonical_bytes(e["payload"]["artifacts"]))
        else:
            unmapped.append({"m0_seq": e["seq"], "m0_event": e["event"]})
            continue
        event = {
            "schema_version": "1.1", "event_id": f"{run_id}:{e['seq']}", "run_id": run_id,
            "sequence": len(events), "occurred_at": e["at"], "actor_id": actor_id, "actor_type": actor_type,
            "event_type": event_type, "previous_event_digest": prev, "manifest_digest": manifest_digest,
            "evidence_digest": evidence, "details": details,
        }
        errors = schema_errors(V11["ledger-event"], event)
        if errors:
            raise RegistryError(f"v1.1 projection invalid for {run_id} seq {e['seq']}: {errors}")
        prev = "sha256:" + sha256_hex(canonical_bytes(event))
        events.append(event)
    return {"events": events, "unmapped_m0_events": unmapped}


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
