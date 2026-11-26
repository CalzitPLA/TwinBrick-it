"""Legolize it's engine: finding it, running it on a site's mesh, opening its window.

TwinBrick it makes the input (a closed coloured mesh of a place); Legolize it (github.com/CalzitPLA/Legolize-it) lays
the bricks. It is found, in this order: TWINBRICK_LEGOLIZE (the Legolize-it folder), an importable `lego_engine`, the
installed `legolize-it` command (its ROOT), a Legolize-it folder next to this one, or the copy install.sh fetched
into ~/.local/share/twinbrick-it/Legolize-it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent.parent
LAUNCHER = Path.home() / ".local" / "bin" / "legolize-it"
DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "twinbrick-it"
LEGOLIZE_URL = "https://github.com/CalzitPLA/Legolize-it"

# the three looks of Legolize it's window, as command-line switches; every run: the mesh at its own size (whole studs
# and plates), plate layers, a hollow base (3 cells of shell), no thin-wall rule (the mesh is solid), its colours
COMMON = ["--scale", "1", "--unit", "plate", "--shell", "3", "--thin-walls", "off", "--colours", "source"]
MODES = {
    "bricky": ["--style", "exact"],
    "detailed": ["--style", "exact", "--shapes", "slope", "--details", "tiles,rods"],
    "artsy": ["--style", "artsy", "--details", "tiles,rods,bars", "--max-length", "12"],
}


def _launcher_values() -> Dict[str, str]:
    try:
        text = LAUNCHER.read_text()
    except OSError:
        return {}
    return dict(re.findall(r'^(ROOT|PY)="([^"]+)"', text, re.M))


def legolize_root() -> Optional[Path]:
    cands: List[Path] = []
    if os.environ.get("TWINBRICK_LEGOLIZE"):
        cands.append(Path(os.environ["TWINBRICK_LEGOLIZE"]))
    try:
        spec = importlib.util.find_spec("lego_engine")
    except (ImportError, ValueError):
        spec = None
    if spec is not None and spec.origin:
        cands.append(Path(spec.origin).resolve().parent.parent)
    lv = _launcher_values()
    if "ROOT" in lv:
        cands.append(Path(lv["ROOT"]))
    cands += [HERE.parent / "Legolize-it", HERE.parent / "legolize_it", DATA / "Legolize-it"]
    for c in cands:
        if (c / "lego_engine" / "__init__.py").exists():
            return c.resolve()
    return None


def ensure() -> Path:
    """Make `lego_engine` importable; its folder."""
    root = legolize_root()
    if root is None:
        raise ImportError("TwinBrick it needs Legolize it (the brick engine): install it from "
                          f"{LEGOLIZE_URL}, or clone it next to this folder, or set TWINBRICK_LEGOLIZE to its folder "
                          "(./install.sh does this for you)")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def engine_python() -> str:
    """The Python Legolize it was installed with (its environment has libigl and manifold3d), else this one."""
    py = _launcher_values().get("PY")
    return py if py and Path(py).exists() else sys.executable


def mode_args(mode: str) -> List[str]:
    if mode not in MODES:
        raise ValueError(f"brick mode must be one of {list(MODES)} or none")
    return COMMON + MODES[mode]


def run(mesh: Path, out: Path, mode: str = "bricky", png: bool = True, manual: bool = False,
        extra: Sequence[str] = (), say: Callable[[str], None] = print) -> Dict:
    """Legolize it on `mesh` into `out` (result.json, render.png, manual/): its summary (dict)."""
    root = ensure()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    args = [engine_python(), "-m", "lego_engine", str(mesh)] + mode_args(mode) + ["--out", str(out)]
    if png:
        args += ["--png", str(out / "render.png")]
    if manual:
        args += ["--manual"]
    args += list(extra)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    say(f"Legolize it ({mode}): {Path(mesh).name} -> {out}")
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    import threading
    err_lines: List[str] = []

    def pump():
        for line in proc.stderr:
            line = line.rstrip()
            err_lines.append(line)
            if line:
                say("  " + line)
    th = threading.Thread(target=pump, daemon=True)
    th.start()
    stdout = proc.stdout.read()
    proc.wait()
    th.join(timeout=5)
    try:
        summary = json.loads(stdout) if stdout.strip() else {}
    except ValueError:
        summary = {"output": stdout[-2000:]}
    if proc.returncode != 0:
        summary.setdefault("error", "\n".join(err_lines[-15:]) or f"exit code {proc.returncode}")
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def open_window(mesh: Path, mode: str = "detailed") -> subprocess.Popen:
    """Legolize it's window with the site's mesh (the user's own look; it starts in the window's background)."""
    flags = {"bricky": ["--bricks-only"], "artsy": ["--artsy"]}.get(mode, [])
    args = ["--scale", "1", "--unit", "plate", "--colours", "source", "--run"] + flags
    if LAUNCHER.exists():
        return subprocess.Popen([str(LAUNCHER), str(mesh)] + args, start_new_session=True)
    root = ensure()
    env = dict(os.environ, PYTHONPATH=str(root))
    env.pop("QT_QPA_PLATFORM", None)
    return subprocess.Popen([engine_python(), "-m", "lego_gui", str(mesh)] + args, env=env, start_new_session=True)
