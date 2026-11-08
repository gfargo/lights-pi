"""Group fixture IDs are always integers.

Groups imported from QLC+ were stored with string IDs ("4"). The API normalises input to ints, so
DELETE /api/groups/<g>/fixtures with [4] compared 4 against "4" and silently removed nothing.
"""
import json

import app as app_module
import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    gf = tmp_path / "fixture_groups.json"
    gf.write_text(json.dumps({"groups": {
        "Work Light": {"fixtures": ["4"], "description": "Imported from QLC+ (ID: 2)"},
        "All Lights": {"fixtures": ["5", "4", "3", "0"], "description": ""},
    }}))
    monkeypatch.setattr(app_module, "GROUPS_FILE", gf)
    monkeypatch.setattr(app_module, "_groups_file", lambda: gf)
    monkeypatch.setattr(app_module, "_existing_fixture_ids", lambda: {0, 1, 3, 4, 5})
    monkeypatch.setattr(app_module, "_emit", lambda *a, **k: None)
    return app_module.app.test_client(), gf


def test_load_groups_coerces_string_ids_to_ints(client):
    _, _ = client
    assert app_module._load_groups()["All Lights"]["fixtures"] == [5, 4, 3, 0]


def test_list_groups_returns_ints(client):
    c, _ = client
    groups = {g["name"]: g["fixtures"] for g in c.get("/api/groups").get_json()["groups"]}
    assert groups["Work Light"] == [4]


def test_remove_fixture_stored_as_string_actually_removes_it(client):
    c, gf = client
    r = c.delete("/api/groups/Work%20Light/fixtures", json={"fixtures": [4]})
    assert r.get_json()["group"]["fixtures"] == []
    assert json.loads(gf.read_text())["groups"]["Work Light"]["fixtures"] == []


def test_add_after_remove_keeps_a_clean_int_list(client):
    c, gf = client
    c.delete("/api/groups/Work%20Light/fixtures", json={"fixtures": [4]})
    r = c.post("/api/groups/Work%20Light/fixtures", json={"fixtures": [1]})
    assert r.get_json()["group"]["fixtures"] == [1]


def test_add_does_not_duplicate_an_id_stored_as_string(client):
    c, _ = client
    r = c.post("/api/groups/All%20Lights/fixtures", json={"fixtures": [4, 1]})
    assert r.get_json()["group"]["fixtures"] == [5, 4, 3, 0, 1]
