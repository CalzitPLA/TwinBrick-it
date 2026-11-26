#!/usr/bin/env bash
# TwinBrick it - installer (Linux): the command `twinbrick-it`, a desktop entry with the Burg Altena icon, a Python
# environment, and Legolize it (the brick engine) if it is not there yet.
#   ./install.sh                 install. The environment: Legolize it's when it is installed (nothing more to
#                                download), else ~/.local/share/twinbrick-it/venv (using the system's packages)
#   ./install.sh --check         check the installation: packages, the engine, the desktop entry, one site built
#                                offline (flat ground) and laid in bricks
#   ./install.sh --uninstall     remove the command, the desktop entry, the icons, and what this installer made in
#                                ~/.local/share/twinbrick-it (its environment, its copy of Legolize it); your results
#                                in ~/legolize_results stay (also: twinbrick-it uninstall)
#   ./install.sh --uninstall --purge   also the data cache (~/.cache/twinbrick-it) and the server list
#                                (~/.config/twinbrick-it)
#   ./install.sh --venv DIR      the environment somewhere else (made if missing)
#   ./install.sh --legolize DIR  use the Legolize it in DIR
#   ./install.sh --no-system     an own environment without the system's site packages (everything from PyPI)
#   PYTHON=/path/to/python3 ./install.sh    another Python (3.10 or newer)
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/twinbrick-it"
OWN_VENV="$DATA/venv"
VENV=""
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"
PY=${PYTHON:-python3}
LEGO_URL="https://github.com/CalzitPLA/Legolize-it.git"
LEGO=""
SYSTEM=1
PURGE=0
MODE=install
while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE=check ;;
    --uninstall) MODE=uninstall ;;
    --venv) VENV=$2; shift ;;
    --legolize) LEGO=$2; shift ;;
    --no-system) SYSTEM=0 ;;
    --purge) PURGE=1 ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done
say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
launcher_value() { [ -f "$1" ] && sed -n "s/^$2=\"\(.*\)\"$/\1/p" "$1" | head -1 || true; }

find_legolize() {     # the same order as twinbrick/engine.py
  local c
  for c in "$LEGO" "${TWINBRICK_LEGOLIZE:-}" "$(launcher_value "$BIN/legolize-it" ROOT)" "$ROOT/../Legolize-it" \
           "$ROOT/../legolize_it" "$DATA/Legolize-it"; do
    [ -n "$c" ] && [ -f "$c/lego_engine/__init__.py" ] && { (cd "$c" && pwd); return 0; }
  done
  return 1
}

if [ $MODE = uninstall ]; then
  [ -z "$VENV" ] && VENV=$(launcher_value "$BIN/twinbrick-it" VENV)
  say "removing the command, the desktop entry and the icons"
  rm -f "$BIN/twinbrick-it" "$APPS/twinbrick-it.desktop"
  for px in 32 64 128 256 512; do rm -f "$ICONS/${px}x${px}/apps/twinbrick-it.png"; done
  if [ -d "$OWN_VENV" ]; then say "removing the environment $OWN_VENV"; rm -rf "$OWN_VENV"; fi
  [ -n "$VENV" ] && [ "$VENV" != "$OWN_VENV" ] && echo "  kept the environment $VENV (not made by this installer)"
  if [ -f "$DATA/Legolize-it/.fetched-by-twinbrick-it" ]; then
    say "removing the copy of Legolize it in $DATA"; rm -rf "$DATA/Legolize-it"
  fi
  rmdir "$DATA" 2>/dev/null || true
  CONF="${XDG_CONFIG_HOME:-$HOME/.config}/twinbrick-it"
  CACHE="${TWINBRICK_CACHE:-$HOME/.cache/twinbrick-it}"
  for d in "$CACHE" "$CONF"; do
    [ -e "$d" ] || continue
    if [ $PURGE = 1 ]; then
      case "$d" in "$HOME"/?*) say "removing $d"; rm -rf "$d" ;; *) warn "$d is outside your home: left as it is" ;; esac
    else
      echo "  kept $d (./install.sh --uninstall --purge removes it)"
    fi
  done
  [ -d "$HOME/legolize_results" ] && echo "  kept your results in $HOME/legolize_results"
  command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
  command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$ICONS" 2>/dev/null || true
  say "TwinBrick it is uninstalled (this folder, $ROOT, stays: ./install.sh installs it again)"
  exit 0
