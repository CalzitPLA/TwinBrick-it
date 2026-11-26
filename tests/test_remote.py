"""Servers over ssh, through a stand-in for ssh that runs the commands here in a fake server home - no network, no
real host, nothing of the user's ssh setup read: the servers file, copying the code, a whole remote build, and that
nothing of the server is left behind or comes back."""
import json
import os
import stat
from pathlib import Path

import pytest

from twinbrick import engine, remote
from twinbrick.cli import main as cli_main

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    server_home = tmp_path / "server_home"
    server_home.mkdir()
    fake = tmp_path / "fake_ssh"
    fake.write_text('#!/usr/bin/env bash\n'
                    'while [ $# -gt 0 ]; do case "$1" in -o) shift 2 ;; --) shift ;; *) break ;; esac; done\n'
                    f'echo "$1" >> {tmp_path / "ssh_calls"}\n'
                    'shift\n'
                    f'export TMPDIR={server_home}\n'
                    f'cd {server_home}\n'
                    'exec bash -c "$*"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(remote, "SSH", str(fake))
    return tmp_path


def test_servers_are_kept_outside_the_repository(fake_ssh):
    s = remote.add_server("lab-a", "~/tb", "python3")
    path = remote.servers_file()
    assert path.is_file() and ROOT not in path.parents
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert remote.load_servers()["lab-a"] == s
    for bad in ("-oProxyCommand=x", "a b", ""):
        with pytest.raises(ValueError):
            remote.add_server(bad, "~/x")
    assert remote.remove_server("lab-a") and not remote.load_servers()


def test_hosts_are_aliases_only(tmp_path):
    cfg = tmp_path / "ssh_config"
    cfg.write_text("Host lab-a lab-b\n  HostName 192.0.2.7\n  User someone\nHost *\n  ServerAliveInterval 30\n")
    hosts = remote.known_hosts(cfg)
    assert hosts == ["lab-a", "lab-b"]


def test_strip_local():
    argv = ["51.3,7.6", "--server", "lab-a", "--out", "/x", "--open", "--server=lab-b", "--out=/y", "--scale", "750"]
    assert remote.strip_local(argv) == ["51.3,7.6", "--scale", "750"]


def test_a_build_on_the_server(fake_ssh, tmp_path):
    import sys
    if engine.legolize_root() is None:
        pytest.skip("Legolize it not found")
    home = tmp_path / "server_home"
    remote.add_server("lab-a", str(home / "tb"), sys.executable)
    remote.sync_code("lab-a")
    assert (home / "tb" / "twinbrick" / "site.py").exists() and (home / "tb" / "lego_engine").is_dir()
    info = remote.check("lab-a")
    assert info["ok"], info
    out = tmp_path / "results"
    code = cli_main(["51.3,7.67", "--shape", "square", "--size", "48", "--scale", "750", "--terrain", "flat",
                     "--buildings", "none", "--trees", "none", "--no-landcover", "--bricks", "bricky", "--no-png",
                     "--server", "lab-a", "--out", str(out)])
    assert code == 0
    assert sorted(p.name for p in home.iterdir()) == ["tb"]               # the work folder is gone
    rec = json.loads((out / "site.json").read_text())
    assert str(out) in rec["tiles"][0]["files"]["mesh"]                   # local paths in what came back
    for f in out.rglob("*"):
        if f.is_file() and f.suffix in (".json", ".txt", ".csv", ".md"):
            assert str(home) not in f.read_text(), f
    calls = (tmp_path / "ssh_calls").read_text().split()
    assert set(calls) == {"lab-a"}
