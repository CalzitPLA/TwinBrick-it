"""Runs on servers over ssh: the whole site - downloads, raster, mesh and the bricks - is built on a server and the
results come back.

    twinbrick-it server list                       the hosts of ~/.ssh/config and the servers set up here
    twinbrick-it server add HOST [PATH] [--python P]   set up a server: where TwinBrick it lives on it (default
                                                   ~/twinbrick_it - `server sync` fills it) and its Python
    twinbrick-it server sync HOST                  copy this TwinBrick it and its Legolize it engine there
    twinbrick-it server check HOST                 can it run? (Python, numpy, scipy, trimesh, igl, manifold3d, ...)
    twinbrick-it server remove HOST
    twinbrick-it cli 51.30,7.67 ... --server HOST  any command-line build, on the server (the window: Run on)

What is kept where - and what is not kept at all (nothing about servers belongs in the repository):
    - hosts are ssh's own aliases (the `Host` lines of ~/.ssh/config, following Include; patterns left out). Only the
      alias is read and used: ssh resolves host name, address, user, port and keys itself, so none of them is read,
      copied or written by TwinBrick it;
    - the servers set up here (alias, path, python) live in ~/.config/twinbrick-it/servers.json ($XDG_CONFIG_HOME, or
      $TWINBRICK_CONFIG_DIR), readable by the user only - never in the repository or in results;
    - a run works in a fresh `mktemp -d` folder on the server, removed afterwards; the files that come back are
      rewritten so they name the local folder, not the server's; the alias is in no result;
    - ssh runs with BatchMode (a key or an agent; no password prompt that would hang) and a connect timeout; files go
      both ways as tar streams through ssh; what comes back is unpacked with every path checked.
The server downloads the geodata itself (into its own ~/.cache/twinbrick-it), so it needs internet access.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .engine import ensure

ensure()
from lego_engine.remote import _tar, _untar, check_alias, remote_path, ssh_config_hosts  # noqa: E402

SSH = os.environ.get("TWINBRICK_SSH", "ssh")          # tests put a stand-in here
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30"]
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = "~/twinbrick_it"
LOCAL_ONLY = ("--server", "--out", "--open")
TEXT_SUFFIXES = {".json", ".txt", ".csv", ".tex", ".md", ".log", ".fcmacro", ".aux", ".out"}


def config_dir() -> Path:
    if os.environ.get("TWINBRICK_CONFIG_DIR"):
        return Path(os.environ["TWINBRICK_CONFIG_DIR"])
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "twinbrick-it"


def servers_file() -> Path:
    return config_dir() / "servers.json"


@dataclass
class Server:
    alias: str
    path: str = DEFAULT_PATH
    python: str = "python3"


def load_servers() -> Dict[str, Server]:
    try:
        data = json.loads(servers_file().read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for alias, d in (data.get("servers") or {}).items():
        try:
            out[check_alias(alias)] = Server(alias, str(d.get("path", DEFAULT_PATH)), str(d.get("python", "python3")))
        except ValueError:
            continue
    return out


def save_servers(servers: Dict[str, Server]) -> Path:
    path = servers_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"servers": {a: {"path": s.path, "python": s.python} for a, s in servers.items()}},
                              indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def add_server(alias: str, path: str = DEFAULT_PATH, python: str = "python3") -> Server:
    alias = check_alias(alias)
    if not str(path).strip() or "\n" in path or "\n" in python:
        raise ValueError("the server's path must be one line")
    servers = load_servers()
    servers[alias] = Server(alias, str(path).strip(), str(python).strip() or "python3")
    save_servers(servers)
    return servers[alias]


def remove_server(alias: str) -> bool:
    servers = load_servers()
    if servers.pop(alias, None) is None:
        return False
    save_servers(servers)
    return True


def known_hosts(config: Optional[Path] = None) -> List[str]:
    configured = list(load_servers())
    return configured + [h for h in ssh_config_hosts(config) if h not in configured]


def ssh(alias: str, command: str, stdin: Optional[bytes] = None, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run([SSH, *SSH_OPTS, check_alias(alias), command], input=stdin, capture_output=True,
                          timeout=timeout)


def _err(proc) -> str:
    text = (proc.stderr or b"").decode(errors="replace").strip().splitlines()
    return text[-1] if text else f"exit code {proc.returncode}"


def sync_code(alias: str, path: Optional[str] = None) -> str:
    """Copy twinbrick and Legolize it's engine (lego_engine, lego_gui for the pictures) into the server's path."""
    server = load_servers().get(alias) or Server(alias)
    path = path or server.path
    eng = ensure()
    data = _tar({"twinbrick": ROOT / "twinbrick", "lego_engine": eng / "lego_engine", "lego_gui": eng / "lego_gui"})
    dst = remote_path(path)
    proc = ssh(alias, f"mkdir -p {dst} && tar xf - -C {dst}", stdin=data, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"copying to the server failed: {_err(proc)}")
    return path


def check(alias: str) -> Dict:
    """Can the server build sites? {"ok", "python", "packages": {...}, "message"}."""
    server = load_servers().get(alias) or Server(alias)
    probe = ("import importlib, json, sys\n"
             "out = {'python': sys.version.split()[0], 'packages': {}}\n"
             "for m in ('numpy', 'scipy', 'trimesh', 'PIL', 'igl', 'manifold3d', 'lego_engine', 'twinbrick',"
             " 'vtkmodules'):\n"
             "    try:\n        importlib.import_module(m); out['packages'][m] = True\n"
             "    except Exception:\n        out['packages'][m] = False\n"
             "print('TWINBRICK_CHECK ' + json.dumps(out))\n")
    cmd = (f"cd {remote_path(server.path)} 2>/dev/null || {{ echo 'TWINBRICK_NOPATH'; exit 3; }}; "
           f"PYTHONPATH=. {remote_path(server.python)} -c {shlex.quote(probe)}")
    try:
        proc = ssh(alias, cmd, timeout=60)
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "no answer (timeout)"}
    out = (proc.stdout or b"").decode(errors="replace")
    if "TWINBRICK_NOPATH" in out:
        return {"ok": False, "message": f"the path {server.path} is not on the server (server sync copies it)"}
    line = [x for x in out.splitlines() if x.startswith("TWINBRICK_CHECK ")]
    if not line:
        return {"ok": False, "message": f"no Python answer: {_err(proc)}"}
    info = json.loads(line[0].split(" ", 1)[1])
    need = ("numpy", "scipy", "trimesh", "PIL", "igl", "manifold3d", "lego_engine", "twinbrick")
    missing = [m for m in need if not info["packages"].get(m)]
    info.update(ok=not missing, message="ready" if not missing else "missing: " + ", ".join(missing))
    if not info["packages"].get("vtkmodules"):
        info["message"] += " (no vtk: no pictures of the bricks)"
    return info


def strip_local(argv: List[str]) -> List[str]:
    """The command line without what only makes sense here (--server, --out, --open)."""
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in ("--server", "--out"):
            skip = True
            continue
        if a == "--open" or a.startswith(("--server=", "--out=")):
            continue
        out.append(a)
    return out


def _scrub(out_dir: Path, replace: Dict[str, str]):
    """Every server path in the text files that came back replaced by the local one (longest first)."""
    pairs = sorted(((a, b) for a, b in replace.items() if a), key=lambda ab: -len(ab[0]))
    for f in out_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in TEXT_SUFFIXES:
            text = f.read_text(errors="replace")
            new = text
            for a, b in pairs:
                new = new.replace(a, b)
            if new != text:
                f.write_text(new)


def run_site(alias: str, argv: List[str], out_dir, progress: Optional[Callable[[str], None]] = None,
             timeout: float = 6 * 3600) -> Dict:
    """`python3 -m twinbrick <argv>` on the server, the results into `out_dir` here. `argv` is a TwinBrick it command
    line without --server, --out and --open. Returns the run's summary with local paths."""
    say = progress or (lambda _t: None)
    server = load_servers().get(alias)
    if server is None:
        raise RuntimeError(f"{alias} is not set up as a server (twinbrick-it server add {alias})")
    out_dir = Path(out_dir).resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise RuntimeError(f"the results folder is not empty: {out_dir}")
    if any(a in LOCAL_ONLY or a.startswith(tuple(x + "=" for x in LOCAL_ONLY)) for a in argv):
        raise ValueError("leave --server, --out and --open out of the server's arguments")
    say(f"server {alias}: a work folder ...")
    proc = ssh(alias, 'mktemp -d "${TMPDIR:-/tmp}/twinbrick.XXXXXX"', timeout=60)
    tmp = (proc.stdout or b"").decode().strip()
    if proc.returncode != 0 or not re.match(r"^/[^\s'\"]*/twinbrick\.[A-Za-z0-9]+$", tmp):
        raise RuntimeError(f"no work folder on the server: {_err(proc)}")
    qtmp, remote_out = shlex.quote(tmp), f"{tmp}/out"
    files: List[Path] = []
    try:
        path = remote_path(server.path)
        cmd = (f"cd {path} && PYTHONPATH={path} QT_QPA_PLATFORM=offscreen {remote_path(server.python)} -m twinbrick "
               f"{' '.join(shlex.quote(a) for a in argv)} --out {shlex.quote(remote_out)}")
        say(f"server {alias}: building the site ...")
        t = time.time()
        p = subprocess.Popen([SSH, *SSH_OPTS, check_alias(alias), cmd], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        lines = []
        for raw in p.stderr:
            line = raw.decode(errors="replace").rstrip().replace(tmp, "<work>")
            lines.append(line)
            if line and not line.startswith("picture of layer"):
                say(f"[{alias}] {line}")
        stdout = p.stdout.read().decode(errors="replace")
        code = p.wait(timeout=timeout)
        if code not in (0, 2):
            raise RuntimeError(f"the build on the server failed (exit {code}): " + "\n".join(lines[-12:]))
        say(f"server {alias}: done in {time.time() - t:.0f} s, fetching the results ...")
        proc = ssh(alias, f"tar cf - -C {shlex.quote(remote_out)} .", timeout=3600)
        if proc.returncode != 0:
            raise RuntimeError(f"fetching the results failed: {_err(proc)}")
        files = _untar(proc.stdout, out_dir)
    finally:
        ssh(alias, f"rm -rf {qtmp}", timeout=60)
    _scrub(out_dir, {remote_out: str(out_dir), tmp: str(out_dir)})
    try:
        summary = json.loads(stdout.replace(remote_out, str(out_dir)).replace(tmp, str(out_dir)))
    except ValueError:
        summary = {}
    summary["server_files"] = len(files)
    say(f"results: {out_dir} ({len(files)} files)")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="twinbrick-it server", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="what", required=True)
    sub.add_parser("list", help="the hosts of ~/.ssh/config and the servers set up here")
    a = sub.add_parser("add", help="set up a server: an ssh alias and where TwinBrick it lives on it")
    a.add_argument("host")
    a.add_argument("path", nargs="?", default=DEFAULT_PATH)
    a.add_argument("--python", default="python3", help="the server's Python (default python3)")
    for name, text in (("remove", "forget a server"), ("check", "can the server build sites?"),
                       ("sync", "copy this TwinBrick it and its engine to the server's path")):
        sub.add_parser(name, help=text).add_argument("host")
    args = ap.parse_args(argv)
    try:
        if args.what == "list":
            servers = load_servers()
            for h in known_hosts():
                s = servers.get(h)
                print(f"{h:24s} " + (f"{s.path}  ({s.python})" if s else "-  (twinbrick-it server add HOST)"))
            print(f"settings: {servers_file()}", file=sys.stderr)
            return 0
        if args.what == "add":
            s = add_server(args.host, args.path, args.python)
            print(f"{s.alias}: {s.path} ({s.python}) - saved in {servers_file()}")
            return 0
        if args.what == "remove":
            return 0 if remove_server(args.host) else 1
        if args.what == "sync":
            print(f"{args.host}: copied to {sync_code(args.host)}")
            return 0
        info = check(args.host)
        print(json.dumps(info, indent=1))
        return 0 if info.get("ok") else 1
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
