"""The model's frame and footprint: where on earth, which way up, which shape, which studs.

Frame: an anchor point (the centre of the shape, or of tile 0,0), the grid bearing of the model's +v axis (the back
edge of the model, "up" on its plan; 0 = north) and the point scale factor of the projection. Local coordinates (u, v)
are metres on the ground: u to the right, v to the back.

Stud grid: one global grid per frame - cell (i, j) spans u in [ou + i p, ou + (i + 1) p], v alike, p = metres per stud.
A stud belongs to a footprint when its centre lies inside. Shapes that tile the plane (square, rectangle, hexagon,
triangle) have neighbours at tile indices (q, r): all tiles of one frame share the grid and the test points carry the
same tiny offset, so a stud centre on a shared edge falls into exactly one tile - neighbouring tiles fit stud for stud,
with no gap and no overlap.

Shapes (size = side length for the regular polygons, diameter for a circle, width,height for rectangle and ellipse):
  square, rectangle    edges along the studs; sizes are rounded to whole studs
  hexagon              "flat" (an edge at the back and the front, along the studs) or "pointy" (edges at the sides)
  triangle             equilateral, tile (0, 0) points to the back; tiles alternate up and down along a row
  circle, ellipse      no tiling
  polygon              any outline (GPS points, or an OSM way / relation), optionally grown by a margin
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .crs import UTM

SHAPES = ("square", "rectangle", "hexagon", "triangle", "circle", "ellipse", "polygon")
TILING = ("square", "rectangle", "hexagon", "triangle")
SQRT3 = math.sqrt(3.0)
_JITTER = np.array([1.2345e-6, 2.7183e-6])          # x p: ties on shared edges go one way, the same for every tile


@dataclass
class Frame:
    """Grid coordinates (easting, northing of `crs`) <-> local ground metres (u, v)."""
    crs: UTM
    east: float
    north: float
    bearing: float                  # grid bearing of +v, degrees clockwise from grid north
    k: float = 1.0                  # point scale factor at the anchor (grid metres per ground metre)

    @property
    def lat_lon(self) -> Tuple[float, float]:
        la, lo = self.crs.inverse(self.east, self.north)
        return float(la), float(lo)

    def to_local(self, e, n):
        b = math.radians(self.bearing)
        c, s = math.cos(b), math.sin(b)
        de, dn = np.asarray(e, float) - self.east, np.asarray(n, float) - self.north
        return (de * c - dn * s) / self.k, (de * s + dn * c) / self.k

    def to_grid(self, u, v):
        b = math.radians(self.bearing)
        c, s = math.cos(b), math.sin(b)
        u, v = np.asarray(u, float) * self.k, np.asarray(v, float) * self.k
        return self.east + u * c + v * s, self.north - u * s + v * c

    def from_latlon(self, lat, lon):
        return self.to_local(*self.crs.forward(lat, lon))

    def to_latlon(self, u, v):
        return self.crs.inverse(*self.to_grid(u, v))

    def local_bearing(self, bearing_grid: float) -> float:
        """Math angle (degrees counter-clockwise from +u) of a grid bearing."""
        return 90.0 - (bearing_grid - self.bearing)


@dataclass
class Footprint:
    """One shape in local metres. `poly` (n, 2) for polygons; circles and ellipses use `radii`."""
    shape: str
    centre: Tuple[float, float] = (0.0, 0.0)
    poly: Optional[np.ndarray] = None
    radii: Optional[Tuple[float, float]] = None
    margin: float = 0.0
    tile: Tuple[int, int] = (0, 0)

    def contains(self, u, v) -> np.ndarray:
        u, v = np.asarray(u, float), np.asarray(v, float)
        if self.radii is not None:
            a, b = self.radii
            inside = ((u - self.centre[0]) / a) ** 2 + ((v - self.centre[1]) / b) ** 2 <= 1.0
        else:
            inside = point_in_polygon(self.poly, u, v)
        if self.margin > 0:
            inside |= polygon_distance(self.outline(), u, v) <= self.margin
        return inside

    def outline(self, n: int = 128) -> np.ndarray:
        if self.radii is not None:
            t = np.linspace(0, 2 * math.pi, n, endpoint=False)
            return np.c_[self.centre[0] + self.radii[0] * np.cos(t), self.centre[1] + self.radii[1] * np.sin(t)]
        return np.asarray(self.poly, float)

    def bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        o = self.outline()
        return o.min(axis=0) - self.margin, o.max(axis=0) + self.margin

    def area(self) -> float:
        o = self.outline(512)
        x, y = o[:, 0], o[:, 1]
        return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


# ------------------------------------------------------------------ geometry helpers
def point_in_polygon(poly: np.ndarray, u, v) -> np.ndarray:
    """Even-odd rule, vectorised over points (any shape) and edges."""
    poly = np.asarray(poly, float)
    shp = np.shape(u)
    pu, pv = np.ravel(u)[:, None], np.ravel(v)[:, None]
    x0, y0 = poly[:, 0][None, :], poly[:, 1][None, :]
    x1, y1 = np.roll(poly[:, 0], -1)[None, :], np.roll(poly[:, 1], -1)[None, :]
    out = np.zeros(pu.shape[0], bool)
    step = max(1, 4_000_000 // max(1, poly.shape[0]))
    for s in range(0, pu.shape[0], step):
        a, b = pu[s:s + step], pv[s:s + step]
        cross = (y0 > b) != (y1 > b)
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = x0 + (b - y0) * (x1 - x0) / (y1 - y0)
        out[s:s + step] = (np.count_nonzero(cross & (a < xi), axis=1) % 2) == 1
    return out.reshape(shp)


def polygon_distance(poly: np.ndarray, u, v) -> np.ndarray:
    """Distance from points to the polygon's outline."""
    poly = np.asarray(poly, float)
    shp = np.shape(u)
    p = np.c_[np.ravel(u), np.ravel(v)]
    a, b = poly, np.roll(poly, -1, axis=0)
    out = np.full(len(p), np.inf)
    for s in range(0, len(p), 20000):
        q = p[s:s + 20000, None, :]
        ab = (b - a)[None]
        t = np.clip(((q - a[None]) * ab).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-12), 0, 1)
        d = np.linalg.norm(q - (a[None] + t[..., None] * ab), axis=-1)
        out[s:s + 20000] = d.min(axis=1)
    return out.reshape(shp)


