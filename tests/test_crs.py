"""Coordinates: UTM both ways, the scale factor, grid north, parsing."""
import math

import numpy as np
import pytest

from twinbrick.crs import UTM, mercator_pixel, parse_latlon, utm_zone


def test_utm_on_the_central_meridian():
    # 51 N on the central meridian of zone 32: easting 500 km, northing = meridian arc (GRS80) x 0.9996
    e, n = UTM(32).forward(51.0, 9.0)
    assert e == pytest.approx(500000.0, abs=1e-6)
    assert n == pytest.approx(5649824.888, abs=0.01)


def test_utm_round_trip():
    u = UTM(32)
    rng = np.random.default_rng(1)
    lat, lon = rng.uniform(47, 55, 200), rng.uniform(5, 13, 200)
    la, lo = u.inverse(*u.forward(lat, lon))
    assert np.abs(la - lat).max() < 1e-9 and np.abs(lo - lon).max() < 1e-9
    s = UTM.for_point(-33.86, 151.21)
    assert s.zone == 56 and s.south
    la, lo = s.inverse(*s.forward(-33.86, 151.21))
    assert la == pytest.approx(-33.86, abs=1e-9) and lo == pytest.approx(151.21, abs=1e-9)


def test_scale_factor_and_grid_north():
    u = UTM(32)
    assert u.scale_factor(51.0, 9.0) == pytest.approx(0.9996, abs=1e-7)
    assert u.scale_factor(51.0, 7.0) > 0.9996
    assert abs(u.north_bearing(51.0, 9.0)) < 1e-6
    # west of the central meridian, true north points to the east of grid north (bearing > 0) by ~ dlon sin(lat)
    assert u.north_bearing(51.3, 7.67) == pytest.approx(-(7.67 - 9.0) * math.sin(math.radians(51.3)), abs=0.02)
    assert utm_zone(7.67) == 32 and utm_zone(-0.1) == 30


def test_mercator_pixels():
    x, y = mercator_pixel(0.0, 0.0, 0)
    assert (x, y) == pytest.approx((128.0, 128.0))


@pytest.mark.parametrize("text, want", [
    ("51.4017, 8.0614", (51.4017, 8.0614)),
    ("51.4017 8.0614", (51.4017, 8.0614)),
    ("51,4017, 8,0614", (51.4017, 8.0614)),
    ("51°24'06.1\"N 8°03'41\"E", (51.401694, 8.061389)),
    ("33.86S,151.21E", (-33.86, 151.21)),
    ("-33.86;151.21", (-33.86, 151.21)),
    ("8.0614E 51.4017N", (51.4017, 8.0614)),
])
def test_parse_latlon(text, want):
    assert parse_latlon(text) == pytest.approx(want, abs=1e-5)


def test_parse_latlon_rejects_nonsense():
    for bad in ("hello", "91,8", "1,2,3"):
        with pytest.raises(ValueError):
            parse_latlon(bad)
