"""A raster of heights as one closed surface with a colour per face - the input the engine likes best.

One closed surface, not buildings overlapping a terrain block: the engine colours each brick from the nearest face of
its input, so hidden faces must not exist (the AR campus model's lesson). Every raster cell is a column from z = 0 to
its top; side faces go where a neighbour is lower (or outside), split at the cell's ground: below it the ground's
colour (a terrain step) or the base colour (the model's outer side), above it the object's side colour (a wall, a tree)
- or the roof's colour where the neighbour is the same kind of object, so a step between two roof levels stays roof.
"""
from __future__ import annotations

import numpy as np
import trimesh


def heightfield_mesh(region: np.ndarray, top: np.ndarray, ground: np.ndarray, obj: np.ndarray,
                     top_rgb: np.ndarray, obj_side_rgb: np.ndarray, ground_side_rgb: np.ndarray,
                     base_rgb, cell: float) -> trimesh.Trimesh:
    """region (nu, nv) bool; top, ground (nu, nv) mm above the model's bottom (ground <= top); obj (nu, nv) bool: the
    part between ground and top is an object (building, tree); colours (nu, nv, 3) in 0..1; cell: raster cell, mm.
    Returns a watertight mesh in mm (x along u, y along v) with face colours."""
    nu, nv = region.shape
    V, F, C = [], [], []
    count = [0]

    def quads(p0, p1, p2, p3, rgb):
        n = len(p0)
        if n == 0:
            return
        k = count[0] + 4 * np.arange(n)[:, None]
        V.append(np.stack([p0, p1, p2, p3], axis=1).reshape(-1, 3))
        F.append(np.concatenate([k + [0, 1, 2], k + [0, 2, 3]]))
        C.append(np.concatenate([rgb, rgb]))
        count[0] += 4 * n

    I, J = np.nonzero(region)
    x0, y0, z = I * cell, J * cell, top[I, J]
    x1, y1 = x0 + cell, y0 + cell
    zero = np.zeros_like(z)
    quads(np.c_[x0, y0, z], np.c_[x1, y0, z], np.c_[x1, y1, z], np.c_[x0, y1, z], top_rgb[I, J])
    base = np.tile(np.asarray(base_rgb, float), (len(I), 1))
    quads(np.c_[x0, y0, zero], np.c_[x0, y1, zero], np.c_[x1, y1, zero], np.c_[x1, y0, zero], base)

    pad = lambda a, v: np.pad(a, [(1, 1), (1, 1)] + [(0, 0)] * (a.ndim - 2), constant_values=v)
    regP, topP, objP = pad(region, False), pad(top, 0.0), pad(obj, False)
    g, isobj = ground[I, J], obj[I, J]
    sides = []
    for di, dj, (ax, ay, bx, by) in ((1, 0, (x1, y0, x1, y1)), (-1, 0, (x0, y1, x0, y0)),
                                     (0, 1, (x1, y1, x0, y1)), (0, -1, (x0, y0, x1, y0))):
        ins = regP[I + 1 + di, J + 1 + dj]
        lo = np.where(ins, topP[I + 1 + di, J + 1 + dj], 0.0)
        nb_obj = objP[I + 1 + di, J + 1 + dj]
        below = np.where(ins[:, None], ground_side_rgb[I, J], base)
        upper = np.where((ins & nb_obj)[:, None], top_rgb[I, J], obj_side_rgb[I, J])
        spans = ((~isobj, lo, z, below),
                 (isobj, lo, np.minimum(g, z), below),
                 (isobj, np.maximum(lo, g), z, upper))
        for m, za, zb, rgb in spans:
            m = m & (zb > za)
            sides.append((ax[m], ay[m], bx[m], by[m], za[m], zb[m], rgb[m]))
    _side_faces(sides, cell, quads, V, F, C, count)
    verts, faces = np.concatenate(V), np.concatenate(F)
    cols = np.clip(np.round(np.concatenate(C) * 255), 0, 255).astype(np.uint8)
    mesh = trimesh.Trimesh(verts, faces, process=False)
    mesh.visual.face_colors = np.c_[cols, np.full(len(cols), 255, np.uint8)]
    mesh.merge_vertices()
    return mesh


