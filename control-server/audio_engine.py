"""Audio engine: USB mic → BPM + onset detection via aubio.

Designed to import cleanly even when aubio/sounddevice are absent — all
hardware-gated code lives inside the capture thread and is guarded by the
`available` flag.  Pure helpers at module level are unit-testable with no
hardware dependency.
"""
import statistics
import threading
import time

# ---------------------------------------------------------------------------
# Pure helpers — no hardware, fully unit-testable
# ---------------------------------------------------------------------------

def bpm_to_interval_ms(bpm: float) -> float:
    """Convert BPM to step interval in milliseconds."""
    if bpm <= 0:
        raise ValueError(f"BPM must be positive, got {bpm}")
    return 60_000.0 / bpm


def clamp_bpm(bpm: float, lo: float = 60.0, hi: float = 200.0) -> float:
    """Clamp detected BPM to a plausible musical range."""
    return max(lo, min(hi, float(bpm)))


def noise_gate_passes(rms: float, threshold: float) -> bool:
    """Return True when RMS energy exceeds the noise-gate threshold."""
    return threshold >= 0 and rms >= threshold


def smooth_bpm(history: list, window: int = 8) -> float:
    """Return a smoothed BPM estimate using a median of recent detections."""
    if not history:
        return 0.0
    recent = list(history[-window:]) if len(history) >= window else list(history)
    if not recent:
        return 0.0
    return float(statistics.median(recent))


# ---------------------------------------------------------------------------
# AudioEngine
# ---------------------------------------------------------------------------

class AudioEngine:
    """Captures audio from a USB mic and publishes BPM + onset events.

    Usage::

        engine = AudioEngine()
        if engine.available:
            engine.subscribe(lambda evt: print(evt))
            engine.start()
            ...
            engine.stop()

    Subscribers are callables that receive one dict argument:
        {"type": "bpm",    "bpm": 120.0}
        {"type": "onset",  "onset_ms": 1700000000000, "rms": 0.042}

    All state mutations are lock-protected so subscribers can be
    added/removed from Flask request threads while the capture thread runs.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: list = []
        self._running = False
        self._thread: threading.Thread | None = None

        # Public state
        self._bpm: float = 0.0
        self._bpm_history: list[float] = []
        self._recent_rms: list[float] = []
        self._last_onset_ms: int = 0
        self._sensitivity: float = 0.02   # RMS noise-gate threshold
        self._device: str | int | None = None  # None = system default

        # Availability is determined by lazy import at init time
        self.available: bool = self._check_deps()

    # ------------------------------------------------------------------
    # Dependency check
    # ------------------------------------------------------------------

    @staticmethod
    def _check_deps() -> bool:
        try:
            import aubio  # noqa: F401
            import numpy  # noqa: F401
            import sounddevice  # noqa: F401
            return True
        except ImportError:
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, device=None, sensitivity: float | None = None) -> bool:
        """Start audio capture. Returns False if deps unavailable."""
        if not self.available:
            return False
        with self._lock:
            if sensitivity is not None:
                self._sensitivity = float(sensitivity)
            if device is not None:
                self._device = device
            if self._running:
                # Restart: signal existing thread to stop, wait briefly
                self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        with self._lock:
            self._running = True
            self._bpm = 0.0
            self._bpm_history = []
            self._recent_rms = []
            self._thread = threading.Thread(
                target=self._capture_loop,
                daemon=True,
                name="audio-engine",
            )
            self._thread.start()
        return True

    def stop(self):
        """Stop audio capture and join the capture thread."""
        with self._lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def get_state(self) -> dict:
        """Thread-safe snapshot of current engine state."""
        with self._lock:
            return {
                "available": self.available,
                "enabled": self._running,
                "bpm": round(self._bpm, 1),
                "last_onset_ms": self._last_onset_ms,
                "sensitivity": self._sensitivity,
                "device": self._device,
            }

    def get_bpm(self) -> float:
        """Return the current smoothed BPM (0.0 if no signal)."""
        with self._lock:
            return self._bpm

    def calibrate(self) -> dict:
        """Auto-set noise gate from recent ambient audio samples.

        Requires the engine to be running so samples are available.
        Sets sensitivity to 3× the measured background RMS floor.
        """
        with self._lock:
            recent = list(self._recent_rms[:30])  # oldest 30 frames = ~350ms
        if not recent:
            return {
                "success": False,
                "error": "No audio samples yet — enable the audio engine first",
            }
        floor = statistics.mean(recent)
        threshold = floor * 3.0
        with self._lock:
            self._sensitivity = threshold
        return {
            "success": True,
            "noise_floor_rms": round(floor, 6),
            "threshold": round(threshold, 6),
        }

    def subscribe(self, cb) -> None:
        with self._lock:
            if cb not in self._subscribers:
                self._subscribers.append(cb)

    def unsubscribe(self, cb) -> None:
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s is not cb]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _notify(self, event: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for cb in subscribers:
            try:
                cb(event)
            except Exception as exc:
                print(f"[audio-engine] subscriber error: {exc}")

    def _capture_loop(self) -> None:
        """Background capture thread: read audio → detect BPM + onset → notify."""
        try:
            import aubio
            import numpy as np
            import sounddevice as sd

            sr = 44100
            hop = 512
            buf = 1024

            onset_det = aubio.onset("complex", buf, hop, sr)
            tempo_det = aubio.tempo("specdiff", buf, hop, sr)

            with sd.InputStream(
                device=self._device,
                channels=1,
                samplerate=sr,
                blocksize=hop,
                dtype="float32",
            ) as stream:
                while self._running:
                    data, _ = stream.read(hop)
                    samples = data[:, 0]
                    rms = float(np.sqrt(np.mean(samples ** 2)))

                    # Rolling RMS history for calibration
                    with self._lock:
                        self._recent_rms.append(rms)
                        if len(self._recent_rms) > 200:
                            self._recent_rms = self._recent_rms[-200:]

                    # Always feed detectors to maintain internal phase
                    tempo_det(samples)
                    onset_out = onset_det(samples)
                    raw_bpm = float(tempo_det.get_bpm())

                    gate_open = noise_gate_passes(rms, self._sensitivity)

                    # Update BPM estimate when gate is open and tempo has a reading
                    if gate_open and raw_bpm > 0:
                        clamped = clamp_bpm(raw_bpm)
                        with self._lock:
                            self._bpm_history.append(clamped)
                            if len(self._bpm_history) > 32:
                                self._bpm_history = self._bpm_history[-32:]
                            self._bpm = smooth_bpm(self._bpm_history)
                        self._notify({"type": "bpm", "bpm": round(self._bpm, 1)})

                    # Fire onset only when above noise gate
                    if gate_open and onset_out[0] > 0:
                        onset_ms = int(time.time() * 1000)
                        with self._lock:
                            self._last_onset_ms = onset_ms
                        self._notify({
                            "type": "onset",
                            "onset_ms": onset_ms,
                            "rms": round(rms, 4),
                        })

        except Exception as exc:
            print(f"[audio-engine] capture error: {exc}")
        finally:
            with self._lock:
                self._running = False


# Module-level singleton — imported and used by app.py
_engine = AudioEngine()
