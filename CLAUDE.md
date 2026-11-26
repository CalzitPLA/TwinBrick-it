# TwinBrick it - a place on earth as a brick model

GPS point + shape + size + orientation + scale -> the place's digital twin (terrain, buildings, trees, land use, roads,
water) as one closed coloured mesh per tile, whole studs wide -> Legolize it (github.com/CalzitPLA/Legolize-it) lays
the bricks. Layout like Legolize it's: `twinbrick/` (the engine), `twinbrick_gui/` (PyQt5 window with an OSM map),
`install.sh` (command `twinbrick-it`, desktop entry, check, uninstall), `tests/`, `packaging/` (the icon: the Burg
Altena brick render). Started 2026-11-26 from the brick models of NRW landmarks made earlier with Geobasis NRW data
(Schlossruine Arnsberg, Burg Altena, Wiesenkirche Soest, a university campus) - their recipe is in `twinbrick/site.py`.

Read `README.md` before exploring the code.

## Commands (from the repository root)

- Tests: `python3 -m pytest tests -q` (~5 s, offline: synthetic OSM in `tests/conftest.py`, flat ground, a stand-in
  for ssh, the window offscreen). They need Legolize it findable (installed, next to this folder, or
  TWINBRICK_LEGOLIZE).
- Command line: `python3 -m twinbrick LAT,LON --shape hexagon --size 85 --scale 500 --rotation auto --bricks detailed
  [--ring 1] [--plan] [--server HOST]`; servers: `python3 -m twinbrick.remote list|add|check|sync`.
- Window: `python3 -m twinbrick_gui [LAT,LON]`; headless look: `QT_QPA_PLATFORM=offscreen python3 -m twinbrick_gui
  LAT,LON --screenshot out.png` (the map needs the network or cached tiles). The window needs the user's display:
  start it in the background and let the user look.
- Icons: `python3 packaging/make_icons.py [render.png]`.
- Installed: `twinbrick-it`, `twinbrick-it cli ...`, `twinbrick-it server ...`, `twinbrick-it check`,
  `twinbrick-it uninstall [--purge]`. When Legolize it is installed, install.sh shares its environment; uninstall only
  removes an environment it made itself (`~/.local/share/twinbrick-it/venv`).

## Facts measured here (don't re-derive)

- UTM: Krüger's series to n^3 gave a 0.7 mm round trip (the n^4 terms of the latitude series are ~1e-10 rad); to n^4
  it is below 1e-9 degrees (a test). NRW data are EPSG:25832 everywhere in NRW (zone 32 even west of 6 E).
- NRW tiles: each product has an `index.json` (dgm1_tiff, ndom50_tiff, dom1_tiff, lod2_gml under
  opengeodata.nrw.de/produkte/geobasis/...); DGM1 names carry a year (`dgm1_32_407_5683_1_nw_2021.tif`), nDOM50 has no
  underscore after the zone (`ndom50_32407_5683_1_nw_2025.tif`, 16 MB, 0.5 m, float32, Pillow reads it), LoD2 none
  (`LoD2_32_407_5683_1_NW.gml`, 20-25 MB in towns).
- Overpass (2026-11-26): overpass-api.de often answers 502 "at capacity" at once; maps.mail.ru answered the Heidelberg
  site (1.5 MB) in 25 s while two other mirrors timed out. The client remembers the last good mirror
  (`~/.cache/twinbrick-it/overpass/last_good.txt`) and tries a few rounds; every answer is cached by the query's hash.
- The mesh: per-cell side quads leave T-junctions (a column's ground split meets a neighbour's face): 236 open edges on
  the Altena tile. Splitting every side face at every corner height its neighbours have closes it; a Python loop over
  faces took 4 min 44 s there, the vectorised strip 3 s. Edges used four times are cells touching only diagonally
  (closed, not manifold) - `is_closed` checks even counts.
- Trees: the true canopy (nDOM) at 1:750 made the Altena hill a dark-green wall; lollipops are the default up to 4 m
  per stud. Lollipop floating parts on Altena (1:500, detailed): 76 with ellipsoid crowns (their narrow bottom cap
  left a plate empty over the trunk) -> 25 with a flat underside (cut at 20 % of the crown) -> 2 with trunks from the
  stud's *mean* ground minus a plate (from the max, on slopes, the terrain's filled top lay a plate lower). Crowns
  smaller than 3/4 stud (at the tile's edge) are left out instead of shrunk. Two crown-top plates stay unsupported -
  Legolize it's greedy layout.
- Bridges in 2.5D stand on the ground: on flat ground they are flush with the banks (not lowered with the water).
- Worldwide path checked on Heidelberg (Terrain Tiles z15, OSM buildings: 482, gabled roofs from the distance to the
  outline), 972 parts at 1:1000.

## Privacy (the repository is public)

- No IP addresses, host names of the user's servers, user names, e-mail addresses (except GitHub's noreply) or home
  folders in any file: `tests/test_gui_and_repo.py::test_nothing_private_in_the_repository` scans every tracked and
  untracked file. Servers are ssh aliases only, kept in `~/.config/twinbrick-it/servers.json`; examples use HOST or
  lab-a; the tests' ssh is a stand-in.
- Requests carry a generic user agent (`legolize-it-geo/0.1`, the map `TwinBrick-it/0.1`) and nothing about the user.

## Working style

- Results are visual: look at the plan and Legolize it's render before claiming they are right.
- Results go to `~/legolize_results/<name>/<time>` unless asked otherwise.
- Commits as CalzitPLA with the GitHub noreply address (set in this repository's config); commit and push when asked.
