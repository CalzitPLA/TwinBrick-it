"""TwinBrick it on the command line.

    twinbrick-it cli 51.2955,7.6735 --shape hexagon --size 120 --scale 750 --bricks detailed
    twinbrick-it cli "51°24'06\"N 8°03'41\"E" --shape square --size 200 --rotation auto --scale 1000
    twinbrick-it cli 51.40,8.06 --shape hexagon --size 100 --scale 1000 --ring 1     # a hexagon and its 6 neighbours
    twinbrick-it cli --osm way/146943431 --margin 6 --scale 750                       # an OSM outline (a campus)
    twinbrick-it cli 51.40,8.06 --plan ...                                            # only the studs, no download
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from .crs import parse_latlon
from .footprint import SHAPES, ring_tiles
from .landcover import THEMES, parse_colours
from .site import RESULTS, Site, SiteSpec
from . import engine, net

BRICK_MODES = ("bricky", "detailed", "artsy", "none")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="twinbrick-it cli", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(__doc__.split("\n")[2:]))
    ap.add_argument("place", nargs="?", help='the centre: "LAT,LON" (decimal or degrees/minutes/seconds, N/S/E/W); '
                                            "south and west as 33.86S,151.21E or with --at")
    ap.add_argument("--at", default=None, metavar="LAT,LON", help="the centre (for coordinates starting with a minus)")
    g = ap.add_argument_group("the shape")
    g.add_argument("--shape", choices=SHAPES, default="hexagon", help="(default hexagon)")
    g.add_argument("--size", default="150", help="metres: the side (square, hexagon, triangle), the diameter "
                                                 "(circle), W,H (rectangle, ellipse); default 150")
    g.add_argument("--corner", default=None, metavar="LAT,LON",
                   help="span the shape to this corner instead (size and rotation from it)")
    g.add_argument("--points", default=None, metavar="LAT,LON;...", help="shape polygon: its corners")
    g.add_argument("--osm", default=None, metavar="way/ID", help="shape polygon: an OpenStreetMap outline")
    g.add_argument("--margin", type=float, default=0.0, help="polygon: ground around it, metres")
    g.add_argument("--rotation", default="0", help="the true bearing the model's back edge faces, degrees (0 = north"
                                                  " up), or auto: the studs along the buildings (default 0)")
    g.add_argument("--hex", choices=("flat", "pointy"), default="flat", help="hexagon: an edge at the back (flat) or "
                                                                            "a corner (pointy)")
    g.add_argument("--triangle", choices=("up", "down"), default="up", help="triangle: tile 0,0 points to the back")
    g.add_argument("--tiles", default=None, metavar="Q,R ...", help='tiles of the tiling, e.g. "0,0 1,0 0,1"')
    g.add_argument("--ring", type=int, default=None, help="tile 0,0 and every tile up to N neighbours away")
    g = ap.add_argument_group("the scale")
    sc = g.add_mutually_exclusive_group()
    sc.add_argument("--scale", type=float, default=None, help="1:N (default 1000: 1 stud = 8 m)")
    sc.add_argument("--stud-m", type=float, default=None, help="metres per stud instead")
    sc.add_argument("--studs", type=float, default=None, help="studs across the shape instead")
    g.add_argument("--vertical", type=float, default=1.0, help="height exaggeration (default 1: true proportions)")
    g.add_argument("--base", type=int, default=2, help="plates under the lowest ground (default 2)")
    g.add_argument("--datum", type=float, default=None, help="the model's bottom, metres above sea level (the same "
                                                             "for tiles built one by one)")
    g.add_argument("--water-drop", type=float, default=1.0, help="plates the water lies below its banks (default 1)")
    g = ap.add_argument_group("the data")
    g.add_argument("--terrain", choices=("auto", "nrw", "terrarium", "flat"), default="auto")
    g.add_argument("--buildings", choices=("auto", "lod2", "osm", "none"), default="auto")
    g.add_argument("--trees", choices=("auto", "ndom", "osm", "none"), default="auto")
    g.add_argument("--tree-style", choices=("auto", "lollipop", "canopy", "flat"), default="auto",
                   help="lollipop: a trunk and a round crown per tree; canopy: the crowns' true surface; flat: none "
                        "(auto: lollipop up to 4 m per stud, else canopy)")
    g.add_argument("--no-landcover", action="store_true", help="no land use, roads or water: ground colour only")
    g.add_argument("--min-line", type=float, default=0.35, help="roads, paths, rails, streams narrower than this "
                                                                "share of a stud are left out (default 0.35)")
    g.add_argument("--forest-height", type=float, default=15.0, help="trees from OSM woods: their height, metres")
    g.add_argument("--offline", action="store_true", help="use the cache only (no downloads)")
    g = ap.add_argument_group("the colours")
    g.add_argument("--theme", choices=list(THEMES), default="classic")
    g.add_argument("--colours", default=None, metavar="CLASS=COLOUR,...",
                   help="e.g. roof=dark_red,road=black (classes: ground field forest sand rock paved rail water path "
                        "road bridge roof wall tree base)")
    g.add_argument("--osm-colours", action="store_true", help="roof:colour and building:colour from OSM")
    g = ap.add_argument_group("output")
    g.add_argument("--name", default=None, help="the site's name (the results folder)")
    g.add_argument("--out", default=None, help="the folder (default ~/legolize_results/<name>)")
    g.add_argument("--bricks", choices=BRICK_MODES, default="bricky",
                   help="lay the bricks with Legolize it: bricky, detailed (slopes, tiles, round 1x1), artsy, none")
    g.add_argument("--manual", action="store_true", help="the building manual too")
    g.add_argument("--no-png", action="store_true", help="no picture of the bricks")
    g.add_argument("--engine-args", default="", help="more options for Legolize it, e.g. \"--cheapest --order de\"")
    g.add_argument("--sub", type=int, default=0, help="raster cells per stud (default: about 0.7 m)")
    g.add_argument("--plan", action="store_true", help="only the studs of each tile (no download)")
    g.add_argument("--open", action="store_true", help="open the result in Legolize it's window")
    g.add_argument("--server", default=None, metavar="HOST",
                   help="build on a server over ssh (an alias of ~/.ssh/config set up with: twinbrick-it server add)")
    return ap


def spec_from_args(a) -> SiteSpec:
    place = a.at or a.place
    lat = lon = None
    if place:
        lat, lon = parse_latlon(place)
    size = [float(x) for x in re.split(r"[,x ]+", str(a.size).strip()) if x]
    if len(size) == 1:
        size = size * 2
    rotation = None if str(a.rotation).lower() == "auto" else float(a.rotation)
    points = tuple(parse_latlon(p) for p in a.points.split(";")) if a.points else ()
    shape = "polygon" if (a.points or a.osm) else a.shape
    scale = a.scale if (a.scale or a.stud_m or a.studs) else 1000.0
    tiles = tuple(tuple(int(x) for x in t.split(",")) for t in a.tiles.split()) if a.tiles else ((0, 0),)
    return SiteSpec(lat=lat, lon=lon, shape=shape, size=tuple(size[:2]), rotation=rotation,
                    hex_orientation=a.hex, triangle_up=(a.triangle == "up"),
                    corner=parse_latlon(a.corner) if a.corner else None, points=points, outline=a.osm,
                    margin=a.margin, tiles=tiles, scale=scale, stud_m=a.stud_m, studs=a.studs,
                    vertical=a.vertical, base=a.base, datum=a.datum, water_drop=a.water_drop, terrain=a.terrain,
                    buildings=a.buildings, trees=a.trees, tree_style=a.tree_style, landcover=not a.no_landcover, min_line=a.min_line,
                    theme=a.theme, colours=parse_colours(a.colours), osm_colours=a.osm_colours,
                    forest_height=a.forest_height, sub=a.sub, name=a.name or "")


def spec_args(s: SiteSpec, bricks: str = "bricky", manual: bool = False) -> list:
    """A command line that builds `s` (for a server); values that may start with a minus are passed as --opt=value."""
    a = []
    if s.lat is not None and s.lon is not None:
        a.append(f"--at={s.lat:.8f},{s.lon:.8f}")
    if s.outline:
        a += ["--osm", s.outline, f"--margin={s.margin}"]
    elif s.points:
        a.append("--points=" + ";".join(f"{la:.8f},{lo:.8f}" for la, lo in s.points))
        a.append(f"--margin={s.margin}")
    else:
        a += ["--shape", s.shape]
    a.append(f"--size={s.size[0]},{s.size[1]}")
    if s.corner is not None:
        a.append(f"--corner={s.corner[0]:.8f},{s.corner[1]:.8f}")
    a.append("--rotation=" + ("auto" if s.rotation is None else f"{s.rotation}"))
    a += ["--hex", s.hex_orientation, "--triangle", "up" if s.triangle_up else "down"]
    a.append("--tiles=" + " ".join(f"{q},{r}" for q, r in s.tiles))
    if s.scale:
        a.append(f"--scale={s.scale}")
    elif s.stud_m:
        a.append(f"--stud-m={s.stud_m}")
    elif s.studs:
        a.append(f"--studs={s.studs}")
    a += [f"--vertical={s.vertical}", f"--base={s.base}", f"--water-drop={s.water_drop}"]
    if s.datum is not None:
        a.append(f"--datum={s.datum}")
    a += ["--terrain", s.terrain, "--buildings", s.buildings, "--trees", s.trees, "--tree-style", s.tree_style,
          "--theme", s.theme,
          f"--min-line={s.min_line}", f"--forest-height={s.forest_height}"]
    if not s.landcover:
        a.append("--no-landcover")
    if s.colours:
        a.append("--colours=" + ",".join(f"{k}={v}" for k, v in s.colours.items()))
    if s.osm_colours:
        a.append("--osm-colours")
    if s.sub:
        a.append(f"--sub={s.sub}")
    if s.name:
        a += ["--name", s.name]
    a += ["--bricks", bricks] + (["--manual"] if manual else [])
    return a


def default_name(site: Site) -> str:
    s = site.spec
    if s.name:
        return s.name
    if site.outline_name:
        base = site.outline_name
    else:
        base = f"{abs(site.lat):.4f}{'N' if site.lat >= 0 else 'S'}_{abs(site.lon):.4f}{'E' if site.lon >= 0 else 'W'}"
    return re.sub(r"[^\w.-]+", "_", f"{base}_{s.shape}").strip("_")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    a = build_parser().parse_args(argv)
    if a.offline:
        import os
        os.environ["TWINBRICK_OFFLINE"] = "1"
    say = lambda t: print(t, file=sys.stderr, flush=True)
    try:
        spec = spec_from_args(a)
        site = Site(spec, say=say)
        if a.ring is not None:
            site.spec.tiles = tuple(ring_tiles(site.layout, a.ring))
        if a.plan:
            say(f"{site.layout.describe()}, 1:{site.scale:.0f} (1 stud = {site.layout.stud:.2f} m, 1 plate = "
                f"{site.plate_m:.2f} m), raster {site.layout.stud / site.sub:.2f} m")
            for t in site.plan():
                say(f"  tile {t['tile'][0]},{t['tile'][1]}: {t['studs'][0]} x {t['studs'][1]} studs, "
                    f"{t['base_studs']} on the base, {t['size_cm'][0]:.1f} x {t['size_cm'][1]:.1f} cm, "
                    f"{t['area_m2'] / 1e4:.2f} ha")
            return 0
        name = default_name(site)
        out = Path(a.out) if a.out else RESULTS / name / time.strftime("%Y%m%d_%H%M%S")
        if a.server:                                  # the whole build there, the results here
            from . import remote
            summary = remote.run_site(a.server, remote.strip_local(argv), out, progress=say)
            print(json.dumps(summary, indent=1))
            if a.open and summary.get("tiles"):
                engine.open_window(Path(summary["tiles"][0]["mesh"]), a.bricks)
            return 0 if all("error" not in (e.get("bricks") or {}) for e in summary.get("tiles", [])) else 2
        t0 = time.time()
        tiles = site.build(out)
        say(f"site built in {time.time() - t0:.0f} s: {out}")
        summary = dict(site=str(out / "site.json"), tiles=[])
        for t in tiles:
            entry = dict(tile=[t.q, t.r], mesh=t.files.get("mesh"), plan=t.files.get("plan"))
            if a.bricks != "none":
                sub = out / ("bricks" if len(tiles) == 1 else f"{t.label}_bricks")
                res = engine.run(Path(t.files["mesh"]), sub, a.bricks, png=not a.no_png, manual=a.manual,
                                 extra=a.engine_args.split() if a.engine_args else (), say=say)
                entry["bricks"] = {k: res.get(k) for k in ("bricks", "layers", "stable", "groups", "floating", "error")
                                   if k in res}
                entry["bricks_dir"] = str(sub)
            summary["tiles"].append(entry)
        print(json.dumps(summary, indent=1))
        if a.open and tiles:
            engine.open_window(Path(tiles[0].files["mesh"]), a.bricks)
        return 0 if all("error" not in (e.get("bricks") or {}) for e in summary["tiles"]) else 2
    except (ValueError, RuntimeError, net.Offline, ImportError, subprocess.TimeoutExpired) as ex:
        say(f"error: {ex}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
