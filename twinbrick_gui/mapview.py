"""A slippy map (OpenStreetMap tiles) to place and span the model: click sets the centre, Shift+click spans the shape
to that corner, drag pans, the wheel zooms. The tiles' outlines and their studs are drawn on top.

Tiles come from tile.openstreetmap.org with this program's own user agent, kept in a disk cache
(~/.cache/twinbrick-it/map_tiles) and shown with the attribution the tile policy asks for.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PyQt5.QtCore import QPointF, QRectF, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkDiskCache, QNetworkReply, QNetworkRequest
from PyQt5.QtWidgets import QWidget

TILE = 256
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
AGENT = b"TwinBrick-it/0.1 (desktop app; tiles cached)"
ATTRIBUTION = "© OpenStreetMap contributors"
CACHE = Path(os.environ.get("TWINBRICK_CACHE", Path.home() / ".cache" / "twinbrick-it")) / "map_tiles"


def world_px(lat: float, lon: float, z: float) -> Tuple[float, float]:
    n = TILE * 2.0 ** z
    s = math.sin(math.radians(max(-85.05, min(85.05, lat))))
    return (lon + 180.0) / 360.0 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n


def world_latlon(x: float, y: float, z: float) -> Tuple[float, float]:
    n = TILE * 2.0 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


class MapView(QWidget):
    clicked = pyqtSignal(float, float)          # lat, lon: the centre
    spanned = pyqtSignal(float, float)          # lat, lon: a corner (Shift+click)

    def __init__(self, parent=None, lat: float = 51.3001, lon: float = 7.6749, zoom: int = 16):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self.zoom = zoom
        self.cx, self.cy = world_px(lat, lon, zoom)
        self.tiles: Dict[Tuple[int, int, int], QPixmap] = {}
        self.pending = set()
        self.overlays: List[dict] = []
        self.marker: Optional[Tuple[float, float]] = (lat, lon)
        self.net = QNetworkAccessManager(self)
        cache = QNetworkDiskCache(self)
        CACHE.mkdir(parents=True, exist_ok=True)
        cache.setCacheDirectory(str(CACHE))
        cache.setMaximumCacheSize(300 * 1024 * 1024)
        self.net.setCache(cache)
        self.net.finished.connect(self._loaded)
        self._press = None
        self._moved = False
        self.offline = os.environ.get("TWINBRICK_OFFLINE", "") not in ("", "0")

    # ------------------------------------------------------------------ view
    def centre_on(self, lat: float, lon: float, zoom: Optional[int] = None):
        if zoom is not None:
            self.zoom = int(max(2, min(19, zoom)))
        self.cx, self.cy = world_px(lat, lon, self.zoom)
        self.update()

    def centre_latlon(self) -> Tuple[float, float]:
        return world_latlon(self.cx, self.cy, self.zoom)

    def fit(self, rings: Sequence[Sequence[Tuple[float, float]]], margin: float = 0.15):
        """Zoom and centre so these lat/lon rings fill the view."""
        pts = [p for r in rings for p in r]
        if not pts:
            return
        lat0, lat1 = min(p[0] for p in pts), max(p[0] for p in pts)
        lon0, lon1 = min(p[1] for p in pts), max(p[1] for p in pts)
        for z in range(19, 1, -1):
            x0, y0 = world_px(lat1, lon0, z)
            x1, y1 = world_px(lat0, lon1, z)
            if (x1 - x0) < self.width() * (1 - 2 * margin) and (y1 - y0) < self.height() * (1 - 2 * margin):
                break
        self.zoom = z
        self.cx, self.cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.update()

    def set_overlays(self, overlays: List[dict], marker: Optional[Tuple[float, float]] = None):
        """overlays: [{"ring": [(lat, lon), ...], "pen": QColor, "fill": QColor|None, "label": str, "dash": bool}]"""
        self.overlays = overlays
        self.marker = marker
        self.update()

    def to_screen(self, lat: float, lon: float) -> QPointF:
        x, y = world_px(lat, lon, self.zoom)
        return QPointF(x - self.cx + self.width() / 2, y - self.cy + self.height() / 2)

    def to_latlon(self, px: float, py: float) -> Tuple[float, float]:
        return world_latlon(self.cx + px - self.width() / 2, self.cy + py - self.height() / 2, self.zoom)

    def metres_per_pixel(self) -> float:
        lat, _ = self.centre_latlon()
        return 2 * math.pi * 6378137.0 * math.cos(math.radians(lat)) / (TILE * 2.0 ** self.zoom)

    # ------------------------------------------------------------------ tiles
    def _request(self, z: int, x: int, y: int):
        key = (z, x, y)
        if key in self.pending or self.offline:
            return
        self.pending.add(key)
        req = QNetworkRequest(QUrl(TILE_URL.format(z=z, x=x, y=y)))
        req.setRawHeader(b"User-Agent", AGENT)
        req.setAttribute(QNetworkRequest.CacheLoadControlAttribute, QNetworkRequest.PreferCache)
        req.setAttribute(QNetworkRequest.User, key)
        self.net.get(req)

    def _loaded(self, reply: QNetworkReply):
        key = reply.request().attribute(QNetworkRequest.User)
        self.pending.discard(key)
        if reply.error() == QNetworkReply.NoError:
            pm = QPixmap()
            if pm.loadFromData(bytes(reply.readAll())):
                self.tiles[tuple(key)] = pm
                if len(self.tiles) > 600:                      # keep memory bounded: drop other zoom levels
                    for k in [k for k in self.tiles if k[0] != self.zoom][:200]:
                        self.tiles.pop(k, None)
                self.update()
        reply.deleteLater()

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(236, 234, 228))
        z = self.zoom
        n = 2 ** z
        x0 = self.cx - self.width() / 2
        y0 = self.cy - self.height() / 2
        for ty in range(int(math.floor(y0 / TILE)), int(math.floor((y0 + self.height()) / TILE)) + 1):
            if ty < 0 or ty >= n:
                continue
            for tx in range(int(math.floor(x0 / TILE)), int(math.floor((x0 + self.width()) / TILE)) + 1):
                key = (z, tx % n, ty)
                pm = self.tiles.get(key)
                target = QRectF(tx * TILE - x0, ty * TILE - y0, TILE, TILE)
                if pm is None:
                    self._request(*key)
                    # a coarser tile, scaled up, while this one loads
                    for dz in range(1, 4):
                        k2 = (z - dz, (tx % n) >> dz, ty >> dz)
                        pm2 = self.tiles.get(k2)
                        if pm2 is not None:
                            s = TILE >> dz
                            src = QRectF(((tx % n) - (k2[1] << dz)) * s, (ty - (k2[2] << dz)) * s, s, s)
                            p.drawPixmap(target, pm2, src)
                            break
                else:
                    p.drawPixmap(target, pm, QRectF(0, 0, TILE, TILE))
        p.fillRect(self.rect(), QColor(255, 255, 255, 40))
        for ov in self.overlays:
            ring = ov.get("ring") or []
            if len(ring) < 2:
                continue
            poly = QPolygonF([self.to_screen(la, lo) for la, lo in ring])
            pen = QPen(ov.get("pen", QColor(200, 30, 30)), ov.get("width", 2.0))
            if ov.get("dash"):
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            fill = ov.get("fill")
            p.setBrush(QBrush(fill) if fill is not None else Qt.NoBrush)
            path = QPainterPath()
            path.addPolygon(poly)
            path.closeSubpath()
            p.drawPath(path)
            if ov.get("label"):
                c = poly.boundingRect().center()
                p.setPen(QColor(30, 30, 30))
                f = QFont()
                f.setBold(True)
                p.setFont(f)
                p.drawText(QRectF(c.x() - 40, c.y() - 10, 80, 20), Qt.AlignCenter, ov["label"])
        if self.marker:
            c = self.to_screen(*self.marker)
            p.setPen(QPen(QColor(20, 20, 20), 2))
            p.setBrush(QColor(255, 210, 0))
            p.drawEllipse(c, 5, 5)
        # scale bar and attribution
        mpp = self.metres_per_pixel()
        nice = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
        m = next((v for v in nice if v / mpp > 80), nice[-1])
        L = m / mpp
        p.setPen(QPen(QColor(20, 20, 20), 2))
        p.drawLine(QPointF(12, self.height() - 14), QPointF(12 + L, self.height() - 14))
        p.drawText(QPointF(14, self.height() - 20), f"{m:g} m" if m < 1000 else f"{m / 1000:g} km")
        text = ATTRIBUTION if not self.offline else ATTRIBUTION + " (offline: cached tiles only)"
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 10
        p.fillRect(QRectF(self.width() - w, self.height() - 18, w, 18), QColor(255, 255, 255, 200))
        p.setPen(QColor(40, 40, 40))
        p.drawText(QRectF(self.width() - w, self.height() - 18, w, 18), Qt.AlignCenter, text)
        p.end()

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._press = (ev.pos(), self.cx, self.cy)
            self._moved = False

    def mouseMoveEvent(self, ev):
        if self._press is not None:
            d = ev.pos() - self._press[0]
            if abs(d.x()) + abs(d.y()) > 3:
                self._moved = True
            if self._moved:
                self.cx, self.cy = self._press[1] - d.x(), self._press[2] - d.y()
                self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._press is not None:
            if not self._moved:
                lat, lon = self.to_latlon(ev.pos().x(), ev.pos().y())
                (self.spanned if ev.modifiers() & Qt.ShiftModifier else self.clicked).emit(lat, lon)
            self._press = None

    def wheelEvent(self, ev):
        step = 1 if ev.angleDelta().y() > 0 else -1
        z = int(max(2, min(19, self.zoom + step)))
        if z == self.zoom:
            return
        pos = ev.pos()
        lat, lon = self.to_latlon(pos.x(), pos.y())         # keep the point under the cursor in place
        self.zoom = z
        x, y = world_px(lat, lon, z)
        self.cx = x - (pos.x() - self.width() / 2)
        self.cy = y - (pos.y() - self.height() / 2)
        self.update()
