"""Guard tests for #66 — MOCK_DMX fallback must never mutate the git-tracked
sample fixture. The fallback path (MOCK_DMX=1, no QLC_WORKSPACE, no
~/.qlcplus/default.qxw) must redirect WORKSPACE_PATH to a scratch copy.
"""
import importlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample.qxw"


def _reload_app_in_fallback_mode(monkeypatch, tmp_home, persist=False, tmp_scratch_root=None):
    """Reload app.py with MOCK_DMX=1, no QLC_WORKSPACE, and HOME pointed at an
    empty tmp dir so ~/.qlcplus/default.qxw is absent — exercising the fallback
    branch rather than an explicit-QLC_WORKSPACE path.

    tmp_scratch_root, when given, is patched in as tempfile.gettempdir() so each
    test gets its own scratch location instead of sharing the real global temp
    dir — avoids cross-test coupling under parallel test execution.
    """
    monkeypatch.setenv("MOCK_DMX", "1")
    monkeypatch.delenv("QLC_WORKSPACE", raising=False)
    if persist:
        monkeypatch.setenv("MOCK_DMX_PERSIST", "1")
    else:
        monkeypatch.delenv("MOCK_DMX_PERSIST", raising=False)
    monkeypatch.setenv("HOME", str(tmp_home))
    if tmp_scratch_root is not None:
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_scratch_root))

    import app as _app_module

    importlib.reload(_app_module)
    return _app_module


class TestMockDmxFallbackWorkspace:
    def test_fallback_workspace_is_not_the_repo_fixture(self, monkeypatch, tmp_path):
        fixture_bytes_before = _FIXTURE.read_bytes()
        scratch_root = tmp_path / "scratch"
        app_module = _reload_app_in_fallback_mode(
            monkeypatch, tmp_path / "home", tmp_scratch_root=scratch_root
        )
        try:
            assert app_module.WORKSPACE_PATH != _FIXTURE
            assert str(app_module.WORKSPACE_PATH).startswith(str(scratch_root))
            assert app_module.WORKSPACE_PATH.exists()
            assert app_module.WORKSPACE_PATH.read_bytes() == fixture_bytes_before
        finally:
            monkeypatch.delenv("MOCK_DMX", raising=False)
            monkeypatch.delenv("HOME", raising=False)
            importlib.reload(app_module)
        assert _FIXTURE.read_bytes() == fixture_bytes_before

    def test_writing_to_fallback_workspace_does_not_touch_fixture(self, monkeypatch, tmp_path):
        """Simulates a writer (scene save / chase creation) hitting WORKSPACE_PATH —
        the git-tracked fixture must remain byte-identical."""
        fixture_bytes_before = _FIXTURE.read_bytes()
        app_module = _reload_app_in_fallback_mode(
            monkeypatch, tmp_path / "home", tmp_scratch_root=tmp_path / "scratch"
        )
        try:
            # Mutate the scratch copy the way a real writer (tree.write(...)) would.
            with open(app_module.WORKSPACE_PATH, "ab") as f:
                f.write(b"<!-- test mutation -->")
            assert app_module.WORKSPACE_PATH.read_bytes() != fixture_bytes_before
        finally:
            monkeypatch.delenv("MOCK_DMX", raising=False)
            monkeypatch.delenv("HOME", raising=False)
            importlib.reload(app_module)
        assert _FIXTURE.read_bytes() == fixture_bytes_before, (
            "tests/fixtures/sample.qxw was mutated — a workspace writer leaked "
            "into the git-tracked fixture"
        )

    def test_persist_flag_reuses_existing_scratch_copy(self, monkeypatch, tmp_path):
        fixture_bytes_before = _FIXTURE.read_bytes()
        app_module = _reload_app_in_fallback_mode(
            monkeypatch, tmp_path / "home", persist=True, tmp_scratch_root=tmp_path / "scratch"
        )
        try:
            scratch = app_module.WORKSPACE_PATH
            marker = b"<!-- persisted marker -->"
            with open(scratch, "ab") as f:
                f.write(marker)

            # Reload again with MOCK_DMX_PERSIST=1 — scratch file already exists,
            # so it should be reused rather than overwritten from the fixture.
            importlib.reload(app_module)
            assert app_module.WORKSPACE_PATH.read_bytes().endswith(marker)
        finally:
            monkeypatch.delenv("MOCK_DMX", raising=False)
            monkeypatch.delenv("MOCK_DMX_PERSIST", raising=False)
            monkeypatch.delenv("HOME", raising=False)
            importlib.reload(app_module)
        assert _FIXTURE.read_bytes() == fixture_bytes_before

    def test_explicit_qlc_workspace_is_unaffected(self, monkeypatch, tmp_path):
        """An explicit QLC_WORKSPACE pointing into the repo is the user's choice —
        only the implicit fallback gets copy-on-use treatment."""
        monkeypatch.setenv("MOCK_DMX", "1")
        monkeypatch.setenv("QLC_WORKSPACE", str(_FIXTURE))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))

        import app as _app_module

        importlib.reload(_app_module)
        try:
            assert _app_module.WORKSPACE_PATH == _FIXTURE
        finally:
            monkeypatch.delenv("MOCK_DMX", raising=False)
            monkeypatch.delenv("QLC_WORKSPACE", raising=False)
            monkeypatch.delenv("HOME", raising=False)
            importlib.reload(_app_module)
