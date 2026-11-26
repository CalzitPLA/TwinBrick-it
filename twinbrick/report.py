"""The results as one HTML file: every site with an interactive 3D view of its bricks, its numbers, colours, plan,
Legolize it's picture and the command that makes it again.

    twinbrick-it report                          every site in ~/legolize_results -> ~/legolize_results/twinbrick_results.html
    twinbrick-it report DIR [DIR ...] --out F    the sites in these folders (a site.json in them or below)

The file needs nothing else: pictures are embedded (WebP), three.js r128 (MIT, twinbrick/vendor) is inlined, and the
3D view runs offline. Only links to openstreetmap.org (to see the place) leave the page, when clicked.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import re
import sys
import time
from dataclasses import fields
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from PIL import Image, ImageChops

from .site import RESULTS, SiteSpec

VENDOR = Path(__file__).resolve().parent / "vendor"
HERO = Path(__file__).resolve().parent.parent / "docs" / "images" / "burg_altena_hero.jpg"


# ------------------------------------------------------------------ collecting
def find_sites(folders: List[Path]) -> List[Path]:
    out = []
    for f in folders:
        f = Path(f).expanduser()
        if (f / "site.json").is_file():
            out.append(f)
        elif f.is_dir():
            out += sorted(p.parent for p in f.rglob("site.json"))
    seen, uniq = set(), []
    for p in out:
        if p.resolve() not in seen:
            seen.add(p.resolve())
            uniq.append(p)
    return uniq


def _img(path: Path, max_px: int = 1400, trim: bool = False, quality: int = 84) -> Optional[str]:
    if not path or not Path(path).is_file():
        return None
    im = Image.open(path).convert("RGB")
    if trim:
        bg = Image.new(im.mode, im.size, im.getpixel((2, 2)))
        box = ImageChops.difference(im, bg).convert("L").point(lambda v: 255 if v > 12 else 0).getbbox()
        if box:
            pad = 14
            im = im.crop((max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad),
                          min(im.height, box[3] + pad)))
    im.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=quality, method=5)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def _palette_name(rgb) -> str:
    from lego_engine.palette import PALETTE
    names = list(PALETTE)
    pal = np.array([PALETTE[n] for n in names])
    return names[int(np.argmin(((pal - np.asarray(rgb)) ** 2).sum(axis=1)))]


def brick_data(result: Path) -> Optional[Dict]:
    """Legolize it's result.json, compact for the viewer: [layer, i, j, w, d, kind, turn, colour] per part."""
    try:
        r = json.loads(Path(result).read_text())
    except (OSError, ValueError):
        return None
    kinds, cols = [], []
    kidx, cidx = {}, {}
    rows = []
    for n, (L, i, j, w, d) in enumerate(r["bricks"]):
        k = r["kinds"][n] if n < len(r.get("kinds", [])) else ""
        t = r["turns"][n] if n < len(r.get("turns", [])) else 0
        c = tuple(round(x, 3) for x in (r["colours"][n] if n < len(r.get("colours", [])) else (0.6, 0.6, 0.6)))
        if k not in kidx:
            kidx[k] = len(kinds)
            kinds.append(k)
        if c not in cidx:
            cidx[c] = len(cols)
            cols.append(c)
        rows.append([L, i, j, w, d, kidx[k], t, cidx[c]])
    rows.sort(key=lambda x: x[0])
    cell = r.get("grid", {}).get("cell", [8.0, 8.0, 3.2])
    counts = {}
    for row in rows:
        counts[row[7]] = counts.get(row[7], 0) + 1
    colours = [dict(hex="#%02x%02x%02x" % tuple(int(round(v * 255)) for v in c), name=_palette_name(c),
                    parts=counts.get(n, 0)) for n, c in enumerate(cols)]
    st = r.get("stats", {})
    return dict(cell=cell, kinds=kinds, colours=colours, b=rows, floating=r.get("floating_bricks", []),
                stats={k: st.get(k) for k in ("bricks", "shaped", "layers", "groups", "floating", "stable",
                                               "size_mm", "counts", "details")},
                settings={k: r.get("settings", {}).get(k) for k in ("style", "shapes", "details", "unit")})


