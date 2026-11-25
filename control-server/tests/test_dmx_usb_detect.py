"""DMX interface presence: USB ids win over the tty node, which QLC+ removes."""
from unittest.mock import MagicMock

from app import _count_dmx_usb_lsusb, _find_usb_device_sysfs, _healthz_status

LSUSB = """\
Bus 001 Device 004: ID 0403:6001 Future Technology Devices International, Ltd FT232 Serial (UART) IC
Bus 001 Device 003: ID 0424:ec00 Microchip Technology, Inc. (formerly SMSC) SMSC9512/9514 Fast Ethernet Adapter
Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
"""


def _usb(root, name, vid, pid):
    d = root / name
    d.mkdir()
    (d / "idVendor").write_text(vid + "\n")
    (d / "idProduct").write_text(pid + "\n")


def _ws(tmp_path):
    p = tmp_path / "w.qxw"
    p.write_text('<Workspace xmlns="http://www.qlcplus.org/Workspace"></Workspace>')
    return p


class TestLsusb:
    def test_counts_ft232_by_id(self):
        assert _count_dmx_usb_lsusb(LSUSB) == 1

    def test_ignores_other_devices_and_empty(self):
        assert _count_dmx_usb_lsusb(LSUSB.splitlines()[1]) == 0
        assert _count_dmx_usb_lsusb("") == 0
        assert _count_dmx_usb_lsusb(None) == 0

    def test_other_ftdi_product_not_counted(self):
        assert _count_dmx_usb_lsusb("Bus 001 Device 005: ID 0403:6015 FTDI Bridge") == 0


class TestSysfs:
    def test_finds_enttec(self, tmp_path):
        _usb(tmp_path, "1-1", "1d6b", "0002")
        _usb(tmp_path, "1-1.3", "0403", "6001")
        (tmp_path / "1-1:1.0").mkdir()  # interface dirs have no id files
        assert _find_usb_device_sysfs(str(tmp_path)) == "0403:6001"

    def test_absent(self, tmp_path):
        _usb(tmp_path, "1-1", "1d6b", "0002")
        assert _find_usb_device_sysfs(str(tmp_path)) is None

    def test_missing_root(self, tmp_path):
        assert _find_usb_device_sysfs(str(tmp_path / "nope")) is None


class TestHealthz:
    def _call(self, tmp_path, glob, sysfs):
        ws = MagicMock()
        ws.closed = False
        return _healthz_status(
            qlc_ws=ws, last_dmx_ts=None, workspace_path=_ws(tmp_path),
            dmx_device_glob=glob, dmx_readable_fn=lambda d: True,
            now=1000.0, usb_sysfs_root=str(sysfs),
        )[0]

    def _sysfs(self, tmp_path, with_enttec):
        sysfs = tmp_path / "usb"
        sysfs.mkdir()
        if with_enttec:
            _usb(sysfs, "1-1.3", "0403", "6001")
        return sysfs

    def test_usb_present_without_tty(self, tmp_path):
        sysfs = self._sysfs(tmp_path, True)
        assert self._call(tmp_path, [], sysfs)["dmx_device"] == "usb:0403:6001"

    def test_tty_still_preferred(self, tmp_path):
        sysfs = self._sysfs(tmp_path, True)
        assert self._call(tmp_path, ["/dev/ttyUSB0"], sysfs)["dmx_device"] == "/dev/ttyUSB0"

    def test_nothing_present(self, tmp_path):
        sysfs = self._sysfs(tmp_path, False)
        assert self._call(tmp_path, [], sysfs)["dmx_device"] is False
