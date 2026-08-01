"""Tests for the RF-scan helpers behind /api/diagnostics/rf_scan.

Wireless DMX transmitters share the 2.4 GHz band with WiFi. These are pure
parsing/analysis helpers over `iw dev wlan0 scan` text — no QLC+ or network
mocking needed.
"""
import app
from app import (
    _analyze_rf_channels,
    _dfi_channel_to_freq_mhz,
    _load_rf_settings,
    _loudest_signal_near_freq,
    _parse_iw_scan_output,
    _save_rf_settings,
    _wifi_channel_from_freq,
)


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

    def test_no_transmitter_settings_no_transmitter_note(self):
        analysis = _analyze_rf_channels([])
        assert analysis["transmitter"] is None

    def test_auto_mode_note(self):
        analysis = _analyze_rf_channels([], transmitter={"mode": "auto", "channel": None})
        assert "Auto" in analysis["suggestions"][0]

    def test_manual_mode_clear_channel(self):
        # Transmitter channel 1 (~2412 MHz) with no APs anywhere nearby.
        aps = [{"ssid": "Far", "signal_dbm": -50.0, "freq_mhz": 2472, "channel": 13}]
        analysis = _analyze_rf_channels(aps, transmitter={"mode": "manual", "channel": 1})
        assert "clear" in analysis["suggestions"][0].lower()

    def test_manual_mode_overlapping_channel(self):
        # Transmitter channel 1 (~2412 MHz) right under a loud AP at 2412 MHz.
        aps = [{"ssid": "OnTop", "signal_dbm": -45.0, "freq_mhz": 2412, "channel": 1}]
        analysis = _analyze_rf_channels(aps, transmitter={"mode": "manual", "channel": 1})
        assert "loud" in analysis["suggestions"][0].lower()
        assert "moving it" in analysis["suggestions"][0].lower()

    def test_manual_mode_already_in_quiet_window_no_contradictory_advice(self):
        # Regression case from live testing: two APs whose bleed covers nearly
        # the whole band (channel 2 AP + channel 6 AP), so the "quietest
        # window" [7,9] is still moderately loud. A transmitter sitting on
        # channel 9 is already inside that window — it shouldn't be told to
        # "move toward" a window it's already in.
        aps = [
            {"ssid": "A", "signal_dbm": -57.0, "freq_mhz": 2417, "channel": 2},
            {"ssid": "B", "signal_dbm": -64.0, "freq_mhz": 2437, "channel": 6},
        ]
        analysis = _analyze_rf_channels(aps, transmitter={"mode": "manual", "channel": 9})
        assert analysis["quiet_window"] == [7, 9]
        note = analysis["suggestions"][0]
        assert "moving it toward" not in note.lower()
        assert "clear as this wifi environment gets" in note.lower()


class TestDfiChannelToFreqMhz:
    def test_channel_1_is_band_floor(self):
        assert _dfi_channel_to_freq_mhz(1) == 2412.0

    def test_channel_16_is_band_ceiling(self):
        assert _dfi_channel_to_freq_mhz(16) == 2484.0

    def test_out_of_range(self):
        assert _dfi_channel_to_freq_mhz(0) is None
        assert _dfi_channel_to_freq_mhz(17) is None

    def test_none_input(self):
        assert _dfi_channel_to_freq_mhz(None) is None


class TestLoudestSignalNearFreq:
    def test_finds_nearby_ap(self):
        aps = [{"freq_mhz": 2412, "signal_dbm": -50.0}, {"freq_mhz": 2462, "signal_dbm": -70.0}]
        assert _loudest_signal_near_freq(aps, 2415, half_width_mhz=20) == -50.0

    def test_ignores_far_ap(self):
        aps = [{"freq_mhz": 2462, "signal_dbm": -40.0}]
        assert _loudest_signal_near_freq(aps, 2412, half_width_mhz=20) is None

    def test_picks_loudest_of_several(self):
        aps = [{"freq_mhz": 2412, "signal_dbm": -70.0}, {"freq_mhz": 2417, "signal_dbm": -45.0}]
        assert _loudest_signal_near_freq(aps, 2412, half_width_mhz=20) == -45.0

    def test_none_freq(self):
        assert _loudest_signal_near_freq([{"freq_mhz": 2412, "signal_dbm": -50.0}], None) is None


class TestRfSettingsPersistence:
    def test_load_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "RF_SETTINGS_FILE", tmp_path / "rf_settings.json")
        assert _load_rf_settings() == {}

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "RF_SETTINGS_FILE", tmp_path / "rf_settings.json")
        _save_rf_settings({"mode": "manual", "channel": 9})
        assert _load_rf_settings() == {"mode": "manual", "channel": 9}

    def test_load_corrupt_file_returns_empty(self, tmp_path, monkeypatch):
        settings_file = tmp_path / "rf_settings.json"
        settings_file.write_text("not json")
        monkeypatch.setattr(app, "RF_SETTINGS_FILE", settings_file)
        assert _load_rf_settings() == {}
