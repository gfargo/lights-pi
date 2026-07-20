"""Tests for the RF-scan helpers behind /api/diagnostics/rf_scan.

Wireless DMX transmitters share the 2.4 GHz band with WiFi. These are pure
parsing/analysis helpers over `iw dev wlan0 scan` text — no QLC+ or network
mocking needed.
"""
from app import _analyze_rf_channels, _parse_iw_scan_output, _wifi_channel_from_freq


class TestWifiChannelFromFreq:
    def test_channel_1(self):
        assert _wifi_channel_from_freq(2412) == 1

    def test_channel_6(self):
        assert _wifi_channel_from_freq(2437) == 6

    def test_channel_11(self):
        assert _wifi_channel_from_freq(2462) == 11

    def test_channel_14(self):
        assert _wifi_channel_from_freq(2484) == 14

    def test_5ghz_out_of_band(self):
        assert _wifi_channel_from_freq(5180) is None

    def test_none_input(self):
        assert _wifi_channel_from_freq(None) is None


IW_SCAN_SAMPLE = """\
BSS aa:bb:cc:dd:ee:01(on wlan0) -- associated
\tlast seen: 100.0s [boottime]
\tTSF: 0 usec (0d, 00:00:00)
\tfreq: 2417
\tbeacon interval: 100 TUs
\tsignal: -53.00 dBm
\tSSID: River's Way
BSS aa:bb:cc:dd:ee:02(on wlan0)
\tfreq: 2437
\tsignal: -67.00 dBm
\tSSID: CBCI-F2B8
BSS aa:bb:cc:dd:ee:03(on wlan0)
\tfreq: 5180
\tsignal: -40.00 dBm
\tSSID: SomeFiveGhzNetwork
BSS aa:bb:cc:dd:ee:04(on wlan0)
\tfreq: 2462
\tsignal: -80.00 dBm
"""


class TestParseIwScanOutput:
    def test_parses_ssid_signal_channel(self):
        aps = _parse_iw_scan_output(IW_SCAN_SAMPLE)
        by_ssid = {ap["ssid"]: ap for ap in aps if ap["ssid"]}
        assert by_ssid["River's Way"]["channel"] == 2
        assert by_ssid["River's Way"]["signal_dbm"] == -53.0
        assert by_ssid["CBCI-F2B8"]["channel"] == 6

    def test_drops_5ghz_results(self):
        aps = _parse_iw_scan_output(IW_SCAN_SAMPLE)
        assert all(ap["freq_mhz"] < 2495 for ap in aps)
        assert not any(ap["ssid"] == "SomeFiveGhzNetwork" for ap in aps)

    def test_hidden_ssid_is_none(self):
        aps = _parse_iw_scan_output(IW_SCAN_SAMPLE)
        hidden = [ap for ap in aps if ap["channel"] == 11]
        assert len(hidden) == 1
        assert hidden[0]["ssid"] is None

    def test_sorted_loudest_first(self):
        aps = _parse_iw_scan_output(IW_SCAN_SAMPLE)
        signals = [ap["signal_dbm"] for ap in aps]
        assert signals == sorted(signals, reverse=True)

    def test_empty_input(self):
        assert _parse_iw_scan_output("") == []

    def test_no_bss_blocks(self):
        assert _parse_iw_scan_output("command failed\n") == []


class TestAnalyzeRfChannels:
    def test_no_access_points_says_clear(self):
        analysis = _analyze_rf_channels([])
        assert any("clear" in s.lower() for s in analysis["suggestions"])

    def test_loud_channel_flagged(self):
        aps = [{"ssid": "Loud", "signal_dbm": -40.0, "freq_mhz": 2437, "channel": 6}]
        analysis = _analyze_rf_channels(aps)
        assert analysis["per_channel_congestion_dbm"][6] == -40.0
        assert any("loud" in s.lower() for s in analysis["suggestions"])

    def test_quiet_window_avoids_loud_channel(self):
        # A loud AP on channel 3 bleeds into channels 1-7 (±4). Channels
        # 8-11 are untouched and should win as the quietest window.
        aps = [{"ssid": "Loud", "signal_dbm": -40.0, "freq_mhz": 2422, "channel": 3}]
        analysis = _analyze_rf_channels(aps)
        start, end = analysis["quiet_window"]
        assert start >= 8
        assert not (start <= 3 <= end)

    def test_offgrid_loud_network_suggests_1_6_11(self):
        aps = [{"ssid": "Offgrid", "signal_dbm": -50.0, "freq_mhz": 2427, "channel": 4}]
        analysis = _analyze_rf_channels(aps)
        assert any("1, 6, or 11" in s for s in analysis["suggestions"])

    def test_nonoverlapping_channels_reported(self):
        analysis = _analyze_rf_channels([])
        assert analysis["nonoverlapping_channels"] == [1, 6, 11]