def regular_polygon(n: int, side: float, first_angle: float, centre=(0.0, 0.0)) -> np.ndarray:
    R = side / (2 * math.sin(math.pi / n))
    t = np.radians(first_angle + 360.0 / n * np.arange(n))
    return np.c_[centre[0] + R * np.cos(t), centre[1] + R * np.sin(t)]


def dominant_angle(polys: Sequence[np.ndarray]) -> float:
    """Length-weighted dominant edge direction of outlines (degrees, math angle, in [-45, 45)) - the angle the
    buildings run at (the brick projects of 2026-11 all turned their grid this way)."""
    hist = np.zeros(180)
    for xy in polys:
        xy = np.asarray(xy, float)
        if len(xy) < 2:
            continue
        d = np.diff(np.vstack([xy, xy[:1]]), axis=0)
        L = np.hypot(d[:, 0], d[:, 1])
        a = (np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 90) * 2
        np.add.at(hist, a.astype(int) % 180, L)
    if not hist.any():
        return 0.0
    k = np.exp(-0.5 * (np.arange(-6, 7) / 2.0) ** 2)
    hist = np.convolve(np.r_[hist[-6:], hist, hist[:6]], k, mode="same")[6:-6]
    a = hist.argmax() / 2.0
    return a - 90 if a >= 45 else a


