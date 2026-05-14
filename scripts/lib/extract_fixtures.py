#!/usr/bin/env python3
"""Extract enriched fixture inventory from a QLC+ workspace.

Emits JSON suitable for an AI scene-generation prompt. Uses the QLC+
.qxf fixture definitions on disk to attach authoritative per-channel info
(name, role, preset, group, colour) for every fixture in the workspace.

Usage:
    python3 extract_fixtures.py /path/to/workspace.qxw
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Make `fixture_definitions` importable. It lives in control-server/.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent  # scripts/lib -> scripts -> repo root
_CONTROL_SERVER = _REPO_ROOT / "control-server"
sys.path.insert(0, str(_CONTROL_SERVER))

try:
    import fixture_definitions
except ImportError as e:  # pragma: no cover
    sys.stderr.write(
        f"Error: could not import fixture_definitions ({e}). "
        f"Expected at {_CONTROL_SERVER}\n"
    )
    fixture_definitions = None  # type: ignore


def _text(elem, tag: str, default: str = "") -> str:
    if elem is None:
        return default
    child = elem.find(tag)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def extract(workspace_path: Path) -> dict:
    if not workspace_path.exists():
        raise FileNotFoundError(workspace_path)

    tree = ET.parse(workspace_path)
    root = tree.getroot()
    ns_prefix = ""
    if root.tag.startswith("{"):
        ns_prefix = root.tag[: root.tag.index("}") + 1]

    fixtures = []
    for fix in root.iter(ns_prefix + "Fixture"):
        try:
            fid = int(_text(fix, ns_prefix + "ID", "0"))
        except ValueError:
            continue

        manufacturer = _text(fix, ns_prefix + "Manufacturer")
        model = _text(fix, ns_prefix + "Model")
        mode_name = _text(fix, ns_prefix + "Mode")
        name = _text(fix, ns_prefix + "Name")
        try:
            universe = int(_text(fix, ns_prefix + "Universe", "0"))
            address = int(_text(fix, ns_prefix + "Address", "0"))
            channels = int(_text(fix, ns_prefix + "Channels", "0"))
        except ValueError:
            continue

        channel_info: list = []
        capabilities: list = []
        roles_present: set = set()
        fixture_type = ""

        if fixture_definitions is not None:
            definition = fixture_definitions.get_definition(manufacturer, model)
            if definition is not None:
                fixture_type = definition.type or ""
                mode = definition.get_mode(mode_name)
                if mode is not None:
                    for ch in mode.channels:
                        info = ch.to_dict()
                        # Add the absolute DMX channel number for convenience —
                        # absolute = address + offset + 1 (1-based for QLC+)
                        info["dmx_channel"] = address + ch.offset + 1
                        channel_info.append(info)
                        if ch.role:
                            roles_present.add(ch.role)

        # Derive a friendlier "capabilities" array from the resolved roles
        cap_map = [
            ("rgb", {"red", "green", "blue"}),
            ("rgbw", {"red", "green", "blue", "white"}),
            ("rgba", {"red", "green", "blue", "amber"}),
            ("rgbaw", {"red", "green", "blue", "amber", "white"}),
            ("rgbawu", {"red", "green", "blue", "amber", "white", "uv"}),
            ("warm_cool_white", {"warm", "cool"}),
            ("warm_cool_amber", {"warm", "cool", "amber"}),
            ("amber", {"amber"}),
            ("uv", {"uv"}),
            ("dimmer", {"dimmer"}),
            ("strobe", {"strobe"}),
            ("color_macro", {"macro"}),
            ("pan_tilt", {"pan", "tilt"}),
        ]
        # Add capability tags whose required roles are all present
        for cap, required in cap_map:
            if required.issubset(roles_present):
                capabilities.append(cap)
        # Always keep dimmer/strobe even when standalone
        for r in ("dimmer", "strobe"):
            if r in roles_present and r not in capabilities:
                capabilities.append(r)

        fixtures.append(
            {
                "id": fid,
                "name": name,
                "manufacturer": manufacturer,
                "model": model,
                "mode": mode_name,
                "type": fixture_type,
                "universe": universe,
                "address": address,
                "channels": channels,
                "capabilities": capabilities or ["unknown"],
                # channel_info is the authoritative per-channel layout
                "channel_info": channel_info,
            }
        )

    return {"fixtures": fixtures}


def main(argv: list) -> int:
    if len(argv) != 2:
        sys.stderr.write("Usage: extract_fixtures.py <workspace.qxw>\n")
        return 2
    try:
        result = extract(Path(argv[1]))
    except FileNotFoundError as e:
        sys.stderr.write(f"Error: workspace not found: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"Error parsing workspace: {e}\n")
        print(json.dumps({"fixtures": []}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
