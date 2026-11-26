"""The TouchOSC layout generator (#27).

A .tosc is zlib-compressed XML. These tests read it back the way TouchOSC
does (decompress, parse) and check every control's OSC address against the
addresses the Pi listens on (docs/OSC.md). TouchOSC itself loaded the
output of this generator with no warnings when it was written; the
structure checked here is what made that work.
"""
import xml.etree.ElementTree as ET
import zlib

import touchosc_layout as tl
from touchosc_layout import FADERS, build_layout, channel_faders, layout_xml

SCENES = [{"name": "Lights ON", "address": "/scene/1"}, {"name": "Red", "address": "/scene/0"}]
CHASES = [{"name": "Sunset", "address": "/chase/12"}]
FADER_LIST = [{"name": f"F{i}", "address": f"/fixture/0/{i}"} for i in range(10)]


def _tree(scenes=SCENES, chases=CHASES, faders=FADER_LIST):
    data = build_layout(scenes, chases, faders)
    return ET.fromstring(zlib.decompress(data))


def _prop(node, key):
    for p in node.find("properties"):
        if p.find("key").text == key:
            return p.find("value")
    return None


def _controls(root, kind):
    """(name, address) for every node of *kind* that sends OSC."""
    out = []
    for node in root.iter("node"):
        if node.get("type") != kind:
            continue
        path = node.find("messages/osc/path/partial/value")
        out.append((_prop(node, "name").text, path.text if path is not None else None))
    return out


def _labels(root):
    texts = []
    for node in root.iter("node"):
        if node.get("type") == "LABEL":
            texts.append(node.find("values/value/default").text)
    return texts


def test_is_zlib_compressed_lexml_v3():
    root = _tree()
    assert root.tag == "lexml" and root.get("version") == "3"
    assert root[0].get("type") == "GROUP"
    assert root[0].find("children/node").get("type") == "PAGER"


def test_one_button_per_scene_with_a_label():
    root = _tree()
    addresses = [a for _, a in _controls(root, "BUTTON")]
    assert "/scene/1" in addresses and "/scene/0" in addresses
    assert "Lights ON" in _labels(root) and "Red" in _labels(root)


def test_scene_buttons_are_momentary_and_chases_toggle():
    root = _tree()
    for node in root.iter("node"):
        if node.get("type") != "BUTTON":
            continue
        address = node.find("messages/osc/path/partial/value").text
        kind = int(_prop(node, "buttonType").text)
        if address.startswith("/chase/"):
            assert kind == 2, "chases need a toggle: 0 stops the chase"
        else:
            assert kind == 0


def test_every_page_has_master_and_blackout():
    root = _tree()
    pager = root[0].find("children/node")
    pages = pager.findall("children/node")
    assert [_prop(p, "tabLabel").text for p in pages] == ["Scenes", "Faders", "Chases"]
    for page in pages:
        addresses = [n.find("messages/osc/path/partial/value").text
                     for n in page.iter("node") if n.find("messages") is not None]
        assert "/master" in addresses and "/blackout" in addresses


def test_cue_transport_on_the_scenes_page():
    addresses = [a for _, a in _controls(_tree(), "BUTTON")]
    assert "/cue/go" in addresses and "/cue/stop" in addresses


def test_eight_channel_faders():
    channel = [a for _, a in _controls(_tree(), "FADER") if a.startswith("/fixture/")]
    assert channel == [f"/fixture/0/{i}" for i in range(FADERS)]


def test_controls_send_and_receive_a_float_on_x():
    root = _tree()
    for osc in root.iter("osc"):
        assert osc.find("send").text == "1" and osc.find("receive").text == "1"
        assert osc.find("triggers/trigger/var").text == "x"
        arg = osc.find("arguments/partial")
        assert arg.find("type").text == "VALUE" and arg.find("conversion").text == "FLOAT"


def test_many_scenes_spill_onto_more_pages():
    scenes = [{"name": f"S{i}", "address": f"/scene/{i}"} for i in range(30)]
    root = _tree(scenes=scenes, chases=[])
    tabs = [_prop(p, "tabLabel").text for p in root[0].find("children/node").findall("children/node")]
    assert tabs == ["Scenes 1", "Scenes 2", "Faders"]
    assert len([a for _, a in _controls(root, "BUTTON") if a.startswith("/scene/")]) == 30


def test_empty_workspace_still_builds():
    root = _tree(scenes=[], chases=[], faders=[])
    assert "No scenes in this workspace yet" in _labels(root)
    assert "No fixtures in this workspace yet" in _labels(root)


def test_awkward_names_survive():
    scenes = [{"name": "Rock & Roll <loud> ]]> end", "address": "/scene/9"}]
    assert "Rock & Roll <loud> ]]> end" in _labels(_tree(scenes=scenes))


def test_output_is_stable():
    assert layout_xml(SCENES, CHASES, FADER_LIST) == layout_xml(SCENES, CHASES, FADER_LIST)


def test_node_ids_are_unique():
    ids = [n.get("ID") for n in _tree().iter("node")]
    assert len(ids) == len(set(ids))


def test_channel_faders_walks_fixtures_in_order():
    fixtures = [
        {"id": 0, "name": "Par", "channel_info": [{"offset": 0, "name": "Dimmer"}, {"offset": 1, "name": None}]},
        {"id": 3, "name": "Wash", "channel_info": [{"offset": "x"}, {"offset": 0, "name": "Red"}]},
    ]
    assert channel_faders(fixtures) == [
        {"name": "Par\nDimmer", "address": "/fixture/0/0"},
        {"name": "Par\nCh 2", "address": "/fixture/0/1"},
        {"name": "Wash\nRed", "address": "/fixture/3/0"},
    ]
    assert len(channel_faders([{"id": 1, "name": "Big", "channel_info": [{"offset": i} for i in range(20)]}])) == 8


class _Resp:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self.data


def _http_error(code, body=b""):
    import io
    import urllib.error
    return urllib.error.HTTPError("http://x/api/osc/touchosc", code, "err", {}, io.BytesIO(body))


def test_cli_writes_the_layout(tmp_path, monkeypatch):
    import urllib.request
    blob = tl.build_layout([], [], [])
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: _Resp(blob))
    out = tmp_path / "x.tosc"
    assert tl.main(["http://pi:5000/", str(out)]) == 0
    assert out.read_bytes() == blob


def test_cli_explains_the_password_gate(tmp_path, monkeypatch, capsys):
    import urllib.request

    def refuse(url, timeout):
        raise _http_error(401, b'{"error": "unauthorized"}')

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert tl.main(["http://pi:5000", str(tmp_path / "x.tosc")]) == 1
    assert "Download layout" in capsys.readouterr().err
    assert not (tmp_path / "x.tosc").exists()


def test_cli_reports_server_errors(tmp_path, monkeypatch, capsys):
    import urllib.request

    def fail(url, timeout):
        raise _http_error(500, b'{"error": "workspace is broken"}')

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    assert tl.main(["http://pi:5000", str(tmp_path / "x.tosc")]) == 1
    assert "HTTP 500: workspace is broken" in capsys.readouterr().err