def title(name: str, rec: Dict) -> str:
    """A readable title: "heidelberg_old_town" -> "Heidelberg old town"; a name made of coordinates -> the shape at
    the place."""
    if re.match(r"^-?\d+\.\d+[NS]_-?\d+\.\d+[EW]", name):
        shape = (rec.get("spec") or {}).get("shape", "site")
        return f"{shape.capitalize()} at {rec.get('place', {}).get('text', name)}"
    name = name.replace("_", " ").strip()
    return name[:1].upper() + name[1:]


def spec_command(spec: Dict) -> str:
    from .cli import spec_args
    known = {f.name for f in fields(SiteSpec)}
    d = {k: v for k, v in spec.items() if k in known}
    for k in ("size", "corner"):
        if d.get(k) is not None:
            d[k] = tuple(d[k])
    d["tiles"] = tuple(tuple(t) for t in d.get("tiles") or [(0, 0)])
    d["points"] = tuple(tuple(p) for p in d.get("points") or ())
    args = spec_args(SiteSpec(**d), "bricky")
    return "twinbrick-it cli " + " ".join(a if " " not in a and ";" not in a else f'"{a}"' for a in args)


def collect(folder: Path) -> Dict:
    rec = json.loads((folder / "site.json").read_text())
    tiles = []
    for t in rec.get("tiles", []):
        files = t.get("files", {})
        plan = Path(files["plan"]) if files.get("plan") else None
        if plan is not None and not plan.is_file():
            plan = folder / plan.name
        mesh = Path(files.get("mesh", "site.ply"))
        stem = mesh.stem if mesh.stem != "site" else ""
        bdir = folder / ("bricks" if not stem else f"{stem}_bricks")
        tiles.append(dict(tile=t["tile"], studs=t["studs"], base=t["base_studs"], size_cm=t["size_cm"],
                          height=t.get("height_plates"), counts=t.get("counts", {}),
                          plan=_img(plan, 1100) if plan else None,
                          render=_img(bdir / "render.png", 1300, trim=True),
                          bricks=brick_data(bdir / "result.json")))
    name = title(rec.get("name") or folder.parent.name, rec)
    try:
        cmd = spec_command(rec.get("spec", {}))
    except Exception:  # noqa: BLE001 - an old or hand-made site.json: no command
        cmd = ""
    return dict(name=name, folder=str(folder), rec=rec, tiles=tiles, cmd=cmd,
                overview=_img(folder / "overview_plan.png", 1100))


