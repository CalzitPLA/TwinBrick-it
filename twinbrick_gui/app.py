"""The TwinBrick it window: a map to place and span the model, the settings, and the results.

Left: the place and shape, its orientation and tiles, the scale, the data, the colours and how the bricks are laid.
Middle: the map (click: the centre; Shift+click: span the shape to a corner; drag, wheel), the plan of the built site,
the bricks. Bottom: the log. Build (F5) fetches the data and makes the mesh in the background, then Legolize it lays the
bricks; Open in Legolize it (Ctrl+L) shows them in its window.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
import traceback
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QThread, QTimer, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QPixmap
from PyQt5.QtWidgets import (QAction, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                             QPushButton, QScrollArea, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from twinbrick import __version__, engine, net
from twinbrick.cli import default_name
from twinbrick.crs import format_latlon, parse_latlon
from twinbrick.footprint import SHAPES, TILING, ring_tiles
from twinbrick.landcover import THEMES
from twinbrick.site import RESULTS, Site, SiteSpec

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / "packaging" / "icons" / "twinbrick-it_256.png"
SCALES = ["1:250", "1:500", "1:750", "1:1000", "1:1500", "1:2500", "1:5000", "1:10000", "1:25000"]
TILE_COLOURS = [QColor(200, 30, 30), QColor(30, 90, 200), QColor(20, 140, 60), QColor(190, 110, 0),
                QColor(130, 40, 160), QColor(0, 140, 150), QColor(120, 120, 120)]
ABOUT = f"""<h3>TwinBrick it {__version__}</h3>
<p>A place on earth as a brick model: a GPS point, a shape, its size and orientation, and a scale. TwinBrick it
fetches the place's digital twin - terrain, buildings, trees, land use, roads and water - and builds a closed coloured
mesh per tile, whole studs wide; <a href="{engine.LEGOLIZE_URL}">Legolize it</a> lays the bricks.</p>
<p><b>Data</b><br>Geobasis NRW (Digitaler Zwilling NRW: DGM1, nDOM50, LoD2): Datenlizenz Deutschland - Zero - 2.0<br>
OpenStreetMap: &copy; OpenStreetMap contributors, ODbL 1.0 - map tiles from tile.openstreetmap.org<br>
Terrain Tiles (AWS open data): SRTM, ETOPO1, EU-DEM (Copernicus), 3DEP and others</p>
<p>GNU General Public License v3. github.com/CalzitPLA/TwinBrick-it</p>"""


class BuildWorker(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, spec: SiteSpec, out: Optional[Path], mode: str, manual: bool, server: Optional[str] = None):
        super().__init__()
        self.spec, self.out, self.mode, self.manual, self.server = spec, out, mode, manual, server

    def run(self):
        try:
            if self.server:
                return self._run_remote()
            t0 = time.time()
            site = Site(self.spec, say=self.log.emit)
            out = self.out or RESULTS / default_name(site) / time.strftime("%Y%m%d_%H%M%S")
            tiles = site.build(out)
            res = dict(out=str(out), bearing=(site.frame.bearing - site.north_b) % 360, tiles=[],
                       overview=str(out / "overview_plan.png") if len(tiles) > 1 else None)
            for t in tiles:
                entry = dict(tile=(t.q, t.r), mesh=t.files.get("mesh"), plan=t.files.get("plan"))
                if self.mode != "none":
                    sub = out / ("bricks" if len(tiles) == 1 else f"{t.label}_bricks")
                    s = engine.run(Path(t.files["mesh"]), sub, self.mode, png=True, manual=self.manual,
                                   say=self.log.emit)
                    entry.update(summary=s, render=str(sub / "render.png"))
                res["tiles"].append(entry)
            self.log.emit(f"done in {time.time() - t0:.0f} s: {out}")
            self.done.emit(res)
        except Exception as ex:  # noqa: BLE001 - shown to the user, with the trace in the log
            self.log.emit(traceback.format_exc())
            self.failed.emit(f"{type(ex).__name__}: {ex}")


    def _run_remote(self):
        """The whole build on a server (twinbrick.remote): the same settings as a command line."""
        from twinbrick import remote
        from twinbrick.cli import spec_args
        t0 = time.time()
        site = Site(self.spec, say=lambda _t: None)
        out = self.out or RESULTS / default_name(site) / time.strftime("%Y%m%d_%H%M%S")
        summary = remote.run_site(self.server, spec_args(self.spec, self.mode, self.manual), out,
                                  progress=self.log.emit)
        info = {}
        try:
            info = json.loads((out / "site.json").read_text())
        except (OSError, ValueError):
            pass
        res = dict(out=str(out), bearing=(info.get("orientation") or {}).get("back_true_bearing", 0.0), tiles=[],
                   overview=str(out / "overview_plan.png") if (out / "overview_plan.png").exists() else None)
        for t in summary.get("tiles", []):
            entry = dict(tile=tuple(t["tile"]), mesh=t.get("mesh"), plan=t.get("plan"), summary=t.get("bricks"))
            if t.get("bricks_dir"):
                entry["render"] = str(Path(t["bricks_dir"]) / "render.png")
            res["tiles"].append(entry)
        self.log.emit(f"done on {self.server} in {time.time() - t0:.0f} s: {out}")
        self.done.emit(res)


class Picture(QLabel):
    """A picture that scales with the widget."""

    def __init__(self, text=""):
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(200, 150)
        self._pm: Optional[QPixmap] = None

    def set_file(self, path: Optional[str]):
        self._pm = QPixmap(path) if path and Path(path).exists() else None
        self._show()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._show()

    def _show(self):
        if self._pm is not None and not self._pm.isNull():
            self.setPixmap(self._pm.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))


class MainWindow(QMainWindow):
    def __init__(self, place: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("TwinBrick it")
        if ICON.exists():
            self.setWindowIcon(QIcon(str(ICON)))
        self.worker: Optional[BuildWorker] = None
        self.result: Optional[dict] = None
        self._site: Optional[Site] = None
        self._timer = QTimer(self, singleShot=True, interval=150, timeout=self.update_overlay)
        self._build_form()
        from .mapview import MapView
        self.map = MapView(self)
        self.map.clicked.connect(self.on_map_click)
        self.map.spanned.connect(self.on_map_span)
        self.plan = Picture("the plan of the site appears here after Build")
        self.bricks = Picture("the bricks appear here after Build")
        self.tabs = QTabWidget()
        self.tabs.addTab(self.map, "Map")
        self.tabs.addTab(self.plan, "Plan")
        self.tabs.addTab(self.bricks, "Bricks")
        self.logbox = QPlainTextEdit(readOnly=True)
        self.logbox.setMaximumBlockCount(5000)
        right = QSplitter(Qt.Vertical)
        right.addWidget(self.tabs)
        right.addWidget(self.logbox)
        right.setSizes([620, 160])
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form)
        scroll.setMinimumWidth(420)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(scroll)
        split.addWidget(right)
        split.setSizes([430, 870])
        self.setCentralWidget(split)
        self._actions()
        self.resize(1300, 860)
        if place:
            self.place.setText(place)
        QTimer.singleShot(0, self.on_place_edited)          # after the window has its size: the map fits the tile

    # ------------------------------------------------------------------ the form
    def _build_form(self):
        self.form = QWidget()
        self.form.setMaximumWidth(440)
        lay = QVBoxLayout(self.form)
        ch = lambda *_: self._timer.start()

        g = QGroupBox("Place")
        f = QFormLayout(g)
        self.place = QLineEdit("51.3001, 7.6749")
        self.place.setToolTip("LAT, LON - decimal or degrees/minutes/seconds (51°18'00\"N 7°40'30\"E); "
                              "or click the map")
        self.place.editingFinished.connect(self.on_place_edited)
        f.addRow("Centre", self.place)
        self.osm = QLineEdit()
        self.osm.setPlaceholderText("way/ID (optional)")
        self.osm.setToolTip("an OpenStreetMap outline (way/ID or relation/ID): the shape becomes that outline")
        self.osm.editingFinished.connect(ch)
        f.addRow("OSM outline", self.osm)
        lay.addWidget(g)

        g = QGroupBox("Shape")
        f = QFormLayout(g)
        self.shape = QComboBox()
        self.shape.addItems([s for s in SHAPES if s != "polygon"])
        self.shape.setCurrentText("hexagon")
        self.shape.currentTextChanged.connect(self._shape_changed)
        f.addRow("Shape", self.shape)
        self.hexo = QComboBox()
        self.hexo.addItems(["flat", "pointy"])
        self.hexo.currentTextChanged.connect(ch)
        f.addRow("Hexagon", self.hexo)
        self.size = QDoubleSpinBox(decimals=0, minimum=5, maximum=100000, value=110, suffix=" m", singleStep=10)
        self.size.setToolTip("the side (square, hexagon, triangle), the diameter (circle), the width (rectangle, "
                             "ellipse) - on the ground; Shift+click on the map spans the shape to a corner")
        self.size.valueChanged.connect(ch)
        f.addRow("Size", self.size)
        self.size2 = QDoubleSpinBox(decimals=0, minimum=5, maximum=100000, value=110, suffix=" m", singleStep=10)
        self.size2.valueChanged.connect(ch)
        f.addRow("Height", self.size2)
        self.margin = QDoubleSpinBox(decimals=0, minimum=0, maximum=5000, value=6, suffix=" m")
        self.margin.valueChanged.connect(ch)
        f.addRow("Margin", self.margin)
        self.rotation = QDoubleSpinBox(decimals=1, minimum=-360, maximum=360, value=0, suffix=" °", wrapping=True)
        self.rotation.setToolTip("the true bearing the model's back edge faces (0 = north up)")
        self.rotation.valueChanged.connect(ch)
        self.auto_rot = QCheckBox("along the buildings")
        self.auto_rot.setChecked(True)
        self.auto_rot.setToolTip("turn the studs so the buildings run along them (decided from the data)")
        self.auto_rot.toggled.connect(lambda on: (self.rotation.setEnabled(not on), ch()))
        self.rotation.setEnabled(False)
        f.addRow("Back faces", self.rotation)
        f.addRow("", self.auto_rot)
        self.rings = QSpinBox(minimum=0, maximum=4)
        self.rings.setToolTip("tile 0,0 and its neighbours, ring by ring (they fit stud for stud)")
        self.rings.valueChanged.connect(ch)
        f.addRow("Neighbour rings", self.rings)
        self.tiles = QLineEdit()
        self.tiles.setPlaceholderText("or tiles: 0,0 1,0 0,1")
        self.tiles.editingFinished.connect(ch)
        f.addRow("Tiles", self.tiles)
        lay.addWidget(g)

        g = QGroupBox("Scale")
        f = QFormLayout(g)
        self.scale = QComboBox(editable=True)
        self.scale.addItems(SCALES)
        self.scale.setCurrentText("1:750")
        self.scale.currentTextChanged.connect(ch)
        f.addRow("Scale", self.scale)
        self.vertical = QDoubleSpinBox(decimals=1, minimum=0.2, maximum=10, value=1.0, singleStep=0.5, suffix=" x")
        self.vertical.setToolTip("height exaggeration: 1 = true proportions")
        self.vertical.valueChanged.connect(ch)
        f.addRow("Heights", self.vertical)
        self.info = QLabel()
        self.info.setWordWrap(True)
        f.addRow(self.info)
        lay.addWidget(g)

        g = QGroupBox("Data and colours")
        f = QFormLayout(g)
        self.terrain = QComboBox()
        self.terrain.addItems(["auto", "nrw", "terrarium", "flat"])
        f.addRow("Terrain", self.terrain)
        self.buildings = QComboBox()
        self.buildings.addItems(["auto", "lod2", "osm", "none"])
        f.addRow("Buildings", self.buildings)
        self.trees = QComboBox()
        self.trees.addItems(["auto", "ndom", "osm", "none"])
        f.addRow("Trees", self.trees)
        self.tree_style = QComboBox()
        self.tree_style.addItems(["auto", "lollipop", "canopy", "flat"])
        self.tree_style.setToolTip("lollipop: a trunk and a round crown per tree; canopy: the crowns' true surface; "
                                   "flat: none (auto: lollipop up to 4 m per stud)")
        f.addRow("Tree style", self.tree_style)
        self.landcover = QCheckBox("land use, roads, water")
        self.landcover.setChecked(True)
        f.addRow("Ground", self.landcover)
        self.theme = QComboBox()
        self.theme.addItems(list(THEMES))
        f.addRow("Colours", self.theme)
        lay.addWidget(g)

        g = QGroupBox("Bricks (Legolize it)")
        f = QFormLayout(g)
        self.mode = QComboBox()
        self.mode.addItems(["bricky", "detailed", "artsy", "none"])
        self.mode.setToolTip("bricky: plain bricks and plates; detailed: slopes on the terrain, tiles, round 1x1; "
                             "artsy: Legolize it's craft mode; none: only the mesh")
        f.addRow("Look", self.mode)
        self.manual = QCheckBox("the building manual too")
        f.addRow("", self.manual)
        lay.addWidget(g)

        g = QGroupBox("Output")
        f = QFormLayout(g)
        self.name = QLineEdit()
        self.name.setPlaceholderText("from the place")
        f.addRow("Name", self.name)
        row = QHBoxLayout()
        self.outdir = QLineEdit()
        self.outdir.setPlaceholderText(f"{RESULTS}/<name>/<time>")
        b = QPushButton("…")
        b.setMaximumWidth(30)
        b.clicked.connect(self._choose_out)
        row.addWidget(self.outdir)
        row.addWidget(b)
        f.addRow("Folder", row)
        row = QHBoxLayout()
        self.run_on = QComboBox()
        self.run_on.setToolTip("build here, or on a server over ssh (the whole site: downloads, mesh, bricks)")
        b = QPushButton("Servers …")
        b.clicked.connect(self._servers)
        row.addWidget(self.run_on, 1)
        row.addWidget(b)
        f.addRow("Run on", row)
        self._fill_servers()
        lay.addWidget(g)

        self.build_btn = QPushButton("Build  (F5)")
        self.build_btn.setStyleSheet("font-weight: bold; padding: 6px;")
        self.build_btn.clicked.connect(self.build)
        lay.addWidget(self.build_btn)
        row = QHBoxLayout()
        self.open_btn = QPushButton("Open in Legolize it")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self.open_legolize)
        self.folder_btn = QPushButton("Results folder")
        self.folder_btn.setEnabled(False)
        self.folder_btn.clicked.connect(self.open_folder)
        row.addWidget(self.open_btn)
        row.addWidget(self.folder_btn)
        lay.addLayout(row)
        lay.addStretch(1)
        self._shape_changed(self.shape.currentText())

    def _actions(self):
        menu = self.menuBar()
        m = menu.addMenu("&File")
        for text, key, fn in (("Build", "F5", self.build), ("Open in Legolize it", "Ctrl+L", self.open_legolize),
                              ("Results folder", "Ctrl+O", self.open_folder), ("Quit", "Ctrl+Q", self.close)):
            a = QAction(text, self, shortcut=QKeySequence(key), triggered=fn)
            m.addAction(a)
        h = menu.addMenu("&Help")
        h.addAction(QAction("About", self, shortcut=QKeySequence("F1"),
                            triggered=lambda: QMessageBox.about(self, "TwinBrick it", ABOUT)))

    def _shape_changed(self, shape: str):
        self.hexo.setEnabled(shape == "hexagon")
        self.size2.setEnabled(shape in ("rectangle", "ellipse"))
        self.rings.setEnabled(shape in TILING)
        self.tiles.setEnabled(shape in TILING)
        self._timer.start()

    def _fill_servers(self):
        from twinbrick import remote
        current = self.run_on.currentText()
        self.run_on.clear()
        self.run_on.addItem("this computer")
        self.run_on.addItems(list(remote.load_servers()))
        if current:
            self.run_on.setCurrentText(current)

    def _servers(self):
        from .servers import ServersDialog
        ServersDialog(self).exec_()
        self._fill_servers()

    def _choose_out(self):
        d = QFileDialog.getExistingDirectory(self, "Results folder", str(RESULTS))
        if d:
            self.outdir.setText(d)

    # ------------------------------------------------------------------ spec, overlay
    def scale_n(self) -> float:
        m = re.findall(r"[\d.]+", self.scale.currentText())
        if not m:
            raise ValueError("the scale: 1:N")
        return float(m[-1])

    def spec(self, corner=None) -> SiteSpec:
        lat, lon = parse_latlon(self.place.text())
        osm = self.osm.text().strip() or None
        shape = "polygon" if osm else self.shape.currentText()
        tiles = ((0, 0),)
        if shape in TILING:
            if self.tiles.text().strip():
                tiles = tuple(tuple(int(x) for x in t.split(",")) for t in self.tiles.text().split())
        return SiteSpec(lat=None if osm else lat, lon=None if osm else lon, shape=shape,
                        size=(self.size.value(), self.size2.value() if shape in ("rectangle", "ellipse")
                              else self.size.value()),
                        rotation=None if self.auto_rot.isChecked() else self.rotation.value(),
                        hex_orientation=self.hexo.currentText(), corner=corner, outline=osm,
                        margin=self.margin.value() if osm else 0.0, tiles=tiles, scale=self.scale_n(),
                        vertical=self.vertical.value(), terrain=self.terrain.currentText(),
                        buildings=self.buildings.currentText(), trees=self.trees.currentText(), tree_style=self.tree_style.currentText(),
                        landcover=self.landcover.isChecked(), theme=self.theme.currentText(),
                        name=self.name.text().strip())

    def on_place_edited(self):
        try:
            lat, lon = parse_latlon(self.place.text())
        except ValueError as ex:
            self.statusBar().showMessage(str(ex), 5000)
            return
        self.map.centre_on(lat, lon)
        self.update_overlay(fit=True)

    def on_map_click(self, lat: float, lon: float):
        self.place.setText(f"{lat:.6f}, {lon:.6f}")
        self.osm.clear()
        self.update_overlay()

    def on_map_span(self, lat: float, lon: float):
        """Shift+click: the shape reaches to this corner - its size (and, for regular shapes, its turn) from it."""
        try:
            site = Site(self.spec(corner=(lat, lon)), say=lambda _t: None)
        except (ValueError, RuntimeError) as ex:
            self.statusBar().showMessage(str(ex), 5000)
            return
        shape = self.shape.currentText()
        if shape in ("rectangle", "ellipse"):
            self.size.setValue(site.layout.size[0])
            self.size2.setValue(site.layout.size[1])
        else:
            self.size.setValue(site.size[0])
        if site.bearing is not None and shape in ("hexagon", "triangle", "square"):
            self.auto_rot.setChecked(False)
            self.rotation.setValue(((site.bearing - site.north_b + 180) % 360) - 180)
        self.update_overlay()

    def update_overlay(self, fit: bool = False):
        try:
            spec = self.spec()
            if spec.shape in TILING and self.rings.value() and not self.tiles.text().strip():
                probe = Site(spec, say=lambda _t: None)
                spec.tiles = tuple(ring_tiles(probe.layout, self.rings.value()))
            site = Site(spec, say=lambda _t: None)
        except (ValueError, RuntimeError, net.Offline) as ex:
            self.info.setText(f"<span style='color:#b00'>{ex}</span>")
            return
        self._site = site
        overlays = []
        total, cm = 0, []
        for k, (q, r) in enumerate(spec.tiles):
            col = TILE_COLOURS[k % len(TILE_COLOURS)]
            ring = [tuple(p) for p in site.outline_latlon(q, r)]
            overlays.append(dict(ring=ring, pen=col, width=1.5, dash=True))
            try:
                for st in site.stud_outline_latlon(q, r):
                    fill = QColor(col)
                    fill.setAlpha(45)
                    overlays.append(dict(ring=[tuple(p) for p in st], pen=col, width=2.0, fill=fill,
                                         label=f"{q},{r}" if len(spec.tiles) > 1 else ""))
            except ValueError:
                pass
        for t in site.plan():
            total += t["base_studs"]
            cm.append(t["size_cm"])
        self.map.set_overlays(overlays, marker=(site.lat, site.lon))
        if fit:
            self.map.fit([o["ring"] for o in overlays])
        w = max(c[0] for c in cm)
        h = max(c[1] for c in cm)
        rot = "turned along the buildings when built" if spec.rotation is None else \
            f"back faces {spec.rotation % 360:.0f}°"
        self.info.setText(f"1 stud = {site.layout.stud:.2f} m, 1 plate = {site.plate_m:.2f} m<br>"
                          f"{len(spec.tiles)} tile(s), {total} studs on the base; a tile up to {w:.1f} x {h:.1f} cm"
                          f"<br>{site.layout.describe()}; {rot}")

    # ------------------------------------------------------------------ build and results
    def build(self):
        if self.worker is not None and self.worker.isRunning():
            self.statusBar().showMessage("a build is running", 3000)
            return
        try:
            spec = self.spec()
            if spec.shape in TILING and self.rings.value() and not self.tiles.text().strip():
                spec.tiles = tuple(ring_tiles(Site(spec, say=lambda _t: None).layout, self.rings.value()))
        except (ValueError, RuntimeError) as ex:
            QMessageBox.warning(self, "TwinBrick it", str(ex))
            return
        out = Path(self.outdir.text()).expanduser() if self.outdir.text().strip() else None
        self.logbox.clear()
        self.build_btn.setEnabled(False)
        self.build_btn.setText("Building …")
        server = None if self.run_on.currentIndex() <= 0 else self.run_on.currentText()
        self.worker = BuildWorker(spec, out, self.mode.currentText(), self.manual.isChecked(), server)
        self.worker.log.connect(self.log)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def log(self, text: str):
        self.logbox.appendPlainText(text)

    def on_done(self, res: dict):
        self.result = res
        self.build_btn.setEnabled(True)
        self.build_btn.setText("Build  (F5)")
        self.open_btn.setEnabled(any(t.get("mesh") for t in res["tiles"]))
        self.folder_btn.setEnabled(True)
        first = res["tiles"][0]
        self.plan.set_file(res.get("overview") or first.get("plan"))
        if first.get("render"):
            self.bricks.set_file(first["render"])
            self.tabs.setCurrentWidget(self.bricks)
        else:
            self.tabs.setCurrentWidget(self.plan)
        if self.auto_rot.isChecked():
            self.statusBar().showMessage(f"turned along the buildings: the back faces {res['bearing']:.1f}°", 10000)
        bad = [t for t in res["tiles"] if (t.get("summary") or {}).get("error")]
        if bad:
            QMessageBox.warning(self, "TwinBrick it", "Legolize it reported an error - see the log")

    def on_failed(self, msg: str):
        self.build_btn.setEnabled(True)
        self.build_btn.setText("Build  (F5)")
        QMessageBox.warning(self, "TwinBrick it", msg)

    def open_legolize(self):
        if not self.result:
            return
        mesh = self.result["tiles"][0].get("mesh")
        mode = self.mode.currentText()
        try:
            engine.open_window(Path(mesh), "detailed" if mode == "none" else mode)
            self.statusBar().showMessage("Legolize it opens the site ...", 5000)
        except (ImportError, OSError) as ex:
            QMessageBox.warning(self, "TwinBrick it", str(ex))

    def open_folder(self):
        if self.result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.result["out"]))

    def closeEvent(self, ev):
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(100)
        super().closeEvent(ev)
