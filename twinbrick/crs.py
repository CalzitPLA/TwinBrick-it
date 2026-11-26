"""Coordinates: WGS84 / ETRS89 latitude and longitude <-> UTM (transverse Mercator, Krüger series), the grid
convergence (true north in grid coordinates), the point scale factor, Web-Mercator tiles, and parsing of coordinates.

No pyproj: the Krüger series to n^4 is accurate to well below a millimetre inside a UTM zone and to centimetres a few
degrees outside it, far below a stud at any map scale. ETRS89 (the German and NRW data, EPSG:25832) and WGS84 (OSM,
GPS) differ by under a metre in 2026; the code treats them as one datum (GRS80 ellipsoid).
"""
from __future__ import annotations

import math
import re
from typing import Tuple

import numpy as np

A_AXIS = 6378137.0
FLAT = 1 / 298.257222101                    # GRS80 (WGS84: 1/298.257223563 - the same to 0.1 mm)
K0 = 0.9996
E0 = 500000.0


def utm_zone(lon: float) -> int:
    return int(math.floor((lon + 180.0) / 6.0)) % 60 + 1


class UTM:
    """Transverse Mercator in UTM zone `zone` (north or south); forward and inverse work on numpy arrays."""

    def __init__(self, zone: int, south: bool = False):
        self.zone, self.south = int(zone), bool(south)
        self.lon0 = math.radians(-183.0 + 6.0 * self.zone)
        self.n0 = 10_000_000.0 if self.south else 0.0
        f = FLAT
        n = f / (2 - f)
        self.e = math.sqrt(f * (2 - f))
        self.A = A_AXIS / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
        # Krüger's series to n^4 (Karney 2011): well below a millimetre inside a zone, both ways
        n2, n3, n4 = n ** 2, n ** 3, n ** 4
        self.alpha = (n / 2 - 2 * n2 / 3 + 5 * n3 / 16 + 41 * n4 / 180, 13 * n2 / 48 - 3 * n3 / 5 + 557 * n4 / 1440,
                      61 * n3 / 240 - 103 * n4 / 140, 49561 * n4 / 161280)
        self.beta = (n / 2 - 2 * n2 / 3 + 37 * n3 / 96 - n4 / 360, n2 / 48 + n3 / 15 - 437 * n4 / 1440,
                     17 * n3 / 480 - 37 * n4 / 840, 4397 * n4 / 161280)
        self.delta = (2 * n - 2 * n2 / 3 - 2 * n3 + 116 * n4 / 45, 7 * n2 / 3 - 8 * n3 / 5 - 227 * n4 / 45,
                      56 * n3 / 15 - 136 * n4 / 35, 4279 * n4 / 630)

    @property
    def epsg(self) -> int:
        return (32700 if self.south else 32600) + self.zone

    @property
    def name(self) -> str:
        return f"UTM {self.zone}{'S' if self.south else 'N'}"

    @classmethod
    def for_point(cls, lat: float, lon: float) -> "UTM":
        return cls(utm_zone(lon), lat < 0)

    def forward(self, lat, lon) -> Tuple[np.ndarray, np.ndarray]:
        """(easting, northing) in metres."""
        phi = np.radians(np.asarray(lat, float))
        lam = np.radians(np.asarray(lon, float)) - self.lon0
        e = self.e
        t = np.sinh(np.arctanh(np.sin(phi)) - e * np.arctanh(e * np.sin(phi)))
        xi = np.arctan2(t, np.cos(lam))
        eta = np.arctanh(np.sin(lam) / np.sqrt(1 + t * t))
        x, y = eta.copy(), xi.copy()
        for j, a in enumerate(self.alpha, 1):
            x = x + a * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
            y = y + a * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
        return E0 + K0 * self.A * x, self.n0 + K0 * self.A * y

    def inverse(self, east, north) -> Tuple[np.ndarray, np.ndarray]:
        """(lat, lon) in degrees."""
        xi = (np.asarray(north, float) - self.n0) / (K0 * self.A)
        eta = (np.asarray(east, float) - E0) / (K0 * self.A)
        xp, ep = xi.copy(), eta.copy()
        for j, b in enumerate(self.beta, 1):
            xp = xp - b * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
            ep = ep - b * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
        chi = np.arcsin(np.sin(xp) / np.cosh(ep))
        phi = chi.copy()
        for j, d in enumerate(self.delta, 1):
            phi = phi + d * np.sin(2 * j * chi)
        lam = self.lon0 + np.arctan2(np.sinh(ep), np.cos(xp))
        return np.degrees(phi), (np.degrees(lam) + 180.0) % 360.0 - 180.0

    def scale_factor(self, lat: float, lon: float) -> float:
        """Point scale factor k: grid metres per metre on the ground (0.9996 on the central meridian)."""
        d = 1e-4
        x0, y0 = self.forward(lat, lon - d)
        x1, y1 = self.forward(lat, lon + d)
        phi = math.radians(lat)
        nu = A_AXIS / math.sqrt(1 - (self.e * math.sin(phi)) ** 2)
        ground = nu * math.cos(phi) * math.radians(2 * d)
        return float(math.hypot(x1 - x0, y1 - y0) / ground)

    def north_bearing(self, lat: float, lon: float) -> float:
        """Grid bearing of true north (degrees clockwise from grid north; the negative of the convergence)."""
        x0, y0 = self.forward(lat - 1e-4, lon)
        x1, y1 = self.forward(lat + 1e-4, lon)
        return float(math.degrees(math.atan2(x1 - x0, y1 - y0)))


