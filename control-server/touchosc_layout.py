"""Build a TouchOSC (Mk2) layout for this rig, with no TouchOSC needed.

A ``.tosc`` file is zlib-compressed XML (``<lexml version='3'>``): a tree of
nodes (GROUP, PAGER, BUTTON, FADER, LABEL), each with properties, values,
OSC messages and children. This module writes that tree for an iPad in
landscape (1024 × 768):

Scenes   a grid of buttons, one per scene (``/scene/<id>``), with the master
         fader, cue GO / STOP and blackout down the right-hand side.
Faders   eight faders on single channels (``/fixture/<id>/<channel>``), plus
         master and blackout.
Chases   a grid of toggle buttons (``/chase/<id>``); only when there are
         chases.

Every control sends and receives on the same address, so the Pi's feedback
lights the active scene's button and moves the master fader (docs/OSC.md).

Pure functions only: ``build_layout`` takes plain lists and returns bytes,
so it is tested without Flask or QLC+. The control server serves it at
``GET /api/osc/touchosc`` for the loaded workspace.
"""
from __future__ import annotations

import uuid
import zlib

WIDTH, HEIGHT = 1024, 768
MARGIN, GAP = 16, 12
TOP = 64                      # below the pager's tab bar
SIDE_W = 216                  # right-hand column: master, cues, blackout
SCENES_PER_PAGE = 24          # 4 columns × 6 rows
FADERS = 8

# Colours (RGBA 0–1). Accent matches the web UI's --amber-tungsten.
INK = (0.04, 0.04, 0.04, 1.0)
PANEL = (0.10, 0.10, 0.10, 1.0)
PAPER = (0.95, 0.95, 0.93, 1.0)
MUTED = (0.95, 0.95, 0.93, 0.6)
ACCENT = (1.0, 0.416, 0.122, 1.0)
CHASE = (0.494, 0.906, 1.0, 1.0)
GREEN = (0.29, 0.87, 0.5, 1.0)
RED = (0.97, 0.27, 0.27, 1.0)

_NS = uuid.UUID("6f1c2d6e-7b1a-4a53-9a65-6c6967687473")  # stable IDs per path


def _cdata(text) -> str:
    # "]]>" can't appear inside CDATA; split it across two sections.
    return "<![CDATA[" + str(text).replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _prop(kind: str, key: str, value) -> str:
    if kind == "r":
        x, y, w, h = (int(round(v)) for v in value)
        val = f"<x>{x}</x><y>{y}</y><w>{w}</w><h>{h}</h>"
    elif kind == "c":
        r, g, b, a = value
        val = f"<r>{r:g}</r><g>{g:g}</g><b>{b:g}</b><a>{a:g}</a>"
    elif kind == "s":
        val = _cdata(value)
    elif kind == "b":
        val = "1" if value else "0"
    else:
        val = str(int(value))
    return f"<property type='{kind}'><key>{_cdata(key)}</key><value>{val}</value></property>"


def _value(key: str, default) -> str:
    return (f"<value><key>{_cdata(key)}</key><locked>0</locked>"
            f"<lockedDefaultCurrent>0</lockedDefaultCurrent>"
            f"<default>{_cdata(default)}</default><defaultPull>0</defaultPull></value>")


def _osc(address: str) -> str:
    """Send and receive ``x`` as a float 0–1 on *address*."""
    return (
        "<messages><osc>"
        "<enabled>1</enabled><send>1</send><receive>1</receive><feedback>0</feedback>"
        "<connections>1111111111</connections>"
        "<triggers><trigger><var>x</var><condition>ANY</condition></trigger></triggers>"
        "<path><partial><type>CONSTANT</type><conversion>STRING</conversion>"
        f"<value>{_cdata(address)}</value><scaleMin>0</scaleMin><scaleMax>1</scaleMax></partial></path>"
        "<arguments><partial><type>VALUE</type><conversion>FLOAT</conversion>"
        "<value>x</value><scaleMin>0</scaleMin><scaleMax>1</scaleMax></partial></arguments>"
        "</osc></messages>"
    )


def _node(kind: str, name: str, frame, props=(), values=(), messages="", children=()) -> str:
    node_id = uuid.uuid5(_NS, f"{kind}:{name}:{frame}")
    base = [_prop("s", "name", name), _prop("r", "frame", frame)]
    return (f"<node ID='{node_id}' type='{kind}'>"
            f"<properties>{''.join(base + list(props))}</properties>"
            f"<values>{''.join(values)}</values>{messages}"
            f"<children>{''.join(children)}</children></node>")


