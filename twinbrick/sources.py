"""Elevation, building and canopy sources. Every source answers in the site's grid coordinates (easting, northing of
its UTM zone) and returns NaN where it has nothing, so sources can be stacked: the first that knows a point wins.

- Geobasis NRW (the "Digitaler Zwilling NRW", Datenlizenz Deutschland - Zero - 2.0; EPSG:25832, 1 km tiles from
  opengeodata.nrw.de, found through each product's index.json):
    DGM1      bare-earth terrain, 1 m
    nDOM50    height above ground, 0.5 m (trees; buildings come from LoD2)
    LoD2      CityGML buildings with their roof surfaces
- Terrain Tiles (Terrarium PNGs on AWS open data, worldwide, zoom <= 15; SRTM, ETOPO1, EU-DEM, 3DEP and others).
- Flat ground (no download; for tests and for sites where only the buildings matter).
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from . import net
from .crs import UTM, mercator_pixel, mercator_resolution

Image.MAX_IMAGE_PIXELS = None

NRW_BASE = "https://www.opengeodata.nrw.de/produkte/geobasis"
NRW_PRODUCTS = {
    "dgm1": ("hm/dgm1_tiff/dgm1_tiff", 1.0),
    "ndom50": ("hm/ndom50_tiff/ndom50_tiff", 0.5),
    "lod2": ("3dg/lod2_gml/lod2_gml", None),
}
NRW_BBOX = (5.80, 50.30, 9.50, 52.55)                # lon/lat: roughly North Rhine-Westphalia (the index decides)
NRW_ATTRIBUTION = "Geobasis NRW (Digitaler Zwilling NRW): Datenlizenz Deutschland - Zero - Version 2.0"
TERRARIUM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TERRARIUM_ATTRIBUTION = ("Terrain Tiles (AWS open data, Mapzen/Tilezen): SRTM, GMTED, ETOPO1, EU-DEM (Copernicus), "
                         "3DEP and others - see github.com/tilezen/joerd/blob/master/docs/attribution.md")


def in_nrw(lat: float, lon: float) -> bool:
    return NRW_BBOX[0] <= lon <= NRW_BBOX[2] and NRW_BBOX[1] <= lat <= NRW_BBOX[3]


# ------------------------------------------------------------------ NRW tiles
class NRWIndex:
    """The tile names of an NRW product, keyed by the 1 km tile (east km, north km)."""

    def __init__(self, product: str):
        self.product = product
        path, _ = NRW_PRODUCTS[product]
        self.url = f"{NRW_BASE}/{path}/"
        d = net.fetch_json(self.url + "index.json", net.CACHE / "nrw" / f"{product}_index.json", max_age_days=30)
        self.names: Dict[Tuple[int, int], str] = {}
        self.sizes: Dict[Tuple[int, int], int] = {}
        for ds in d.get("datasets", []):
            for f in ds.get("files", []):
                key = _nrw_key(f["name"])
                if key:
                    self.names[key] = f["name"]
                    self.sizes[key] = int(f.get("size", 0) or 0)

    def tiles_for(self, box) -> List[Tuple[int, int]]:
        e0, n0, e1, n1 = box
        return [(e, n) for e in range(int(e0 // 1000), int(e1 // 1000) + 1)
                for n in range(int(n0 // 1000), int(n1 // 1000) + 1)]

    def covers(self, box) -> bool:
        return all(t in self.names for t in self.tiles_for(box))

    def download_size(self, box) -> int:
        return sum(self.sizes.get(t, 0) for t in self.tiles_for(box))

    def path(self, tile: Tuple[int, int]) -> Optional[Path]:
        name = self.names.get(tile)
        if name is None:
            return None
        dest = net.CACHE / "nrw" / self.product / name
        if not dest.exists():
            net.say(f"  download {name} ({self.sizes.get(tile, 0) / 1e6:.1f} MB)")
        return net.fetch(self.url + name, dest, timeout=300)


def _nrw_key(name: str) -> Optional[Tuple[int, int]]:
    import re
    m = re.match(r"^(?:dgm1_32_|ndom50_32|dom1_32_|LoD2_32_)(\d{3})_(\d{4})_", name)
    return (int(m.group(1)), int(m.group(2))) if m else None


class GeoTiffTiles:
    """Bilinear sampling of 1 km NRW GeoTIFF tiles (float32, pixel-is-area, upper-left tie point)."""

    def __init__(self, index: NRWIndex, box, nodata_below: float = -1000.0):
        self.tiles = []
        for t in index.tiles_for(box):
            p = index.path(t)
            if p is None:
                continue
            im = Image.open(p)
            tie = im.tag_v2.get(33922)
            scale = im.tag_v2.get(33550)
            a = np.asarray(im, dtype=np.float32)
            a = np.where(a < nodata_below, np.nan, a)
            self.tiles.append((t[0] * 1000, t[1] * 1000, float(tie[3]), float(tie[4]), float(scale[0]), a))

    def sample(self, e, n) -> np.ndarray:
        e, n = np.asarray(e, float), np.asarray(n, float)
        out = np.full(e.shape, np.nan)
        for te, tn, x0, y0, px, a in self.tiles:
            k = (e >= te) & (e < te + 1000) & (n >= tn) & (n < tn + 1000)
            if not k.any():
                continue
            fx = np.clip((e[k] - x0) / px - 0.5, 0, a.shape[1] - 1.001)
            fy = np.clip((y0 - n[k]) / px - 0.5, 0, a.shape[0] - 1.001)
            i, j = fx.astype(int), fy.astype(int)
            u, v = fx - i, fy - j
            out[k] = (a[j, i] * (1 - u) * (1 - v) + a[j, i + 1] * u * (1 - v)
                      + a[j + 1, i] * (1 - u) * v + a[j + 1, i + 1] * u * v)
        return out

    def max_in_cells(self, e, n, half: float) -> np.ndarray:
        """The highest value within +-half metres of each point (axis-aligned in the grid; for canopy tops)."""
        steps = max(1, int(round(half / 0.5)))
        best = np.full(np.shape(e), -np.inf)
        for de in np.linspace(-half, half, 2 * steps + 1):
            for dn in np.linspace(-half, half, 2 * steps + 1):
                best = np.fmax(best, self.sample(np.asarray(e) + de, np.asarray(n) + dn))
        return np.where(np.isfinite(best), best, np.nan)


# ------------------------------------------------------------------ terrain
class Terrain:
    name = "terrain"
    attribution = ""

    def height(self, e, n) -> np.ndarray:
        raise NotImplementedError


class FlatTerrain(Terrain):
    name = "flat"

    def __init__(self, z: float = 0.0):
        self.z = z

    def height(self, e, n):
        return np.full(np.shape(e), float(self.z))


class NRWTerrain(Terrain):
    name = "NRW DGM1 (1 m)"
    attribution = NRW_ATTRIBUTION

    def __init__(self, box):
        self.tiff = GeoTiffTiles(NRWIndex("dgm1"), box)

    def height(self, e, n):
        return self.tiff.sample(e, n)


class TerrariumTerrain(Terrain):
    attribution = TERRARIUM_ATTRIBUTION

    def __init__(self, crs: UTM, box, resolution: float, max_tiles: int = 256):
        self.crs = crs
        e0, n0, e1, n1 = box
        la, lo = crs.inverse(np.array([e0, e1, e0, e1]), np.array([n0, n0, n1, n1]))
        lat_c = float(np.mean(la))
        z = int(np.clip(math.ceil(math.log2(mercator_resolution(lat_c, 0) / max(resolution, 1.0))), 1, 15))
        while True:
            x0, y0 = mercator_pixel(la.max(), lo.min(), z)
            x1, y1 = mercator_pixel(la.min(), lo.max(), z)
            tx0, ty0, tx1, ty1 = int(x0 // 256), int(y0 // 256), int(x1 // 256), int(y1 // 256)
            if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) <= max_tiles or z <= 1:
                break
            z -= 1
        self.zoom = z
        self.name = f"Terrain Tiles z{z} (~{mercator_resolution(lat_c, z):.0f} m)"
        self.tx0, self.ty0 = tx0, ty0
        rows = []
        for ty in range(ty0, ty1 + 1):
            row = []
            for tx in range(tx0, tx1 + 1):
                p = net.fetch(TERRARIUM.format(z=z, x=tx, y=ty), net.CACHE / "terrarium" / str(z) / f"{tx}_{ty}.png")
                rgb = np.asarray(Image.open(p).convert("RGB"), dtype=np.float64)
                row.append(rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768)
            rows.append(np.hstack(row))
        self.a = np.vstack(rows)

    def height(self, e, n):
        la, lo = self.crs.inverse(e, n)
        x, y = mercator_pixel(la, lo, self.zoom)
        fx = np.clip(x - self.tx0 * 256 - 0.5, 0, self.a.shape[1] - 1.001)
        fy = np.clip(y - self.ty0 * 256 - 0.5, 0, self.a.shape[0] - 1.001)
        i, j = fx.astype(int), fy.astype(int)
        u, v = fx - i, fy - j
        a = self.a
        return (a[j, i] * (1 - u) * (1 - v) + a[j, i + 1] * u * (1 - v)
                + a[j + 1, i] * (1 - u) * v + a[j + 1, i + 1] * u * v)


class StackedTerrain(Terrain):
    """The first source that knows a point."""

    def __init__(self, sources: Sequence[Terrain]):
        self.sources = list(sources)
        self.name = " + ".join(s.name for s in self.sources)
        self.attribution = "; ".join(s.attribution for s in self.sources if s.attribution)

    def height(self, e, n):
        out = np.full(np.shape(e), np.nan)
        for s in self.sources:
            miss = np.isnan(out)
            if not miss.any():
                break
            out[miss] = s.height(np.asarray(e)[miss], np.asarray(n)[miss])
        return out


# ------------------------------------------------------------------ buildings
@dataclass
class Building:
    """One building in grid coordinates. LoD2: `roofs` (3D polygons). OSM: `height` above its lowest ground, roof
    shape and height. `grounds`: footprint rings (outer and inner)."""
    ref: str
    grounds: List[np.ndarray]
    holes: List[np.ndarray] = field(default_factory=list)
    roofs: List[np.ndarray] = field(default_factory=list)
    height: Optional[float] = None
    roof_shape: str = "flat"
    roof_height: float = 0.0
    wall_rgb: Optional[Tuple[float, float, float]] = None
    roof_rgb: Optional[Tuple[float, float, float]] = None
    function: str = ""
    part: bool = False                      # an OSM building:part


def nrw_lod2(box, buffer: float = 0.0) -> List[Building]:
    """LoD2 buildings (Geobasis NRW) whose footprint touches the box (grid metres, EPSG:25832)."""
    index = NRWIndex("lod2")
    e0, n0, e1, n1 = box
    out = []
    for t in index.tiles_for(box):
        p = index.path(t)
        if p is None:
            continue
        for _, el in ET.iterparse(p, events=("end",)):
            if not el.tag.endswith("}Building"):
                continue
            grounds, roofs, func = [], [], ""
            for s in el.iter():
                tag = s.tag.rsplit("}", 1)[-1]
                if tag == "function" and not func and s.text:
                    func = s.text.strip()
                if tag in ("GroundSurface", "RoofSurface"):
                    for pl in s.iter():
                        if pl.tag.endswith("}posList") and pl.text:
                            q = np.array(pl.text.split(), float).reshape(-1, 3)
                            (grounds if tag == "GroundSurface" else roofs).append(q[:, :2] if tag == "GroundSurface"
                                                                                  else q)
            if roofs:
                allp = np.vstack([r[:, :2] for r in roofs] + grounds)
                if (allp[:, 0].max() >= e0 and allp[:, 0].min() <= e1 and allp[:, 1].max() >= n0
                        and allp[:, 1].min() <= n1):
                    out.append(Building(el.get("{http://www.opengis.net/gml}id", ""), grounds or
                                        [r[:, :2] for r in roofs], roofs=roofs, function=func))
            el.clear()
    return out


# OSM: wall heights by building type when no height or levels are tagged (metres; roof shape, roof height)
OSM_DEFAULTS = {
    "house": (6.0, "gabled", 3.5), "detached": (6.0, "gabled", 3.5), "semidetached_house": (6.0, "gabled", 3.5),
    "terrace": (7.0, "gabled", 3.5), "residential": (9.0, "gabled", 3.5), "apartments": (12.0, "gabled", 3.5),
    "farm": (6.0, "gabled", 4.0), "barn": (6.0, "gabled", 4.0), "farm_auxiliary": (5.0, "gabled", 3.0),
    "garage": (2.8, "flat", 0.0), "garages": (2.8, "flat", 0.0), "carport": (2.6, "flat", 0.0),
    "shed": (2.6, "flat", 0.0), "hut": (2.8, "gabled", 1.5), "roof": (4.0, "flat", 0.0),
    "church": (14.0, "gabled", 8.0), "chapel": (7.0, "gabled", 4.0), "cathedral": (25.0, "gabled", 12.0),
    "industrial": (9.0, "flat", 0.0), "warehouse": (9.0, "flat", 0.0), "retail": (6.0, "flat", 0.0),
    "supermarket": (6.0, "flat", 0.0), "commercial": (12.0, "flat", 0.0), "office": (15.0, "flat", 0.0),
    "school": (10.0, "flat", 0.0), "university": (14.0, "flat", 0.0), "hospital": (15.0, "flat", 0.0),
    "public": (10.0, "flat", 0.0), "civic": (10.0, "flat", 0.0), "train_station": (9.0, "flat", 0.0),
    "hotel": (15.0, "flat", 0.0), "stadium": (15.0, "flat", 0.0), "greenhouse": (3.5, "gabled", 1.0),
    "service": (3.0, "flat", 0.0), "transformer_tower": (6.0, "flat", 0.0), "bunker": (4.0, "flat", 0.0),
}
LEVEL_M = 3.0


def osm_buildings(features, frame_crs: UTM, osm_colours: bool = False) -> List[Building]:
    """OSM buildings (ways and multipolygons; building:part where tagged) with heights from height, levels or type."""
    from .osm import colour, metres, number
    out = []
    for f in features:
        if f.kind != "area" or not (f.get("building") or f.get("building:part")):
            continue
        if f.get("building") in ("no", "construction", "ruins", "demolished") and not f.get("building:part"):
            continue
        if f.get("location") in ("underground",) or f.get("layer", "0").startswith("-"):
            continue
        kind = f.get("building") or f.get("building:part") or "yes"
        rings = [np.c_[frame_crs.forward(r[:, 0], r[:, 1])] for r in f.outer]
        holes = [np.c_[frame_crs.forward(r[:, 0], r[:, 1])] for r in f.inner]
        area = _ring_area(rings[0]) if rings else 0.0
        default = OSM_DEFAULTS.get(kind)
        if default is None:                       # "yes" and the rest: houses when house-sized, else flat blocks
            default = (6.0, "gabled", 3.5) if area < 250 else (8.0, "flat", 0.0)
        shape = (f.get("roof:shape") or default[1]).lower()
        roof_h = metres(f.get("roof:height"))
        if roof_h is None and number(f.get("roof:levels")) is not None:
            roof_h = number(f.get("roof:levels")) * 2.5
        if roof_h is None:
            roof_h = 0.0 if shape == "flat" else (default[2] if default[1] != "flat" else 3.0)
        height = metres(f.get("height"))
        if height is None:
            lv = number(f.get("building:levels"))
            height = (lv * LEVEL_M + roof_h) if lv is not None else default[0] + roof_h
        min_h = metres(f.get("min_height"))
        if min_h is not None and min_h > 0 and f.get("building:part"):
            height = max(height, min_h + 1.0)                    # 2.5D: a part on stilts stands on the ground
        roof_h = min(roof_h, 0.8 * height)
        out.append(Building(f.osm, rings, holes, height=height, roof_shape=shape, roof_height=roof_h,
                            wall_rgb=colour(f.get("building:colour")) if osm_colours else None,
                            roof_rgb=colour(f.get("roof:colour")) if osm_colours else None, function=kind,
                            part=bool(f.get("building:part")) and not f.get("building")))
    return drop_outlines_with_parts(out)


def drop_outlines_with_parts(buildings: List[Building]) -> List[Building]:
    """The OSM 3D rule: an outline that holds building:parts is shown by its parts alone (a church outline with a
    default height would otherwise hide its nave and stand up to its tower)."""
    from .footprint import point_in_polygon
    centres = np.array([np.vstack(b.grounds).mean(axis=0) for b in buildings if b.part and b.grounds])
    if not len(centres):
        return buildings
    keep = []
    for b in buildings:
        if not b.part and b.grounds and point_in_polygon(b.grounds[0], centres[:, 0], centres[:, 1]).any():
            continue
        keep.append(b)
    return keep


def _ring_area(r: np.ndarray) -> float:
    x, y = r[:, 0], r[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)
