"""Shapes and tilings: whole studs for squares, neighbours that fit stud for stud, rings, orientation."""
import numpy as np
import pytest
from scipy import ndimage as ndi

from twinbrick.footprint import Layout, dominant_angle, point_in_polygon, polygon_distance, ring_tiles


def cells(layout, q, r):
    m, (i0, j0) = layout.stud_cells(layout.footprint(q, r))
    ii, jj = np.nonzero(m)
    return set(zip((ii + i0).tolist(), (jj + j0).tolist()))


@pytest.mark.parametrize("shape, kw", [("hexagon", dict(hex_orientation="flat")),
                                       ("hexagon", dict(hex_orientation="pointy")),
                                       ("square", {}), ("rectangle", {}), ("triangle", {}),
                                       ("triangle", dict(triangle_up=False))])
def test_neighbours_fit_stud_for_stud(shape, kw):
    size = (37.0, 23.0) if shape == "rectangle" else (37.0, 37.0)
    lay = Layout(shape, size, 3.0, **kw)
    tiles = ring_tiles(lay, 2)
    sets = [cells(lay, q, r) for q, r in tiles]
    union = set()
    for s in sets:
        assert s, "every tile has studs"
        assert not (union & s), "no stud in two tiles"
        union |= s
    # no gaps: the inner ring's tiles are surrounded, so their union has no holes
    inner = set().union(*(cells(lay, q, r) for q, r in ring_tiles(lay, 1)))
    ii = np.array([c[0] for c in union]); jj = np.array([c[1] for c in union])
    grid = np.zeros((np.ptp(ii) + 3, np.ptp(jj) + 3), bool)
    grid[ii - ii.min() + 1, jj - jj.min() + 1] = True
    filled = ndi.binary_fill_holes(grid)
    assert (filled == grid).all(), "no hole between neighbouring tiles"
    assert len(inner) < len(union)
    # every neighbour touches its tile along studs (4-connected)
    q, r = 0, 0
    me = cells(lay, q, r)
    for nb in lay.neighbours(q, r):
        other = cells(lay, *nb)
        touch = any((i + di, j + dj) in other for i, j in me for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)))
        assert touch, f"{shape}: neighbour {nb} does not touch"


def test_ring_sizes():
    assert len(ring_tiles(Layout("hexagon", (10, 10), 1.0), 2)) == 19
    assert len(ring_tiles(Layout("square", (10, 10), 1.0), 1)) == 5
    assert len(ring_tiles(Layout("triangle", (10, 10), 1.0), 1)) == 4
    assert ring_tiles(Layout("circle", (10, 10), 1.0), 3) == [(0, 0)]


def test_squares_snap_to_whole_studs():
    lay = Layout("square", (100.0, 100.0), 6.0)
    assert lay.size == (102.0, 102.0) and lay.notes
    m, _ = lay.stud_cells(lay.footprint())
    assert m.shape == (17, 17) and m.all()
    even = Layout("rectangle", (48.0, 24.0), 6.0)
    m, _ = even.stud_cells(even.footprint())
    assert m.shape == (8, 4) and m.all() and even.offset == (0.0, 0.0)


def test_circle_and_polygon():
    lay = Layout("circle", (60.0, 60.0), 2.0)
    m, _ = lay.stud_cells(lay.footprint())
    assert abs(m.sum() * 4.0 - np.pi * 30 ** 2) / (np.pi * 30 ** 2) < 0.05
    with pytest.raises(ValueError):
        lay.tile_centre(1, 0)
    sq = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
    inside = point_in_polygon(sq, np.array([5, 15, -1, 9.9]), np.array([5, 5, 5, 0.1]))
    assert inside.tolist() == [True, False, False, True]
    assert polygon_distance(sq, np.array([15.0]), np.array([5.0]))[0] == pytest.approx(5.0)
    grown = Layout("polygon", (10, 10), 1.0, poly=sq - 5, margin=3.0)
    m, _ = grown.stud_cells(grown.footprint())
    assert m.shape == (16, 16)


def test_dominant_angle_of_turned_buildings():
    def rect(angle, w=20, h=8, c=(0, 0)):
        a = np.radians(angle)
        R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        return (np.array([[-w, -h], [w, -h], [w, h], [-w, h]]) / 2) @ R.T + c
    assert dominant_angle([rect(12.0), rect(12.0, c=(50, 10))]) == pytest.approx(12.0, abs=1.0)
    assert dominant_angle([rect(-30.0), rect(60.0, c=(40, 0))]) == pytest.approx(-30.0, abs=1.0)