def _label(name: str, text: str, frame, size: int = 16, color=PAPER) -> str:
    return _node("LABEL", name, frame, props=[
        _prop("b", "interactive", False),
        _prop("b", "background", False),
        _prop("b", "outline", False),
        _prop("i", "textSize", size),
        _prop("c", "textColor", color),
    ], values=[_value("text", text)])


def _button(name: str, address: str, frame, color, toggle: bool = False) -> str:
    # buttonType: 0 momentary, 2 toggle on press.
    return _node("BUTTON", name, frame, props=[
        _prop("c", "color", color),
        _prop("i", "buttonType", 2 if toggle else 0),
        _prop("b", "outline", True),
    ], messages=_osc(address))


def _labelled_button(name, text, address, frame, color, toggle=False, size=16) -> list[str]:
    """A button with its text drawn on top (TouchOSC buttons have no text)."""
    return [_button(name, address, frame, color, toggle),
            _label(f"{name}.label", text, frame, size=size)]


def _fader(name: str, address: str, frame, color=ACCENT) -> str:
    return _node("FADER", name, frame, props=[
        _prop("c", "color", color),
        _prop("b", "outline", True),
    ], messages=_osc(address))


def _grid(count: int, cols: int, rows: int, x0, y0, width, height):
    """Yield the frame of each cell, row by row."""
    cw = (width - GAP * (cols - 1)) / cols
    ch = (height - GAP * (rows - 1)) / rows
    for i in range(count):
        r, c = divmod(i, cols)
        yield (x0 + c * (cw + GAP), y0 + r * (ch + GAP), cw, ch)


def _side_column(page: str, with_cues: bool) -> list[str]:
    """Master fader, cue GO / STOP and blackout down the right edge."""
    x = WIDTH - MARGIN - SIDE_W
    out = [_label(f"{page}.master.label", "MASTER", (x, TOP, SIDE_W, 28), size=14, color=MUTED)]
    fader_bottom = 520 if with_cues else 600
    out.append(_fader(f"{page}.master", "/master", (x + 60, TOP + 36, 96, fader_bottom - TOP - 36)))
    if with_cues:
        half = (SIDE_W - GAP) / 2
        out += _labelled_button(f"{page}.cue.go", "GO", "/cue/go", (x, 536, half, 64), GREEN)
        out += _labelled_button(f"{page}.cue.stop", "STOP", "/cue/stop", (x + half + GAP, 536, half, 64), PAPER)
    top = 616
    out += _labelled_button(f"{page}.blackout", "BLACKOUT", "/blackout",
                            (x, top, SIDE_W, HEIGHT - MARGIN - top), RED, size=20)
    return out


def _page(name: str, tab: str, children: list[str]) -> str:
    return _node("GROUP", name, (0, 0, WIDTH, HEIGHT), props=[
        _prop("s", "tabLabel", tab),
        _prop("c", "color", INK),
        _prop("b", "background", True),
    ], children=children)


def _button_page(name, tab, items, kind_color, toggle, with_cues) -> str:
    grid_w = WIDTH - 2 * MARGIN - SIDE_W - GAP
    rows = 6
    children = []
    for item, frame in zip(items, _grid(len(items), 4, rows, MARGIN, TOP, grid_w, HEIGHT - TOP - MARGIN),
                           strict=False):
        children += _labelled_button(f"{name}.{item['address']}", item["name"], item["address"],
                                     frame, kind_color, toggle=toggle)
    if not items:
        children.append(_label(f"{name}.empty", "No scenes in this workspace yet",
                               (MARGIN, TOP, grid_w, 80), color=MUTED))
    children += _side_column(name, with_cues)
    return _page(name, tab, children)


def _fader_page(faders: list[dict]) -> str:
    grid_w = WIDTH - 2 * MARGIN - SIDE_W - GAP
    children = []
    frames = list(_grid(FADERS, FADERS, 1, MARGIN, TOP, grid_w, HEIGHT - TOP - MARGIN))
    for i, (fader, (x, y, w, h)) in enumerate(zip(faders[:FADERS], frames, strict=False)):
        children.append(_label(f"faders.{i}.label", fader["name"], (x, y, w, 40), size=12, color=MUTED))
        children.append(_fader(f"faders.{i}", fader["address"], (x, y + 48, w, h - 48)))
    if not faders:
        children.append(_label("faders.empty", "No fixtures in this workspace yet",
                               (MARGIN, TOP, grid_w, 80), color=MUTED))
    children += _side_column("faders", with_cues=False)
    return _page("faders", "Faders", children)


