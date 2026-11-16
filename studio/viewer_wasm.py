"""The Rerun web viewer wasm (70 MB, not in git). Served from the frontend build if a
developer built it, else from a user cache filled once from the pinned npm tarball,
checked against the sha512 integrity in studio/web/package-lock.json. No other source."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import tarfile
import threading
import urllib.request
from collections.abc import Callable
from pathlib import Path

VERSION = "0.43.1"
TARBALL_URL = f"https://registry.npmjs.org/@rerun-io/web-viewer/-/web-viewer-{VERSION}.tgz"
INTEGRITY = "sha512-894k3sQlzlk91eLgScNrkryCa8+AN7jCgAaC56k8+aSEqc7rhRBpPJlkhznfKKs93LX3leab6qbKWF0vHJgRfg=="
MEMBER = "package/re_viewer_bg.wasm"
NAME = "re_viewer_bg.wasm"


class IntegrityError(ValueError):
    pass


def default_cache() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "haplous-studio" / f"rerun-web-viewer-{VERSION}"


def locate(static_dir: Path, cache: Path) -> Path | None:
    for candidate in (static_dir / "rerun-web-viewer" / NAME, cache / NAME):
        if candidate.is_file():
            return candidate
    return None


def install(tarball: bytes, cache: Path) -> Path:
    """Check the tarball against the pinned integrity, then extract only the wasm."""
    algo, _, expected = INTEGRITY.partition("-")
    if base64.b64encode(hashlib.new(algo, tarball).digest()).decode() != expected:
        raise IntegrityError(f"tarball does not match pinned {algo} integrity")
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        member = tar.getmember(MEMBER)
        fh = tar.extractfile(member)
        if fh is None:
            raise IntegrityError(f"{MEMBER} is not a regular file in the tarball")
        payload = fh.read()
    cache.mkdir(parents=True, exist_ok=True)
    tmp = cache / f"{NAME}.tmp"
    tmp.write_bytes(payload)
    os.replace(tmp, cache / NAME)
    return cache / NAME


def fetch(cache: Path, download: Callable[[str], bytes] | None = None, log: Callable[[str], None] = print) -> Path | None:
    download = download or (lambda url: urllib.request.urlopen(url, timeout=300).read())
    log(f"studio: downloading the Rerun web viewer {VERSION} from {TARBALL_URL} (once, into {cache})")
    try:
        path = install(download(TARBALL_URL), cache)
    except Exception as exc:  # offline or tampered: the replay page shows its notice, charts still work
        log(f"studio: Rerun viewer unavailable ({type(exc).__name__}: {exc}); 3D replay is disabled")
        return None
    log(f"studio: Rerun viewer cached at {path}")
    return path


def ensure_in_background(static_dir: Path, cache: Path) -> threading.Thread | None:
    if locate(static_dir, cache) is not None:
        return None
    thread = threading.Thread(target=fetch, args=(cache,), daemon=True, name="rerun-wasm-fetch")
    thread.start()
    return thread