# ------------------------------------------------------------------ the page
CSS = """
:root{--ink:#1d2327;--soft:#5b6670;--line:#dde2e6;--bg:#f5f6f4;--card:#fff;--accent:#c4281c}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
color:var(--ink);background:var(--bg)}
header{position:relative;height:300px;overflow:hidden;background:#cfd3d0}
header img{width:100%;height:100%;object-fit:cover;object-position:50% 38%}
header .t{position:absolute;left:0;right:0;bottom:0;padding:28px 40px;color:#fff;
background:linear-gradient(transparent,rgba(0,0,0,.62))}
header h1{margin:0;font-size:34px;letter-spacing:.2px}header p{margin:4px 0 0;opacity:.92}
nav{position:sticky;top:0;z-index:5;background:rgba(245,246,244,.94);backdrop-filter:blur(6px);
border-bottom:1px solid var(--line);padding:10px 40px;display:flex;gap:8px;flex-wrap:wrap}
nav a{color:var(--ink);text-decoration:none;padding:4px 12px;border-radius:999px;border:1px solid var(--line);
background:#fff;font-size:13px}nav a:hover{border-color:var(--ink)}
main{max-width:1320px;margin:0 auto;padding:24px 40px 60px}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;margin:26px 0;padding:24px 26px}
section h2{margin:0 0 2px;font-size:24px}.sub{color:var(--soft);margin:0 0 16px}
.sub a{color:var(--soft)}
.grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:22px}
@media(max-width:980px){.grid{grid-template-columns:1fr}main,nav{padding-left:16px;padding-right:16px}}
.viewer{position:relative;aspect-ratio:16/11;background:radial-gradient(#fbfbfa,#e7e9e6);border-radius:10px;
overflow:hidden;border:1px solid var(--line)}
.viewer canvas{display:block;width:100%;height:100%}
.viewer .hint{position:absolute;left:12px;top:10px;font-size:12px;color:var(--soft)}
.controls{display:flex;align-items:center;gap:12px;margin:10px 0 0;font-size:13px;color:var(--soft)}
.controls input[type=range]{flex:1;accent-color:var(--accent)}
.controls button{border:1px solid var(--line);background:#fff;border-radius:8px;padding:4px 10px;cursor:pointer;
font-size:13px}.controls button:hover{border-color:var(--ink)}
table{border-collapse:collapse;width:100%;font-size:14px}td{padding:5px 0;border-bottom:1px solid var(--line);
vertical-align:top}td:first-child{color:var(--soft);width:42%;padding-right:12px}
.big{display:flex;gap:22px;margin:0 0 14px}.big div{flex:1}.big b{display:block;font-size:26px;line-height:1.1}
.big span{color:var(--soft);font-size:12px}
.sw{display:flex;flex-wrap:wrap;gap:6px 12px;margin:10px 0 0}.sw span{display:inline-flex;align-items:center;gap:6px;
font-size:12.5px}.sw i{width:14px;height:14px;border-radius:3px;border:1px solid rgba(0,0,0,.18);display:inline-block}
.ok{color:#1d7a3a;font-weight:600}.warn{color:#b26b00;font-weight:600}
.pics{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:20px}
.pics figure{margin:0}.pics img{width:100%;border-radius:8px;border:1px solid var(--line);cursor:zoom-in;
background:#fafafa}.pics figcaption{font-size:12.5px;color:var(--soft);margin-top:4px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-top:16px}
.tiles figure{margin:0}.tiles img{width:100%;border-radius:6px;border:1px solid var(--line);cursor:zoom-in}
code{display:block;white-space:pre-wrap;word-break:break-all;background:#f1f3f2;border-radius:8px;padding:10px 12px;
font-size:12.5px;margin-top:14px;color:#2b3338}
.lb{position:fixed;inset:0;background:rgba(15,18,20,.88);display:none;align-items:center;justify-content:center;
z-index:20;cursor:zoom-out}.lb img{max-width:94vw;max-height:92vh;border-radius:8px}
footer{color:var(--soft);font-size:12.5px;max-width:1320px;margin:0 auto;padding:0 40px 40px}
"""

