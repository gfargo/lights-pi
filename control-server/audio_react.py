"""Audio-reactive lighting: the maths, with no hardware and no Flask.

The capture thread (audio_engine.py) measures loudness and classifies hits;
the runner in app.py turns those readings into DMX frames with the helpers
here. Everything in this module is a pure function or a small value class,
so it is tested without a microphone, numpy or QLC+.

Effects
-------
flash      On each matching hit the brightness channels jump to full and
           fade back to the look underneath over ``decay_ms``.
amplitude  Colour follows loudness: quiet is deep blue, then violet, red and
           amber as it gets louder, with brightness rising alongside.
spectrum   Bands to fixtures: each fixture follows the loudness of one
           frequency band (low, mid or high). By default the fixtures are
           split into thirds from left to right across the Stage plan, like
           a spectrum analyser. RGB fixtures take the band's colour.

Hit kinds
---------
A hit is classed by where its energy sits: mostly below ~150 Hz is a kick,
a large share above ~2 kHz with little low end is a snare (or clap/hat),
anything else is "other". ``react_to`` = "any" accepts all three.
"""
from __future__ import annotations

import colorsys
import math

EFFECTS = ("flash", "amplitude", "spectrum")
BANDS = ("low", "mid", "high")
REACT_TO = ("any", "kick", "snare")

# Frequency bands (Hz) used to class a hit.
LOW_BAND = (30.0, 150.0)
MID_BAND = (150.0, 2000.0)
HIGH_BAND = (2000.0, 8000.0)


# ---------------------------------------------------------------------------
# Hit classification
# ---------------------------------------------------------------------------

def band_energies(magnitudes, sample_rate: float, fft_size: int):
    """Sum squared magnitudes of an rfft into (low, mid, high) energies.

    *magnitudes* is any sequence of rfft bin magnitudes (length
    fft_size // 2 + 1). Bin *i* is centred on i * sample_rate / fft_size Hz.
    """
    bin_hz = sample_rate / fft_size
    low = mid = high = 0.0
    for i, m in enumerate(magnitudes):
        f = i * bin_hz
        e = float(m) * float(m)
        if LOW_BAND[0] <= f < LOW_BAND[1]:
            low += e
        elif MID_BAND[0] <= f < MID_BAND[1]:
            mid += e
        elif HIGH_BAND[0] <= f < HIGH_BAND[1]:
            high += e
    return low, mid, high


def band_bins(sample_rate: float, fft_size: int, n_bins: int | None = None) -> dict:
    """Half-open rfft bin ranges ``{band: (start, end)}`` for LOW/MID/HIGH.

    The same edges as ``band_energies``, as index ranges so the capture loop
    can sum a numpy spectrum with slices instead of a Python loop.
    """
    bin_hz = sample_rate / fft_size
    n_bins = n_bins if n_bins is not None else fft_size // 2 + 1

    def index(freq):  # first bin whose centre is >= freq
        return max(0, min(n_bins, math.ceil(freq / bin_hz)))

    return {name: (index(lo), index(hi))
            for name, (lo, hi) in zip(BANDS, (LOW_BAND, MID_BAND, HIGH_BAND), strict=True)}


def classify_hit(low: float, mid: float, high: float) -> str:
    """Return "kick", "snare" or "other" from band energies."""
    total = low + mid + high
    if total <= 0:
        return "other"
    low_share, high_share = low / total, high / total
    if low_share >= 0.5:
        return "kick"
    if high_share >= 0.3 and low_share < 0.25:
        return "snare"
    return "other"


def hit_matches(react_to: str | None, kind: str | None) -> bool:
    """Whether a hit of *kind* should trigger an effect listening for *react_to*."""
    if not react_to:
        return False
    if react_to == "any":
        return True
    return react_to == kind


# ---------------------------------------------------------------------------
# Loudness
# ---------------------------------------------------------------------------

class LevelTracker:
    """Turn raw RMS into a steady 0–1 loudness with automatic gain.

    The level rises quickly (``attack``) and falls slowly (``release``) so
    colour doesn't flicker on every sample. The reference peak follows the
    loudest recent signal and sinks slowly (``peak_decay`` per update), so a
    quiet room and a loud party both use the whole range. Anything under the
    noise gate reads as 0.
    """

    def __init__(self, attack=0.5, release=0.05, peak_decay=0.999, min_peak=0.01):
        self.attack = attack
        self.release = release
        self.peak_decay = peak_decay
        self.min_peak = min_peak
        self.smoothed = 0.0
        self.peak = min_peak
        self.level = 0.0

    def update(self, rms: float, gate: float = 0.0) -> float:
        rms = max(0.0, float(rms))
        signal = rms if rms >= gate else 0.0
        coeff = self.attack if signal > self.smoothed else self.release
        self.smoothed += (signal - self.smoothed) * coeff
        self.peak = max(self.min_peak, max(self.peak * self.peak_decay, self.smoothed))
        floor = max(0.0, gate)
        span = self.peak - floor
        self.level = 0.0 if span <= 0 else max(0.0, min(1.0, (self.smoothed - floor) / span))
        return self.level


# ---------------------------------------------------------------------------
# Effect curves
# ---------------------------------------------------------------------------

def flash_envelope(elapsed_ms: float, decay_ms: float) -> float:
    """Flash strength 1.0 at the hit, easing to 0.0 at *decay_ms*.

    Quadratic ease-out: it drops fast at first, so consecutive hits read as
    separate pulses, then settles gently into the look underneath.
    """
    if elapsed_ms < 0 or decay_ms <= 0 or elapsed_ms >= decay_ms:
        return 0.0
    t = elapsed_ms / decay_ms
    return (1.0 - t) ** 2


