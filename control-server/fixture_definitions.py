#!/usr/bin/env python3
"""
QLC+ Fixture Definition Parser

Reads .qxf files from QLC+ system + user fixture directories and resolves
per-channel role metadata for any fixture/mode in the workspace.

The .qxf format provides authoritative channel info including:
- Channel name
- Preset (e.g. IntensityRed, IntensityMasterDimmer, ShutterStrobeSlowFast)
- Group (Intensity, Colour, Shutter, Pan, Tilt, Effect, Speed, Maintenance)
- Colour subtag for color channels (Red, Green, Blue, White, Amber, UV, ...)

Using these is more reliable than guessing roles from fixture name + channel
count, especially since fixtures often have multiple modes with different
channel orderings.
"""

from __future__ import annotations

import os
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional

QLC_NS = "http://www.qlcplus.org/FixtureDefinition"

# Default search paths — system install + user override
SYSTEM_FIXTURE_DIR = Path("/usr/share/qlcplus/fixtures")
USER_FIXTURE_DIR = Path.home() / ".qlcplus" / "fixtures"


# ---------------------------------------------------------------------------
# Role inference from .qxf preset / colour / group / name
# ---------------------------------------------------------------------------

# Mapping from QLC+ preset names → semantic role used by the control server
PRESET_TO_ROLE: Dict[str, str] = {
    "IntensityMasterDimmer": "dimmer",
    "IntensityDimmer": "dimmer",
    "IntensityRed": "red",
    "IntensityGreen": "green",
    "IntensityBlue": "blue",
    "IntensityWhite": "white",
    "IntensityAmber": "amber",
    "IntensityUV": "uv",
    "IntensityCyan": "cyan",
    "IntensityMagenta": "magenta",
    "IntensityYellow": "yellow",
    "IntensityIndigo": "indigo",
    "IntensityLime": "lime",
    "IntensityHue": "hue",
    "IntensitySaturation": "saturation",
    "ColourMacro": "macro",
    "ShutterStrobeSlowFast": "strobe",
    "ShutterStrobe": "strobe",
}

# Lower-cased channel names → role (fallback when no Preset attribute)
NAME_TO_ROLE: Dict[str, str] = {
    "master dimmer": "dimmer",
    "dimmer": "dimmer",
    "intensity": "dimmer",
    "red": "red",
    "green": "green",
    "blue": "blue",
    "white": "white",
    "warm white": "warm",
    "warmwhite": "warm",
    "warm": "warm",
    "cool white": "cool",
    "coolwhite": "cool",
    "cool": "cool",
    "amber": "amber",
    "uv": "uv",
    "cyan": "cyan",
    "magenta": "magenta",
    "yellow": "yellow",
    "color macro": "macro",
    "color macros": "macro",
    "colour macro": "macro",
    "strobe": "strobe",
    "shutter": "strobe",
    "pan": "pan",
    "tilt": "tilt",
}

# Colour subtag values → role
COLOUR_TO_ROLE: Dict[str, str] = {
    "red": "red",
    "green": "green",
    "blue": "blue",
    "white": "white",
    "warm white": "warm",
    "cool white": "cool",
    "amber": "amber",
    "uv": "uv",
    "ultraviolet": "uv",
    "cyan": "cyan",
    "magenta": "magenta",
    "yellow": "yellow",
    "indigo": "indigo",
    "lime": "lime",
}


def _classify_channel(name: str, preset: Optional[str], group: Optional[str], colour: Optional[str]) -> Optional[str]:
    """Determine the semantic role for a single channel.

    Order of precedence: explicit Preset → Colour subtag → exact Channel name →
    Group classification → fuzzy substring on name. Channels in non-Intensity
    groups (Speed, Maintenance, Effect, etc.) never get fuzzy-matched to color
    or dimmer roles, since their names often contain those words incidentally
    (e.g. "Dimmer Speed Mode", "Color Wheel Reset").

    Returns None when the channel doesn't map to a recognized role.
    """
    if preset and preset in PRESET_TO_ROLE:
        return PRESET_TO_ROLE[preset]

    if colour:
        key = colour.strip().lower()
        if key in COLOUR_TO_ROLE:
            # If the channel name disambiguates a generic "White" subtag into
            # warm/cool, prefer the more specific role.
            lowered_name = (name or "").strip().lower()
            if key == "white":
                if "warm" in lowered_name:
                    return "warm"
                if "cool" in lowered_name:
                    return "cool"
            return COLOUR_TO_ROLE[key]

    lowered = (name or "").strip().lower()
    if lowered in NAME_TO_ROLE:
        return NAME_TO_ROLE[lowered]

    # Group-driven classification (authoritative for non-Intensity groups)
    group_lower = (group or "").strip().lower()
    if group_lower:
        # Channels with these groups are configuration/effect controls and
        # should NOT be fuzzy-matched to color/dimmer roles.
        if group_lower in ("speed", "maintenance", "effect"):
            return None
        if group_lower == "shutter":
            return "strobe"
        if group_lower == "colour":
            return "macro"
        if group_lower == "pan":
            return "pan"
        if group_lower == "tilt":
            return "tilt"

    # Substring matches only for Intensity-group or unclassified channels —
    # safe because we've already filtered out Speed/Maintenance/Effect above.
    for key, role in NAME_TO_ROLE.items():
        if key in lowered:
            return role

    if group_lower == "intensity":
        return "dimmer"

    return None


