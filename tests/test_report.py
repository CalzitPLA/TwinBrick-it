"""The results page: one HTML file with the site's numbers, pictures, the 3D data of its bricks and three.js inlined."""
import json

import pytest

from twinbrick import engine, report
from twinbrick.cli import main as cli_main


def test_a_results_page(tmp_path, capsys):
    if engine.legolize_root() is None:
        pytest.skip("Legolize it not found")
    site = tmp_path / "flat_square" / "run"
    assert cli_main(["51.3,7.67", "--shape", "square", "--size", "48", "--scale", "750", "--terrain", "flat",
                     "--buildings", "none", "--trees", "none", "--no-landcover", "--bricks", "bricky",
                     "--out", str(site)]) == 0
    capsys.readouterr()
    found = report.find_sites([tmp_path])
    assert found == [site]
    s = report.collect(site)
    b = s["tiles"][0]["bricks"]
    assert b and len(b["b"]) == b["stats"]["bricks"] and b["colours"][0]["name"]
    assert s["name"] == "Flat square"
    assert s["cmd"].startswith("twinbrick-it cli --at=51.3")
    out = report.write([s], tmp_path / "page.html")
    text = out.read_text()
    assert "THREE" in text and "OrbitControls" in text and 'data-src="d0"' in text
    data = text.split('<script type="application/json" id="d0">')[1].split("</script>")[0]
    assert json.loads(data)["stats"]["bricks"] == b["stats"]["bricks"]
    assert "https://" not in text.replace("https://www.openstreetmap.org", "").split("<script>")[0]
    assert report.title("51.3001N_7.6749E_hexagon", {"spec": {"shape": "hexagon"}, "place": {"text": "x"}}) == \
        "Hexagon at x"
