"""The window (offscreen, no map tiles: offline), the command line's round trip, and the repository itself: nothing
private in it - no IP addresses, no home folders, no e-mail addresses but GitHub's noreply."""
import re
import subprocess
from pathlib import Path

import pytest

from twinbrick.cli import build_parser, spec_args, spec_from_args
from twinbrick.site import SiteSpec

ROOT = Path(__file__).resolve().parent.parent


def test_spec_round_trip_through_the_command_line():
    from dataclasses import asdict
    s = SiteSpec(lat=-33.8568, lon=151.2153, shape="triangle", size=(300.0, 300.0), rotation=-30.0, triangle_up=False,
                 tiles=((-1, 0), (0, 0)), scale=2500.0, vertical=2.0, datum=-5.0, terrain="terrarium",
                 buildings="osm", trees="none", tree_style="flat", landcover=False, theme="architecture",
                 colours={"roof": "dark_red"}, name="harbour")
    t = spec_from_args(build_parser().parse_args(spec_args(s, "artsy", True)))
    assert asdict(t) == asdict(s)


def test_the_window_builds_its_spec():
    pytest.importorskip("PyQt5")
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from twinbrick_gui.app import MainWindow
    w = MainWindow("51.3001, 7.6749")
    w.shape.setCurrentText("square")
    w.size.setValue(96)
    w.scale.setCurrentText("1:750")
    w.rings.setValue(1)
    w.update_overlay()
    s = w.spec()
    assert s.shape == "square" and s.scale == 750 and s.rotation is None
    assert "5 tile(s)" in w.info.text()
    assert len(w.map.overlays) >= 10                                     # outline and studs of each tile
    w.on_map_span(51.3005, 7.6755)                                        # Shift+click: a corner
    assert w.size.value() > 50
    w.close()
    app.processEvents()


PRIVATE = [
    (re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), "an IPv4 address"),
    (re.compile(r"/home/[A-Za-z0-9_.-]+|/Users/[A-Za-z0-9_.-]+|C:\\\\Users"), "a home folder"),
    (re.compile(r"[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "an e-mail address"),
]
ALLOWED = {"192.0.2.7"}                                                    # TEST-NET-1 (RFC 5737) in the tests


def test_nothing_private_in_the_repository():
    try:
        files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    hits = []
    for name in files:
        p = ROOT / name
        if not p.is_file() or p.suffix.lower() in (".png", ".jpg", ".jpeg", ".ply", ".npz", ".blend", ".pdf"):
            continue
        text = p.read_text(errors="replace")
        for rx, what in PRIVATE:
            for m in rx.finditer(text):
                if m.group(0) in ALLOWED:
                    continue
                if what == "an IPv4 address" and all(int(x) < 256 for x in m.group(0).split(".")) is False:
                    continue
                hits.append(f"{name}: {what}: {m.group(0)}")
    assert not hits, "\n".join(hits)