def layout_xml(scenes: list[dict], chases: list[dict], faders: list[dict],
               title: str = "Lights Pi") -> str:
    """The layout's XML.

    *scenes* and *chases*: ``[{"name", "address"}]`` as /api/osc/addresses
    returns them. *faders*: ``[{"name", "address"}]`` for single channels,
    such as ``/fixture/0/1``; the first eight are used.
    """
    pages = []
    for start in range(0, max(1, len(scenes)), SCENES_PER_PAGE):
        chunk = scenes[start:start + SCENES_PER_PAGE]
        n = start // SCENES_PER_PAGE + 1
        tab = "Scenes" if len(scenes) <= SCENES_PER_PAGE else f"Scenes {n}"
        pages.append(_button_page(f"scenes{n}", tab, chunk, ACCENT, toggle=False, with_cues=True))
    pages.append(_fader_page(faders))
    for start in range(0, len(chases), SCENES_PER_PAGE):
        chunk = chases[start:start + SCENES_PER_PAGE]
        n = start // SCENES_PER_PAGE + 1
        tab = "Chases" if len(chases) <= SCENES_PER_PAGE else f"Chases {n}"
        pages.append(_button_page(f"chases{n}", tab, chunk, CHASE, toggle=True, with_cues=False))

    pager = _node("PAGER", "pages", (0, 0, WIDTH, HEIGHT), props=[
        _prop("b", "tabbar", True),
        _prop("i", "tabbarSize", 48),
        _prop("c", "color", PANEL),
    ], children=pages)
    root = _node("GROUP", title, (0, 0, WIDTH, HEIGHT), props=[
        _prop("c", "color", INK),
        _prop("b", "background", True),
    ], children=[pager])
    return "<?xml version='1.0' encoding='UTF-8'?><lexml version='3'>" + root + "</lexml>"


def build_layout(scenes, chases, faders, title: str = "Lights Pi") -> bytes:
    """The ``.tosc`` file: the layout XML, zlib-compressed."""
    return zlib.compress(layout_xml(scenes, chases, faders, title).encode("utf-8"), 9)


def channel_faders(fixtures: list[dict], limit: int = FADERS) -> list[dict]:
    """The first *limit* channels across *fixtures*, as fader entries.

    *fixtures* are /api/fixtures entries (``id``, ``name``, ``channel_info``
    with ``offset`` and ``name``); channels count from 0, as ``/fixture``
    expects.
    """
    out = []
    for fx in fixtures:
        for ch in fx.get("channel_info") or []:
            offset = ch.get("offset")
            if not isinstance(offset, int):
                continue
            out.append({
                "name": f"{fx.get('name', fx.get('id'))}\n{ch.get('name') or f'Ch {offset + 1}'}",
                "address": f"/fixture/{fx['id']}/{offset}",
            })
            if len(out) >= limit:
                return out
    return out


def main(argv=None) -> int:
    """Write a layout for a running control server: ``python touchosc_layout.py URL OUT``."""
    import argparse
    import json
    import sys
    import urllib.error
    import urllib.request

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("server", help="control server URL, e.g. http://lights.local:5000")
    parser.add_argument("out", help="where to write the .tosc file")
    args = parser.parse_args(argv)
    base = args.server.rstrip("/")
    url = f"{base}/api/osc/touchosc"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            print("The server asks for a password (LIGHTS_PASSWORD is set). Download the layout "
                  "from the web UI instead: Diagnostics → OSC → Download layout.", file=sys.stderr)
            return 1
        try:
            message = json.loads(exc.read()).get("error")
        except ValueError:
            message = None
        print(f"{url}: HTTP {exc.code}{': ' + message if message else ''}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print(f"Could not reach {url}: {getattr(exc, 'reason', exc)}", file=sys.stderr)
        return 1
    if not data.startswith(b"\x78"):  # zlib header; anything else is an error page
        print(f"{url} did not return a layout", file=sys.stderr)
        return 1
    with open(args.out, "wb") as fh:
        fh.write(data)
    print(f"Wrote {args.out} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