def mix(base: int, peak: int, amount: float) -> int:
    """Blend from *base* toward *peak* by *amount* (0–1)."""
    amount = max(0.0, min(1.0, amount))
    return int(round(base + (peak - base) * amount))


def amplitude_color(level: float) -> tuple[int, int, int]:
    """Colour for a loudness 0–1: deep blue, violet, red, then amber.

    Brightness rises with level too, from about a quarter to full, so a quiet
    room glows rather than going dark.
    """
    level = max(0.0, min(1.0, level))
    # Hue walks backwards round the wheel from blue (240°) through magenta
    # and red to amber (35° = 395° - 360°).
    hue = (240.0 + 155.0 * level) % 360.0
    value = 0.25 + 0.75 * level
    r, g, b = colorsys.hsv_to_rgb(hue / 360.0, 1.0, value)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def quantize(value: int, step: int = 4) -> int:
    """Round a 0–255 value to *step* so small wobbles don't flood DMX writes.

    0 and 255 are kept exact.
    """
    value = max(0, min(255, int(value)))
    if value in (0, 255) or step <= 1:
        return value
    return max(0, min(255, int(step * math.floor(value / step + 0.5))))


# ---------------------------------------------------------------------------
# Fixture plan and frames
# ---------------------------------------------------------------------------

def plan_fixture(roles: dict, absolute) -> dict:
    """Absolute DMX channels an effect writes for one fixture.

    *roles* is the fixture's role map (``_fixture_roles``); *absolute(offset)*
    turns a 0-based offset into an absolute channel. Returns
    ``{"brightness": [...], "dimmer": ch | None, "rgb": (r, g, b) | None}``.
    """
    def one(role):
        off = roles.get(role)
        return absolute(off) if isinstance(off, int) else None

    rgb = tuple(one(r) for r in ("red", "green", "blue"))
    return {
        "brightness": [absolute(o) for o in roles.get("brightness", []) if isinstance(o, int)],
        "dimmer": one("dimmer"),
        "rgb": rgb if all(c is not None for c in rgb) else None,
    }


def flash_frame(plan: list, base: dict, strength: float) -> list:
    """Channel values for the flash effect at *strength* (0–1).

    Each brightness channel moves from its *base* value (the look before the
    flash) toward full.
    """
    out = {}
    for fx in plan:
        for ch in fx["brightness"]:
            out[ch] = mix(int(base.get(ch, 0)), 255, strength)
    return sorted(out.items())


def amplitude_frame(plan: list, level: float, step: int = 4) -> list:
    """Channel values for the amplitude effect at loudness *level*.

    RGB fixtures take the colour and open their dimmer fully; fixtures
    without RGB (warm/cool whites) take the level on their brightness
    channels.
    """
    r, g, b = amplitude_color(level)
    bright = quantize(int(round((0.25 + 0.75 * max(0.0, min(1.0, level))) * 255)), step)
    out = {}
    for fx in plan:
        if fx["rgb"]:
            for ch, v in zip(fx["rgb"], (r, g, b), strict=True):
                out[ch] = quantize(v, step)
            if fx["dimmer"] is not None:
                out[fx["dimmer"]] = 255
        else:
            for ch in fx["brightness"]:
                out[ch] = bright
    return sorted(out.items())


def changed_values(frame: list, last_sent: dict) -> list:
    """The entries of *frame* whose value differs from what was last sent."""
    return [(ch, v) for ch, v in frame if last_sent.get(ch) != v]


# ---------------------------------------------------------------------------
# Spectrum: bands to fixtures
# ---------------------------------------------------------------------------

BAND_COLOURS = {"low": (255, 24, 0), "mid": (255, 150, 0), "high": (40, 140, 255)}
SPECTRUM_FLOOR = 0.08   # a silent band glows faintly instead of going dark


def assign_bands(fixture_ids: list, positions: dict | None = None) -> dict:
    """Split fixtures into low / mid / high: ``{fixture_id: band}``.

    With a Stage position (``{"x": metres}``) for every fixture, they are
    ordered left to right, so bass sits on the left like a spectrum
    analyser; otherwise they keep the given order. Thirds, except that two
    fixtures take low and high and one takes low.
    """
    ids = list(fixture_ids)
    positions = positions or {}

    def x_of(fid):
        pos = positions.get(str(fid)) or positions.get(fid)
        return pos.get("x") if isinstance(pos, dict) else None

    if ids and all(isinstance(x_of(f), (int, float)) for f in ids):
        ids.sort(key=lambda f: (x_of(f), str(f)))
    n = len(ids)
    if n == 2:
        return {ids[0]: "low", ids[1]: "high"}
    return {fid: BANDS[min(2, i * 3 // n)] for i, fid in enumerate(ids)}


def spectrum_frame(plan: list, levels: dict, step: int = 4) -> list:
    """Channel values for the spectrum effect.

    Each plan entry carries ``band``; *levels* maps band → 0–1. RGB fixtures
    show the band's colour at that level with the dimmer open; others take
    the level on their brightness channels.
    """
    out = {}
    for fx in plan:
        band = fx.get("band")
        if band not in BAND_COLOURS:
            continue
        level = max(0.0, min(1.0, float(levels.get(band, 0.0))))
        value = SPECTRUM_FLOOR + (1.0 - SPECTRUM_FLOOR) * level
        if fx["rgb"]:
            for ch, c in zip(fx["rgb"], BAND_COLOURS[band], strict=True):
                out[ch] = quantize(int(round(c * value)), step)
            if fx["dimmer"] is not None:
                out[fx["dimmer"]] = 255
        else:
            for ch in fx["brightness"]:
                out[ch] = quantize(int(round(value * 255)), step)
    return sorted(out.items())
