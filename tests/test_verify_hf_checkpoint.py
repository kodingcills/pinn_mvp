"""Offline checks of scripts/verify_hf_checkpoint.py against a hand-built safetensors file."""

from __future__ import annotations

import hashlib
import json
import struct

import pytest

from scripts.verify_hf_checkpoint import HF, safetensors_summary, verify


def _safetensors(tensors: dict[str, tuple[str, list[int], int]]) -> bytes:
    header, offset = {"__metadata__": {"format": "pt"}}, 0
    for name, (dtype, shape, itemsize) in tensors.items():
        n = itemsize
        for d in shape:
            n *= d
        header[name] = {"dtype": dtype, "shape": shape, "data_offsets": [offset, offset + n]}
        offset += n
    h = json.dumps(header).encode()
    return struct.pack("<Q", len(h)) + h + b"\0" * offset


def _fetcher(blobs: dict[str, bytes]):
    def fetch(url, byte_range):
        data = blobs[url]
        return data[byte_range[0]: byte_range[1] + 1] if byte_range else data

    return fetch


def test_param_count_and_weight_bytes():
    blob = _safetensors({"w": ("BF16", [1000, 3000], 2), "b": ("F32", [3000], 4), "s": ("I64", [], 8)})
    s = safetensors_summary("u", _fetcher({"u": blob}))
    assert s["params"] == 3_000_000 + 3000 + 1
    assert s["weight_bytes"] == 6_000_000 + 12_000 + 8
    assert s["by_dtype"]["BF16"] == {"tensors": 1, "params": 3_000_000, "bytes": 6_000_000}


def test_inconsistent_header_rejected():
    blob = bytearray(_safetensors({"w": ("BF16", [10], 2)}))
    blob[:] = blob.replace(b'"BF16"', b'"F32" ')  # dtype now disagrees with offsets
    with pytest.raises(ValueError, match="offsets"):
        safetensors_summary("u", _fetcher({"u": bytes(blob)}))


def test_verify_pins_revision_and_reads_tree():
    sha = "a" * 40
    model = _safetensors({"w": ("BF16", [4, 4], 2)})
    cfg = b'{"type": "pi05"}'
    tree = [
        {"type": "file", "path": "p/model.safetensors", "size": len(model),
         "lfs": {"oid": hashlib.sha256(model).hexdigest(), "size": len(model)}},
        {"type": "file", "path": "p/config.json", "size": len(cfg)},
        {"type": "file", "path": "p/.gitattributes", "size": 3},
        {"type": "directory", "path": "p/sub"},
    ]
    fetch = _fetcher({
        f"{HF}/api/models/o/r/revision/main": json.dumps({"sha": sha}).encode(),
        f"{HF}/api/models/o/r/tree/{sha}/p?recursive=true": json.dumps(tree).encode(),
        f"{HF}/o/r/resolve/{sha}/p/model.safetensors": model,
        f"{HF}/o/r/resolve/{sha}/p/config.json": cfg,
        f"{HF}/o/r/resolve/{sha}/p/.gitattributes": b"*.x",
    })
    out = verify("o/r", "p", "main", download=False, fetch=fetch)
    assert out["revision_sha"] == sha
    assert out["totals_from_safetensors"] == {"params": 16, "weight_bytes": 32}
    assert out["text_files"]["p/config.json"] == '{"type": "pi05"}'
    assert [f["sha256"] for f in out["files"]] == [
        hashlib.sha256(b"*.x").hexdigest(), hashlib.sha256(cfg).hexdigest(), tree[0]["lfs"]["oid"]]
    assert ".gitattributes" not in "".join(out["text_files"])