def _ragged(starts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """concatenate(arange(s, s + c) for s, c in zip(starts, counts)), vectorised."""
    counts = np.asarray(counts, np.int64)
    if counts.sum() == 0:
        return np.zeros(0, np.int64)
    nz = counts > 0
    s, c = np.asarray(starts, np.int64)[nz], counts[nz]
    e = np.cumsum(c)
    out = np.ones(e[-1], np.int64)
    out[0] = s[0]
    out[e[:-1]] = s[1:] - (s[:-1] + c[:-1] - 1)
    return np.cumsum(out)


def _side_faces(sides, cell, quads, V, F, C, count):
    """The side faces, split wherever another face has a corner on their vertical edges: no T-junctions, the mesh is
    closed edge to edge (for slicers as well as for the engine's winding numbers). Vectorised: a face with extra
    corners becomes a strip of triangles up its two edges, always taking the lower next corner."""
    ax, ay, bx, by, za, zb = (np.concatenate([s[k] for s in sides]) for k in range(6))
    rgb = np.concatenate([s[6] for s in sides])
    if not len(za):
        return
    width = np.int64(np.round(max(ax.max(), bx.max()) / cell)) + 2
    code = lambda x, y: np.round(x / cell).astype(np.int64) * width + np.round(y / cell).astype(np.int64)
    ca, cb = code(ax, ay), code(bx, by)
    levels, inv = np.unique(np.concatenate([za, zb]), return_inverse=True)    # exact heights, ranked
    ra, rb = inv[:len(za)].astype(np.int64), inv[len(za):].astype(np.int64)
    NL = np.int64(len(levels))
    U = np.unique(np.concatenate([ca * NL + ra, ca * NL + rb, cb * NL + ra, cb * NL + rb]))
    Uh = levels[U % NL]
    la0, la1 = np.searchsorted(U, ca * NL + ra, "right"), np.searchsorted(U, ca * NL + rb, "left")
    lb0, lb1 = np.searchsorted(U, cb * NL + ra, "right"), np.searchsorted(U, cb * NL + rb, "left")
    plain = (la1 <= la0) & (lb1 <= lb0)
    p = plain
    quads(np.c_[ax[p], ay[p], za[p]], np.c_[bx[p], by[p], za[p]], np.c_[bx[p], by[p], zb[p]],
          np.c_[ax[p], ay[p], zb[p]], rgb[p])
    f = np.nonzero(~plain)[0]
    if not len(f):
        return
    na, nb = la1[f] - la0[f], lb1[f] - lb0[f]                    # extra corners on each edge
    a, b = na + 2, nb + 2                                         # vertices on each edge
    off = count[0] + np.r_[0, np.cumsum(a + b)[:-1]]
    nf = len(f)
    # vertex heights: left edge [za, extras..., zb], then right edge alike
    def edge_heights(n_extra, lo):
        fid = np.repeat(np.arange(nf), n_extra + 2)
        k = _ragged(np.zeros(nf, np.int64), n_extra + 2)
        h = np.empty(len(fid))
        first, last = k == 0, k == (n_extra + 1)[fid]
        h[first], h[last] = za[f][fid[first]], zb[f][fid[last]]
        mid = ~first & ~last
        h[mid] = Uh[_ragged(lo, n_extra)]
        return fid, k, h
    fa, ka, ha = edge_heights(na, la0[f])
    fb, kb, hb = edge_heights(nb, lb0[f])
    vert = np.empty((int((a + b).sum()), 3))
    ia = off[fa] - count[0] + ka
    ib = off[fb] - count[0] + a[fb] + kb
    vert[ia] = np.c_[ax[f][fa], ay[f][fa], ha]
    vert[ib] = np.c_[bx[f][fb], by[f][fb], hb]
    # events: every vertex above the bottom one, on either edge; sorted by face, height, right edge first
    ea, eb = ka > 0, kb > 0
    ev_face = np.r_[fa[ea], fb[eb]]
    ev_h = np.r_[ha[ea], hb[eb]]
    ev_left = np.r_[np.ones(ea.sum(), bool), np.zeros(eb.sum(), bool)]
    order = np.lexsort((ev_left, ev_h, ev_face))
    ev_face, ev_left = ev_face[order], ev_left[order]
    start = np.r_[0, np.cumsum(a + b - 2)[:-1]]                   # each face's first event
    cl = np.cumsum(ev_left) - ev_left                             # left events before this one (all faces)
    cr = np.cumsum(~ev_left) - (~ev_left)
    i = cl - cl[start][ev_face]
    j = cr - cr[start][ev_face]
    o, A_ = off[ev_face], a[ev_face]
    L_i, R_j = o + i, o + A_ + j
    third = np.where(ev_left, o + i + 1, o + A_ + j + 1)
    V.append(vert)
    F.append(np.c_[L_i, R_j, third])
    C.append(rgb[f][ev_face])
    count[0] += len(vert)


def is_closed(mesh: trimesh.Trimesh) -> bool:
    """Every edge shared by an even number of faces (two; four where cells touch only along an edge)."""
    e = np.sort(mesh.edges, axis=1)
    _, cnt = np.unique(e, axis=0, return_counts=True)
    return bool(len(cnt)) and bool(np.all(cnt % 2 == 0))


def tree_meshes(trees, z_mm, stud_mm: float, plate_mm: float, mm_per_m: float, rgb) -> list:
    """Stylised trees as closed bodies next to the heightfield: a trunk exactly one stud square (from a plate below
    the stud's mean ground up into the crown's lowest plate) and an ellipsoid crown. `z_mm(z)`: model height of an
    absolute height; `rgb(name)`: a theme colour."""
    out = []
    unit = trimesh.creation.icosphere(subdivisions=2)
    for tr in trees:
        x0, y0 = tr["i"] * stud_mm, tr["j"] * stud_mm
        zg, zb, zt = z_mm(tr["ground"]) - plate_mm, z_mm(tr["bottom"]), z_mm(tr["top"])
        if zt - zb < plate_mm:
            continue
        crown = unit.copy()
        crown.apply_scale([tr["r"] * mm_per_m, tr["r"] * mm_per_m, (zt - zb) / 2])
        crown.apply_translation([x0 + stud_mm / 2, y0 + stud_mm / 2, (zb + zt) / 2])
        # a flat underside (cut at a fifth of the crown's height): its lowest plate is wide over the trunk, so the
        # crown stands on the trunk's stud instead of a narrow cap that leaves a plate empty
        cut = zb + 0.2 * (zt - zb)
        crown.vertices[:, 2] = np.maximum(crown.vertices[:, 2], cut)
        trunk = trimesh.creation.box(bounds=[[x0, y0, zg], [x0 + stud_mm, y0 + stud_mm, cut + plate_mm / 2]])
        trunk.visual.face_colors = np.r_[np.round(np.asarray(rgb("trunk")) * 255), 255].astype(np.uint8)
        crown.visual.face_colors = np.r_[np.round(np.asarray(rgb(tr["colour"])) * 255), 255].astype(np.uint8)
        out += [trunk, crown]
    return out