# ------------------------------------------------------------------ the layout of one or more tiles
@dataclass
class Layout:
    """Shape, size and tiling in local metres; `stud` metres per stud; `offset` (ou, ov) of the stud grid."""
    shape: str
    size: Tuple[float, float]               # (side, side) / (w, h) / (d, d) / (a, b) - metres on the ground
    stud: float
    hex_orientation: str = "flat"
    triangle_up: bool = True                # tile (0, 0) of a triangle tiling points to the back
    poly: Optional[np.ndarray] = None       # shape "polygon": the outline, local metres around the anchor
    margin: float = 0.0
    offset: Tuple[float, float] = (0.0, 0.0)
    notes: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.shape not in SHAPES:
            raise ValueError(f"shape must be one of {SHAPES}, not {self.shape!r}")
        if self.shape in ("square", "rectangle"):        # whole studs, edges on cell boundaries
            w, h = self.size if self.shape == "rectangle" else (self.size[0], self.size[0])
            nw, nh = max(1, round(w / self.stud)), max(1, round(h / self.stud))
            if abs(nw * self.stud - w) > 1e-6 or abs(nh * self.stud - h) > 1e-6:
                self.notes.append(f"{self.shape} snapped to whole studs: {nw} x {nh} studs = "
                                  f"{nw * self.stud:.1f} x {nh * self.stud:.1f} m")
            self.size = (nw * self.stud, nh * self.stud)
            self.offset = ((nw % 2) * self.stud / 2, (nh % 2) * self.stud / 2)

    @property
    def tiles(self) -> bool:
        return self.shape in TILING

    def tile_centre(self, q: int, r: int) -> Tuple[float, float]:
        w, h = self.size
        if (q, r) == (0, 0):
            return 0.0, 0.0
        if not self.tiles:
            raise ValueError(f"a {self.shape} does not tile the plane: only tile 0,0")
        if self.shape in ("square", "rectangle"):
            return q * w, r * h
        if self.shape == "hexagon":
            s = w
            if self.hex_orientation == "flat":
                return 1.5 * s * q, SQRT3 * s * (r + q / 2.0)
            return SQRT3 * s * (q + r / 2.0), 1.5 * s * r
        # triangle: column q, row r; up/down alternate; centroids
        s = w
        th = s * SQRT3 / 2
        up = self._tri_up(q, r)
        base = r * th - th / 3 if self.triangle_up else r * th - 2 * th / 3
        return q * s / 2, base + (th / 3 if up else 2 * th / 3)

    def _tri_up(self, q: int, r: int) -> bool:
        return ((q + r) % 2 == 0) == self.triangle_up

    def footprint(self, q: int = 0, r: int = 0) -> Footprint:
        c = self.tile_centre(q, r)
        w, h = self.size
        if self.shape in ("square", "rectangle"):
            p = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]]) + c
            return Footprint(self.shape, c, p, tile=(q, r))
        if self.shape == "hexagon":
            first = 0.0 if self.hex_orientation == "flat" else 30.0
            return Footprint("hexagon", c, regular_polygon(6, w, first, c), tile=(q, r))
        if self.shape == "triangle":
            first = 90.0 if self._tri_up(q, r) else -90.0
            return Footprint("triangle", c, regular_polygon(3, w, first, c), tile=(q, r))
        if self.shape == "circle":
            return Footprint("circle", c, radii=(w / 2, w / 2))
        if self.shape == "ellipse":
            return Footprint("ellipse", c, radii=(w / 2, h / 2))
        return Footprint("polygon", c, np.asarray(self.poly, float), margin=self.margin)

    def neighbours(self, q: int, r: int) -> List[Tuple[int, int]]:
        if self.shape in ("square", "rectangle"):
            return [(q + 1, r), (q - 1, r), (q, r + 1), (q, r - 1)]
        if self.shape == "hexagon":
            return [(q + 1, r), (q - 1, r), (q, r + 1), (q, r - 1), (q + 1, r - 1), (q - 1, r + 1)]
        if self.shape == "triangle":
            return [(q + 1, r), (q - 1, r), (q, r - 1 if self._tri_up(q, r) else r + 1)]
        return []

    def stud_cells(self, fp: Footprint) -> Tuple[np.ndarray, Tuple[int, int]]:
        """(mask[ni, nj], (i0, j0)): the studs whose centre lies in `fp`, in the global grid's indices."""
        lo, hi = fp.bounds()
        ou, ov = self.offset
        p = self.stud
        i0, j0 = int(math.floor((lo[0] - ou) / p)) - 1, int(math.floor((lo[1] - ov) / p)) - 1
        i1, j1 = int(math.ceil((hi[0] - ou) / p)) + 1, int(math.ceil((hi[1] - ov) / p)) + 1
        I, J = np.meshgrid(np.arange(i0, i1), np.arange(j0, j1), indexing="ij")
        cu = ou + (I + 0.5) * p + _JITTER[0] * p
        cv = ov + (J + 0.5) * p + _JITTER[1] * p
        mask = fp.contains(cu, cv)
        if not mask.any():
            raise ValueError("the footprint holds no stud: make it larger or the studs smaller")
        ii, jj = np.nonzero(mask)
        a0, a1, b0, b1 = ii.min(), ii.max() + 1, jj.min(), jj.max() + 1
        return mask[a0:a1, b0:b1], (i0 + int(a0), j0 + int(b0))

    def radius(self) -> float:
        """Circumscribed radius of tile (0, 0) about the anchor (for fetching data)."""
        lo, hi = self.footprint(0, 0).bounds()
        return float(np.hypot(*np.maximum(np.abs(lo), np.abs(hi))))

    def describe(self) -> str:
        w, h = self.size
        if self.shape in ("square", "triangle"):
            return f"{self.shape}, side {w:.0f} m"
        if self.shape == "hexagon":
            return f"hexagon ({self.hex_orientation}), side {w:.0f} m, {2 * w:.0f} m across the corners"
        if self.shape == "circle":
            return f"circle, {w:.0f} m across"
        if self.shape in ("rectangle", "ellipse"):
            return f"{self.shape}, {w:.0f} x {h:.0f} m"
        return f"polygon of {len(self.poly)} points" + (f", grown by {self.margin:.0f} m" if self.margin else "")


def parse_tiles(spec: Optional[str]) -> List[Tuple[int, int]]:
    """"0,0" / "0,0 1,0 0,1" / "ring1" (the tile and its neighbours) -> [(q, r)]."""
    if not spec:
        return [(0, 0)]
    out = []
    for t in spec.replace(";", " ").split():
        q, r = (int(x) for x in t.split(","))
        out.append((q, r))
    return out


def ring_tiles(layout: Layout, n: int, centre: Tuple[int, int] = (0, 0)) -> List[Tuple[int, int]]:
    """The tile `centre` and every tile up to `n` neighbour steps away (a hexagon and its rings: 1, 7, 19 ...)."""
    seen = [tuple(centre)]
    front = [tuple(centre)]
    for _ in range(max(0, n)):
        nxt = []
        for t in front:
            for nb in layout.neighbours(*t):
                if nb not in seen:
                    seen.append(nb)
                    nxt.append(nb)
        front = nxt
    return seen
