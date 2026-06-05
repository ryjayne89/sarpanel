"""Download ASF scene products to local ZIP files."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional


def product_download_url(prod) -> Optional[str]:
    """Best-effort extraction of an HTTP download URL from an ASF product."""
    props = getattr(prod, "properties", {}) or {}
    for key in ("downloadUrl", "url", "fileURL", "remote_url"):
        val = props.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    for meth in ("download_url", "getDownloadUrl", "get_download_url"):
        if hasattr(prod, meth):
            try:
                val = getattr(prod, meth)()
                if isinstance(val, str) and val.startswith("http"):
                    return val
            except Exception:
                pass
    return None


def download_with_asf_session(url: str, dest: Path, sess, max_retries: int = 3) -> Path:
    """Stream ``url`` to ``dest`` using an authenticated ASF session, with retries."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")

    for attempt in range(1, max_retries + 1):
        try:
            with sess.get(url, allow_redirects=True, stream=True, timeout=120) as r:
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status_code}")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
            tmp.replace(dest)
            return dest
        except Exception as e:
            print(f"  download attempt {attempt}/{max_retries} failed: {e}")
            time.sleep(2 * attempt)

    raise RuntimeError("Download failed after retries")
