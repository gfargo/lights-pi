"""Tests for workspace-management helpers added in OSS-33.

Covers pure helpers (_safe_workspace_name, _list_workspace_files,
_active_workspace_name, _validate_qxw, _groups_file) plus the
route-level happy-paths and guards using a tmpdir QLC workspace dir.
"""
import json
import shutil
import textwrap
from pathlib import Path

import pytest

from app import (
    _active_workspace_name,
    _bust_scene_swatch_cache,
    _groups_file,
    _list_workspace_files,
    _safe_workspace_name,
    _set_active_workspace_name,
    _validate_qxw,
)


# ---------------------------------------------------------------------------
# _safe_workspace_name
# ---------------------------------------------------------------------------

class TestSafeWorkspaceName:
    def test_valid_simple(self):
        assert _safe_workspace_name("gig.qxw") == "gig.qxw"

    def test_valid_with_dash_dot(self):
        assert _safe_workspace_name("venue-a_2024.qxw") == "venue-a_2024.qxw"

    def test_valid_mixed_case(self):
        assert _safe_workspace_name("Studio.qxw") == "Studio.qxw"

    def test_rejects_default(self):
        assert _safe_workspace_name("default.qxw") is None

    def test_rejects_autostart(self):
        assert _safe_workspace_name("autostart.qxw") is None

    def test_rejects_no_extension(self):
        assert _safe_workspace_name("myvenue") is None

    def test_rejects_wrong_extension(self):
        assert _safe_workspace_name("myvenue.xml") is None

    def test_rejects_space_in_name(self):
        assert _safe_workspace_name("my venue.qxw") is None

    def test_rejects_traversal(self):
        assert _safe_workspace_name("../etc/passwd.qxw") is None

    def test_rejects_slash(self):
        assert _safe_workspace_name("foo/bar.qxw") is None

    def test_rejects_non_string(self):
        assert _safe_workspace_name(None) is None
        assert _safe_workspace_name(123) is None

    def test_strips_whitespace(self):
        assert _safe_workspace_name("  gig.qxw  ") == "gig.qxw"


# ---------------------------------------------------------------------------
# _validate_qxw
# ---------------------------------------------------------------------------

_VALID_QXW = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE Workspace>
    <Workspace xmlns="http://www.qlcplus.org/Workspace" CurrentWindow="VirtualConsole">
      <Engine></Engine>
    </Workspace>
""")

_WRONG_NS_QXW = textwrap.dedent("""\
    <?xml version="1.0"?>
    <Root xmlns="http://example.com/Other">
      <Child/>
    </Root>
