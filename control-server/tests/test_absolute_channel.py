"""Tests for _absolute_channel — converts (fixture, channel offset) into
the global DMX channel address used by QLC+ over the WebSocket.

Math:    abs = universe * 512 + address + offset + 1

The +1 is because QLC+ uses 1-based channel addressing externally while
internally everything else is 0-based. Getting this wrong by one corrupts
every DMX frame, so it's worth covering.
"""
import pytest
from app import _absolute_channel


def _fixture(universe: int, address: int) -> dict:
    """Minimal fixture stub — _absolute_channel only reads these two keys."""
    return {"universe": universe, "address": address}


class TestAbsoluteChannel:
    def test_universe_0_address_0_offset_0(self):
        """First fixture at the start of universe 0 → QLC+ channel 1."""
        assert _absolute_channel(_fixture(0, 0), 0) == 1

    def test_universe_0_address_0_offset_5(self):
        """Sixth channel of the first fixture → QLC+ channel 6."""
        assert _absolute_channel(_fixture(0, 0), 5) == 6

    def test_universe_0_address_7_offset_0(self):
        """SlimPAR 56 (DMX d008 = address 7) at offset 0 → QLC+ channel 8."""
        assert _absolute_channel(_fixture(0, 7), 0) == 8

    def test_universe_0_address_7_offset_2(self):
        """SlimPAR 56 blue channel (offset 2 in RGB mode) → QLC+ channel 10."""
        assert _absolute_channel(_fixture(0, 7), 2) == 10

    def test_universe_1_resets_address_block(self):
        """Universe 1 starts at channel 513 (1-based: 1*512 + 0 + 0 + 1)."""
        assert _absolute_channel(_fixture(1, 0), 0) == 513

    def test_universe_2_address_100_offset_10(self):
        """2 * 512 + 100 + 10 + 1 = 1135."""
        assert _absolute_channel(_fixture(2, 100), 10) == 1135

    def test_last_channel_of_universe_0(self):
        """Channel 512 = universe 0, address 511, offset 0."""
        assert _absolute_channel(_fixture(0, 511), 0) == 512

    def test_first_channel_of_universe_1(self):
        """Channel 513 = universe 1, address 0, offset 0. The boundary
        between universes is the one I always second-guess; pin it."""
        assert _absolute_channel(_fixture(1, 0), 0) == 513

    @pytest.mark.parametrize("u,a,o,expected", [
        (0, 0, 0, 1),
        (0, 0, 1, 2),
        (0, 0, 6, 7),       # SlimPAR Pro is 7CH; last channel of first fixture
        (0, 7, 0, 8),       # SlimPAR 56 starts at d008
        (0, 7, 2, 10),      # SlimPAR 56 blue (offset 2)
        (1, 0, 0, 513),     # universe boundary
        (1, 0, 511, 1024),  # last channel of universe 1
        (3, 255, 100, 1892),  # arbitrary mid-range case
    ])
    def test_canonical_addresses(self, u, a, o, expected):
        assert _absolute_channel(_fixture(u, a), o) == expected

    def test_riversway_rig_specific_addresses(self):
        """The actual studio setup uses SlimPAR Pro at d001 (7CH) +
        SlimPAR 56 at d008 (3CH). Pin the per-channel addresses since
        this is the rig we ship against."""
        pro = _fixture(0, 0)     # d001 = address 0
        slim56 = _fixture(0, 7)  # d008 = address 7
        # SlimPAR Pro 7CH: master, R, G, B, strobe, mode, dim_curve
        for offset in range(7):
            assert _absolute_channel(pro, offset) == 1 + offset
        # SlimPAR 56 3CH: R, G, B
        for offset in range(3):
            assert _absolute_channel(slim56, offset) == 8 + offset