# ------------------------------------------------------------------ Web Mercator (slippy map tiles)
def mercator_pixel(lat, lon, zoom: int, tile: int = 256) -> Tuple[np.ndarray, np.ndarray]:
    """Global pixel coordinates (x right, y down) at `zoom`."""
    lat = np.clip(np.asarray(lat, float), -85.05112878, 85.05112878)
    n = tile * 2.0 ** zoom
    x = (np.asarray(lon, float) + 180.0) / 360.0 * n
    s = np.sin(np.radians(lat))
    y = (0.5 - np.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def mercator_resolution(lat: float, zoom: int, tile: int = 256) -> float:
    """Ground metres per pixel at `lat` and `zoom`."""
    return 2 * math.pi * A_AXIS * math.cos(math.radians(lat)) / (tile * 2.0 ** zoom)


# ------------------------------------------------------------------ parsing
_HEMI = {"N": 1, "S": -1, "E": 1, "W": -1, "O": 1}      # O: Ost (German maps)


def parse_angle(text: str) -> Tuple[float, str]:
    """One coordinate: decimal ("51.4017", "-8.06", "51.4017N") or degrees/minutes/seconds ("51°24'06.1\"N",
    "51 24 6.1 N", "8°3.7'E"). Returns (value, hemisphere letter or "")."""
    s = text.strip().upper().replace(",", ".") if text.count(",") == 1 and "." not in text else text.strip().upper()
    hemi = ""
    m = re.search(r"([NSEWO])\s*$", s) or re.match(r"^\s*([NSEWO])", s)
    if m:
        hemi = m.group(1)
        s = (s[:m.start()] + s[m.end():]).strip()
    nums = [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?", s)]
    if not nums or len(nums) > 3:
        raise ValueError(f"not a coordinate: {text!r}")
    sign = -1.0 if nums[0] < 0 or s.lstrip().startswith("-") else 1.0
    value = abs(nums[0]) + (nums[1] / 60.0 if len(nums) > 1 else 0) + (nums[2] / 3600.0 if len(nums) > 2 else 0)
    if hemi:
        sign *= _HEMI[hemi]
    return sign * value, hemi


def parse_latlon(text: str) -> Tuple[float, float]:
    """"LAT,LON" / "LAT LON" / "LAT;LON", decimal or DMS, with or without N/S/E/W. A pair whose letters say the
    longitude comes first ("8.06E 51.40N") is swapped."""
    t = text.strip()
    if ";" in t:
        parts = t.split(";")
    elif t.count(",") == 1:
        parts = t.split(",")
    elif t.count(",") == 3:                              # decimal commas: "51,4017, 8,0614"
        a = t.split(",")
        parts = [a[0] + "." + a[1], a[2] + "." + a[3]]
    else:
        m = re.match(r"^(.*?[NS])\s*(.*[EWO])$", t.upper()) or re.match(r"^(.*?[EWO])\s*(.*[NS])$", t.upper())
        parts = [m.group(1), m.group(2)] if m else t.split()
    if len(parts) != 2:
        raise ValueError(f"expected LAT,LON, not {text!r}")
    (a, ha), (b, hb) = parse_angle(parts[0]), parse_angle(parts[1])
    if ha in "EWO" and ha and hb in "NS" and hb:
        a, b = b, a
    if not (-90 <= a <= 90 and -180 <= b <= 180):
        raise ValueError(f"out of range: {text!r} (latitude first)")
    return a, b


def format_latlon(lat: float, lon: float) -> str:
    return f"{abs(lat):.5f}°{'N' if lat >= 0 else 'S'} {abs(lon):.5f}°{'E' if lon >= 0 else 'W'}"
