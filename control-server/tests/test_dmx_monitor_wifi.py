"""Tests for the Wi-Fi parsing helpers in scripts/services/dmx-monitor.py.
The monitor is a standalone stdlib script with a hyphenated filename, so
it is loaded by path."""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "services" / "dmx-monitor.py"
_spec = importlib.util.spec_from_file_location("dmx_monitor", _PATH)
dmx_monitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dmx_monitor)

PROC_WIRELESS = """\
Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   58.  -52.  -256        0      0      0    108      0        3
"""

IW_LINK = """\
Connected to d8:0d:17:af:09:30 (on wlan0)
\tSSID: River's Way
\tfreq: 2457.0
\tsignal: -52 dBm
\trx bitrate: 39.0 MBit/s
\ttx bitrate: 19.5 MBit/s
"""


class TestParseProcWireless:
    def test_reads_signal_and_counters(self):
        assert dmx_monitor.parse_proc_wireless(PROC_WIRELESS, "wlan0") == {
            "link": 58, "signal_dbm": -52, "retry": 108, "missed_beacon": 3,
        }

    def test_missing_interface_is_none(self):
        assert dmx_monitor.parse_proc_wireless(PROC_WIRELESS, "wlan1") is None
        assert dmx_monitor.parse_proc_wireless("", "wlan0") is None

    def test_truncated_row_is_none(self):
        assert dmx_monitor.parse_proc_wireless(" wlan0: 0000   58.", "wlan0") is None


class TestParseIwLink:
    def test_reads_ap_freq_and_rate(self):
        assert dmx_monitor.parse_iw_link(IW_LINK) == {
            "bssid": "d8:0d:17:af:09:30", "freq_mhz": 2457, "tx_mbit": 19.5,
        }

    def test_not_connected_is_empty(self):
        assert dmx_monitor.parse_iw_link("Not connected.\n") == {}


class TestFormatWifi:
    def test_down_when_no_stats(self):
        assert dmx_monitor.format_wifi(None) == "wifi=down"

    def test_full_line(self):
        line = dmx_monitor.format_wifi(
            dmx_monitor.parse_proc_wireless(PROC_WIRELESS, "wlan0"),
            dmx_monitor.parse_iw_link(IW_LINK),
        )
        assert line == (
            "wifi_dbm=-52 wifi_link=58/70 wifi_retry=108 wifi_missed_beacon=3 "
            "wifi_tx_mbit=19.5 wifi_freq=2457 wifi_ap=d8:0d:17:af:09:30"
        )

    def test_missing_iw_shows_placeholders(self):
        line = dmx_monitor.format_wifi(dmx_monitor.parse_proc_wireless(PROC_WIRELESS, "wlan0"))
        assert "wifi_ap=?" in line and "wifi_dbm=-52" in line
