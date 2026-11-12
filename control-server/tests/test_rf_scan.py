"""Tests for the RF-scan helpers behind /api/diagnostics/rf_scan.

Wireless DMX transmitters share the 2.4 GHz band with WiFi. These are pure
parsing/analysis helpers over `iw dev wlan0 scan` text — no QLC+ or network
mocking needed.
"""
import app
import pytest
from app import (
    _analyze_rf_channels,
    _append_bounded_jsonl,
    _bssid_radio_key,
    _dedupe_radios,
    _dfi_channel_interference_dbm,
    _dfi_channel_to_freq_mhz,
    _interference_at_freq_dbm,
    _load_rf_settings,
    _parse_iw_scan_output,
    _read_rf_history,
    _recommend_dfi_channel,
    _rf_history_entry,
    _save_rf_settings,
    _wifi_channel_from_freq,
    _wifi_mask_attenuation_db,
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
        assert "consider moving it to ch16" in analysis["suggestions"][0].lower()

    def test_far_ap_does_not_make_channel_loud(self):
        # Old model: a -51 dBm AP on ch10 marked ch6 "loud" (±4 channels at
        # full strength). 20 MHz away the mask knocks it down by 28 dB.
        aps = [{"ssid": "Ten", "signal_dbm": -51.0, "freq_mhz": 2457, "channel": 10}]
        analysis = _analyze_rf_channels(aps)
        assert analysis["per_channel_congestion_dbm"][6] == pytest.approx(-79.0)
        assert analysis["per_channel_congestion_dbm"][10] == pytest.approx(-51.0)

    def test_limitations_reported(self):
        analysis = _analyze_rf_channels([])
        text = " ".join(analysis["limitations"]).lower()
        assert "bluetooth" in text and "receiver" in text
        assert "can't see" in analysis["suggestions"][-1].lower()

    def test_manual_mode_already_best_no_contradictory_advice(self):
        # A transmitter sitting on the recommended channel must not be told
        # to move.
        aps = [
            {"ssid": "A", "signal_dbm": -57.0, "freq_mhz": 2417, "channel": 2},
            {"ssid": "B", "signal_dbm": -64.0, "freq_mhz": 2462, "channel": 11},
        ]
        best = _analyze_rf_channels(aps)["recommended_dfi_channel"]["channel"]
        analysis = _analyze_rf_channels(aps, transmitter={"mode": "manual", "channel": best})
        note = analysis["suggestions"][0].lower()
        assert "consider moving" not in note


# Reference scan from the studio Pi, 2026-11-12. River's Way main + guest are
# one radio (BSSIDs differ only in the first octet).
REFERENCE_SCAN = """\
BSS d8:0d:17:af:09:30(on wlan0) -- associated
\tfreq: 2457
\tsignal: -55.00 dBm
\tSSID: River's Way
BSS de:0d:17:af:09:30(on wlan0)
\tfreq: 2457
\tsignal: -51.00 dBm
\tSSID: River's Way Guest
BSS 3c:84:6a:00:00:01(on wlan0)
\tfreq: 2412
\tsignal: -60.00 dBm
\tSSID: CBCI-F2B8
BSS 3c:84:6a:00:00:02(on wlan0)
\tfreq: 2462
\tsignal: -70.00 dBm
\tSSID: SC_storage
BSS 3c:84:6a:00:00:03(on wlan0)
\tfreq: 2437
\tsignal: -84.00 dBm
\tSSID: dorfmanfidium
"""


class TestReferenceScan:
    def setup_method(self):
        self.aps = _parse_iw_scan_output(REFERENCE_SCAN)
        self.analysis = _analyze_rf_channels(self.aps, transmitter={"mode": "manual", "channel": 2})

    def test_main_and_guest_counted_once(self):
        assert len(self.aps) == 5
        radios = self.analysis["radios"]
        assert len(radios) == 4
        rw = radios[0]
        assert rw["signal_dbm"] == -51.0
        assert set(rw["ssids"]) == {"River's Way", "River's Way Guest"}

    def test_quiet_window_is_mid_band(self):
        start, end = self.analysis["quiet_window"]
        assert 4 <= start and end <= 7

    def test_wifi_ch6_not_loud(self):
        assert self.analysis["per_channel_congestion_dbm"][6] < -75

    def test_current_dfi_ch2_is_loud(self):
        assert self.analysis["dfi_channels"][1]["band"] == "loud"
        note = self.analysis["suggestions"][0]
        assert "CH02" in note and "loud" in note and "Consider moving" in note

    def test_recommendation_avoids_loud_radios(self):
        rec = self.analysis["recommended_dfi_channel"]
        assert rec["band"] == "quiet"
        # Under CBCI-F2B8 (CH01-03) and River's Way (CH08-13) are both out.
        assert rec["channel"] not in range(1, 4)
        assert rec["channel"] not in range(8, 14)
        # The mid-band pocket (D-Fi ~CH05, Wi-Fi ch5) is offered too.
        offered = {rec["channel"], *rec["alternates"]}
        assert 5 in offered

    def test_quiet_window_kept_for_backward_compat(self):
        assert set(self.analysis) >= {"per_channel_congestion_dbm", "quiet_window", "suggestions"}


class TestWifiMaskAttenuation:
    def test_flat_in_band(self):
        assert _wifi_mask_attenuation_db(0) == 0
        assert _wifi_mask_attenuation_db(-9) == 0

    def test_breakpoints(self):
        assert _wifi_mask_attenuation_db(11) == -20
        assert _wifi_mask_attenuation_db(20) == -28
        assert _wifi_mask_attenuation_db(-30) == -40

    def test_interpolates(self):
        assert _wifi_mask_attenuation_db(25) == pytest.approx(-34)
        assert _wifi_mask_attenuation_db(10) == pytest.approx(-10)

    def test_beyond_mask_is_none(self):
        assert _wifi_mask_attenuation_db(31) is None

    def test_40mhz_is_stretched(self):
        assert _wifi_mask_attenuation_db(18, width_mhz=40) == 0
        assert _wifi_mask_attenuation_db(40, width_mhz=40) == -28


class TestInterferenceAtFreq:
    def test_single_radio_on_frequency(self):
        radios = [{"freq_mhz": 2437, "signal_dbm": -60.0}]
        assert _interference_at_freq_dbm(radios, 2437) == pytest.approx(-60.0)

    def test_power_sum_of_equal_contributions(self):
        radios = [{"freq_mhz": 2437, "signal_dbm": -80.0}, {"freq_mhz": 2437, "signal_dbm": -80.0}]
        assert _interference_at_freq_dbm(radios, 2437) == pytest.approx(-76.99, abs=0.01)

    def test_attenuated_by_offset(self):
        radios = [{"freq_mhz": 2457, "signal_dbm": -51.0}]
        assert _interference_at_freq_dbm(radios, 2437) == pytest.approx(-79.0)

    def test_nothing_in_reach(self):
        assert _interference_at_freq_dbm([{"freq_mhz": 2412, "signal_dbm": -40.0}], 2462) is None
        assert _interference_at_freq_dbm([], 2437) is None
        assert _interference_at_freq_dbm([{"freq_mhz": 2412, "signal_dbm": -40.0}], None) is None

    def test_uses_40mhz_centre(self):
        radios = [{"freq_mhz": 2412, "center_freq_mhz": 2422, "width_mhz": 40, "signal_dbm": -60.0}]
        assert _interference_at_freq_dbm(radios, 2437) == pytest.approx(-60.0)


class TestDfiRecommendation:
    def test_no_radios_no_recommendation(self):
        per_channel, rec = _recommend_dfi_channel([])
        assert rec is None
        assert len(per_channel) == 16
        assert all(c["interference_dbm"] is None for c in per_channel)

    def test_picks_far_side_of_single_loud_ap(self):
        _per, rec = _recommend_dfi_channel([{"freq_mhz": 2412, "signal_dbm": -40.0}])
        assert rec["channel"] == 16

    def test_channel_score_hedges_estimate(self):
        # The score covers ± one channel spacing, so a channel right beside
        # a loud AP's flat band counts as loud even if its estimated centre
        # is just past the mask edge.
        radios = [{"freq_mhz": 2412, "signal_dbm": -50.0}]
        assert _dfi_channel_interference_dbm(radios, 3) == pytest.approx(-50.0)


class TestBssidDedupe:
    def test_radio_key_ignores_first_octet(self):
        assert _bssid_radio_key("D8:0D:17:AF:09:30") == _bssid_radio_key("de:0d:17:af:09:30")
        assert _bssid_radio_key("d8:0d:17:af:09:31") != _bssid_radio_key("d8:0d:17:af:09:30")

    def test_bad_bssid(self):
        assert _bssid_radio_key(None) is None
        assert _bssid_radio_key("nonsense") is None

    def test_keeps_loudest_and_all_ssids(self):
        aps = [
            {"bssid": "d8:0d:17:af:09:30", "ssid": "Main", "signal_dbm": -55.0, "channel": 10, "freq_mhz": 2457},
            {"bssid": "de:0d:17:af:09:30", "ssid": "Guest", "signal_dbm": -51.0, "channel": 10, "freq_mhz": 2457},
        ]
        radios = _dedupe_radios(aps)
        assert len(radios) == 1
        assert radios[0]["signal_dbm"] == -51.0
        assert radios[0]["ssids"] == ["Main", "Guest"]

    def test_same_suffix_different_channel_kept_apart(self):
        aps = [
            {"bssid": "d8:0d:17:af:09:30", "ssid": "A", "signal_dbm": -55.0, "channel": 10},
            {"bssid": "de:0d:17:af:09:30", "ssid": "B", "signal_dbm": -51.0, "channel": 1},
        ]
        assert len(_dedupe_radios(aps)) == 2

    def test_missing_bssid_not_merged(self):
        aps = [{"ssid": "A", "signal_dbm": -55.0, "channel": 6}, {"ssid": "A", "signal_dbm": -55.0, "channel": 6}]
        assert len(_dedupe_radios(aps)) == 2


class TestParseIwHtAndBssid:
    SAMPLE = """\
BSS AA:BB:CC:DD:EE:01(on wlan0)
\tfreq: 2412
\tsignal: -50.00 dBm
\tSSID: Wide
\tHT operation:
\t\t * primary channel: 1
\t\t * secondary channel offset: above
\t\t * STA channel width: any
BSS aa:bb:cc:dd:ee:02(on wlan0)
\tfreq: 2437
\tsignal: -60.00 dBm
\tSSID: Narrow
\tHT operation:
\t\t * primary channel: 6
\t\t * secondary channel offset: no secondary
\t\t * STA channel width: 20 MHz
"""

    def test_bssid_parsed_lowercase(self):
        aps = _parse_iw_scan_output(self.SAMPLE)
        assert aps[0]["bssid"] == "aa:bb:cc:dd:ee:01"

    def test_ht40_above(self):
        wide = _parse_iw_scan_output(self.SAMPLE)[0]
        assert wide["width_mhz"] == 40
        assert wide["center_freq_mhz"] == 2422
        assert wide["channel"] == 1

    def test_20mhz_default(self):
        narrow = _parse_iw_scan_output(self.SAMPLE)[1]
        assert narrow["width_mhz"] == 20
        assert narrow["center_freq_mhz"] == 2437


class TestRfHistory:
    def test_entry_shape(self):
        analysis = _analyze_rf_channels(_parse_iw_scan_output(REFERENCE_SCAN),
                                        transmitter={"mode": "manual", "channel": 2})
        entry = _rf_history_entry(analysis, 1700000000.4)
        assert entry["ts"] == 1700000000
        assert entry["transmitter_channel"] == 2
        assert entry["transmitter_dbm"] == pytest.approx(-60.0)
        assert entry["recommended_dfi_channel"] == analysis["recommended_dfi_channel"]["channel"]
        assert len(entry["dfi_dbm"]) == 16
        assert len(entry["radios"]) == 4

    def test_bounded_append_and_read(self, tmp_path):
        path = tmp_path / "rf_history.jsonl"
        for i in range(5):
            _append_bounded_jsonl(path, {"ts": i}, max_entries=3)
        assert [e["ts"] for e in _read_rf_history(path)] == [2, 3, 4]
        assert [e["ts"] for e in _read_rf_history(path, limit=1)] == [4]

    def test_read_missing_and_corrupt(self, tmp_path):
        path = tmp_path / "rf_history.jsonl"
        assert _read_rf_history(path) == []
        path.write_text('{"ts": 1}\nnot json\n')
        assert _read_rf_history(path) == [{"ts": 1}]


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
