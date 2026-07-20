"""Tests for the client-side `escHtml`/`escJsAttr` helpers in templates/index.html.

`escHtml` is interpolated into HTML *attribute* contexts (e.g. scene card
`title="${escHtml(s.name)}"`), not just text nodes. The old implementation
used the textContent->innerHTML DOM trick, which only escapes `& < >` —
not `"` or `'` — so a stored value like a scene name containing a `"` could
break out of a double-quoted attribute and inject a live event handler
(stored attribute injection). This pins the fix: `escHtml` must map all
five HTML-significant characters via an explicit regex/object-literal.

`escJsAttr` covers a second, subtler case: values interpolated into a
*single-quoted JS string literal inside an inline event-handler attribute*,
e.g. `onclick="applyGroupTemplate('${escJsAttr(g.name)}','warm')"`. A
browser HTML-decodes attribute values *before* handing them to the JS
parser, so `escHtml`'s `'` -> `&#39;` re-hydrates back into a real quote
and still lets a stored name break out of the JS string (and out of the
`onclick` handler's own scope) even though the HTML parser sees a
well-formed attribute. `escJsAttr` JS-escapes first (so the quote survives
decoding as an escaped `\\'`) and then HTML-escapes the result.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "index.html"
NODE = shutil.which("node")


def _read_esc_html_source():
    text = TEMPLATE_PATH.read_text()
    m = re.search(r"function escHtml\(s\) \{.*?\n        \}", text, re.DOTALL)
    assert m, "escHtml function not found in templates/index.html"
    return m.group(0)


def _read_esc_js_attr_source():
    text = TEMPLATE_PATH.read_text()
    m = re.search(r"function escJsAttr\(s\) \{.*?\n        \}", text, re.DOTALL)
    assert m, "escJsAttr function not found in templates/index.html"
    return m.group(0)


def _run_node(script):
    result = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"node script failed:\n{result.stderr}"
    return result.stdout.strip().splitlines()[-1]


class TestEscHtmlSource:
    """Static checks on the escHtml source — no JS runtime needed."""

    def test_escapes_all_five_entities(self):
        src = _read_esc_html_source()
        for char, entity in [
            ("&", "&amp;"),
            ("<", "&lt;"),
            (">", "&gt;"),
            ('"', "&quot;"),
            ("'", "&#39;"),
        ]:
            assert entity in src, f"escHtml source missing mapping for {char!r} -> {entity!r}"

    def test_does_not_use_dom_texcontent_trick(self):
        """The old implementation (textContent -> innerHTML) only escapes
        `& < >`, not quotes. Guard against regressing back to it."""
        src = _read_esc_html_source()
        assert "textContent" not in src
        assert "innerHTML" not in src

    @pytest.mark.parametrize("char", ["&", "<", ">", '"', "'"])
    def test_regex_class_covers_char(self, char):
        src = _read_esc_html_source()
        m = re.search(r"replace\(/(\[.*?\])/g", src)
        assert m, "escHtml must use a single regex character class covering & < > \" '"
        char_class = m.group(1)
        assert char in char_class or (char == "'" and "'" in char_class)


@pytest.mark.skipif(NODE is None, reason="node not available in this environment")
class TestEscJsAttrRealExecution:
    """Actually run the browser's HTML-attribute-decode + JS-parse pipeline
    in Node, rather than just inspecting source text, so a regression here
    is caught even if some future refactor keeps the same entity mappings
    but breaks the JS-string escaping."""

    def _build_harness(self, payload):
        esc_html_src = _read_esc_html_source()
        esc_js_attr_src = _read_esc_js_attr_source()
        return f"""
        {esc_html_src}
        {esc_js_attr_src}
        // Mirrors the browser: HTML attribute values are entity-decoded
        // before the parsed onclick="..." text is handed to the JS engine.
        function decodeHtmlAttr(s) {{
            return s.replace(/&amp;|&lt;|&gt;|&quot;|&#39;/g, m => ({{
                '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'"
            }}[m]));
        }}
        const payload = {json.dumps(payload)};
        // Same shape as the templates in index.html, e.g.
        // onclick="applyGroupTemplate('${{escJsAttr(g.name)}}','warm')"
        const attrText = "applyGroupTemplate('" + escJsAttr(payload) + "','warm')";
        const jsSource = decodeHtmlAttr(attrText);

        global.pwned = false;
        global.marker = function() {{ global.pwned = true; }};
        global.received = null;
        global.applyGroupTemplate = function(name, tmpl) {{ global.received = name; }};

        (0, eval)(jsSource);
        console.log(JSON.stringify({{pwned: global.pwned, received: global.received}}));
        """

    @pytest.mark.parametrize(
        "payload",
        [
            "x'); marker(); //",
            "x');marker();('",
            "'); marker(); ('",
            "x\\'); marker(); //",
        ],
    )
    def test_quote_breakout_is_neutralized(self, payload):
        out = json.loads(self._run_and_capture(payload))
        assert out["pwned"] is False, (
            f"escJsAttr({payload!r}) allowed JS execution to escape the "
            "intended string argument after HTML-attribute decoding"
        )
        assert out["received"] == payload, (
            "the decoded call should receive the original payload back as "
            "an inert string argument, not truncate or re-interpret it"
        )

    def _run_and_capture(self, payload):
        return _run_node(self._build_harness(payload))