""")


class TestValidateQxw:
    def test_valid_workspace(self, tmp_path):
        p = tmp_path / "studio.qxw"
        p.write_text(_VALID_QXW)
        assert _validate_qxw(p) is True

    def test_wrong_namespace(self, tmp_path):
        p = tmp_path / "wrong.qxw"
        p.write_text(_WRONG_NS_QXW)
        assert _validate_qxw(p) is False

    def test_not_xml(self, tmp_path):
        p = tmp_path / "bad.qxw"
        p.write_text("this is not xml!!")
        assert _validate_qxw(p) is False

    def test_missing_file(self, tmp_path):
        assert _validate_qxw(tmp_path / "missing.qxw") is False


# ---------------------------------------------------------------------------
# _active_workspace_name / _set_active_workspace_name (monkeypatched pointer)
# ---------------------------------------------------------------------------

class TestActiveWorkspaceName:
    def test_defaults_to_default_when_no_pointer(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app._WORKSPACE_POINTER", tmp_path / "current_workspace")
        assert _active_workspace_name() == "default"

    def test_reads_pointer_file(self, tmp_path, monkeypatch):
        pointer = tmp_path / "current_workspace"
        pointer.write_text("venue-a")
        monkeypatch.setattr("app._WORKSPACE_POINTER", pointer)
        assert _active_workspace_name() == "venue-a"

    def test_set_and_read_roundtrip(self, tmp_path, monkeypatch):
        pointer = tmp_path / "current_workspace"
        monkeypatch.setattr("app._WORKSPACE_POINTER", pointer)
        _set_active_workspace_name("my-gig")
        assert _active_workspace_name() == "my-gig"

    def test_empty_pointer_falls_back(self, tmp_path, monkeypatch):
        pointer = tmp_path / "current_workspace"
        pointer.write_text("   ")
        monkeypatch.setattr("app._WORKSPACE_POINTER", pointer)
        assert _active_workspace_name() == "default"


# ---------------------------------------------------------------------------
# _list_workspace_files
# ---------------------------------------------------------------------------

class TestListWorkspaceFiles:
    def _setup_dir(self, tmp_path, monkeypatch, names):
        monkeypatch.setattr("app.WORKSPACE_DIR", tmp_path)
        monkeypatch.setattr("app._WORKSPACE_POINTER", tmp_path / "current_workspace")
        for n in names:
            (tmp_path / n).write_text(_VALID_QXW)

    def test_excludes_reserved(self, tmp_path, monkeypatch):
        self._setup_dir(tmp_path, monkeypatch, ["default.qxw", "autostart.qxw", "venue.qxw"])
        result = _list_workspace_files()
        names = [w["filename"] for w in result]
        assert "default.qxw" not in names
        assert "autostart.qxw" not in names
        assert "venue.qxw" in names

    def test_active_flag(self, tmp_path, monkeypatch):
        self._setup_dir(tmp_path, monkeypatch, ["studio.qxw", "venue.qxw"])
        pointer = tmp_path / "current_workspace"
        pointer.write_text("studio")
        result = _list_workspace_files()
        by_name = {w["filename"]: w for w in result}
        assert by_name["studio.qxw"]["active"] is True
        assert by_name["venue.qxw"]["active"] is False

    def test_empty_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.WORKSPACE_DIR", tmp_path)
        monkeypatch.setattr("app._WORKSPACE_POINTER", tmp_path / "current_workspace")
        assert _list_workspace_files() == []


# ---------------------------------------------------------------------------
# _groups_file — per-workspace fallback
# ---------------------------------------------------------------------------

class TestGroupsFile:
    def test_returns_per_workspace_path_when_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.WORKSPACE_DIR", tmp_path)
        monkeypatch.setattr("app._WORKSPACE_POINTER", tmp_path / "current_workspace")
        pointer = tmp_path / "current_workspace"
        pointer.write_text("venue-a")
        per_ws = tmp_path / "fixture_groups.venue-a.json"
        per_ws.write_text("{}")
        result = _groups_file()
        assert result == per_ws

    def test_falls_back_to_global_when_per_workspace_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.WORKSPACE_DIR", tmp_path)
        monkeypatch.setattr("app._WORKSPACE_POINTER", tmp_path / "current_workspace")
        monkeypatch.setattr("app.GROUPS_FILE", tmp_path / "fixture_groups.json")
        pointer = tmp_path / "current_workspace"
        pointer.write_text("venue-a")
        # No per-workspace file; should return the global fallback
        result = _groups_file()
        assert result == tmp_path / "fixture_groups.json"


# ---------------------------------------------------------------------------
# _bust_scene_swatch_cache
# ---------------------------------------------------------------------------

class TestBustSceneSwatchCache:
    def test_clears_cache_and_resets_mtime(self, monkeypatch):
        import app as app_mod
        app_mod._scene_swatch_cache = {1: "data:image/svg+xml,..."}
        app_mod._scene_swatch_cache_mtime = 12345.0
        _bust_scene_swatch_cache()
        assert app_mod._scene_swatch_cache == {}
        assert app_mod._scene_swatch_cache_mtime == 0.0


# ---------------------------------------------------------------------------
# Route-level tests via Flask test client
# ---------------------------------------------------------------------------

@pytest.fixture()
def ws_dir(tmp_path, monkeypatch):
    """Patch WORKSPACE_DIR and related paths to a temp dir."""
    monkeypatch.setattr("app.WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr("app._WORKSPACE_POINTER", tmp_path / "current_workspace")
    monkeypatch.setattr("app.GROUPS_FILE", tmp_path / "fixture_groups.json")
    monkeypatch.setattr("app.IS_LOCAL", True)
    # Write the canonical "active" workspace so default.qxw exists
    (tmp_path / "default.qxw").write_text(_VALID_QXW)
    (tmp_path / "autostart.qxw").write_text(_VALID_QXW)
    return tmp_path


@pytest.fixture()
def client(ws_dir):
    import app as app_mod
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c


class TestListWorkspacesRoute:
    def test_empty_returns_success(self, client):
        r = client.get("/api/workspaces")
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["workspaces"] == []

    def test_lists_qxw_files(self, client, ws_dir):
        (ws_dir / "venue-a.qxw").write_text(_VALID_QXW)
        r = client.get("/api/workspaces")
        data = r.get_json()
        names = [w["filename"] for w in data["workspaces"]]
        assert "venue-a.qxw" in names
        assert "default.qxw" not in names


class TestGetCurrentWorkspaceRoute:
    def test_returns_default_initially(self, client):
        r = client.get("/api/workspaces/current")
        data = r.get_json()
        assert data["success"] is True
        assert data["name"] == "default"


class TestCreateWorkspaceRoute:
    def test_creates_new_workspace(self, client, ws_dir):
        r = client.post("/api/workspaces", json={"name": "venue-a"})
        assert r.status_code == 201
        data = r.get_json()
        assert data["success"] is True
        assert (ws_dir / "venue-a.qxw").exists()

    def test_creates_with_qxw_suffix_in_body(self, client, ws_dir):
        r = client.post("/api/workspaces", json={"name": "venue-b.qxw"})
        assert r.status_code == 201
        assert (ws_dir / "venue-b.qxw").exists()

    def test_conflict_409(self, client, ws_dir):
        (ws_dir / "existing.qxw").write_text(_VALID_QXW)
        r = client.post("/api/workspaces", json={"name": "existing"})
        assert r.status_code == 409

    def test_invalid_name_400(self, client):
        r = client.post("/api/workspaces", json={"name": "a b c"})
        assert r.status_code == 400

    def test_reserved_name_rejected(self, client):
        r = client.post("/api/workspaces", json={"name": "default"})
        assert r.status_code == 400

    def test_copy_from(self, client, ws_dir):
        (ws_dir / "source.qxw").write_text(_VALID_QXW)
        r = client.post("/api/workspaces", json={"name": "copy", "copy_from": "source"})
        assert r.status_code == 201
        assert (ws_dir / "copy.qxw").read_text() == _VALID_QXW

    def test_copy_from_missing_source_404(self, client):
        r = client.post("/api/workspaces", json={"name": "copy", "copy_from": "nonexistent"})
        assert r.status_code == 404


class TestDeleteWorkspaceRoute:
    def test_deletes_non_active(self, client, ws_dir):
        (ws_dir / "old-venue.qxw").write_text(_VALID_QXW)
        r = client.delete("/api/workspaces/old-venue.qxw")
        assert r.status_code == 200
        assert not (ws_dir / "old-venue.qxw").exists()

    def test_refuses_active_workspace(self, client, ws_dir):
        pointer = ws_dir / "current_workspace"
        pointer.write_text("active-ws")
        (ws_dir / "active-ws.qxw").write_text(_VALID_QXW)
        r = client.delete("/api/workspaces/active-ws.qxw")
        assert r.status_code == 409

    def test_not_found_404(self, client):
        r = client.delete("/api/workspaces/missing.qxw")
        assert r.status_code == 404


class TestLoadWorkspaceRoute:
    def test_load_switches_workspace(self, client, ws_dir, monkeypatch):
        (ws_dir / "venue-a.qxw").write_text(_VALID_QXW)
        # Stub out restart so no real systemctl runs
        monkeypatch.setattr("app._restart_qlc", lambda: {"success": True, "output": "", "error": ""})
        r = client.post("/api/workspaces/venue-a.qxw/load")
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["name"] == "venue-a"
        # Pointer file updated
        assert (ws_dir / "current_workspace").read_text() == "venue-a"

    def test_load_already_active_noop(self, client, ws_dir):
        pointer = ws_dir / "current_workspace"
        pointer.write_text("venue-a")
        (ws_dir / "venue-a.qxw").write_text(_VALID_QXW)
        r = client.post("/api/workspaces/venue-a.qxw/load")
        assert r.status_code == 200
        data = r.get_json()
        assert data["message"] == "already active"

    def test_load_missing_404(self, client):
        r = client.post("/api/workspaces/missing.qxw/load")
        assert r.status_code == 404

    def test_load_reserved_name_400(self, client):
        # 'default.qxw' is reserved and should be rejected even if the file exists
        r = client.post("/api/workspaces/default.qxw/load")
        assert r.status_code == 400
