"""Downloads with a cache (~/.cache/twinbrick-it), retries, and an Overpass client with mirrors.

Every file is fetched once and kept: elevation tiles, NRW tiles and Overpass answers (keyed by the query's hash).
Requests carry a generic user agent and nothing about the user; the only thing a server learns is the area asked for.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

CACHE = Path(os.environ.get("TWINBRICK_CACHE", Path.home() / ".cache" / "twinbrick-it"))
AGENT = "legolize-it-geo/0.1"            # generic: nothing about the user
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]

say: Callable[[str], None] = print


class Offline(RuntimeError):
    pass


def offline() -> bool:
    return os.environ.get("TWINBRICK_OFFLINE", "") not in ("", "0")


def fetch(url: str, dest: Path, retries: int = 4, timeout: float = 120, data: Optional[bytes] = None) -> Path:
    """The file at `url`, downloaded once to `dest` (atomically). Raises FileNotFoundError on a 404."""
    dest = Path(dest)
    if dest.exists():
        return dest
    if offline():
        raise Offline(f"offline and not cached: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for n in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": AGENT, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(body)
            tmp.replace(dest)
            return dest
        except urllib.error.HTTPError as ex:
            if ex.code == 404:
                raise FileNotFoundError(url) from ex
            last = ex
        except Exception as ex:  # noqa: BLE001 - network errors of any kind: retry
            last = ex
        time.sleep(2.0 * (n + 1))
    raise RuntimeError(f"download failed: {url}: {last}")


def fetch_json(url: str, dest: Path, max_age_days: Optional[float] = None) -> dict:
    dest = Path(dest)
    if dest.exists() and max_age_days is not None and time.time() - dest.stat().st_mtime > max_age_days * 86400 \
            and not offline():
        try:
            dest.unlink()
        except OSError:
            pass
    return json.loads(fetch(url, dest).read_text())


def overpass(query: str, timeout: float = 180) -> dict:
    """The answer of an Overpass query (JSON), cached by the query's hash; mirrors are tried in turn."""
    key = CACHE / "overpass" / (hashlib.sha1(query.encode()).hexdigest() + ".json")
    if key.exists():
        return json.loads(key.read_text())
    if offline():
        raise Offline("offline and the OpenStreetMap query is not cached")
    body = urllib.parse.urlencode({"data": query}).encode()
    last, wait = None, 3.0
    for n in range(2 * len(OVERPASS)):
        url = OVERPASS[n % len(OVERPASS)]
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read())
            if "elements" not in d:
                raise RuntimeError(d.get("remark", "no elements"))
            if d.get("remark") and "error" in d["remark"].lower():
                raise RuntimeError(d["remark"])
            key.parent.mkdir(parents=True, exist_ok=True)
            key.write_text(json.dumps(d))
            return d
        except Exception as ex:  # noqa: BLE001 - busy servers answer 429/504 or time out: next mirror
            last = ex
            say(f"  OpenStreetMap server {urllib.parse.urlparse(url).netloc}: {ex} - trying again")
            time.sleep(wait)
            wait = min(wait * 1.6, 30)
    raise RuntimeError(f"OpenStreetMap (Overpass) failed: {last}")
