"""The heightfield mesh: closed edge to edge (no T-junctions), the right volume, the colours where they belong."""
import numpy as np
import trimesh

from twinbrick.mesh import _ragged, heightfield_mesh, is_closed, tree_meshes


def test_ragged():
    assert _ragged(np.array([5, 0, 9]), np.array([2, 0, 3])).tolist() == [5, 6, 9, 10, 11]
    assert _ragged(np.array([3, 7]), np.array([0, 2])).tolist() == [7, 8]


def test_random_heightfield_is_closed():
    rng = np.random.default_rng(3)
    n = 50
    region = np.ones((n, n), bool)
    region[:5, :7] = False
    region[30:, 40:] = False
    ground = 3.2 + np.round(rng.random((n, n)) * 5) * 3.2
    obj = rng.random((n, n)) < 0.3
    top = ground + np.where(obj, rng.random((n, n)) * 20, 0)
    c = np.zeros((n, n, 3)) + 0.5
    m = heightfield_mesh(region, top, ground, obj, c, c, c, (0.1, 0.1, 0.1), 1.0)
    assert is_closed(m) and m.is_winding_consistent
    assert np.isclose(m.volume, top[region].sum())


def test_colours_of_roof_wall_ground_and_base():
    region = np.ones((3, 3), bool)
    ground = np.full((3, 3), 3.2)
    top = ground.copy()
    top[1, 1] = 20.0
    obj = np.zeros((3, 3), bool)
    obj[1, 1] = True
    green, red, white, grey = (0, 0.5, 0), (0.8, 0, 0), (1, 1, 1), (0.4, 0.4, 0.4)
    top_rgb = np.tile(green, (3, 3, 1)).astype(float)
    top_rgb[1, 1] = red
    side = np.tile(green, (3, 3, 1)).astype(float)
    side[1, 1] = white
    m = heightfield_mesh(region, top, ground, obj, top_rgb, side, np.tile(green, (3, 3, 1)).astype(float), grey, 8.0)
    cols = m.visual.face_colors[:, :3]
    up = m.face_normals[:, 2] > 0.9
    down = m.face_normals[:, 2] < -0.9
    side_f = ~up & ~down
    hi = m.triangles_center[:, 2] > 10
    assert (cols[up & hi] == [204, 0, 0]).all()                  # the roof
    assert (cols[side_f & hi] == [255, 255, 255]).all()           # its walls
    assert (cols[down] == [102, 102, 102]).all()                  # the bottom
    assert is_closed(m)


def test_trees_are_closed_and_stand_on_a_stud():
    trees = [dict(i=2, j=3, ground=10.0, bottom=14.0, top=20.0, r=4.0, colour="tree")]
    rgb = {"tree": (0, 0.3, 0.1), "trunk": (0.35, 0.16, 0.09)}
    parts = tree_meshes(trees, lambda z: z * 2.0, 8.0, 3.2, 2.0, lambda c: rgb[c])
    trunk, crown = parts
    assert all(is_closed(p) for p in parts)
    assert np.allclose(trunk.bounds[:, :2], [[16, 24], [24, 32]])     # exactly the stud (2, 3)
    assert trunk.bounds[0, 2] < 20.0 < trunk.bounds[1, 2]             # from below the ground into the crown
    assert crown.bounds[0, 2] > 28.0 - 1e-6                          # the crown above its bottom
    whole = trimesh.util.concatenate(parts)
    assert len(whole.visual.face_colors) == len(whole.faces)
