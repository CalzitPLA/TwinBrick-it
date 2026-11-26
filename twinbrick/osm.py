"""OpenStreetMap data for a site: one Overpass query (cached), parsed into areas, lines and points in lat/lon.

Areas are lists of rings (outer and inner) - a multipolygon relation's member ways are joined into rings first. The
classes the model uses are decided in `twinbrick.landcover`; this module only reads.
OpenStreetMap data (c) OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import net

ATTRIBUTION = "OpenStreetMap: (c) OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright)"

AREA_KEYS = ("building", "building:part", "landuse", "natural", "leisure", "amenity", "place", "area:highway",
             "man_made", "waterway", "water")
LINE_ONLY = {("natural", "tree_row"), ("natural", "coastline"), ("natural", "cliff"), ("waterway", "river"),
             ("waterway", "stream"), ("waterway", "canal"), ("waterway", "ditch"), ("waterway", "drain")}


@dataclass
class Feature:
    osm: str                                    # "way/123", "relation/4", "node/5"
    tags: Dict[str, str]
    kind: str                                   # "area", "line", "point"
    outer: List[np.ndarray] = field(default_factory=list)   # rings (n, 2) [lat, lon]
    inner: List[np.ndarray] = field(default_factory=list)
    line: Optional[np.ndarray] = None
    point: Optional[Tuple[float, float]] = None

    def get(self, key, default=None):
        return self.tags.get(key, default)


def site_query(s: float, w: float, n: float, e: float, buildings: bool) -> str:
    b = f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
    parts = [
        f'way["highway"]{b};',
        f'way["railway"~"^(rail|light_rail|tram|subway|narrow_gauge|preserved|monorail)$"]{b};',
        f'way["waterway"~"^(river|stream|canal|ditch|drain|riverbank|dock)$"]{b};',
        f'nwr["natural"~"^(water|wood|scrub|heath|grassland|sand|beach|bare_rock|scree|shingle|wetland|glacier|'
        f'tree_row|tree)$"]{b};',
        f'way["landuse"]{b};', f'relation["landuse"]["type"="multipolygon"]{b};',
        f'nwr["leisure"~"^(park|garden|pitch|playground|golf_course|stadium|track|swimming_pool|sports_centre|'
        f'dog_park|miniature_golf)$"]{b};',
        f'nwr["amenity"~"^(parking|grave_yard|marketplace)$"]{b};',
        f'way["place"="square"]{b};', f'way["area:highway"]{b};',
        f'way["man_made"~"^(bridge|pier|breakwater)$"]{b};',
        f'way["natural"="coastline"]{b};',
    ]
    if buildings:
        parts += [f'way["building"]{b};', f'relation["building"]{b};', f'way["building:part"]{b};',
                  f'relation["building:part"]{b};']
    return "[out:json][timeout:180][maxsize:1073741824];(\n" + "\n".join(parts) + "\n);out geom;"


def outline_query(ref: str) -> str:
    kind, num = ref.split("/")
    return f"[out:json][timeout:90];{kind}({int(num)});out geom tags;"


def _coords(geom) -> np.ndarray:
    return np.array([[p["lat"], p["lon"]] for p in geom if p is not None], float)


def join_rings(segments: List[np.ndarray]) -> List[np.ndarray]:
    """Member ways of a multipolygon joined end to end into rings (unclosed leftovers are closed as they are)."""
    segs = [s for s in segments if len(s) >= 2]
    rings = []
    while segs:
        cur = segs.pop(0)
        grown = True
        while grown and not np.array_equal(cur[0], cur[-1]):
            grown = False
            for k, s in enumerate(segs):
                if np.array_equal(cur[-1], s[0]):
                    cur = np.vstack([cur, s[1:]])
                elif np.array_equal(cur[-1], s[-1]):
                    cur = np.vstack([cur, s[::-1][1:]])
                elif np.array_equal(cur[0], s[-1]):
                    cur = np.vstack([s, cur[1:]])
                elif np.array_equal(cur[0], s[0]):
                    cur = np.vstack([s[::-1], cur[1:]])
                else:
                    continue
                segs.pop(k)
                grown = True
                break
        if len(cur) >= 3:
            rings.append(cur)
    return rings


def _is_area(tags: Dict[str, str], closed: bool) -> bool:
    if not closed:
        return False
    if tags.get("area") == "no":
        return False
    if tags.get("area") == "yes":
        return True
    if "highway" in tags or "railway" in tags or "barrier" in tags:
        return "area:highway" in tags
    return any(k in tags and (k, tags[k]) not in LINE_ONLY for k in AREA_KEYS)


def parse(data: dict) -> List[Feature]:
    out: List[Feature] = []
    for el in data.get("elements", []):
        tags = el.get("tags", {}) or {}
        t = el["type"]
        ref = f"{t}/{el['id']}"
        if t == "node":
            if "lat" in el:
                out.append(Feature(ref, tags, "point", point=(el["lat"], el["lon"])))
        elif t == "way":
            g = el.get("geometry")
            if not g:
                continue
            c = _coords(g)
            if len(c) < 2:
                continue
            closed = len(c) >= 4 and np.array_equal(c[0], c[-1])
            if _is_area(tags, closed):
                out.append(Feature(ref, tags, "area", outer=[c]))
            else:
                out.append(Feature(ref, tags, "line", line=c))
        elif t == "relation":
            if tags.get("type") not in ("multipolygon", "building", None):
                continue
            outer, inner = [], []
            for m in el.get("members", []):
                if m.get("type") != "way" or not m.get("geometry"):
                    continue
                (inner if m.get("role") == "inner" else outer).append(_coords(m["geometry"]))
            ro, ri = join_rings(outer), join_rings(inner)
            if ro:
                out.append(Feature(ref, tags, "area", outer=ro, inner=ri))
    return out


def fetch_site(s: float, w: float, n: float, e: float, buildings: bool) -> List[Feature]:
    return parse(net.overpass(site_query(s, w, n, e, buildings)))


def fetch_outline(ref: str) -> Feature:
    """The outline of one way or relation ("way/146943431")."""
    if not re.match(r"^(way|relation)/\d+$", ref):
        raise ValueError(f"an OSM outline is way/ID or relation/ID, not {ref!r}")
    feats = [f for f in parse(net.overpass(outline_query(ref))) if f.osm == ref]
    if not feats:
        raise ValueError(f"{ref}: not found in OpenStreetMap")
    f = feats[0]
    if f.kind == "line":                                       # a closed way without area tags: take it as area
        f = Feature(f.osm, f.tags, "area", outer=[f.line])
    return f


# ------------------------------------------------------------------ tag values
_NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def metres(value: Optional[str]) -> Optional[float]:
    """"12", "12 m", "12.5m", "40'", "40 ft", "3,5" -> metres (None when it does not parse)."""
    if not value:
        return None
    v = value.strip().lower().split(";")[0]
    m = _NUM.search(v)
    if not m:
        return None
    x = float(m.group(0).replace(",", "."))
    if "'" in v or "ft" in v or "feet" in v:
        x *= 0.3048
        inch = re.search(r"(\d+)\s*\"", v)
        if inch:
            x += int(inch.group(1)) * 0.0254
    elif "cm" in v:
        x /= 100.0
    elif "km" in v:
        x *= 1000.0
    return x if np.isfinite(x) else None


def number(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    m = _NUM.search(value)
    return float(m.group(0).replace(",", ".")) if m else None


CSS = {"white": (1.0, 1.0, 1.0), "black": (0.0, 0.0, 0.0), "grey": (0.5, 0.5, 0.5), "gray": (0.5, 0.5, 0.5),
       "silver": (0.75, 0.75, 0.75), "red": (0.8, 0.1, 0.1), "darkred": (0.55, 0.0, 0.0), "maroon": (0.5, 0.0, 0.0),
       "brown": (0.55, 0.27, 0.07), "orange": (1.0, 0.55, 0.0), "yellow": (1.0, 0.85, 0.2), "beige": (0.96, 0.96, 0.86),
       "tan": (0.82, 0.71, 0.55), "green": (0.1, 0.5, 0.1), "darkgreen": (0.0, 0.39, 0.0), "blue": (0.1, 0.3, 0.8),
       "lightblue": (0.68, 0.85, 0.9), "darkgrey": (0.33, 0.33, 0.33), "darkgray": (0.33, 0.33, 0.33),
       "lightgrey": (0.83, 0.83, 0.83), "lightgray": (0.83, 0.83, 0.83), "cream": (0.99, 0.98, 0.85),
       "pink": (1.0, 0.75, 0.8), "purple": (0.5, 0.0, 0.5), "gold": (0.85, 0.65, 0.13), "copper": (0.45, 0.65, 0.55)}


def colour(value: Optional[str]) -> Optional[Tuple[float, float, float]]:
    """An OSM colour tag ("#aabbcc", "red", "dark_grey") -> RGB 0..1, or None."""
    if not value:
        return None
    v = value.strip().lower().split(";")[0].replace("_", "").replace(" ", "").replace("-", "")
    if v.startswith("#") and len(v) in (4, 7):
        h = v[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        try:
            return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        except ValueError:
            return None
    return CSS.get(v)