fi

check() {
  local py ok=1 lego tmp
  [ -z "$VENV" ] && VENV=$(launcher_value "$BIN/twinbrick-it" VENV)
  py="$VENV/bin/python"
  [ -x "$py" ] || { echo "no environment (${VENV:-none}) - run ./install.sh first" >&2; exit 1; }
  lego=$(find_legolize) || { warn "Legolize it (the brick engine) not found"; exit 1; }
  say "packages ($VENV)"
  PYTHONPATH="$ROOT:$lego" "$py" - <<'PYEOF' || ok=0
import importlib, sys
bad = []
for m in ["numpy", "scipy", "trimesh", "PIL", "igl", "manifold3d", "PyQt5", "vtk", "lego_engine", "twinbrick"]:
    try:
        mod = importlib.import_module(m)
        print(f"  ok       {m} {getattr(mod, '__version__', '')}")
    except Exception as e:
        print(f"  MISSING  {m} ({e})")
        if m != "vtk":
            bad.append(m)
sys.exit(1 if bad else 0)
PYEOF
  say "the brick engine: Legolize it in $lego"
  tmp=$(mktemp -d)
  say "one site, offline (flat ground, no downloads): a square of 8 x 8 studs into bricks"
  if PYTHONPATH="$ROOT:$lego" "$py" -m twinbrick 51.3,7.67 --shape square --size 48 --scale 750 --terrain flat \
       --buildings none --trees none --no-landcover --bricks bricky --no-png --out "$tmp/site" \
       > "$tmp/summary.json" 2> "$tmp/log.txt"; then
    "$py" - "$tmp/summary.json" <<'PYEOF' || ok=0
import json, sys
s = json.load(open(sys.argv[1]))
b = s["tiles"][0].get("bricks") or {}
print(f"  {b.get('bricks', 0)} bricks in {b.get('layers', 0)} layers, holds together: {b.get('stable')}")
sys.exit(0 if b.get("bricks", 0) > 0 else 1)
PYEOF
  else
    warn "the build failed - the log: $tmp/log.txt"; ok=0
  fi
  local desk="$APPS/twinbrick-it.desktop"
  say "the desktop entry"
  if [ -f "$desk" ]; then
    if command -v desktop-file-validate >/dev/null; then
      desktop-file-validate "$desk" && echo "  $desk: valid" || ok=0
    else
      echo "  $desk (desktop-file-validate not installed: not checked)"
    fi
  else
    warn "no desktop entry at $desk - run ./install.sh"; ok=0
  fi
  [ $ok = 1 ] && say "the installation works (output in $tmp)" || { warn "the installation is incomplete (see above)"; exit 1; }
}
if [ $MODE = check ]; then check; exit 0; fi

say "Python"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Python 3.10 or newer is needed ($("$PY" --version 2>&1)); set PYTHON=/path/to/python3.x" >&2; exit 1; }
echo "  $("$PY" --version) at $(command -v "$PY")"

say "the brick engine: Legolize it"
if LEGO=$(find_legolize); then
  echo "  found: $LEGO"
else
  command -v git >/dev/null || { echo "git is needed to fetch Legolize it (or give --legolize DIR)" >&2; exit 1; }
  mkdir -p "$DATA"
  echo "  not found: fetching it from $LEGO_URL into $DATA/Legolize-it"
  git clone --quiet --depth 1 "$LEGO_URL" "$DATA/Legolize-it"
  touch "$DATA/Legolize-it/.fetched-by-twinbrick-it"
  LEGO="$DATA/Legolize-it"