JS = r"""
const LIP = 1/6;
function prismGeom(L, W, rot, cell){
  // a slope in its own frame: u (fall) 0..L, v 0..W, t 0..1; turned into the footprint, in three's frame
  const prof = L >= 2 ? [[0,0],[L,0],[L,LIP],[1,1],[0,1]] : [[0,0],[1,0],[1,LIP],[0,1]];
  const fp = (u, v) => rot === 0 ? [u, v] : rot === 1 ? [W - v, u] : rot === 2 ? [L - u, W - v] : [v, L - u];
  const P = (u, v, t) => { const [x, y] = fp(u, v); return [x * cell[0], t * cell[2], -y * cell[1]]; };
  const pos = [];
  const tri = (a, b, c) => pos.push(...a, ...b, ...c);
  const n = prof.length;
  for (let k = 1; k < n - 1; k++){                       // the two end caps (convex fans)
    tri(P(prof[0][0], 0, prof[0][1]), P(prof[k][0], 0, prof[k][1]), P(prof[k+1][0], 0, prof[k+1][1]));
    tri(P(prof[0][0], W, prof[0][1]), P(prof[k+1][0], W, prof[k+1][1]), P(prof[k][0], W, prof[k][1]));
  }
  for (let k = 0; k < n; k++){                           // the sides
    const a = prof[k], b = prof[(k+1) % n];
    tri(P(a[0],0,a[1]), P(b[0],0,b[1]), P(b[0],W,b[1])); tri(P(a[0],0,a[1]), P(b[0],W,b[1]), P(a[0],W,a[1]));
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.computeVertexNormals();
  return g;
}
function studCells(kind, rot, w, d){
  // the cells of a part that carry a stud on top (none for tiles; a slope's high row only)
  const out = [];
  if (kind.startsWith('tile')) return out;
  if (kind.startsWith('slope')){
    const L = +kind.split('_')[1].split('x')[0];
    if (L < 2) return out;
    for (let x = 0; x < w; x++) for (let y = 0; y < d; y++){
      const hi = rot === 0 ? x === 0 : rot === 1 ? y === 0 : rot === 2 ? x === w - 1 : y === d - 1;
      if (hi) out.push([x, y]);
    }
    return out;
  }
  for (let x = 0; x < w; x++) for (let y = 0; y < d; y++) out.push([x, y]);
  return out;
}
function Viewer(el, data){
  const cell = data.cell, rows = data.b;
  const canvas = el.querySelector('canvas');
  const renderer = new THREE.WebGLRenderer({canvas, antialias: true, alpha: true});
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, 1, 50000);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f94, 0.72));
  const sun = new THREE.DirectionalLight(0xffffff, 0.78); sun.position.set(-0.6, 1.4, 0.9); scene.add(sun);
  const fill = new THREE.DirectionalLight(0xffffff, 0.22); fill.position.set(0.8, 0.4, -1); scene.add(fill);
  const mat = new THREE.MeshLambertMaterial({side: THREE.DoubleSide});
  const cols = data.colours.map(c => new THREE.Color(c.hex));
  // occupancy (for studs): which part fills each cell, its layer
  const occ = new Map(); const key = (l, i, j) => l * 1e8 + i * 1e4 + j;
  rows.forEach(r => { for (let x = 0; x < r[3]; x++) for (let y = 0; y < r[4]; y++) occ.set(key(r[0], r[1]+x, r[2]+y), 1); });
  // groups of instances: plain boxes, rounds, slopes per (kind, turn)
  const groups = new Map();
  const add = (gk, make, row) => { if (!groups.has(gk)) groups.set(gk, {make, rows: []}); groups.get(gk).rows.push(row); };
  rows.forEach(r => {
    const k = data.kinds[r[5]];
    if (k.startsWith('slope') || k.startsWith('islope')) add(k + '/' + r[6], 'slope', r);
    else if (k === 'round_1') add('round', 'round', r);
    else add('box', 'box', r);
  });
  const meshes = [];
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
  const gap = 0.12;
  groups.forEach((g, gk) => {
    let geom;
    if (g.make === 'box') geom = new THREE.BoxGeometry(1, 1, 1);
    else if (g.make === 'round') geom = new THREE.CylinderGeometry(3.9, 3.9, cell[2] - gap, 18);
    else { const k = gk.split('/')[0], rot = +gk.split('/')[1]; const [L, W] = k.split('_')[1].split('x').map(Number);
           geom = prismGeom(L, W, rot, cell); }
    const im = new THREE.InstancedMesh(geom, mat, g.rows.length);
    g.rows.forEach((r, n) => {
      const [l, i, j, w, d] = r;
      if (g.make === 'box'){
        s.set(w * cell[0] - gap, cell[2] - gap * 0.6, d * cell[1] - gap);
        p.set((i + w / 2) * cell[0], (l + 0.5) * cell[2], -(j + d / 2) * cell[1]);
      } else if (g.make === 'round'){
        s.set(1, 1, 1); p.set((i + 0.5) * cell[0], (l + 0.5) * cell[2], -(j + 0.5) * cell[1]);
      } else { s.set(1, 1, 1); p.set(i * cell[0], l * cell[2], -j * cell[1]); }
      m4.compose(p, q, s); im.setMatrixAt(n, m4); im.setColorAt(n, cols[r[7]]);
    });
    im.userData.layers = g.rows.map(r => r[0]);
    scene.add(im); meshes.push(im);
  });
  // studs: where the cell above is empty (or not built yet)
  const studs = [];
  rows.forEach(r => {
    const k = data.kinds[r[5]];
    studCells(k, r[6], r[3], r[4]).forEach(([x, y]) => {
      const i = r[1] + x, j = r[2] + y;
      let above = Infinity;
      for (let l = r[0] + 1; l < r[0] + 400; l++){ if (occ.has(key(l, i, j))) { above = l; break; } if (l > r[0] + 1) break; }
      studs.push([r[0], i, j, above, r[7]]);
    });
  });
  const studMesh = new THREE.InstancedMesh(new THREE.CylinderGeometry(2.4, 2.4, 1.7, 14), mat, Math.max(1, studs.length));
  scene.add(studMesh);
  const maxLayer = rows.length ? rows[rows.length - 1][0] : 0;
  function upTo(N){
    meshes.forEach(im => { const L = im.userData.layers; let c = 0; while (c < L.length && L[c] <= N) c++; im.count = c; });
    let c = 0;
    studs.forEach(st => {
      if (st[0] <= N && !(st[3] <= N)){
        p.set((st[1] + 0.5) * cell[0], (st[0] + 1) * cell[2] + 0.85, -(st[2] + 0.5) * cell[1]); s.set(1, 1, 1);
        m4.compose(p, q, s); studMesh.setMatrixAt(c, m4); studMesh.setColorAt(c, cols[st[4]]); c++;
      }
    });
    studMesh.count = c; studMesh.instanceMatrix.needsUpdate = true;
    if (studMesh.instanceColor) studMesh.instanceColor.needsUpdate = true;
    draw();
  }
  // camera
  const box = new THREE.Box3(); meshes.forEach(m => { m.computeBoundingBox && 0; });
  let xmax = 0, zmin = 0;
  rows.forEach(r => { xmax = Math.max(xmax, (r[1] + r[3]) * cell[0]); zmin = Math.min(zmin, -(r[2] + r[4]) * cell[1]); });
  const centre = new THREE.Vector3(xmax / 2, (maxLayer + 1) * cell[2] * 0.35, zmin / 2);
  const radius = Math.hypot(xmax, -zmin, (maxLayer + 1) * cell[2]) * 0.5;
  const controls = new THREE.OrbitControls(camera, canvas);
  controls.enableDamping = true; controls.dampingFactor = 0.12;
  function view(kind){
    const dist = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * (kind === 'top' ? 0.95 : 0.78);
    const dir = kind === 'top' ? new THREE.Vector3(0.0001, 1, 0.0001) : new THREE.Vector3(0.62, 0.62, 0.78);
    camera.position.copy(centre).addScaledVector(dir.normalize(), dist);
    controls.target.copy(centre); controls.update(); draw();
  }
  function size(){ const w = el.clientWidth, h = el.clientHeight; renderer.setSize(w, h, false);
                   camera.aspect = w / h; camera.updateProjectionMatrix(); draw(); }
  let pending = false;
  function draw(){ if (pending) return; pending = true; requestAnimationFrame(() => { pending = false;
                   controls.update(); renderer.render(scene, camera); }); }
  controls.addEventListener('change', draw);
  new ResizeObserver(size).observe(el);
  const slider = el.parentElement.querySelector('input[type=range]');
  const lab = el.parentElement.querySelector('.lay');
  slider.max = maxLayer; slider.value = maxLayer;
  slider.oninput = () => { lab.textContent = `layer ${+slider.value + 1} of ${maxLayer + 1}`; upTo(+slider.value); };
  lab.textContent = `layer ${maxLayer + 1} of ${maxLayer + 1}`;
  el.parentElement.querySelector('.iso').onclick = () => view('iso');
  el.parentElement.querySelector('.top').onclick = () => view('top');
  el.parentElement.querySelector('.play').onclick = () => {
    let N = 0; const step = () => { slider.value = N; slider.oninput(); if (N++ < maxLayer) setTimeout(step, 70); }; step(); };
  size(); upTo(maxLayer); view('iso');
}
const started = new Set(), queue = [];
function next(){                                        // one 3D view at a time (slow machines, many sites)
  const el = queue.shift(); if (!el) return;
  try { Viewer(el, JSON.parse(document.getElementById(el.dataset.src).textContent)); }
  catch (err) { el.querySelector('.hint').textContent = '3D view not available: ' + err.message; }
  setTimeout(next, 60);
}
const io = new IntersectionObserver(es => es.forEach(e => {
  if (e.isIntersecting && !started.has(e.target)){ started.add(e.target); queue.push(e.target);
    if (queue.length === 1) setTimeout(next, 0); }
}), {rootMargin: '200px'});
document.querySelectorAll('.viewer[data-src]').forEach(v => io.observe(v));
const lb = document.querySelector('.lb');
document.querySelectorAll('.pics img, .tiles img').forEach(im => im.onclick = () => {
  lb.querySelector('img').src = im.src; lb.style.display = 'flex'; });
lb.onclick = () => lb.style.display = 'none';
"""


