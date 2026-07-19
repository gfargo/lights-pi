"""Tests for the GFS backup retention pruning in scripts/services/lighting-backup.sh.

`prune_retention` runs on the Pi to enforce a keep-7-daily / 4-weekly /
6-monthly grandfather-father-son schedule. These tests shell out to the real
bash function (sourced, not re-implemented in Python) against a temp
BACKUP_DIR populated with empty, dated snapshot files — regression coverage
for gfargo/lights-pi#69, where the weekly/monthly tiers kept the *oldest*
qualifying snapshots forever instead of rotating in the newest ones.
"""
import datetime
import subprocess
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "services"
    / "lighting-backup.sh"
)

SNAPSHOT_PREFIX = "lights-pi-backup-"
SNAPSHOT_SUFFIX = ".tar.gz"


def _filename(date_str):
    return f"{SNAPSHOT_PREFIX}{date_str}{SNAPSHOT_SUFFIX}"


def run_prune(tmp_path, dates, retention_days=7):
    """Touch one snapshot file per date, run prune_retention, return survivors."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(exist_ok=True)
    for date_str in dates:
        (backup_dir / _filename(date_str)).touch()

    subprocess.run(
        ["bash", "-c", f"source {SCRIPT}; prune_retention"],
        env={
            "BACKUP_DIR": str(backup_dir),
            "BACKUP_RETENTION_DAYS": str(retention_days),
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
        },
        check=True,
        capture_output=True,
        text=True,
    )

    return {
        p.name[len(SNAPSHOT_PREFIX) : -len(SNAPSHOT_SUFFIX)]
        for p in backup_dir.glob(f"{SNAPSHOT_PREFIX}*{SNAPSHOT_SUFFIX}")
    }


def daterange(end, days):
    """`days` consecutive ISO date strings ending on (and including) `end`."""
    return [
        (end - datetime.timedelta(days=days - 1 - i)).isoformat()
        for i in range(days)
    ]


def expected_gfs(dates, daily_keep=7, weekly_keep=4, monthly_keep=6):
    """Pure-Python reference implementation of the GFS keep-set spec."""
    parsed = sorted(datetime.date.fromisoformat(d) for d in dates)
    newest_first = list(reversed(parsed))

    daily = newest_first[:daily_keep]
    remaining = newest_first[daily_keep:]

    weekly = [d for d in remaining if d.isoweekday() == 7][:weekly_keep]
    monthly = [d for d in remaining if d.day == 1][:monthly_keep]

    return {d.isoformat() for d in daily + weekly + monthly}


class TestPruneRetentionGFS:
    def test_newest_sunday_survives_121_day_run(self, tmp_path):
        # The issue's exact repro: 121 consecutive dailies ending 2026-07-12.
        end = datetime.date(2026, 7, 12)
        dates = daterange(end, 121)

        survivors = run_prune(tmp_path, dates)

        assert "2026-07-05" in survivors  # newest Sunday — must be kept
        for stale_sunday in ("2026-03-15", "2026-03-22", "2026-03-29", "2026-04-05"):
            assert stale_sunday not in survivors

    def test_matches_gfs_reference_set(self, tmp_path):
        end = datetime.date(2026, 7, 12)
        dates = daterange(end, 121)

        survivors = run_prune(tmp_path, dates)

        assert survivors == expected_gfs(dates)

    def test_small_set_untouched(self, tmp_path):
        end = datetime.date(2026, 7, 12)
        dates = daterange(end, 5)  # fewer than daily_keep=7

        survivors = run_prune(tmp_path, dates)

        assert survivors == set(dates)

    def test_no_pruning_below_daily_keep_threshold(self, tmp_path):
        # Regression for the "18-file cliff": pruning must kick in once
        # dailies exceed daily_keep(7), not only once total files hit
        # daily+weekly+monthly (17). 10 consecutive days is well under the
        # old cliff but should already shed the non-tier-eligible excess.
        end = datetime.date(2026, 7, 8)
        dates = daterange(end, 10)

        survivors = run_prune(tmp_path, dates)

        # 2026-06-29 and 2026-06-30 are neither Sundays nor month-firsts
        # and fall outside the 7-newest daily window — must be pruned.
        assert "2026-06-29" not in survivors
        assert "2026-06-30" not in survivors
        assert len(survivors) < len(dates)

    def test_six_month_simulation_always_keeps_newest_sunday(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        start = datetime.date(2026, 1, 1)
        newest_sunday = None

        for offset in range(182):  # ~6 months of daily runs
            today = start + datetime.timedelta(days=offset)
            (backup_dir / _filename(today.isoformat())).touch()
            if today.isoweekday() == 7:
                newest_sunday = today

            subprocess.run(
                ["bash", "-c", f"source {SCRIPT}; prune_retention"],
                env={
                    "BACKUP_DIR": str(backup_dir),
                    "BACKUP_RETENTION_DAYS": "7",
                    "HOME": str(tmp_path),
                    "PATH": "/usr/bin:/bin",
                },
                check=True,
                capture_output=True,
                text=True,
            )

            survivors = {
                p.name[len(SNAPSHOT_PREFIX) : -len(SNAPSHOT_SUFFIX)]
                for p in backup_dir.glob(f"{SNAPSHOT_PREFIX}*{SNAPSHOT_SUFFIX}")
            }

            if newest_sunday is not None:
                assert newest_sunday.isoformat() in survivors, (
                    f"newest Sunday {newest_sunday} missing on day {today}"
                )
