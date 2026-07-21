"""Tests for BACKUP_INCLUDE_ENV gating in scripts/services/lighting-backup.sh.

Regression coverage for gfargo/lights-pi#71: the nightly snapshot used to
always embed ~/.env (production API keys) and push it, unencrypted, to
whatever BACKUP_REMOTE points at. create_snapshot() must only archive .env
when BACKUP_INCLUDE_ENV=1 is explicitly set.
"""
import subprocess
import tarfile
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "services"
    / "lighting-backup.sh"
)


def run_create_snapshot(tmp_path, include_env=None):
    """Populate a fake HOME with control-server/ and .env, run
    create_snapshot(), and return the set of top-level archive members.
    """
    home = tmp_path / "home"
    (home / "control-server").mkdir(parents=True)
    (home / "control-server" / "app.py").write_text("# fixture\n")
    (home / ".env").write_text("ANTHROPIC_API_KEY=sk-fixture-secret\n")

    backup_dir = tmp_path / "backups"

    env = {
        "BACKUP_DIR": str(backup_dir),
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
    }
    if include_env is not None:
        env["BACKUP_INCLUDE_ENV"] = include_env

    result = subprocess.run(
        ["bash", "-c", f"source {SCRIPT}; create_snapshot"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    snapshot_path = Path(result.stdout.strip().splitlines()[-1])
    with tarfile.open(snapshot_path) as tf:
        members = {name.split("/")[0] for name in tf.getnames()}

    return members, result.stderr


class TestBackupIncludeEnvGate:
    def test_env_excluded_by_default(self, tmp_path):
        members, _ = run_create_snapshot(tmp_path)

        assert ".env" not in members
        assert "control-server" in members

    def test_env_excluded_when_explicitly_zero(self, tmp_path):
        members, _ = run_create_snapshot(tmp_path, include_env="0")

        assert ".env" not in members

    def test_env_included_when_opted_in(self, tmp_path):
        members, _ = run_create_snapshot(tmp_path, include_env="1")

        assert ".env" in members
        assert "control-server" in members

    def test_warns_when_env_included_and_remote_set(self, tmp_path):
        home = tmp_path / "home"
        (home / "control-server").mkdir(parents=True)
        (home / ".env").write_text("ANTHROPIC_API_KEY=sk-fixture-secret\n")
        backup_dir = tmp_path / "backups"

        result = subprocess.run(
            ["bash", "-c", f"source {SCRIPT}; create_snapshot"],
            env={
                "BACKUP_DIR": str(backup_dir),
                "HOME": str(home),
                "PATH": "/usr/bin:/bin",
                "BACKUP_INCLUDE_ENV": "1",
                "BACKUP_REMOTE": "s3://example-bucket/lights-pi/",
            },
            check=True,
            capture_output=True,
            text=True,
        )

        assert "WARN" in result.stderr
        assert "unencrypted" in result.stderr

    def test_no_warning_when_env_excluded(self, tmp_path):
        _, stderr = run_create_snapshot(tmp_path)

        assert "WARN" not in stderr
