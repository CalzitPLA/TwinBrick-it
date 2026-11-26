"""Shared setup: no network (TWINBRICK_OFFLINE, a private cache), Qt offscreen, the engine found like the program does,
and a small synthetic OpenStreetMap world for builds that need no download."""
import os
import sys
from pathlib import Path

os.environ["TWINBRICK_OFFLINE"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "4")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def private_cache(tmp_path, monkeypatch):
    from twinbrick import net
    monkeypatch.setattr(net, "CACHE", tmp_path / "cache")
    monkeypatch.setenv("TWINBRICK_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path


LAT, LON = 51.3001, 7.6749


def offset(dn, de):
    """lat, lon of a point dn metres north and de metres east of (LAT, LON)."""
    return LAT + dn / 111_200.0, LON + de / (111_200.0 * np.cos(np.radians(LAT)))


def ring(points):
    pts = np.array([offset(n, e) for n, e in points])
    return np.vstack([pts, pts[:1]])


@pytest.fixture
def world(monkeypatch):
    """OpenStreetMap as the Overpass answer would give it, parsed: a road, a river with a bridge, a lake, a wood with
    a clearing, a house, a church with a tower part, a tree."""
    from twinbrick import osm
    F = osm.Feature
    feats = [
        F("way/1", {"highway": "residential"}, "line", line=np.array([offset(-60, -60), offset(60, 60)])),
        F("way/2", {"waterway": "river", "width": "10"}, "line", line=np.array([offset(-60, 30), offset(60, 30)])),
        F("way/3", {"highway": "tertiary", "bridge": "yes"}, "line",
          line=np.array([offset(-20, 18), offset(-20, 42)])),
        F("way/4", {"natural": "water"}, "area", outer=[ring([(-50, -50), (-50, -30), (-30, -30), (-30, -50)])]),
        F("relation/5", {"landuse": "forest", "type": "multipolygon"}, "area",
          outer=[ring([(20, -60), (20, -10), (60, -10), (60, -60)])],
          inner=[ring([(35, -45), (35, -30), (45, -30), (45, -45)])]),
        F("way/6", {"building": "house"}, "area", outer=[ring([(-10, -10), (-10, 4), (2, 4), (2, -10)])]),
        F("way/7", {"building": "church"}, "area", outer=[ring([(10, 40), (10, 60), (30, 60), (30, 40)])]),
        F("way/8", {"building:part": "yes", "height": "40", "roof:shape": "pyramidal", "roof:height": "15"}, "area",
          outer=[ring([(10, 40), (10, 46), (16, 46), (16, 40)])]),
        F("node/9", {"natural": "tree", "height": "14"}, "point", point=offset(-40, 10)),
    ]
    monkeypatch.setattr(osm, "fetch_site", lambda *a, **k: feats)
    return feats
