"""Whole sites, offline: flat ground and a synthetic OpenStreetMap world - classes, buildings, trees, water, bridges,
tiles that share a datum, the files and the record; and Legolize it laying the bricks."""
import json

import numpy as np
import pytest

from twinbrick import engine
from twinbrick.cli import main as cli_main
from twinbrick.landcover import CLASSES, area_class, line_class
from twinbrick.mesh import is_closed
from twinbrick.site import OBJ_BRIDGE, OBJ_BUILDING, Site, SiteSpec

from conftest import LAT, LON

import trimesh

CI = {c: i for i, c in enumerate(CLASSES)}


def spec(**kw):
    d = dict(lat=LAT, lon=LON, shape="square", size=(120.0, 120.0), scale=250.0, rotation=0.0, terrain="flat",
             buildings="osm", trees="osm", tree_style="lollipop", theme="classic")
    d.update(kw)
    return SiteSpec(**d)


def test_classes_from_tags():
    assert area_class({"landuse": "forest"}) == "forest"
    assert area_class({"natural": "water", "water": "lake"}) == "water"
    assert area_class({"amenity": "parking"}) == "paved"
    assert area_class({"landuse": "farmland"}) == "field"
    assert area_class({"building": "yes"}) is None
    assert line_class({"highway": "primary"}) == ("road", 12.0, False)
    assert line_class({"highway": "primary", "lanes": "6"})[1] == pytest.approx(19.5)
    assert line_class({"highway": "service", "service": "driveway"}) is None
    assert line_class({"highway": "residential", "tunnel": "yes"}) is None
    assert line_class({"railway": "rail", "bridge": "viaduct"}) == ("rail", 4.0, True)


def test_a_square_of_the_synthetic_world(world, tmp_path):
    site = Site(spec(), say=lambda _t: None)
    tiles = site.build(tmp_path)
    t = tiles[0]
    assert t.studs.shape == (60, 60) and t.studs.all()                   # 120 m at 2 m per stud
    sc = t.stud_class
    assert (sc == CI["road"]).sum() > 40                                  # the diagonal road, a line of studs
    assert (sc == CI["water"]).sum() > 50                                 # the lake and the river
    assert (sc == CI["forest"]).sum() > 200
    assert (t.obj == OBJ_BUILDING).any()
    # the bridge crosses the river at the banks' height (flat ground: flush, not lowered with the water)
    assert t.bridge_studs.any() and not (t.water & t.bridge_studs).any()
    assert (sc[t.bridge_studs] == CI["road"]).all()
    tower = t.top[t.obj == OBJ_BUILDING].max()
    assert tower == pytest.approx(40.0, abs=0.5)                          # the church's tower part, not its outline
    assert t.trees and all(tr["colour"] in ("tree", "tree2") for tr in t.trees)
    # the water lies a plate below its banks
    water = np.repeat(np.repeat(t.water, site.sub, 0), site.sub, 1)
    assert t.ground[water].max() < 0.0
    m = trimesh.load(t.files["mesh"], process=False)
    ext = m.bounds[1] - m.bounds[0]
    assert np.allclose(ext[:2], [480.0, 480.0])                           # whole studs
    m.merge_vertices()
    assert is_closed(m)
    rec = json.loads((tmp_path / "site.json").read_text())
    assert rec["scale"]["ratio"] == "1:250" and rec["tiles"][0]["studs"] == [60, 60]
    assert any("OpenStreetMap" in a for a in rec["attribution"])
    assert (tmp_path / "site_plan.png").exists() and (tmp_path / "site_raster.npz").exists()


def test_neighbour_tiles_share_the_datum_and_the_grid(world, tmp_path):
    s = spec(shape="hexagon", size=(30.0, 30.0), tiles=((0, 0), (1, 0), (0, 1)), trees="none", buildings="none")
    site = Site(s, say=lambda _t: None)
    tiles = site.build(tmp_path)
    assert len(tiles) == 3 and (tmp_path / "overview_plan.png").exists()
    rec = json.loads((tmp_path / "site.json").read_text())
    origins = [tuple(t["origin_stud"]) for t in rec["tiles"]]
    assert len(set(origins)) == 3
    cells = []
    for t in tiles:
        ii, jj = np.nonzero(t.studs)
        cells.append(set(zip(ii + t.origin[0], jj + t.origin[1])))
    assert not (cells[0] & cells[1]) and not (cells[0] & cells[2]) and not (cells[1] & cells[2])
    assert len({rec["scale"]["datum_m"]}) == 1


def test_orientation_along_the_buildings(world):
    s = spec(rotation=None)
    site = Site(s, say=lambda _t: None)
    site.fetch()
    assert abs(((site.frame.bearing - site.north_b) + 45) % 90 - 45) < 2.0    # the synthetic world is north-up


def test_corner_spans_a_hexagon():
    from twinbrick.crs import UTM
    u = UTM(32)
    e, n = u.forward(LAT, LON)
    k = u.scale_factor(LAT, LON)
    north = u.north_bearing(LAT, LON)
    import math
    b = math.radians(north + 90.0)                                        # 100 m due east (true) of the centre
    lat, lon = (float(x) for x in u.inverse(e + 100 * k * math.sin(b), n + 100 * k * math.cos(b)))
    site = Site(SiteSpec(lat=LAT, lon=LON, shape="hexagon", corner=(lat, lon), scale=1000, terrain="flat"),
                say=lambda _t: None)
    assert site.size[0] == pytest.approx(100.0, rel=0.002)
    # a flat hexagon's first corner lies on its +u axis: the back faces north
    assert ((site.bearing - site.north_b + 180) % 360 - 180) == pytest.approx(0.0, abs=0.2)


def test_plan_needs_no_data():
    site = Site(SiteSpec(lat=LAT, lon=LON, shape="triangle", size=(100, 100), scale=750, tiles=((0, 0), (1, 0))),
                say=lambda _t: None)
    p = site.plan()
    assert len(p) == 2 and all(t["base_studs"] > 50 for t in p)
    rings = site.stud_outline_latlon(0, 0)
    assert len(rings) == 1 and len(rings[0]) > 6


def test_cli_offline_with_bricks(tmp_path, capsys):
    if engine.legolize_root() is None:
        pytest.skip("Legolize it not found")
    code = cli_main(["51.3,7.67", "--shape", "square", "--size", "48", "--scale", "750", "--terrain", "flat",
                     "--buildings", "none", "--trees", "none", "--no-landcover", "--bricks", "bricky", "--no-png",
                     "--out", str(tmp_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    b = out["tiles"][0]["bricks"]
    assert b["bricks"] > 0 and b["stable"]
    assert (tmp_path / "bricks" / "result.json").exists()
