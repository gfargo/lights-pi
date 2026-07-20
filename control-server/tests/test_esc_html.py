"""Tests for the client-side `escHtml` helper in templates/index.html.

`escHtml` is interpolated into HTML *attribute* contexts (e.g. scene card
`title="${escHtml(s.name)}"`), not just text nodes. The old implementation
used the textContent->innerHTML DOM trick, which only escapes `& < >` —
not `"` or `'` — so a stored value like a scene name containing a `"` could
break out of a double-quoted attribute and inject a live event handler
(stored attribute injection). This pins the fix: `escHtml` must map all
five HTML-significant characters via an explicit regex/object-literal.
"""
import re
from pathlib import Path

import pytest

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "index.html"


def _read_esc_html_source():
    text = TEMPLATE_PATH.read_text()
    m = re.search(r"function escHtml\(s\) \{.*?\n        \}", text, re.DOTALL)
    assert m, "escHtml function not found in templates/index.html"
    return m.group(0)


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
