"""GET /api/osc/touchosc serves a layout for the loaded workspace (#27)."""
import xml.etree.ElementTree as ET
import zlib

SCENE_XML = (
    '<Function ID="0" Type="Scene" Name="placeholder" Path="Tests">'
    '<Speed FadeIn="0" FadeOut="0" Duration="0"/>'
    '<FixtureVal ID="1">0,200</FixtureVal>'
    "</Function>"
)


def test_layout_has_the_workspace_scenes(flask_client):
    client, _ = flask_client
    saved = client.post("/api/scenes/save", json={"name": "Stage Wash", "path": "Tests", "scene_xml": SCENE_XML})
    assert saved.status_code == 200, saved.get_data(as_text=True)[:300]

    response = client.get("/api/osc/touchosc")
    assert response.status_code == 200
    assert "lights-pi.tosc" in response.headers["Content-Disposition"]
    root = ET.fromstring(zlib.decompress(response.data))

    book = client.get("/api/osc/addresses").get_json()
    assert any(s["name"] == "Stage Wash" for s in book["scenes"])
    addresses = {v.text for v in root.iter("value") if v.text and v.text.startswith("/")}
    labels = {v.text for v in root.iter("default")}
    for scene in book["scenes"]:
        assert scene["address"] in addresses
    assert "Stage Wash" in labels
    assert "/master" in addresses and "/blackout" in addresses