def _e(x) -> str:
    return html.escape(str(x))


def _pretty_kind(counts: Dict) -> str:
    groups = {"bricks and plates": 0, "slopes": 0, "tiles": 0, "round 1x1": 0, "other": 0}
    for k, n in (counts or {}).items():
        if k.startswith("slope") or k.startswith("curve"):
            groups["slopes"] += n
        elif k.startswith("tile"):
            groups["tiles"] += n
        elif k.startswith("round"):
            groups["round 1x1"] += n
        elif "x" in k and k.replace("x", "").isdigit():
            groups["bricks and plates"] += n
        else:
            groups["other"] += n
    return ", ".join(f"{n:,} {k}" for k, n in groups.items() if n)


def section(site: Dict, idx: int) -> str:
    rec, sc = site["rec"], site["rec"].get("scale", {})
    place = rec.get("place", {})
    lat, lon = place.get("lat"), place.get("lon")
    osm = (f'<a href="https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=17/{lat:.5f}/{lon:.5f}" '
           f'target="_blank" rel="noopener">{_e(place.get("text", ""))}</a>') if lat is not None else ""
    made = rec.get("made", "")
    parts = [f'<section id="s{idx}"><h2>{_e(site["name"])}</h2>',
             f'<p class="sub">{osm} · {_e(rec.get("shape", ""))} · {_e(sc.get("ratio", ""))} · made {_e(made)}</p>']
    t0 = site["tiles"][0] if site["tiles"] else None
    multi = len(site["tiles"]) > 1
    b = t0["bricks"] if t0 else None
    if b and not multi:
        st = b["stats"]
        holds = (f'<span class="ok">one piece, nothing floating</span>' if st.get("groups") == 1
                 and not st.get("floating") else
                 f'<span class="warn">{st.get("groups")} groups, {st.get("floating")} parts floating</span>')
        size = st.get("size_mm") or [0, 0, 0]
        look = b["settings"]
        look_txt = ("artsy" if look.get("style") == "artsy" else "detailed" if look.get("shapes") else "bricky")
        parts.append('<div class="grid"><div>')
        parts.append(f'<div class="viewer" data-src="d{idx}"><canvas></canvas><div class="hint">drag to turn · '
                     f'wheel to zoom · right-drag to move</div></div>')
        parts.append('<div class="controls"><button class="iso">3/4 view</button><button class="top">from above'
                     '</button><button class="play">build up</button><input type="range" min="0" step="1">'
                     '<span class="lay"></span></div></div><div>')
        parts.append(f'<div class="big"><div><b>{st.get("bricks", 0):,}</b><span>parts</span></div>'
                     f'<div><b>{size[0] / 10:.1f} × {size[1] / 10:.1f}</b><span>cm base</span></div>'
                     f'<div><b>{size[2] / 10:.1f}</b><span>cm high ({st.get("layers")} plates)</span></div></div>')
        rows = [("Holds together", holds),
                ("Parts", _e(_pretty_kind(st.get("counts")))),
                ("Look", _e(look_txt)),
                ("Studs", f'{t0["studs"][0]} × {t0["studs"][1]} ({t0["base"]:,} on the base)'),
                ("1 stud / 1 plate", f'{sc.get("stud_m", 0):.2f} m / {sc.get("plate_m", 0):.2f} m on the ground'
                                     + (f', heights × {sc.get("vertical")}' if sc.get("vertical", 1) != 1 else "")),
                ("Faces", _e(f'{rec.get("orientation", {}).get("back_true_bearing", 0):.1f}° (the back edge, true)')),
                ("Contents", _e(", ".join(f"{v} {k.replace('_', ' ')}" for k, v in t0["counts"].items()
                                          if k in ("buildings", "trees", "water_studs", "sea_studs") and v))),
                ("Data", _e("; ".join(f"{k}: {v}" for k, v in rec.get("sources", {}).items())))]
        parts.append("<table>" + "".join(f"<tr><td>{a}</td><td>{v}</td></tr>" for a, v in rows) + "</table>")
        parts.append('<div class="sw">' + "".join(
            f'<span><i style="background:{c["hex"]}"></i>{_e(c["name"].replace("_", " "))} · {c["parts"]}</span>'
            for c in sorted(b["colours"], key=lambda c: -c["parts"])) + "</div>")
        parts.append("</div></div>")
        parts.append(f'<script type="application/json" id="d{idx}">{json.dumps(b, separators=(",", ":"))}</script>')
    pics = []
    if multi and site.get("overview"):
        pics.append((site["overview"], f"{len(site['tiles'])} tiles in place - the seams stud for stud"))
    if t0 and t0.get("plan") and not multi:
        pics.append((t0["plan"], "the plan: colours per stud, buildings and trees shaded"))
    if t0 and t0.get("render") and not multi:
        pics.append((t0["render"], "Legolize it's picture: the input translucent over the bricks"))
    if pics:
        parts.append('<div class="pics">' + "".join(f'<figure><img src="{src}" alt="{_e(cap)}" loading="lazy">'
                                                     f'<figcaption>{_e(cap)}</figcaption></figure>'
                                                     for src, cap in pics) + "</div>")
    if multi:
        parts.append('<div class="tiles">' + "".join(
            f'<figure><img src="{t["plan"]}" alt="tile {t["tile"][0]},{t["tile"][1]}" loading="lazy">'
            f'<figcaption>tile {t["tile"][0]},{t["tile"][1]} · {t["studs"][0]} × {t["studs"][1]} studs · '
            f'{t["counts"].get("buildings", 0)} buildings</figcaption></figure>'
            for t in site["tiles"] if t.get("plan")) + "</div>")
    if site.get("cmd"):
        parts.append(f"<code>{_e(site['cmd'])}</code>")
    parts.append("</section>")
    return "\n".join(parts)


