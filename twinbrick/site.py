"""A place on earth as brick-ready input: GPS point + shape + size + orientation + scale -> one closed, coloured
heightfield mesh per tile (PLY, mm, whole studs wide), a preview, and site.json with the georeference.

    spec = SiteSpec(lat=51.4017, lon=8.0614, shape="hexagon", size=(150, 150), scale=750)
    site = Site(spec); tiles = site.build(out_dir)

The recipe is the one the brick models of 2026-11 found (Schlossruine and landmarks in Arnsberg, Burg Altena,
Wiesenkirche Soest, AR campus Siegen): terrain from a DGM, buildings from LoD2 roofs (or OSM footprints with heights),
trees from the normalised surface model (or OSM), ground colour from land use; the grid turned so the buildings run
along the studs; one closed surface with a colour per face so the engine's nearest-surface colouring sees no hidden
faces; extents in whole studs so `--scale 1` puts every cell where it was meant; structures smaller than a stud become
one stud column when tall or go when low; slivers thinner than about half a stud go.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

from lego_engine.palette import PALETTE

from . import net, osm as osm_mod, sources
from .crs import UTM, format_latlon
from .footprint import SHAPES, Footprint, Frame, Layout, dominant_angle, point_in_polygon
from .landcover import AREA_CLASSES, CLASSES, area_class, line_class, theme
from .mesh import heightfield_mesh, is_closed, tree_meshes

PITCH, PLATE = 8.0, 3.2                         # mm
RESULTS = Path.home() / "legolize_results"
LINE_ORDER = ("water", "rail", "path", "paved", "road")
OBJ_NONE, OBJ_BUILDING, OBJ_TREE, OBJ_BRIDGE = 0, 1, 2, 3
CI = {c: i for i, c in enumerate(CLASSES)}


@dataclass
class SiteSpec:
    lat: Optional[float] = None
    lon: Optional[float] = None
    shape: str = "hexagon"
    size: Tuple[float, float] = (150.0, 150.0)       # metres: side / diameter / width,height (see footprint)
    rotation: Optional[float] = 0.0                  # true bearing of the model's back edge; None = along buildings
    hex_orientation: str = "flat"
    triangle_up: bool = True
    corner: Optional[Tuple[float, float]] = None     # a corner (lat, lon): sets size and rotation of the shape
    points: Tuple[Tuple[float, float], ...] = ()     # shape "polygon": its corners (lat, lon)
    outline: Optional[str] = None                    # shape "polygon": an OSM way/relation ("way/146943431")
    margin: float = 0.0                              # polygon: ground around it, metres
    tiles: Tuple[Tuple[int, int], ...] = ((0, 0),)
    scale: Optional[float] = None                    # 1:N
    stud_m: Optional[float] = None                   # or: metres per stud
    studs: Optional[float] = None                    # or: studs across the shape (along the model's width)
    vertical: float = 1.0                            # height exaggeration
    base: int = 2                                    # plates under the lowest ground
    datum: Optional[float] = None                    # metres: the model's bottom (same for tiles built apart)
    water_drop: float = 1.0                          # plates the water lies below its banks
    terrain: str = "auto"                            # auto | nrw | terrarium | flat
    buildings: str = "auto"                          # auto | lod2 | osm | none
    trees: str = "auto"                              # auto | ndom | osm | none
    tree_style: str = "auto"                         # auto (lollipop up to 4 m per stud, else canopy) | lollipop |
                                                     # canopy | flat
    landcover: bool = True
    min_line: float = 0.35                           # roads, paths, rails, streams narrower than this share of a
                                                     # stud are left out; the others are at least one stud wide
    theme: str = "classic"
    colours: Dict[str, str] = field(default_factory=dict)
    osm_colours: bool = False
    forest_height: float = 15.0
    min_building: float = 2.0                        # metres above ground for a building to count
    sub: int = 0                                     # raster cells per stud (0: about 0.7 m, 3 .. 12)
    name: str = ""

    def scale_n(self) -> Optional[float]:
        if self.scale:
            return float(self.scale)
        if self.stud_m:
            return self.stud_m * 1000.0 / PITCH
        return None


@dataclass
class Tile:
    q: int
    r: int
    footprint: Footprint
    studs: np.ndarray                    # (ni, nj) bool
    origin: Tuple[int, int]              # global stud index of studs[0, 0]
    u0: float
    v0: float
    res: float
    E: np.ndarray = None
    N: np.ndarray = None
    region: np.ndarray = None
    terrain: np.ndarray = None
    ground: np.ndarray = None            # absolute m (water lowered)
    top: np.ndarray = None               # absolute m
    obj: np.ndarray = None               # OBJ_* per raster cell
    stud_class: np.ndarray = None        # (ni, nj) CLASSES index
    line_class: np.ndarray = None        # (ni, nj) CLASSES index or 255
    top_rgb: np.ndarray = None
    side_rgb: np.ndarray = None
    ground_rgb: np.ndarray = None
    counts: Dict[str, int] = field(default_factory=dict)
    files: Dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"tile_{self.q}_{self.r}".replace("-", "m")


class Site:
    def __init__(self, spec: SiteSpec, say: Callable[[str], None] = print):
        if spec.shape not in SHAPES:
            raise ValueError(f"shape must be one of {SHAPES}")
        self.spec, self.say = spec, say
        net.say = say
        self.palette = theme(spec.theme, spec.colours)
        self.rgb = {k: np.asarray(PALETTE[v], float) for k, v in self.palette.items()}
        self.attribution: List[str] = []
        self.source_names: Dict[str, str] = {}
        self.notes: List[str] = []
        self._outline_grid: Optional[np.ndarray] = None
        self._building_rgb: Dict[str, list] = {}
        self.outline_name = ""
        self._anchor()
        self._layout(self.bearing if self.bearing is not None else self.north_b)

    # ------------------------------------------------------------------ frame and layout
    def _anchor(self):
        s = self.spec
        pts = None
        if s.outline:
            f = osm_mod.fetch_outline(s.outline)
            ring = max(f.outer, key=len)
            pts = ring[:-1] if np.array_equal(ring[0], ring[-1]) else ring
            self.outline_name = f.get("name", s.outline)
            self.attribution.append(osm_mod.ATTRIBUTION)
        elif s.points:
            pts = np.asarray(s.points, float)
        if s.lat is None or s.lon is None:
            if pts is None:
                raise ValueError("give the place: LAT,LON (or the points of a polygon, or an OSM outline)")
            lat, lon = float(pts[:, 0].mean()), float(pts[:, 1].mean())
        else:
            lat, lon = float(s.lat), float(s.lon)
        self.crs = UTM(32) if sources.in_nrw(lat, lon) else UTM.for_point(lat, lon)
        e, n = self.crs.forward(lat, lon)
        if pts is not None:
            pe, pn = self.crs.forward(pts[:, 0], pts[:, 1])
            self._outline_grid = np.c_[pe, pn]
            if s.lat is None:                                   # the area centroid, not the mean of the corners
                x, y = pe, pn
                a = x * np.roll(y, -1) - np.roll(x, -1) * y
                if abs(a.sum()) > 1e-6:
                    e = float(((x + np.roll(x, -1)) * a).sum() / (3 * a.sum()))
                    n = float(((y + np.roll(y, -1)) * a).sum() / (3 * a.sum()))
                lat, lon = (float(v) for v in self.crs.inverse(e, n))
        self.lat, self.lon = lat, lon
        self.k = self.crs.scale_factor(lat, lon)
        self.north_b = self.crs.north_bearing(lat, lon)        # grid bearing of true north
        self.east, self.north = float(e), float(n)
        self.size = tuple(float(x) for x in s.size)
        self.bearing = None if s.rotation is None else s.rotation + self.north_b
        if s.corner is not None:
            ce, cn = self.crs.forward(*s.corner)
            de, dn = float(ce) - self.east, float(cn) - self.north
            R = math.hypot(de, dn) / self.k
            bc = math.degrees(math.atan2(de, dn))
            first = {"hexagon": 0.0 if s.hex_orientation == "flat" else 30.0, "triangle": 90.0 if s.triangle_up
                     else -90.0, "square": 45.0}.get(s.shape)
            if s.shape == "hexagon":
                self.size = (R, R)
            elif s.shape == "triangle":
                self.size = (R * math.sqrt(3),) * 2
            elif s.shape == "square":
                self.size = (R * math.sqrt(2),) * 2
            elif s.shape == "circle":
                self.size = (2 * R, 2 * R)
            elif s.shape in ("rectangle", "ellipse"):
                self._corner_rect = (de, dn)
            if first is not None:
                self.bearing = bc - 90.0 + first

    def _stud_m(self, width_hint: float) -> float:
        s = self.spec
        if s.scale:
            return PITCH * s.scale / 1000.0
        if s.stud_m:
            return float(s.stud_m)
        if s.studs:
            return width_hint / float(s.studs)
        raise ValueError("give the scale: --scale N (1:N), --stud-m M or --studs N")

    def _layout(self, bearing_grid: float):
        s = self.spec
        self.frame = Frame(self.crs, self.east, self.north, bearing_grid, self.k)
        size = self.size
        if getattr(self, "_corner_rect", None) is not None:
            u, v = self.frame.to_local(self.east + self._corner_rect[0], self.north + self._corner_rect[1])
            size = (2 * abs(float(u)), 2 * abs(float(v)))
        poly = None
        if s.shape == "polygon":
            if self._outline_grid is None:
                raise ValueError("a polygon needs --points or --osm")
            pu, pv = self.frame.to_local(self._outline_grid[:, 0], self._outline_grid[:, 1])
            poly = np.c_[pu, pv]
            size = tuple(np.ptp(poly, axis=0))
        width = {"hexagon": 2 * size[0], "triangle": size[0]}.get(s.shape, size[0])
        stud = self._stud_m(width + (2 * s.margin if s.shape == "polygon" else 0))
        self.layout = Layout(s.shape, (size[0], size[1] if len(size) > 1 else size[0]), stud, s.hex_orientation,
                             s.triangle_up, poly, s.margin)
        self.scale = self.layout.stud * 1000.0 / PITCH         # 1:N
        self.mm = 1000.0 / self.scale                         # model mm per ground metre (before exaggeration)
        self.plate_m = PLATE / (self.mm * s.vertical)          # ground metres per plate
        sub = s.sub or int(round(self.layout.stud / 0.7))
        n_studs = sum(self.layout.footprint(q, r).area() for q, r in s.tiles) / self.layout.stud ** 2
        cap = int(math.sqrt(3.0e6 / max(n_studs, 1.0)))
        self.sub = int(np.clip(sub, 2, max(2, min(12, cap))))

    def outline_latlon(self, q: int = 0, r: int = 0) -> np.ndarray:
        """The tile's outline as (n, 2) lat, lon - for maps."""
        o = self.layout.footprint(q, r).outline()
        la, lo = self.frame.to_latlon(o[:, 0], o[:, 1])
        return np.c_[la, lo]

    def stud_outline_latlon(self, q: int = 0, r: int = 0) -> List[np.ndarray]:
        """The outlines of the tile's studs as built (staircase edges), lat/lon rings."""
        fp = self.layout.footprint(q, r)
        studs, (i0, j0) = self.layout.stud_cells(fp)
        p = self.layout.stud
        rings = []
        for ring in _mask_outlines(studs):
            u = self.layout.offset[0] + (i0 + ring[:, 0]) * p
            v = self.layout.offset[1] + (j0 + ring[:, 1]) * p
            la, lo = self.frame.to_latlon(u, v)
            rings.append(np.c_[la, lo])
        return rings

    def plan(self) -> List[Dict]:
        """Every tile's studs and size, without any download."""
        out = []
        for q, r in self.spec.tiles:
            fp = self.layout.footprint(q, r)
            studs, origin = self.layout.stud_cells(fp)
            out.append(dict(tile=(q, r), studs=studs.shape, base_studs=int(studs.sum()), origin=origin,
                            size_cm=(studs.shape[0] * PITCH / 10, studs.shape[1] * PITCH / 10),
                            area_m2=fp.area()))
        return out

    def fetch_box(self) -> Tuple[float, float, float, float]:
        """Grid box (e0, n0, e1, n1) around every tile, independent of the rotation (auto rotation is decided
        from the data inside it)."""
        lay = self.layout
        R = lay.radius() + 3 * lay.stud
        reach = max(math.hypot(*lay.tile_centre(q, r)) for q, r in self.spec.tiles) + R
        d = reach * self.k
        return self.east - d, self.north - d, self.east + d, self.north + d

    def latlon_box(self, box) -> Tuple[float, float, float, float]:
        e0, n0, e1, n1 = box
        la, lo = self.crs.inverse(np.array([e0, e1, e0, e1]), np.array([n0, n0, n1, n1]))
        return float(la.min()), float(lo.min()), float(la.max()), float(lo.max())

    # ------------------------------------------------------------------ data
    def fetch(self):
        s, say = self.spec, self.say
        box = self.fetch_box()
        res = self.layout.stud / self.sub
        say(f"site {format_latlon(self.lat, self.lon)}, {self.crs.name}; area {(box[2] - box[0]) / 1000:.2f} km "
            f"square; {self.layout.describe()}; 1:{self.scale:.0f} (1 stud = {self.layout.stud:.2f} m)")
        nrw = sources.in_nrw(self.lat, self.lon) and self.crs.zone == 32

        # terrain: NRW's DGM1 where it covers the site, the worldwide tiles where it does not
        mode = s.terrain
        terrains, covered = [], False
        if mode == "flat":
            terrains = [sources.FlatTerrain(0.0)]
        elif mode in ("auto", "nrw") and nrw:
            try:
                idx = sources.NRWIndex("dgm1")
                covered = idx.covers(box)
                if covered or mode == "nrw" or any(t in idx.names for t in idx.tiles_for(box)):
                    say(f"terrain: Geobasis NRW DGM1 (1 m), {len(idx.tiles_for(box))} tile(s)")
                    terrains.append(sources.NRWTerrain(box))
                    self.attribution.append(sources.NRW_ATTRIBUTION)
            except (net.Offline, RuntimeError, FileNotFoundError) as ex:
                if mode == "nrw":
                    raise
                say(f"  NRW terrain not available ({ex}): worldwide tiles instead")
        elif mode == "nrw":
            raise ValueError("the NRW data (Geobasis NRW) cover North Rhine-Westphalia only")
        if mode != "flat" and (mode == "terrarium" or not covered):
            t = sources.TerrariumTerrain(self.crs, box, res)
            say(f"terrain: {t.name}" + (" (where NRW has no tile)" if terrains else ""))
            terrains.append(t)
            self.attribution.append(sources.TERRARIUM_ATTRIBUTION)
        self.terrain = terrains[0] if len(terrains) == 1 else sources.StackedTerrain(terrains)
        self.source_names["terrain"] = self.terrain.name

        # buildings
        bmode = s.buildings
        if bmode == "auto":
            bmode = "lod2" if nrw and self._nrw_covers("lod2", box) else "osm"
        self.building_mode = bmode
        # OpenStreetMap: land cover, roads, water, trees (and buildings outside NRW)
        self.features: List[osm_mod.Feature] = []
        if s.landcover or bmode == "osm" or s.trees in ("auto", "osm"):
            la0, lo0, la1, lo1 = self.latlon_box(box)
            say("OpenStreetMap: land use, roads, water" + (", buildings" if bmode == "osm" else "") + " ...")
            self.features = osm_mod.fetch_site(la0, lo0, la1, lo1, buildings=(bmode == "osm"))
            say(f"  {len(self.features)} features")
            if osm_mod.ATTRIBUTION not in self.attribution:
                self.attribution.append(osm_mod.ATTRIBUTION)
        if bmode == "lod2":
            self.buildings = sources.nrw_lod2(box)
            say(f"buildings: Geobasis NRW LoD2, {len(self.buildings)}")
            self.source_names["buildings"] = "NRW LoD2 (CityGML)"
            if sources.NRW_ATTRIBUTION not in self.attribution:
                self.attribution.append(sources.NRW_ATTRIBUTION)
        elif bmode == "osm":
            self.buildings = sources.osm_buildings(self.features, self.crs, s.osm_colours)
            say(f"buildings: OpenStreetMap, {len(self.buildings)} (heights from height / levels / type)")
            self.source_names["buildings"] = "OpenStreetMap footprints"
        else:
            self.buildings = []
            self.source_names["buildings"] = "none"

        # trees
        tmode = s.trees
        self.ndom = None
        if tmode in ("auto", "ndom") and nrw:
            try:
                idx = sources.NRWIndex("ndom50")
                size = idx.download_size(box)
                if idx.covers(box) and (size < 250e6 or tmode == "ndom"):
                    say(f"trees: Geobasis NRW nDOM50 (height above ground, 0.5 m), {len(idx.tiles_for(box))} "
                        f"tile(s), {size / 1e6:.0f} MB")
                    self.ndom = sources.GeoTiffTiles(idx, box)
                    tmode = "ndom"
                elif tmode == "auto":
                    say(f"  nDOM50 would be {size / 1e6:.0f} MB: trees from OpenStreetMap instead")
            except (net.Offline, RuntimeError, FileNotFoundError) as ex:
                if tmode == "ndom":
                    raise
                say(f"  nDOM50 not available ({ex})")
        if tmode == "auto":
            tmode = "osm"
        self.tree_mode = tmode
        self.tree_style = s.tree_style if s.tree_style != "auto" else \
            ("lollipop" if self.layout.stud <= 4.0 else "canopy")
        self.source_names["trees"] = {"ndom": "NRW nDOM50", "osm": "OpenStreetMap (woods, single trees)",
                                      "none": "none"}.get(tmode, tmode) + f", {self.tree_style}"

        # the orientation: along the buildings
        if self.bearing is None:
            polys = [g for b in self.buildings for g in b.grounds
                     if math.hypot(*(g.mean(axis=0) - (self.east, self.north))) < self.layout.radius() * self.k]
            alpha = dominant_angle(polys) if polys else 0.0
            self.bearing = -alpha
            say(f"orientation: along the buildings, {len(polys)} outlines -> the model's back faces "
                f"{(self.bearing - self.north_b) % 360:.1f} deg (true)" if polys else
                "orientation: no buildings - north up")
            if not polys:
                self.bearing = self.north_b
        self._layout(self.bearing)
        for note in self.layout.notes:
            say("  " + note)
        self._localise()

    def _nrw_covers(self, product: str, box) -> bool:
        try:
            return sources.NRWIndex(product).covers(box)
        except (net.Offline, RuntimeError, FileNotFoundError):
            return False

    def _localise(self):
        """Features and buildings in local metres of the final frame."""
        fr = self.frame
        self.local = []
        R = self.layout.radius() + max(math.hypot(*self.layout.tile_centre(q, r)) for q, r in self.spec.tiles)
        for f in self.features:
            conv = lambda a: np.c_[fr.from_latlon(a[:, 0], a[:, 1])]
            if f.kind == "area":
                outer = [conv(r) for r in f.outer]
                lo = np.min([o.min(axis=0) for o in outer], axis=0)
                hi = np.max([o.max(axis=0) for o in outer], axis=0)
                if (lo > R + 50).any() or (hi < -R - 50).any():
                    continue
                self.local.append((f, outer, [conv(r) for r in f.inner]))
            elif f.kind == "line":
                self.local.append((f, conv(f.line), None))
            else:
                u, v = fr.from_latlon(*f.point)
                self.local.append((f, np.array([[float(u), float(v)]]), None))
        self.local_buildings = []
        for b in self.buildings:
            g = [np.c_[fr.to_local(x[:, 0], x[:, 1])] for x in b.grounds]
            h = [np.c_[fr.to_local(x[:, 0], x[:, 1])] for x in b.holes]
            roofs = [np.c_[np.c_[fr.to_local(x[:, 0], x[:, 1])], x[:, 2]] for x in b.roofs]
            self.local_buildings.append((b, g, h, roofs))

    # ------------------------------------------------------------------ one tile
    def make_tile(self, q: int, r: int) -> Tile:
        lay, S = self.layout, self.sub
        fp = lay.footprint(q, r)
        studs, (i0, j0) = lay.stud_cells(fp)
        p = lay.stud
        t = Tile(q, r, fp, studs, (i0, j0), lay.offset[0] + i0 * p, lay.offset[1] + j0 * p, p / S)
        ni, nj = studs.shape
        nu, nv = ni * S, nj * S
        uu = t.u0 + (np.arange(nu) + 0.5) * t.res
        vv = t.v0 + (np.arange(nv) + 0.5) * t.res
        U, V = np.meshgrid(uu, vv, indexing="ij")
        t.E, t.N = self.frame.to_grid(U, V)
        t.region = np.repeat(np.repeat(studs, S, 0), S, 1)
        terr = self.terrain.height(t.E, t.N)
        if np.isnan(terr).all():
            raise RuntimeError("no terrain data for this place")
        if np.isnan(terr).any():
            _, (ii, jj) = ndi.distance_transform_edt(np.isnan(terr), return_indices=True)
            terr = terr[ii, jj]
        t.terrain = terr
        self._classes(t)
        self._water(t)
        self._buildings(t)
        self._trees(t)
        self._bridges(t)
        self._colours(t)
        return t

    # canvas helpers -----------------------------------------------------
    def _canvas(self, t: Tile, stud: bool, fill: int = 0):
        ni, nj = t.studs.shape
        size = (ni, nj) if stud else (ni * self.sub, nj * self.sub)
        img = Image.new("L", size, fill)
        cell = self.layout.stud if stud else t.res

        def xy(a):
            a = np.asarray(a, float)
            return [((x - t.u0) / cell - 0.5, (y - t.v0) / cell - 0.5) for x, y in a[:, :2]]
        return img, ImageDraw.Draw(img), xy, cell

    def _in_tile(self, t: Tile, a, pad: float = 0.0) -> bool:
        lo, hi = np.min(a[:, :2], axis=0), np.max(a[:, :2], axis=0)
        ni, nj = t.studs.shape
        U1, V1 = t.u0 + ni * self.layout.stud, t.v0 + nj * self.layout.stud
        return not (hi[0] < t.u0 - pad or lo[0] > U1 + pad or hi[1] < t.v0 - pad or lo[1] > V1 + pad)

    def _window(self, t: Tile, rings, holes=()):
        """(slice_u, slice_v, mask) of the raster cells inside the rings (minus holes), or None."""
        allp = np.vstack([r[:, :2] for r in rings])
        a0 = max(0, int(math.floor((allp[:, 0].min() - t.u0) / t.res)) - 1)
        b0 = max(0, int(math.floor((allp[:, 1].min() - t.v0) / t.res)) - 1)
        a1 = min(t.region.shape[0], int(math.ceil((allp[:, 0].max() - t.u0) / t.res)) + 2)
        b1 = min(t.region.shape[1], int(math.ceil((allp[:, 1].max() - t.v0) / t.res)) + 2)
        if a1 <= a0 or b1 <= b0:
            return None
        img = Image.new("L", (a1 - a0, b1 - b0), 0)
        d = ImageDraw.Draw(img)
        conv = lambda r: [((x - t.u0) / t.res - 0.5 - a0, (y - t.v0) / t.res - 0.5 - b0) for x, y in r[:, :2]]
        for r in rings:
            if len(r) >= 3:
                d.polygon(conv(r), fill=1)
        for r in holes:
            if len(r) >= 3:
                d.polygon(conv(r), fill=0)
        m = np.asarray(img, bool).T
        if not m.any():
            return None
        return slice(a0, a1), slice(b0, b1), m

    # land cover ---------------------------------------------------------
    def _classes(self, t: Tile):
        S, ni, nj = self.sub, *t.studs.shape
        img, d, xy, _ = self._canvas(t, stud=False, fill=CI["ground"])
        drawn = 0
        if self.spec.landcover:
            for cls in AREA_CLASSES:
                for f, outer, inner in self.local:
                    if f.kind != "area" or area_class(f.tags) != cls or not self._in_tile(t, np.vstack(outer)):
                        continue
                    for ring in outer:
                        if len(ring) >= 3:
                            d.polygon(xy(ring), fill=CI[cls])
                    for ring in inner:
                        if len(ring) >= 3:
                            d.polygon(xy(ring), fill=CI["ground"])
                    drawn += 1
        cls_r = np.asarray(img).T
        # the class covering most of each stud (water counts a little more: it is flat and says "river")
        weights = {"water": 1.25, "paved": 1.1}
        best = np.zeros((ni, nj))
        stud_class = np.full((ni, nj), CI["ground"], np.uint8)
        for c in AREA_CLASSES:
            cover = (cls_r == CI[c]).reshape(ni, S, nj, S).mean(axis=(1, 3)) * weights.get(c, 1.0)
            better = cover > best
            stud_class[better], best[better] = CI[c], cover[better]
        # one-stud specks of a class take their neighbours' class
        stud_class = _despeckle(stud_class, t.studs)
        # lines, straight onto the studs: at least one stud wide
        limg, ld, lxy, cell = self._canvas(t, stud=True, fill=255)
        bimg, bd, bxy, _ = self._canvas(t, stud=True, fill=0)
        t.bridge_lines = []
        lines = 0
        if self.spec.landcover:
            items = []
            for f, a, _ in self.local:
                if f.kind != "line" or len(a) < 2:
                    continue
                lc = line_class(f.tags)
                if lc is None or not self._in_tile(t, a, 30):
                    continue
                c, w, bridge = lc
                if w < self.spec.min_line * self.layout.stud:
                    continue
                items.append((LINE_ORDER.index(c) + (10 if bridge else 0), c, w, bridge, a, f))
            for _, c, w, bridge, a, f in sorted(items, key=lambda x: x[0]):
                width = max(1, int(round(w / cell)))
                ld.line(lxy(a), fill=CI[c], width=width, joint="curve" if width > 2 else None)
                if bridge:
                    bd.line(bxy(a), fill=1, width=width)
                    t.bridge_lines.append((a, c))
                lines += 1
        line_cls = np.asarray(limg).T.copy()
        line_cls[~t.studs] = 255
        t.line_class = line_cls
        t.bridge_studs = np.asarray(bimg, bool).T & t.studs
        stud_class = np.where(line_cls != 255, line_cls, stud_class).astype(np.uint8)
        t.stud_class = stud_class
        t.counts.update(areas=drawn, lines=lines)

    def _water(self, t: Tile):
        S = self.sub
        ni, nj = t.studs.shape
        water = (t.stud_class == CI["water"]) & t.studs & ~t.bridge_studs
        gmin = t.terrain.reshape(ni, S, nj, S).min(axis=(1, 3))
        level = ndi.minimum_filter(np.where(t.studs, gmin, np.inf), size=3, mode="nearest")
        level = level - self.spec.water_drop * self.plate_m
        g = t.terrain.copy()
        wr = np.repeat(np.repeat(water, S, 0), S, 1)
        lr = np.repeat(np.repeat(level, S, 0), S, 1)
        g[wr] = np.minimum(g[wr], lr[wr])
        t.ground = g
        t.water = water
        t.top = g.copy()
        t.obj = np.zeros(t.region.shape, np.uint8)
        t.counts["water_studs"] = int(water.sum())

    # buildings ----------------------------------------------------------
    def _buildings(self, t: Tile):
        if not self.local_buildings:
            t.counts["buildings"] = 0
            return
        roof = np.full(t.region.shape, -np.inf)
        foot = np.zeros(t.region.shape, bool)
        n = 0
        for b, grounds, holes, roofs in self.local_buildings:
            if not grounds or not self._in_tile(t, np.vstack(grounds), 5):
                continue
            w = self._window(t, grounds, holes)
            if w is None:
                continue
            su, sv, m = w
            n += 1
            foot[su, sv] |= m
            if roofs:                                                       # LoD2: every roof surface's plane
                for rp in roofs:
                    if len(rp) < 3:
                        continue
                    rw = self._window(t, [rp])
                    if rw is None:
                        continue
                    ru, rv, rm = rw
                    c = rp.mean(axis=0)
                    _, _, vt = np.linalg.svd(rp - c)
                    nx, ny, nz = vt[-1]
                    if abs(nz) < 0.2:                                       # (nearly) vertical: no height
                        continue
                    uu = t.u0 + (np.arange(ru.start, ru.stop) + 0.5) * t.res
                    vv = t.v0 + (np.arange(rv.start, rv.stop) + 0.5) * t.res
                    UU, VV = np.meshgrid(uu, vv, indexing="ij")
                    z = np.clip(c[2] - (nx * (UU - c[0]) + ny * (VV - c[1])) / nz, rp[:, 2].min(), rp[:, 2].max())
                    blk = roof[ru, rv]
                    blk[rm] = np.maximum(blk[rm], z[rm])
            else:                                                           # OSM: walls and a roof shape
                zb = float(np.nanmin(t.terrain[su, sv][m]))
                top = zb + b.height
                eave = top - b.roof_height
                shape = b.roof_shape
                if b.roof_height <= 0 or shape in ("flat", "skillion"):
                    z = np.full(m.shape, top if shape == "flat" else eave + b.roof_height / 2)
                else:
                    dist = ndi.distance_transform_edt(np.pad(m, 1))[1:-1, 1:-1] * t.res
                    s = np.clip(dist / max(dist.max(), 1e-9), 0, 1)
                    if shape in ("dome", "onion", "round"):
                        s = np.sqrt(np.clip(1 - (1 - s) ** 2, 0, 1))
                    z = eave + b.roof_height * s
                blk = roof[su, sv]
                blk[m] = np.maximum(blk[m], z[m])
                if b.roof_rgb is not None or b.wall_rgb is not None:
                    self._building_rgb.setdefault(t.label, []).append((su, sv, m, b.roof_rgb, b.wall_rgb))
        has = np.isfinite(roof)
        if (foot & ~has).any() and has.any():
            _, (ii, jj) = ndi.distance_transform_edt(~has, return_indices=True)
            fill = foot & ~has
            roof[fill] = roof[ii[fill], jj[fill]]
        bld = (foot | has) & (roof > t.terrain + self.spec.min_building) & t.region
        bld = ndi.binary_opening(bld, structure=np.ones((3, 3)))            # no slivers thinner than ~3 cells
        bld, roof = self._small_objects(t, bld, roof, min_studs=1.5, what="building")
        t.top = np.where(bld, np.maximum(roof, t.ground), t.top)
        t.obj[bld] = OBJ_BUILDING
        t.counts["buildings"] = n

    def _small_objects(self, t: Tile, mask, top, min_studs: float, what: str):
        """Objects smaller than `min_studs` studs: a tall one becomes one whole stud column, a low one goes."""
        S = self.sub
        lab, n = ndi.label(mask)
        columns = 0
        for k, sl in enumerate(ndi.find_objects(lab), 1):
            if sl is None:
                continue
            cells = lab[sl] == k
            if cells.sum() >= min_studs * S * S:
                continue
            hmax = float(np.max(top[sl][cells]))
            gmin = float(np.min(t.ground[sl][cells]))
            mask[sl][cells] = False
            if hmax - gmin >= 3 * self.plate_m:
                ii, jj = np.nonzero(cells)
                ci, cj = int(np.mean(ii)) + sl[0].start, int(np.mean(jj)) + sl[1].start
                blk = (slice(ci // S * S, ci // S * S + S), slice(cj // S * S, cj // S * S + S))
                if t.region[blk].all() and not (t.obj[blk] != OBJ_NONE).any():
                    mask[blk] = True
                    top[blk] = np.maximum(np.where(np.isfinite(top[blk]), top[blk], -np.inf), hmax)
                    columns += 1
        if columns:
            t.counts[f"{what}_columns"] = t.counts.get(f"{what}_columns", 0) + columns
        return mask, top

    # trees ----------------------------------------------------------------
    def _trees(self, t: Tile):
        """Trees from the normalised surface model (NRW) or OpenStreetMap, in one of three styles: "canopy" (the
        crowns' true surface on the heightfield), "lollipop" (one stylised tree per crown: a 1x1 trunk column under
        an ellipsoid crown - separate bodies added to the mesh, kept inside the tile) or "flat" (none)."""
        S = self.sub
        t.trees = []
        style = self.tree_style
        if self.tree_mode == "none" or style == "flat":
            t.counts["trees"] = 0
            return
        free = t.region & (t.obj == OBJ_NONE)
        free &= ~ndi.binary_dilation(t.obj == OBJ_BUILDING, iterations=max(1, int(round(2.0 / t.res))))
        wr = np.repeat(np.repeat(t.water | t.bridge_studs, S, 0), S, 1)
        free &= ~wr
        rng = np.random.default_rng(abs(hash((round(self.lat, 5), round(self.lon, 5), t.q, t.r))) % (2 ** 32))
        chm = np.zeros(t.region.shape)                        # height above ground, m
        points = []                                           # OSM single trees: (u, v, height, crown radius)
        forest = np.zeros(t.region.shape, bool)
        if self.tree_mode == "ndom" and self.ndom is not None:
            h = self.ndom.sample(t.E, t.N)
            if t.res > 1.0:                                     # coarse raster: the crowns' tops, not the gaps
                h = np.fmax(h, self.ndom.max_in_cells(t.E, t.N, t.res / 2))
            h = np.nan_to_num(h, nan=0.0)
            m = (h >= 2.5) & free
            m = ndi.binary_opening(m, structure=np.ones((2, 2)))
            chm = np.where(m, h, 0.0)
        elif self.tree_mode == "osm":
            img, d, xy, _ = self._canvas(t, stud=False, fill=0)
            for f, outer, inner in self.local:
                if f.kind == "area" and area_class(f.tags) == "forest" and self._in_tile(t, np.vstack(outer)):
                    for ring in outer:
                        if len(ring) >= 3:
                            d.polygon(xy(ring), fill=1)
                    for ring in inner:
                        if len(ring) >= 3:
                            d.polygon(xy(ring), fill=0)
            forest = np.asarray(img, bool).T & free
            noise = ndi.gaussian_filter(rng.random(forest.shape), sigma=max(1.0, 0.8 * S))
            noise = (noise - noise.min()) / max(np.ptp(noise), 1e-9)
            chm = np.where(forest, self.spec.forest_height * (0.75 + 0.5 * noise), 0.0)
            for f, a, _ in self.local:
                pts = None
                if f.kind == "point" and f.get("natural") == "tree":
                    pts = a
                elif f.kind == "line" and f.get("natural") == "tree_row" and len(a) >= 2:
                    seg = np.linalg.norm(np.diff(a, axis=0), axis=1)
                    sc = np.r_[0, np.cumsum(seg)]
                    st = np.arange(0, sc[-1] + 1e-9, 8.0)
                    pts = np.c_[np.interp(st, sc, a[:, 0]), np.interp(st, sc, a[:, 1])]
                if pts is None or not self._in_tile(t, pts, 10):
                    continue
                crown = osm_mod.metres(f.get("diameter_crown")) or 6.0
                height = osm_mod.metres(f.get("height")) or 10.0
                points += [(x, y, height, crown / 2) for x, y in pts]
        if style == "lollipop":
            self._lollipops(t, chm, forest, points, free, rng)
            return
        # canopy: the surface itself
        canopy = np.where(chm > 0, t.terrain + chm, -np.inf)
        if points:
            img2, d2, xy2, _ = self._canvas(t, stud=False, fill=0)
            himg = np.zeros(t.region.shape)
            for x, y, height, r_m in points:
                (cx, cy), = xy2([(x, y)])
                r = r_m / t.res
                d2.ellipse([cx - r, cy - r, cx + r, cy + r], fill=1)
                a0, a1 = int(max(0, cx - r - 1)), int(min(himg.shape[0], cx + r + 2))
                b0, b1 = int(max(0, cy - r - 1)), int(min(himg.shape[1], cy + r + 2))
                himg[a0:a1, b0:b1] = np.maximum(himg[a0:a1, b0:b1], height)
            single = np.asarray(img2, bool).T & free
            canopy = np.where(single, np.fmax(canopy, t.terrain + himg), canopy)
        m = np.isfinite(canopy) & (canopy > t.ground + 2.0)
        if not m.any():
            t.counts["trees"] = 0
            return
        m, canopy = self._small_objects(t, m, canopy, min_studs=1.0, what="tree")
        m &= t.obj == OBJ_NONE
        t.top = np.where(m, np.maximum(canopy, t.ground), t.top)
        t.obj[m] = OBJ_TREE
        t.counts["tree_cells"] = int(m.sum())

    def _lollipops(self, t: Tile, chm, forest, points, free, rng):
        """Stylised trees: the crowns' tops (local maxima of the canopy height, or OSM's trees, or a loose grid in OSM
        woods), one trunk per stud, at least two studs apart, crowns shrunk to stay inside the tile."""
        S, p = self.sub, self.layout.stud
        cand = []                                                       # (u, v, height)
        if chm.any() and not forest.any():                              # measured canopy: its local maxima
            sm = ndi.gaussian_filter(chm, sigma=max(0.5, 1.0 / t.res))
            size = max(3, int(round(2 * max(3.0, 0.8 * p) / t.res)) | 1)
            peaks = (sm == ndi.maximum_filter(sm, size=size)) & (sm >= 4.0) & free
            ii, jj = np.nonzero(peaks)
            cand += [(t.u0 + (i + 0.5) * t.res, t.v0 + (j + 0.5) * t.res, float(chm[i, j])) for i, j in zip(ii, jj)]
        if forest.any():                                                # OSM woods: a loose, jittered grid
            step = max(2 * p, 9.0)
            ni, nj = t.region.shape
            for gu in np.arange(t.u0 + step / 2, t.u0 + ni * t.res, step):
                for gv in np.arange(t.v0 + step / 2, t.v0 + nj * t.res, step):
                    u, v = gu + rng.uniform(-0.3, 0.3) * step, gv + rng.uniform(-0.3, 0.3) * step
                    i, j = int((u - t.u0) / t.res), int((v - t.v0) / t.res)
                    if 0 <= i < ni and 0 <= j < nj and forest[i, j]:
                        cand.append((u, v, float(chm[i, j])))
        cand += [(x, y, h) for x, y, h, _ in points]
        if not cand:
            t.counts["trees"] = 0
            return
        studs_free = free.reshape(*t.studs.shape[0:1], S, t.studs.shape[1], S).all(axis=(1, 3)) & t.studs
        inside = ndi.distance_transform_edt(np.pad(t.studs, 1))[1:-1, 1:-1]   # studs to the nearest stud outside
        taken = np.zeros(t.studs.shape, bool)
        gap = 2
        # the engine fills a stud of terrain up to about its mean height: trees stand on that (and their trunk
        # reaches a plate below it, so no plate stays empty between ground and trunk on a slope)
        ground_stud = t.ground.reshape(t.studs.shape[0], S, t.studs.shape[1], S).mean(axis=(1, 3))
        for u, v, h in sorted(cand, key=lambda c: -c[2]):
            i, j = int((u - t.u0) // p), int((v - t.v0) // p)
            if not (0 <= i < taken.shape[0] and 0 <= j < taken.shape[1]) or not studs_free[i, j]:
                continue
            if taken[max(0, i - gap + 1):i + gap, max(0, j - gap + 1):j + gap].any():
                continue
            r = float(np.clip(0.22 * h + 1.0, 2.0, 7.0))
            r = max(0.75 * p, min(r, (inside[i, j] - 0.5) * p))           # the crown stays over the tile's studs
            if r > (inside[i, j] - 0.5) * p + 1e-9:                        # no room for a crown of 3/4 stud: none
                continue
            taken[i, j] = True
            g = float(ground_stud[i, j])
            t.trees.append(dict(i=i, j=j, ground=g, bottom=g + 0.4 * h, top=g + h, r=r,
                                colour="tree" if rng.random() < 0.6 else "tree2"))
        t.counts["trees"] = len(t.trees)

    # bridges ----------------------------------------------------------------
    def _bridges(self, t: Tile):
        """A bridge is a deck between the heights of its two ends (2.5D: it stands on the ground below)."""
        if not t.bridge_studs.any():
            return
        S = self.sub
        deck = np.full(t.studs.shape, -np.inf)
        for a, c in t.bridge_lines:
            e, n = self.frame.to_grid(a[[0, -1], 0], a[[0, -1], 1])
            z = self.terrain.height(np.asarray(e), np.asarray(n))
            if np.isnan(z).any():
                continue
            img, d, xy, cell = self._canvas(t, stud=True, fill=0)
            d.line(xy(a), fill=1, width=max(1, int(round(4.0 / cell))))
            m = np.asarray(img, bool).T & t.bridge_studs
            if not m.any():
                continue
            # the height along the bridge: from one end's ground to the other's
            ii, jj = np.nonzero(m)
            pu = t.u0 + (ii + 0.5) * self.layout.stud
            pv = t.v0 + (jj + 0.5) * self.layout.stud
            ab = a[-1, :2] - a[0, :2]
            s = np.clip(((pu - a[0, 0]) * ab[0] + (pv - a[0, 1]) * ab[1]) / max(ab @ ab, 1e-9), 0, 1)
            deck[ii, jj] = np.maximum(deck[ii, jj], z[0] + (z[1] - z[0]) * s)
        br = np.isfinite(deck)
        dr = np.repeat(np.repeat(deck, S, 0), S, 1)
        brr = np.repeat(np.repeat(br, S, 0), S, 1) & t.region & (t.obj == OBJ_NONE) & (dr > t.ground + self.plate_m)
        t.top = np.where(brr, dr, t.top)
        t.obj[brr] = OBJ_BRIDGE
        t.counts["bridge_studs"] = int(br.sum())

    # colours ----------------------------------------------------------------
    def _colours(self, t: Tile):
        S = self.sub
        names = np.array(CLASSES)
        pal = np.array([self.rgb[c] for c in CLASSES])
        sc = np.repeat(np.repeat(t.stud_class, S, 0), S, 1)
        ground_rgb = pal[sc]
        top_rgb = ground_rgb.copy()
        side_rgb = ground_rgb.copy()
        b, tr, br = t.obj == OBJ_BUILDING, t.obj == OBJ_TREE, t.obj == OBJ_BRIDGE
        top_rgb[b], side_rgb[b] = self.rgb["roof"], self.rgb["wall"]
        top_rgb[tr], side_rgb[tr] = self.rgb["tree"], self.rgb["tree"]
        lc = np.repeat(np.repeat(t.line_class, S, 0), S, 1)
        top_rgb[br] = pal[np.where(lc[br] == 255, CI["road"], lc[br])]
        side_rgb[br] = self.rgb["bridge"]
        for su, sv, m, roof_rgb, wall_rgb in self._building_rgb.get(t.label, []):
            mm = m & b[su, sv]
            if roof_rgb is not None:
                top_rgb[su, sv][mm] = roof_rgb
            if wall_rgb is not None:
                side_rgb[su, sv][mm] = wall_rgb
        t.top_rgb, t.side_rgb, t.ground_rgb = top_rgb, side_rgb, ground_rgb
        used = {names[i] for i in np.unique(t.stud_class[t.studs])}
        if b.any():
            used |= {"roof", "wall"}
        if tr.any():
            used.add("tree")
        for x in getattr(t, "trees", []):
            used |= {x["colour"], "trunk"}
        if br.any():
            used.add("bridge")
        t.counts["colours"] = sorted({self.palette[c] for c in used} | {self.palette["base"]})

    # ------------------------------------------------------------------ the whole site
    def build(self, out: Path, mesh: bool = True) -> List[Tile]:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        self._building_rgb = {}
        if not hasattr(self, "terrain"):
            self.fetch()
        say = self.say
        tiles = []
        for q, r in self.spec.tiles:
            t0 = time.time()
            t = self.make_tile(q, r)
            tiles.append(t)
            ni, nj = t.studs.shape
            trees = (f"{t.counts.get('trees', 0)} trees" if self.tree_style == "lollipop" else
                     f"{t.counts.get('tree_cells', 0) / self.sub ** 2:.0f} studs of trees")
            say(f"tile {q},{r}: {ni} x {nj} studs ({int(t.studs.sum())} on the base), "
                f"{t.counts.get('buildings', 0)} buildings, {trees}, {t.counts.get('water_studs', 0)} studs of "
                f"water  [{time.time() - t0:.1f} s]")
        # one datum for all tiles, so neighbours meet at the same heights
        zlow = min(float(np.min(t.ground[t.region])) for t in tiles)
        if self.spec.datum is not None:
            self.datum = float(self.spec.datum)
            if self.datum > zlow - self.plate_m:
                self.notes.append(f"the datum {self.datum:.1f} m lies above the lowest ground ({zlow:.1f} m): ground "
                                  f"below it is raised to one plate")
        else:
            self.datum = math.floor((zlow - self.spec.base * self.plate_m) * 10) / 10
        zmm = lambda z: np.maximum((z - self.datum) * self.mm * self.spec.vertical, PLATE)
        for t in tiles:
            top, ground = zmm(t.top), zmm(t.ground)
            ground = np.minimum(ground, top)
            t.height_plates = float(max([top[t.region].max()] + [zmm(np.array(tr["top"])) for tr in t.trees]) / PLATE)
            stem = out / t.label if len(tiles) > 1 else out / "site"
            if mesh:
                m = heightfield_mesh(t.region, top, ground, t.obj != OBJ_NONE, t.top_rgb, t.side_rgb, t.ground_rgb,
                                     self.rgb["base"], t.res * self.mm)
                if t.trees:
                    import trimesh
                    m = trimesh.util.concatenate([m] + tree_meshes(
                        t.trees, lambda z: float(zmm(np.array(z))), PITCH, PLATE, self.mm, lambda c: self.rgb[c]))
                ply = stem.with_suffix(".ply")
                m.export(ply)
                t.files["mesh"] = str(ply)
                ext = m.bounds[1] - m.bounds[0]
                say(f"  {ply.name}: {len(m.faces)} faces, {'closed' if is_closed(m) else 'NOT closed'}, "
                    f"{ext[0] / PITCH:.2f} x {ext[1] / PITCH:.2f} studs x {ext[2] / PLATE:.1f} plates "
                    f"({ext[0] / 10:.1f} x {ext[1] / 10:.1f} x {ext[2] / 10:.1f} cm)")
            png = stem.parent / (stem.name + "_plan.png")
            self.preview(t, top).save(png)
            t.files["plan"] = str(png)
            np.savez_compressed(stem.parent / (stem.name + "_raster.npz"), top_mm=top.astype(np.float32),
                                ground_mm=ground.astype(np.float32), region=t.region, obj=t.obj,
                                stud_class=t.stud_class, studs=t.studs, res_mm=t.res * self.mm,
                                origin=np.array(t.origin))
        if len(tiles) > 1:
            self.overview(tiles).save(out / "overview_plan.png")
        self.write_json(out, tiles)
        for n_ in self.notes:
            say("note: " + n_)
        return tiles

    # ------------------------------------------------------------------ pictures and the record
    def preview(self, t: Tile, top_mm: np.ndarray, px: int = 0) -> Image.Image:
        """Top view: the face colours, shaded by the slope, studs' grid faint; back of the model at the top."""
        cell = t.res * self.mm
        gx, gy = np.gradient(top_mm, cell)
        n = np.stack([-gx, -gy, np.ones_like(gx)], -1)
        n /= np.linalg.norm(n, axis=-1, keepdims=True)
        light = np.array([-0.5, 0.6, 0.8])
        light /= np.linalg.norm(light)
        shade = np.clip(0.55 + 0.45 * (n @ light), 0.3, 1.1)
        img = np.clip(t.top_rgb * shade[..., None], 0, 1)
        img[~t.region] = 1.0
        S = self.sub
        grid = np.zeros(t.region.shape, bool)
        grid[::S, :] = True
        grid[:, ::S] = True
        img[grid & t.region] *= 0.88
        if t.trees:                                    # stylised trees: their crowns, lit from the same side
            U, V = np.meshgrid(np.arange(img.shape[0]) + 0.5, np.arange(img.shape[1]) + 0.5, indexing="ij")
            for tr in sorted(t.trees, key=lambda x: x["top"]):
                cu, cv, rr = (tr["i"] + 0.5) * S, (tr["j"] + 0.5) * S, tr["r"] / t.res
                a0, a1 = int(max(0, cu - rr - 1)), int(min(img.shape[0], cu + rr + 2))
                b0, b1 = int(max(0, cv - rr - 1)), int(min(img.shape[1], cv + rr + 2))
                du, dv = (U[a0:a1, b0:b1] - cu) / rr, (V[a0:a1, b0:b1] - cv) / rr
                d2 = du ** 2 + dv ** 2
                disc = d2 <= 1
                nz = np.sqrt(np.clip(1 - d2, 0, 1))
                lit = np.clip(0.55 + 0.45 * (-0.5 * du + 0.6 * dv + 0.8 * nz) / 1.1, 0.35, 1.1)
                blk = img[a0:a1, b0:b1]
                blk[disc] = self.rgb[tr["colour"]] * lit[disc][:, None]
        a = (np.transpose(img, (1, 0, 2))[::-1] * 255).astype(np.uint8)
        im = Image.fromarray(a)
        scale = px or max(1, int(round(900 / max(im.size))))
        return im.resize((im.size[0] * scale, im.size[1] * scale), Image.NEAREST)

    def overview(self, tiles: Sequence[Tile]) -> Image.Image:
        """All tiles in place: shaded like the plans, the seams between them drawn faintly."""
        S = self.sub
        i0 = min(t.origin[0] for t in tiles)
        j0 = min(t.origin[1] for t in tiles)
        i1 = max(t.origin[0] + t.studs.shape[0] for t in tiles)
        j1 = max(t.origin[1] + t.studs.shape[1] for t in tiles)
        canvas = np.ones(((i1 - i0) * S, (j1 - j0) * S, 3))
        light = np.array([-0.5, 0.6, 0.8]) / np.linalg.norm([-0.5, 0.6, 0.8])
        for t in tiles:
            a, b = (t.origin[0] - i0) * S, (t.origin[1] - j0) * S
            gx, gy = np.gradient(t.top * self.spec.vertical, t.res)
            n = np.stack([-gx, -gy, np.ones_like(gx)], -1)
            n /= np.linalg.norm(n, axis=-1, keepdims=True)
            shade = np.clip(0.55 + 0.45 * (n @ light), 0.3, 1.1)
            img = np.clip(t.top_rgb * shade[..., None], 0, 1)
            edge = t.region & ~ndi.binary_erosion(t.region, iterations=max(1, S // 4))
            img[edge] *= 0.6
            blk = canvas[a:a + t.region.shape[0], b:b + t.region.shape[1]]
            blk[t.region] = img[t.region]
        im = Image.fromarray((np.transpose(canvas, (1, 0, 2))[::-1] * 255).astype(np.uint8))
        scale = max(1, int(round(1000 / max(im.size))))
        return im.resize((im.size[0] * scale, im.size[1] * scale), Image.NEAREST)

    def write_json(self, out: Path, tiles: Sequence[Tile]):
        s = self.spec
        d = dict(
            name=s.name, made=time.strftime("%Y-%m-%d %H:%M"),
            place=dict(lat=self.lat, lon=self.lon, text=format_latlon(self.lat, self.lon)),
            crs=dict(name=self.crs.name, epsg=25832 if self.crs.zone == 32 and sources.in_nrw(self.lat, self.lon)
                     else self.crs.epsg, anchor=[self.east, self.north], scale_factor=self.k),
            orientation=dict(back_true_bearing=(self.frame.bearing - self.north_b) % 360,
                             back_grid_bearing=self.frame.bearing % 360,
                             note="the model's back edge (+y) faces this bearing; +x is 90 degrees clockwise"),
            shape=self.layout.describe(), shape_spec=dict(shape=s.shape, size=list(self.layout.size),
                                                          hex=s.hex_orientation, margin=s.margin),
            scale=dict(ratio=f"1:{self.scale:.0f}", stud_m=self.layout.stud, plate_m=self.plate_m,
                       vertical=s.vertical, datum_m=self.datum, mm_per_m=self.mm * s.vertical),
            grid=dict(offset_m=list(self.layout.offset), raster_per_stud=self.sub),
            tiles=[dict(tile=[t.q, t.r], studs=list(t.studs.shape), base_studs=int(t.studs.sum()),
                        origin_stud=list(t.origin),
                        size_cm=[t.studs.shape[0] * 0.8, t.studs.shape[1] * 0.8],
                        height_plates=round(getattr(t, "height_plates", 0.0), 1), counts=t.counts, files=t.files,
                        neighbours=[list(x) for x in self.layout.neighbours(t.q, t.r)])
                   for t in tiles],
            sources=self.source_names, theme=s.theme, colours=self.palette,
            attribution=sorted(set(self.attribution)), spec=_jsonable(asdict(s)),
            engine=dict(hint="legolize-it cli site.ply --scale 1 --unit plate --shell 3 --thin-walls off"),
        )
        (out / "site.json").write_text(json.dumps(d, indent=1, ensure_ascii=False))


def _despeckle(cls: np.ndarray, studs: np.ndarray) -> np.ndarray:
    """A stud whose class none of its eight neighbours share takes the commonest class around it."""
    out = cls.copy()
    ni, nj = cls.shape
    P = np.pad(cls.astype(np.int16), 1, constant_values=-1)
    nb = np.stack([P[1 + di:1 + di + ni, 1 + dj:1 + dj + nj] for di in (-1, 0, 1) for dj in (-1, 0, 1)
                   if (di, dj) != (0, 0)], -1)
    same = (nb == cls[..., None]).sum(-1)
    lone = (same == 0) & studs
    for i, j in zip(*np.nonzero(lone)):
        v = nb[i, j][nb[i, j] >= 0]
        if len(v):
            out[i, j] = np.bincount(v).argmax()
    return out


def _mask_outlines(mask: np.ndarray) -> List[np.ndarray]:
    """The boundary of a cell mask as closed rings of cell corners (index units), outer and inner."""
    ni, nj = mask.shape
    P = np.pad(mask, 1)
    segs = []                                             # directed edges, the mask on the left
    for i in range(ni):
        for j in range(nj):
            if not mask[i, j]:
                continue
            if not P[i + 1, j]:
                segs.append(((i, j), (i + 1, j)))
            if not P[i + 2, j + 1]:
                segs.append(((i + 1, j), (i + 1, j + 1)))
            if not P[i + 1, j + 2]:
                segs.append(((i + 1, j + 1), (i, j + 1)))
            if not P[i, j + 1]:
                segs.append(((i, j + 1), (i, j)))
    nxt: Dict[tuple, List[tuple]] = {}
    for a, b in segs:
        nxt.setdefault(a, []).append(b)
    rings = []
    while nxt:
        start = next(iter(nxt))
        ring = [start]
        cur = start
        while True:
            b = nxt[cur].pop()
            if not nxt[cur]:
                del nxt[cur]
            if b == start:
                break
            ring.append(b)
            cur = b
            if cur not in nxt:
                break
        rings.append(np.array(ring, float))
    return rings


def _jsonable(x):
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    return x
