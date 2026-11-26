"""TwinBrick it - a place on earth as a brick model.

Give a GPS point, a shape (square, rectangle, hexagon, triangle, circle, ellipse, polygon or an OpenStreetMap outline),
its size and orientation, and a scale: TwinBrick it fetches the place's digital twin - terrain, buildings, trees,
land use, roads and water (Geobasis NRW where it has data, OpenStreetMap and worldwide elevation tiles elsewhere) - and
builds one closed coloured mesh per tile, whole studs wide, for Legolize it to lay the bricks. Shapes that tile the
plane come with neighbour tiles that fit stud for stud.
"""
from .engine import ensure as _ensure

__version__ = "0.1.0"
_ensure()
