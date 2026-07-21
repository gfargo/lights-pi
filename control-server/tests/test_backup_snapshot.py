"""Tests for atomic snapshot creation and remote-push handling in
scripts/services/lighting-backup.sh, plus static regressions for the
status/logs/unit-file fixes in scripts/lib/backup.sh.

Regression coverage for gfargo/lights-pi#72:
1. `create_snapshot` must write to a `.tmp` file and `gzip -t` it before the
   final `mv` — an interrupted run must never leave a corrupt archive under
   the real snapshot name (which would count toward retention).
2. `backup_timer_status` must not print "Service not installed" for a
   healthy idle oneshot service.
3. `backup_timer_logs` must read the full unit's journal (both the `logger`
   tag and ExecStart stderr), not just the `logger -t` tag.
4. Remote push failures must be non-fatal (local snapshot retained, distinct
   exit code) and scp must be non-interactive.
"""
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SNAPSHOT_SCRIPT = REPO_ROOT / "scripts" / "services" / "lighting-backup.sh"
BACKUP_LIB = REPO_ROOT / "scripts" / "lib" / "backup.sh"

PUSH_FAILED_EXIT_CODE = 75


def _make_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("BACKUP_REMOTE=\n")
    # create_snapshot only tars .config/qlcplus, .qlcplus, control-server,
    # and (opt-in via BACKUP_INCLUDE_ENV) .env — a bare .env alone leaves
    # `dirs` empty and create_snapshot bails out with "nothing to back up".
    qlcplus_dir = home / ".qlcplus"
    qlcplus_dir.mkdir()
    (qlcplus_dir / "studio.qxw").write_text("<Workspace/>\n")
    return home


def _stub_bin(tmp_path, name, script_body):
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / name
    stub.write_text(f"#!/usr/bin/env bash\n{script_body}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return bin_dir


class TestAtomicSnapshot:
    def test_success_leaves_no_tmp_and_passes_gzip_test(self, tmp_path):
        home = _make_home(tmp_path)
        backup_dir = tmp_path / "backups"

        subprocess.run(
            ["bash", "-c", f"source {SNAPSHOT_SCRIPT}; create_snapshot"],
            env={
                "HOME": str(home),
                "BACKUP_DIR": str(backup_dir),
                "PATH": "/usr/bin:/bin",
            },
            check=True,
            capture_output=True,
            text=True,
        )

        tar_files = list(backup_dir.glob("*.tar.gz"))
        tmp_files = list(backup_dir.glob("*.tmp"))
        assert len(tar_files) == 1
        assert tmp_files == []
        subprocess.run(["gzip", "-t", str(tar_files[0])], check=True)

    def test_interrupted_run_leaves_only_tmp_and_prune_ignores_it(self, tmp_path):
        home = _make_home(tmp_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Simulate a truncated/interrupted run: a stray .tmp sits alongside
        # a full set of otherwise-prunable daily snapshots.
        stray = backup_dir / "lights-pi-backup-2026-07-20.tar.gz.tmp"
        stray.write_text("not a valid gzip stream")

        import datetime

        end = datetime.date(2026, 7, 19)
        for i in range(10):
            date_str = (end - datetime.timedelta(days=i)).isoformat()
            (backup_dir / f"lights-pi-backup-{date_str}.tar.gz").touch()

        subprocess.run(
            ["bash", "-c", f"source {SNAPSHOT_SCRIPT}; prune_retention"],
            env={
                "HOME": str(home),
                "BACKUP_DIR": str(backup_dir),
                "BACKUP_RETENTION_DAYS": "7",
                "PATH": "/usr/bin:/bin",
            },
            check=True,
            capture_output=True,
            text=True,
        )

        # The stray .tmp is untouched — prune's glob only matches *.tar.gz.
        assert stray.exists()
        assert stray.read_text() == "not a valid gzip stream"

    def test_corrupt_tar_output_is_rejected_before_final_name(self, tmp_path):
        home = _make_home(tmp_path)
        backup_dir = tmp_path / "backups"

        # Stub `tar` to simulate a truncated/corrupt write (e.g. disk full
        # mid-write): it "succeeds" but produces invalid gzip content.
        # create_snapshot always calls `tar -czf "${tmp}" -C ...`, so $2 is
        # the output path regardless of which dirs get tarred.
        bin_dir = _stub_bin(
            tmp_path,
            "tar",
            'out="$2"\n'
            'echo "not a real gzip stream" > "$out"\n',
        )

        result = subprocess.run(
            ["bash", "-c", f"source {SNAPSHOT_SCRIPT}; create_snapshot"],
            env={
                "HOME": str(home),
                "BACKUP_DIR": str(backup_dir),
                "PATH": f"{bin_dir}:/usr/bin:/bin",
            },
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert list(backup_dir.glob("*.tar.gz")) == []


class TestMainPushHandling:
    def test_push_failure_is_non_fatal_and_retains_local_snapshot(self, tmp_path):
        home = _make_home(tmp_path)
        backup_dir = tmp_path / "backups"

        bin_dir = _stub_bin(tmp_path, "scp", "exit 1")

        result = subprocess.run(
            ["bash", "-c", f"source {SNAPSHOT_SCRIPT}; main"],
            env={
                "HOME": str(home),
                "BACKUP_DIR": str(backup_dir),
                "BACKUP_REMOTE": "user@bad-host:/backups",
                "PATH": f"{bin_dir}:/usr/bin:/bin",
            },
            capture_output=True,
            text=True,
        )

        assert result.returncode == PUSH_FAILED_EXIT_CODE
        tar_files = list(backup_dir.glob("*.tar.gz"))
        assert len(tar_files) == 1

    def test_successful_push_exits_zero(self, tmp_path):
        home = _make_home(tmp_path)
        backup_dir = tmp_path / "backups"

        bin_dir = _stub_bin(tmp_path, "scp", "exit 0")

        result = subprocess.run(
            ["bash", "-c", f"source {SNAPSHOT_SCRIPT}; main"],
            env={
                "HOME": str(home),
                "BACKUP_DIR": str(backup_dir),
                "BACKUP_REMOTE": "user@good-host:/backups",
                "PATH": f"{bin_dir}:/usr/bin:/bin",
            },
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0


class TestScpFlags:
    def test_push_remote_scp_branch_is_non_interactive(self):
        text = SNAPSHOT_SCRIPT.read_text()
        assert "BatchMode=yes" in text
        assert "StrictHostKeyChecking=accept-new" in text


class TestUnitFileNetworkOrdering:
    def test_service_unit_waits_for_network_online(self):
        text = BACKUP_LIB.read_text()
        assert "After=network-online.target" in text
        assert "Wants=network-online.target" in text


class TestBackupTimerStatusGating:
    def test_status_guards_with_systemctl_cat(self):
        text = BACKUP_LIB.read_text()
        assert "systemctl cat lighting-backup.service" in text

    def test_status_no_longer_unconditionally_prints_not_installed(self):
        # The old bug: `systemctl status ... || echo "Service not installed"`
        # fired that echo unconditionally for an idle oneshot unit (exit 3).
        text = BACKUP_LIB.read_text()
        assert (
            'systemctl status lighting-backup.service --no-pager 2>/dev/null || echo "Service not installed"'
            not in text
        )


class TestBackupTimerLogs:
    def test_logs_use_unit_scoped_journalctl(self):
        text = BACKUP_LIB.read_text()
        assert "journalctl -u lighting-backup.service" in text
        assert "journalctl -t lighting-backup " not in text
