"""
Mock DMX bus for local development (MOCK_DMX=1).

Replaces the QLC+ WebSocket sink with an in-process dict of
{(universe, channel): value} (both 0-based).

All DMX writes in app.py flow through _qlc_send_commands which sends
"CH|<abs1based>|<val>" frames.  This module parses those and updates the bus.
"""

from __future__ import annotations

import asyncio

# ---------------------------------------------------------------------------
# The bus
# ---------------------------------------------------------------------------

_BUS: dict[tuple[int, int], int] = {}


def apply_commands(commands: list[str]) -> None:
    """Apply a list of raw QLC+ CH|abs|val commands to the mock bus."""
    for cmd in commands:
        parts = cmd.split("|")
        if len(parts) == 3 and parts[0] == "CH":
            try:
                # QLC+ absolute channels are 1-based; convert to 0-based
                # (universe, channel) via divmod on (abs - 1).
                abs_ch = int(parts[1])
                val = max(0, min(255, int(parts[2])))
                if abs_ch >= 1:
                    universe, channel = divmod(abs_ch - 1, 512)
                    _BUS[(universe, channel)] = val
            except (ValueError, IndexError):
                pass


def snapshot() -> dict[str, int]:
    """Return the current bus state with string keys ("u/ch", e.g. "0/12")."""
    return {f"{u}/{c}": v for (u, c), v in sorted(_BUS.items())}


def serialize_get_channels_values(max_ch: int) -> str:
    """Synthesize a QLC+API|getChannelsValues|... reply string.

    _fetch_channel_values in app.py parses this triplet layout:
        QLC+API|getChannelsValues|<abs1>|<val1>||<abs2>|<val2>||...
    where i in range(2, len-1, 3) with parts[i]=abs, parts[i+1]=val.
    """
    parts = ["QLC+API", "getChannelsValues"]
    for abs_ch in range(1, max_ch + 1):
        universe, channel = divmod(abs_ch - 1, 512)
        val = _BUS.get((universe, channel), 0)
        parts += [str(abs_ch), str(val), ""]
    return "|".join(parts)


def reset() -> None:
    """Clear the bus (used in tests)."""
    _BUS.clear()


# ---------------------------------------------------------------------------
# Mock WebSocket shim
# ---------------------------------------------------------------------------

class MockQLCWebSocket:
    """Drop-in replacement for a websockets connection in mock mode.

    send() routes CH|... commands to the bus; other commands are silently
    accepted so the rest of app.py doesn't need to be branched.
    """

    closed: bool = False

    async def send(self, message: str) -> None:
        # Only CH commands write to the bus; ignore everything else
        # (QLC+API|setFunctionStatus etc.).
        if message.startswith("CH|"):
            apply_commands([message])

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        # Never yields real messages; block forever so _qlc_reader sleeps.
        await asyncio.sleep(3600)
        raise StopAsyncIteration