fi

if [ -z "$VENV" ]; then
  LEGO_PY=$(launcher_value "$BIN/legolize-it" PY)
  if [ -n "$LEGO_PY" ] && [ -x "$LEGO_PY" ] && [ $SYSTEM = 1 ]; then
    VENV=$(dirname "$(dirname "$LEGO_PY")")
    say "the environment: Legolize it's ($VENV)"
  else
    VENV="$OWN_VENV"
    say "the environment: $VENV"
  fi
else
  say "the environment: $VENV"
fi
if [ ! -x "$VENV/bin/python" ]; then
  mkdir -p "$(dirname "$VENV")"
  args=(); [ $SYSTEM = 1 ] && args+=(--system-site-packages)
  "$PY" -m venv "${args[@]}" "$VENV"
fi
say "packages (requirements.txt, and Legolize it's)"
"$VENV/bin/python" -m pip install --quiet -r "$ROOT/requirements.txt" -r "$LEGO/requirements.txt" \
  -r "$LEGO/requirements-gui.txt"

say "the command: $BIN/twinbrick-it"
mkdir -p "$BIN"
cat > "$BIN/twinbrick-it" <<LAUNCHER
#!/usr/bin/env bash
# TwinBrick it, installed by $ROOT/install.sh
#   twinbrick-it [LAT,LON]                                   the window: a map, the shape, the scale, Build
#   twinbrick-it cli LAT,LON --shape hexagon --size 150 --scale 750 --bricks detailed   the command line (cli --help)
#   twinbrick-it server list|add HOST [PATH]|check HOST|sync HOST   build on servers over ssh (cli ... --server HOST)
#   twinbrick-it report [DIR ...] [--open]                   the results as one HTML page, with 3D views of the bricks
#   twinbrick-it check                                       check this installation
#   twinbrick-it uninstall [--purge]                         remove it (--purge: the data cache, the server list)
ROOT="$ROOT"
LEGO="$LEGO"
VENV="$VENV"
PY="\$VENV/bin/python"
export PYTHONPATH="\$ROOT:\$LEGO\${PYTHONPATH:+:\$PYTHONPATH}"
export TWINBRICK_LEGOLIZE="\$LEGO"
export RESOURCE_NAME=twinbrick-it     # the window's WM_CLASS instance = the desktop file's name (the dock's icon)
case "\${1:-}" in
  cli) shift; exec "\$PY" -m twinbrick "\$@" ;;
  server) shift; exec "\$PY" -m twinbrick.remote "\$@" ;;
  report) shift; exec "\$PY" -m twinbrick.report "\$@" ;;
  check) exec "\$ROOT/install.sh" --check --venv "\$VENV" ;;
  uninstall) shift; exec "\$ROOT/install.sh" --uninstall --venv "\$VENV" "\$@" ;;
  results) mkdir -p "\$HOME/legolize_results"; exec xdg-open "\$HOME/legolize_results" ;;
  -h|--help) sed -n '3,8p' "\$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac
exec "\$PY" -m twinbrick_gui "\$@"
LAUNCHER
chmod +x "$BIN/twinbrick-it"

say "the desktop entry and the icon"
mkdir -p "$APPS"
for px in 32 64 128 256 512; do
  mkdir -p "$ICONS/${px}x${px}/apps"
  cp "$ROOT/packaging/icons/twinbrick-it_${px}.png" "$ICONS/${px}x${px}/apps/twinbrick-it.png"
done
sed "s|^Exec=twinbrick-it|Exec=$BIN/twinbrick-it|" "$ROOT/packaging/twinbrick-it.desktop" > "$APPS/twinbrick-it.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$ICONS" 2>/dev/null || true
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN is not on your PATH: add it, or start $BIN/twinbrick-it" ;; esac
say "done: twinbrick-it (or 'TwinBrick it' in the applications menu);"
echo "  twinbrick-it check tests the installation, twinbrick-it uninstall removes it"
