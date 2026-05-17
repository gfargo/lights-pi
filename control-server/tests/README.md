# Control server tests

Pure-function unit tests for the helpers that have grown out of the v2.3 →
v2.11 buildout. Anything that can be tested without standing up Flask, the
QLC+ WebSocket, or external AI providers lives here.

## Running

```bash
cd control-server
pip install -r requirements-dev.txt
pytest -v
```

About 180 tests, sub-second. CI runs the same suite on every PR.

## What's covered

| File | What it tests |
|------|---------------|
| `test_time_parser.py` | `_parse_time_ms` and `_format_time_ms` — cue list timestamp parsing (`"0:32.500"`, `"32s"`, `"1:23:45"`, int ms) |
| `test_cct.py` | `_cct_to_rgb` Tanner Helland algorithm + `_wwa_mix` warm/cool/amber proportional blend |
| `test_strobe.py` | `_strobe_dmx_value` rate → DMX 0..255 mapping, off forms, clamping |
| `test_palette.py` | `_normalize_palette_value` — all six accepted shapes per group |
| `test_cue_normalizer.py` | `_normalize_cue` — scene / chase / action cue shapes |
| `test_misc_helpers.py` | Direction + run-order enums, fixture ID coercion, `_parse_level` (0-255 / `'75%'` / `'+30'`) |

## What's NOT covered (yet)

By design, this suite avoids:

- **Flask routes** — would need a test client + mocked QLC+ WebSocket
- **The persistent WebSocket loop** — async + external service, integration-test territory
- **AI provider calls** — would need either mocking or live API keys
- **XML workspace mutations** — depends on a real `.qxw` file
- **Cue list playback engine** — async + timing-sensitive

These are all candidates for follow-up integration tests if/when they
become regression hotspots.

## Adding new tests

1. Drop a new `test_*.py` file in this directory.
2. Import the helper(s) from `app` (the parent `conftest.py` adds the
   control-server dir to `sys.path`).
3. Prefer `@pytest.mark.parametrize` for value-table tests — keeps the
   suite readable as a spec.
4. Test the contract, not the implementation. Examples of contract-style
   tests: "input X returns Y", "input garbage returns None cleanly",
   "edge case Z doesn't raise".