# ---------------------------------------------------------------------------
# .qxf cache and lookup
# ---------------------------------------------------------------------------

_definition_cache: Dict[str, "FixtureDefinition"] = {}
_index_built = False
_cache_lock = threading.Lock()


class FixtureChannel:
    """One channel from a fixture mode, resolved to a role."""

    __slots__ = ("offset", "name", "preset", "group", "colour", "role")

    def __init__(self, offset: int, name: str, preset: Optional[str],
                 group: Optional[str], colour: Optional[str], role: Optional[str]):
        self.offset = offset
        self.name = name
        self.preset = preset
        self.group = group
        self.colour = colour
        self.role = role

    def to_dict(self) -> dict:
        return {
            "offset": self.offset,
            "name": self.name,
            "preset": self.preset,
            "group": self.group,
            "colour": self.colour,
            "role": self.role,
        }


class FixtureMode:
    """One mode from a fixture definition."""

    __slots__ = ("name", "channels")

    def __init__(self, name: str, channels: List[FixtureChannel]):
        self.name = name
        self.channels = channels

    def channel_count(self) -> int:
        return len(self.channels)

    def role_offsets(self) -> Dict[str, object]:
        """Return {role: offset} (or {role: [offsets]} for brightness)."""
        roles: Dict[str, object] = {}
        for ch in self.channels:
            if ch.role and ch.role not in roles:
                roles[ch.role] = ch.offset
        # Brightness tracking — used by adjust_brightness / fade.
        # Prefer the dedicated dimmer; otherwise track all RGB-ish channels.
        if "dimmer" in roles:
            roles["brightness"] = [roles["dimmer"]]
        else:
            color_roles = ("red", "green", "blue", "white", "warm", "cool", "amber")
            offsets = [
                ch.offset for ch in self.channels
                if ch.role in color_roles
            ]
            if offsets:
                roles["brightness"] = offsets
            elif self.channels:
                roles["brightness"] = [self.channels[0].offset]
        return roles


class FixtureDefinition:
    """A parsed .qxf file."""

    __slots__ = ("manufacturer", "model", "type", "modes", "channel_defs", "source_path")

    def __init__(self, manufacturer: str, model: str, fixture_type: str,
                 modes: Dict[str, FixtureMode],
                 channel_defs: Dict[str, FixtureChannel],
                 source_path: Path):
        self.manufacturer = manufacturer
        self.model = model
        self.type = fixture_type
        self.modes = modes
        self.channel_defs = channel_defs
        self.source_path = source_path

    def get_mode(self, mode_name: str) -> Optional[FixtureMode]:
        # Exact match first
        if mode_name in self.modes:
            return self.modes[mode_name]
        # Case/whitespace-insensitive fallback
        target = (mode_name or "").strip().lower().replace(" ", "")
        for name, mode in self.modes.items():
            if name.strip().lower().replace(" ", "") == target:
                return mode
        # Match by channel count token (e.g. "9 Channel" vs "9-Ch")
        digits = "".join(ch for ch in (mode_name or "") if ch.isdigit())
        if digits:
            for name, mode in self.modes.items():
                name_digits = "".join(ch for ch in name if ch.isdigit())
                if name_digits == digits:
                    return mode
        return None


