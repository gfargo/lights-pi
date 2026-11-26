"""Audio engine: USB mic → BPM + onset detection via aubio.

Designed to import cleanly even when aubio/sounddevice are absent — all
hardware-gated code lives inside the capture thread and is guarded by the
`available` flag.  Pure helpers at module level are unit-testable with no
hardware dependency.
"""
import statistics
import threading
import time

from audio_react import LevelTracker, band_energies, classify_hit

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
        {"type": "onset",  "onset_ms": 1700000000000, "rms": 0.042, "kind": "kick"}
        {"type": "level",  "level": 0.63}            (about 10 per second)

    Threading. Capture is a blocking PortAudio read every ~12 ms, so it must
    run on a real OS thread: as a green thread under gunicorn's eventlet
    worker it would never yield and would starve every HTTP request (the
    #109 failure). app.py calls ``use_threading()`` with eventlet's original
    threading module and ``set_dispatch()`` with a function that hands each
    subscriber call to the hub, so subscribers never run on the capture
    thread there. Readers on other OS threads (the QLC+ loop) use the
    lock-free ``*_snapshot()`` methods.
    """

    def __init__(self):
        self._threading = threading
        self._lock = threading.Lock()
        self._subscribers: list = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._dispatch = lambda cb, event: cb(event)

        # Public state
        self._bpm: float = 0.0
        self._bpm_history: list[float] = []
        self._recent_rms: list[float] = []
        self._last_onset_ms: int = 0
        self._last_onset_kind: str = ""
        self._level: float = 0.0          # 0–1 loudness, auto-gained
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
        except (ImportError, OSError):
            # sounddevice raises OSError when PortAudio native lib is absent
            return False

    # ------------------------------------------------------------------
    # Runtime wiring (see the class docstring)
    # ------------------------------------------------------------------

    def use_threading(self, module) -> None:
        """Run capture on *module*'s threads and locks (call before start)."""
        self._threading = module
        self._lock = module.Lock()

    def set_dispatch(self, dispatch) -> None:
        """*dispatch(callback, event)* delivers one event to one subscriber."""
        self._dispatch = dispatch

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
            self._level = 0.0
            self._thread = self._threading.Thread(
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
                "last_onset_kind": self._last_onset_kind,
                "level": round(self._level, 3),
                "sensitivity": self._sensitivity,
                "device": self._device,
            }

    def get_bpm(self) -> float:
        """Return the current smoothed BPM (0.0 if no signal)."""
        with self._lock:
            return self._bpm

    def bpm_snapshot(self) -> float:
        """Lock-free read of the current BPM, safe from any OS thread.

        Reading a float attribute is atomic under the GIL. Use this from the
        QLC+ loop thread, where taking self._lock (a green lock under
        eventlet) can wedge the loop.
        """
        return self._bpm

    def onset_snapshot(self) -> tuple:
        """Lock-free (last_onset_ms, kind) for the QLC+ loop thread."""
        return self._last_onset_ms, self._last_onset_kind

    def level_snapshot(self) -> float:
        """Lock-free 0–1 loudness for the QLC+ loop thread."""
        return self._level

    def set_sensitivity(self, value: float) -> None:
        """Change the noise gate while running."""
        with self._lock:
            self._sensitivity = float(value)

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
                self._dispatch(cb, event)
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

            # Hit classification looks at the last 2048 samples (~46 ms,
            # ~21 Hz bins) so the kick band has enough resolution.
            fft_size = 2048
            window = np.hanning(fft_size).astype("float32")
            ring = np.zeros(fft_size, dtype="float32")
            tracker = LevelTracker()
            last_level_emit = 0.0
            last_bpm_emitted = -1.0
            last_bpm_emit = 0.0

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
                    ring = np.concatenate((ring[hop:], samples))

                    # Loudness for the amplitude effect (plain float write:
                    # readers on other threads take no lock).
                    self._level = tracker.update(rms, self._sensitivity)
                    now = time.time()
                    if now - last_level_emit >= 0.1:
                        last_level_emit = now
                        self._notify({"type": "level", "level": round(self._level, 3)})

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
                        # Only tell the UI when the readout would change, at
                        # most twice a second (this branch runs ~86 times/s).
                        if abs(self._bpm - last_bpm_emitted) >= 0.5 and now - last_bpm_emit >= 0.5:
                            last_bpm_emitted, last_bpm_emit = self._bpm, now
                            self._notify({"type": "bpm", "bpm": round(self._bpm, 1)})

                    # Fire onset only when above noise gate
                    if gate_open and onset_out[0] > 0:
                        onset_ms = int(time.time() * 1000)
                        spectrum = np.abs(np.fft.rfft(ring * window))
                        kind = classify_hit(*band_energies(spectrum, sr, fft_size))
                        # Kind first, then the timestamp readers key on.
                        self._last_onset_kind = kind
                        self._last_onset_ms = onset_ms
                        self._notify({
                            "type": "onset",
                            "onset_ms": onset_ms,
                            "rms": round(rms, 4),
                            "kind": kind,
                        })

        except Exception as exc:
            print(f"[audio-engine] capture error: {exc}")
        finally:
            with self._lock:
                self._running = False


# Module-level singleton — imported and used by app.py
_engine = AudioEngine()
