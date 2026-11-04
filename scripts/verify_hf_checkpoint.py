"""Pin a Hugging Face checkpoint to an exact revision and measure its weight memory.

For each file under --path at the pinned commit:
  * size and LFS SHA-256 (HF's LFS oid is the SHA-256 of the file content);
  * for *.safetensors: dtype/shape of every tensor from the header (byte-range read,
    no full download) -> parameter count and weight bytes per dtype;
  * with --download: stream the file and recompute SHA-256 locally (must equal the oid).
Small text files (config.json, README.md, *.json < 1 MB) are fetched and stored verbatim.

Weight bytes are a lower bound on GPU memory (no activations, KV cache, CUDA context).

Usage:
  python scripts/verify_hf_checkpoint.py --repo rsj2003/libero-dynamic-paper-models \
      --path pi05/static_official --out results/checkpoints/pi05_static_official.json [--download]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

HF = "https://huggingface.co"
DTYPE_BYTES = {"F64": 8, "F32": 4, "F16": 2, "BF16": 2, "I64": 8, "I32": 4, "I16": 2, "I8": 1, "U8": 1,
               "BOOL": 1, "F8_E4M3": 1, "F8_E5M2": 1}
SMALL_TEXT = 1_000_000

Fetch = Callable[[str, tuple[int, int] | None], bytes]


def http_fetch(url: str, byte_range: tuple[int, int] | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "haplous-checkpoint-verify"})
    if byte_range:
        req.add_header("Range", f"bytes={byte_range[0]}-{byte_range[1]}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def safetensors_summary(url: str, fetch: Fetch) -> dict:
    (n,) = struct.unpack("<Q", fetch(url, (0, 7)))
    if not 0 < n < 100_000_000:
        raise ValueError(f"implausible safetensors header length {n}")
    header = json.loads(fetch(url, (8, 8 + n - 1)))
    by_dtype: dict[str, dict[str, int]] = {}
    for name, t in header.items():
        if name == "__metadata__":
            continue
        numel = 1
        for d in t["shape"]:
            numel *= d
        if t["data_offsets"][1] - t["data_offsets"][0] != numel * DTYPE_BYTES[t["dtype"]]:
            raise ValueError(f"{name}: offsets disagree with dtype/shape")
        agg = by_dtype.setdefault(t["dtype"], {"tensors": 0, "params": 0, "bytes": 0})
        agg["tensors"] += 1
        agg["params"] += numel
        agg["bytes"] += numel * DTYPE_BYTES[t["dtype"]]
    return {
        "params": sum(v["params"] for v in by_dtype.values()),
        "weight_bytes": sum(v["bytes"] for v in by_dtype.values()),
        "by_dtype": by_dtype,
        "metadata": header.get("__metadata__"),
    }


def stream_sha256(url: str) -> str:
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "haplous-checkpoint-verify"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        while chunk := resp.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()


def verify(repo: str, path: str, revision: str, download: bool, fetch: Fetch = http_fetch) -> dict:
    q = urllib.parse.quote
    sha = json.loads(fetch(f"{HF}/api/models/{repo}/revision/{q(revision, safe='')}", None))["sha"]
    tree = json.loads(fetch(f"{HF}/api/models/{repo}/tree/{sha}/{q(path)}?recursive=true", None))
    files, text, totals = [], {}, {"params": 0, "weight_bytes": 0}
    for entry in sorted((e for e in tree if e["type"] == "file"), key=lambda e: e["path"]):
        url = f"{HF}/{repo}/resolve/{sha}/{q(entry['path'])}"
        rec = {"path": entry["path"], "size": entry["size"], "sha256": (entry.get("lfs") or {}).get("oid")}
        if entry["path"].endswith(".safetensors"):
            rec["safetensors"] = safetensors_summary(url, fetch)
            totals["params"] += rec["safetensors"]["params"]
            totals["weight_bytes"] += rec["safetensors"]["weight_bytes"]
        elif entry["size"] < SMALL_TEXT and entry["path"].endswith((".json", ".md", ".yaml", ".txt")):
            body = fetch(url, None)
            text[entry["path"]] = body.decode("utf-8", errors="replace")
            rec["sha256"] = rec["sha256"] or hashlib.sha256(body).hexdigest()
        if download:
            rec["sha256_local"] = stream_sha256(url)
            if rec["sha256"] and rec["sha256_local"] != rec["sha256"]:
                raise ValueError(f"{entry['path']}: local hash != LFS oid")
        files.append(rec)
    return {
        "repo": repo,
        "path": path,
        "requested_revision": revision,
        "revision_sha": sha,
        "files": files,
        "formats": sorted({Path(f["path"]).suffix or "<none>" for f in files}),
        "totals_from_safetensors": totals,
        "weight_gib": round(totals["weight_bytes"] / 2**30, 3),
        "note": "weight bytes only; excludes activations, CUDA context, allocator overhead",
        "text_files": text,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--path", default="")
    ap.add_argument("--revision", default="main")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--download", action="store_true")
    a = ap.parse_args()
    result = verify(a.repo, a.path, a.revision, a.download)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: result[k] for k in ("revision_sha", "formats", "totals_from_safetensors", "weight_gib")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