def _local_name(tag: str) -> str:
    """Strip the {namespace} prefix from an XML tag."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _parse_qxf(path: Path) -> Optional[FixtureDefinition]:
    """Parse a .qxf file. Returns None on parse error."""
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return None
    root = tree.getroot()

    def find_text(parent, tag: str, default: str = "") -> str:
        for child in parent:
            if _local_name(child.tag) == tag:
                return (child.text or default).strip()
        return default

    manufacturer = find_text(root, "Manufacturer")
    model = find_text(root, "Model")
    fixture_type = find_text(root, "Type")

    # First pass: parse channel definitions keyed by Name attribute
    channel_defs: Dict[str, FixtureChannel] = {}
    for child in root:
        if _local_name(child.tag) != "Channel":
            continue
        ch_name = child.get("Name", "").strip()
        if not ch_name:
            continue
        preset = child.get("Preset")
        group_text: Optional[str] = None
        colour_text: Optional[str] = None
        for sub in child:
            local = _local_name(sub.tag)
            if local == "Group" and sub.text:
                group_text = sub.text.strip()
            elif local == "Colour" and sub.text:
                colour_text = sub.text.strip()
        role = _classify_channel(ch_name, preset, group_text, colour_text)
        channel_defs[ch_name] = FixtureChannel(
            offset=-1,  # set per-mode below
            name=ch_name,
            preset=preset,
            group=group_text,
            colour=colour_text,
            role=role,
        )

    # Second pass: build modes from <Mode><Channel Number="...">name</Channel></Mode>
    modes: Dict[str, FixtureMode] = {}
    for child in root:
        if _local_name(child.tag) != "Mode":
            continue
        mode_name = child.get("Name", "").strip()
        if not mode_name:
            continue
        mode_channels: List[FixtureChannel] = []
        for sub in child:
            if _local_name(sub.tag) != "Channel":
                continue
            offset_str = sub.get("Number", "").strip()
            ch_name = (sub.text or "").strip()
            if not offset_str.isdigit() or not ch_name:
                continue
            base = channel_defs.get(ch_name)
            if base is None:
                # Some fixtures reference unknown channels; create a minimal entry
                base = FixtureChannel(
                    offset=int(offset_str),
                    name=ch_name,
                    preset=None,
                    group=None,
                    colour=None,
                    role=_classify_channel(ch_name, None, None, None),
                )
            mode_channels.append(FixtureChannel(
                offset=int(offset_str),
                name=base.name,
                preset=base.preset,
                group=base.group,
                colour=base.colour,
                role=base.role,
            ))
        mode_channels.sort(key=lambda c: c.offset)
        modes[mode_name] = FixtureMode(name=mode_name, channels=mode_channels)

    if not manufacturer or not model:
        return None

    return FixtureDefinition(
        manufacturer=manufacturer,
        model=model,
        fixture_type=fixture_type,
        modes=modes,
        channel_defs=channel_defs,
        source_path=path,
    )


def _cache_key(manufacturer: str, model: str) -> str:
    return f"{manufacturer.strip().lower()}|{model.strip().lower()}"


def _build_index(force: bool = False) -> None:
    """Walk fixture dirs and parse every .qxf into the cache."""
    global _index_built
    with _cache_lock:
        if _index_built and not force:
            return
        _definition_cache.clear()

        for base in (USER_FIXTURE_DIR, SYSTEM_FIXTURE_DIR):
            if not base.exists():
                continue
            for qxf_path in base.rglob("*.qxf"):
                definition = _parse_qxf(qxf_path)
                if definition is None:
                    continue
                key = _cache_key(definition.manufacturer, definition.model)
                # User overrides win because they're loaded first
                _definition_cache.setdefault(key, definition)

        _index_built = True


def get_definition(manufacturer: str, model: str) -> Optional[FixtureDefinition]:
    """Look up a parsed .qxf by manufacturer + model."""
    if not _index_built:
        _build_index()
    return _definition_cache.get(_cache_key(manufacturer, model))


def get_mode(manufacturer: str, model: str, mode_name: str) -> Optional[FixtureMode]:
    """Look up a specific mode within a fixture definition."""
    definition = get_definition(manufacturer, model)
    if definition is None:
        return None
    return definition.get_mode(mode_name)


def reload_definitions() -> int:
    """Force a cache rebuild. Returns the number of fixtures indexed."""
    _build_index(force=True)
    return len(_definition_cache)


def index_size() -> int:
    if not _index_built:
        _build_index()
    return len(_definition_cache)


def add_search_path(path: Path) -> None:
    """Add a custom .qxf search root and rebuild the index."""
    global SYSTEM_FIXTURE_DIR, USER_FIXTURE_DIR
    if not path.exists():
        return
    # Insert as user-priority directory, ahead of system defaults
    if path != USER_FIXTURE_DIR and path != SYSTEM_FIXTURE_DIR:
        # We can't edit the constants in-place easily without a list, so
        # rebuild the cache treating this path with priority by putting its
        # entries in first.
        with _cache_lock:
            for qxf_path in path.rglob("*.qxf"):
                definition = _parse_qxf(qxf_path)
                if definition is None:
                    continue
                key = _cache_key(definition.manufacturer, definition.model)
                _definition_cache[key] = definition  # override


# Allow override via env var (mostly for tests)
_env_extra = os.getenv("QLC_FIXTURE_DIR")
if _env_extra:
    add_search_path(Path(_env_extra))