def write(sites: List[Dict], out: Path) -> Path:
    three = (VENDOR / "three.min.js").read_text()
    orbit = (VENDOR / "OrbitControls.js").read_text()
    hero = _img(HERO, 2000, quality=80) if HERO.exists() else None
    attributions = sorted({a for s in sites for a in s["rec"].get("attribution", [])})
    nav = "".join(f'<a href="#s{i}">{_e(s["name"])}</a>' for i, s in enumerate(sites))
    body = "\n".join(section(s, i) for i, s in enumerate(sites))
    total = sum((t["bricks"] or {}).get("stats", {}).get("bricks", 0) or 0 for s in sites for t in s["tiles"])
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>TwinBrick it - results</title>
<style>{CSS}</style></head><body>
<header>{f'<img src="{hero}" alt="Burg Altena in bricks">' if hero else ''}<div class="t"><h1>TwinBrick it - results</h1>
<p>{len(sites)} sites · {total:,} parts · {time.strftime("%Y-%m-%d %H:%M")}</p></div></header>
<nav>{nav}</nav><main>{body}</main>
<footer><p><b>Data:</b> {" · ".join(_e(a) for a in attributions)}</p>
<p>Made with TwinBrick it (github.com/CalzitPLA/TwinBrick-it) and Legolize it; 3D view: three.js (MIT). The 3D view
draws plain parts as blocks, slopes as wedges, round 1x1 bricks as cylinders and studs where nothing covers them.</p>
</footer><div class="lb"><img alt=""></div>
<script>{three}</script><script>{orbit}</script><script>{JS}</script></body></html>"""
    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="twinbrick-it report", description=__doc__.split("\n\n")[0])
    ap.add_argument("folders", nargs="*", help=f"result folders (default: every site in {RESULTS})")
    ap.add_argument("--out", default=None, help=f"the HTML file (default {RESULTS}/twinbrick_results.html)")
    ap.add_argument("--open", action="store_true", help="open it in the browser")
    a = ap.parse_args(argv)
    folders = [Path(f) for f in a.folders] or [RESULTS]
    found = find_sites(folders)
    if not found:
        print("no site.json found in " + ", ".join(map(str, folders)), file=sys.stderr)
        return 2
    sites = []
    for f in found:
        try:
            sites.append(collect(f))
            print(f"  {f}", file=sys.stderr)
        except (OSError, ValueError, KeyError) as ex:
            print(f"  skipped {f}: {ex}", file=sys.stderr)
    out = write(sites, Path(a.out) if a.out else RESULTS / "twinbrick_results.html")
    print(out)
    if a.open:
        import webbrowser
        webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
