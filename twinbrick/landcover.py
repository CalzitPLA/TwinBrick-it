"""What the ground is (from OpenStreetMap tags) and which brick colour each kind gets (themes).

Area classes are measured per stud (the class covering most of a stud wins); line classes (roads, paths, rails,
streams) are drawn straight onto the studs, at least one stud wide, so a road stays one connected line of studs
instead of falling apart into specks. Colours are names of `lego_engine.palette.PALETTE`.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from lego_engine.palette import PALETTE

from .osm import metres, number

# area classes in drawing order (later ones win where areas overlap) and line classes (drawn after all areas)
AREA_CLASSES = ("ground", "field", "forest", "sand", "rock", "paved", "rail", "water")
LINE_CLASSES = ("water", "rail", "path", "road")
CLASSES = ("ground", "field", "forest", "sand", "rock", "paved", "rail", "water", "path", "road", "bridge",
           "roof", "wall", "tree", "tree2", "trunk", "base")

THEMES: Dict[str, Dict[str, str]] = {
    # green ground, white houses with red roofs, grey roads, blue water - readable from across the room
    "classic": dict(ground="green", field="tan", forest="dark_green", sand="tan", rock="light_bluish_grey",
                    paved="light_bluish_grey", rail="dark_tan", water="blue", path="tan", road="dark_bluish_grey",
                    bridge="light_bluish_grey", roof="red", wall="white", tree="dark_green", tree2="green",
                    trunk="reddish_brown", base="dark_bluish_grey"),
    # the AR campus's few colours (cheap to buy): green, white, two greys, blue, dark green
    "minimal": dict(ground="green", field="green", forest="green", sand="green", rock="light_bluish_grey",
                    paved="light_bluish_grey", rail="light_bluish_grey", water="blue", path="light_bluish_grey",
                    road="light_bluish_grey", bridge="light_bluish_grey", roof="dark_bluish_grey", wall="white",
                    tree="dark_green", tree2="dark_green", trunk="reddish_brown", base="dark_bluish_grey"),
    # an architect's site model: white buildings on tan ground, black base
    "architecture": dict(ground="tan", field="tan", forest="tan", sand="tan", rock="light_bluish_grey",
                         paved="light_bluish_grey", rail="light_bluish_grey", water="medium_blue",
                         path="light_bluish_grey", road="light_bluish_grey", bridge="white", roof="white",
                         wall="white", tree="green", tree2="green", trunk="tan", base="black"),
    # slate roofs and greywacke walls of the Sauerland
    "sauerland": dict(ground="green", field="lime", forest="dark_green", sand="tan", rock="dark_bluish_grey",
                      paved="light_bluish_grey", rail="dark_tan", water="blue", path="tan", road="dark_bluish_grey",
                      bridge="light_bluish_grey", roof="black", wall="white", tree="dark_green",
                      tree2="green", trunk="reddish_brown", base="dark_bluish_grey"),
}


def theme(name: str, overrides: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    if name not in THEMES:
        raise ValueError(f"theme must be one of {list(THEMES)}, not {name!r}")
    t = dict(THEMES[name])
    for k, v in (overrides or {}).items():
        if k not in CLASSES:
            raise ValueError(f"unknown class {k!r} (classes: {', '.join(CLASSES)})")
        if v not in PALETTE:
            raise ValueError(f"unknown colour {v!r} (colours: {', '.join(PALETTE)})")
        t[k] = v
    return t


def parse_colours(spec: Optional[str]) -> Dict[str, str]:
    """"roof=dark_red,road=black" -> {"roof": "dark_red", "road": "black"}."""
    out = {}
    for item in (spec or "").replace(";", ",").split(","):
        if item.strip():
            k, _, v = item.partition("=")
            out[k.strip()] = v.strip()
    return out


_PAVED_SURFACES = {"asphalt", "concrete", "paved", "paving_stones", "sett", "cobblestone", "tartan", "acrylic",
                   "concrete:plates", "metal", "wood", "clay"}


def area_class(tags: Dict[str, str]) -> Optional[str]:
    """The class of an area, or None (not ground cover: buildings, boundaries ...)."""
    g = tags.get
    if g("building") or g("building:part"):
        return None
    lu, nat, lei, am = g("landuse"), g("natural"), g("leisure"), g("amenity")
    if nat == "water" or g("water") or g("waterway") in ("riverbank", "dock") or lu in ("reservoir", "basin") \
            or lei == "swimming_pool":
        return "water"
    if nat in ("wood",) or lu == "forest":
        return "forest"
    if nat in ("sand", "beach", "shingle") or g("golf") == "bunker":
        return "sand"
    if nat in ("bare_rock", "scree", "glacier") or lu in ("quarry", "landfill"):
        return "rock"
    if lu == "railway":
        return "rail"
    if am in ("parking", "marketplace") and g("surface") not in ("grass", "grass_paver", "gravel"):
        return "paved"
    if g("place") == "square" or g("area:highway") or g("highway") in ("pedestrian", "footway", "service") \
            or g("man_made") in ("pier", "bridge", "breakwater"):
        return "paved"
    if lu in ("industrial", "commercial", "retail", "construction", "garages", "depot", "port"):
        return "paved"
    if lei in ("pitch", "track", "stadium") and g("surface") in _PAVED_SURFACES:
        return "paved"
    if lu in ("farmland", "vineyard", "orchard", "greenhouse_horticulture", "plant_nursery"):
        return "field"
    if lu in ("grass", "meadow", "village_green", "recreation_ground", "allotments", "cemetery", "residential",
              "farmyard", "brownfield", "greenfield") or lei in ("park", "garden", "pitch", "playground",
                                                                  "golf_course", "dog_park", "common") \
            or nat in ("grassland", "heath", "scrub", "wetland") or am == "grave_yard":
        return "ground"
    return None


ROAD_WIDTH = {"motorway": 24.0, "trunk": 18.0, "primary": 12.0, "secondary": 10.0, "tertiary": 8.0,
              "motorway_link": 7.0, "trunk_link": 7.0, "primary_link": 7.0, "secondary_link": 6.0,
              "tertiary_link": 6.0, "unclassified": 6.0, "residential": 6.0, "living_street": 5.0, "road": 6.0,
              "busway": 6.0, "service": 4.0}
PATH_WIDTH = {"pedestrian": 5.0, "track": 3.0, "footway": 2.0, "cycleway": 2.0, "bridleway": 2.0, "path": 1.5,
              "steps": 2.0}
WATERWAY_WIDTH = {"river": 20.0, "canal": 15.0, "stream": 3.0, "ditch": 1.5, "drain": 1.5}
RAIL_WIDTH = {"rail": 4.0, "light_rail": 3.5, "tram": 3.0, "subway": 3.5, "narrow_gauge": 3.0, "preserved": 4.0,
              "monorail": 2.0}


def line_class(tags: Dict[str, str]) -> Optional[Tuple[str, float, bool]]:
    """(class, width in metres, on a bridge) of a line, or None (tunnels, culverts, private driveways ...)."""
    g = tags.get
    if g("tunnel") in ("yes", "culvert", "building_passage") or g("covered") == "yes" or g("area") == "yes":
        return None
    if g("location") == "underground" or (g("layer") or "0").startswith("-"):
        return None
    bridge = g("bridge") not in (None, "no")
    width = metres(g("width")) or metres(g("est_width"))
    hw = g("highway")
    if hw:
        if g("service") in ("parking_aisle", "driveway", "drive-through") or hw in ("proposed", "construction",
                                                                                   "platform", "corridor"):
            return None
        if hw in ROAD_WIDTH:
            lanes = number(g("lanes"))
            w = width or (max(ROAD_WIDTH[hw], lanes * 3.25) if lanes else ROAD_WIDTH[hw])
            return "road", w, bridge
        if hw in PATH_WIDTH:
            return ("paved" if hw == "pedestrian" else "path"), width or PATH_WIDTH[hw], bridge
        return None
    rw = g("railway")
    if rw in RAIL_WIDTH:
        return "rail", width or RAIL_WIDTH[rw], bridge
    ww = g("waterway")
    if ww in WATERWAY_WIDTH:
        return "water", width or WATERWAY_WIDTH[ww], False
    return None
