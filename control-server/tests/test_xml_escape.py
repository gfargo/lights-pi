"""Tests for _xml_escape — used when injecting AI-generated scenes into
the QLC+ workspace XML.

The function has to handle every XML special character correctly because
malformed workspace XML can corrupt the user's `.qxw` file. The order of
substitutions matters too: `&` must be escaped first, or `&amp;` would
become `&amp;amp;`.
"""
import pytest
from app import _xml_escape


class TestXmlEscape:
    """Five XML entities, ordering matters, must roundtrip safely."""

    @pytest.mark.parametrize("inp,expected", [
        ("&", "&amp;"),
        ("<", "&lt;"),
        (">", "&gt;"),
        ('"', "&quot;"),
        ("'", "&apos;"),
    ])
    def test_individual_entities(self, inp, expected):
        assert _xml_escape(inp) == expected

    def test_no_special_chars_passthrough(self):
        assert _xml_escape("hello world") == "hello world"
        assert _xml_escape("Chorus Sunset") == "Chorus Sunset"

    def test_empty_string(self):
        assert _xml_escape("") == ""

    def test_ampersand_escaped_before_others(self):
        """If `&` weren't substituted first, `&amp;` would become
        `&amp;amp;` when the second pass hit the new ampersand. Verify
        order-of-operations is correct."""
        assert _xml_escape("&") == "&amp;"
        # Compound case: input contains both `&` and `<`.
        assert _xml_escape("a & b < c") == "a &amp; b &lt; c"

    def test_already_escaped_text_gets_double_escaped(self):
        """This is intentional — the function escapes raw text, not
        partially-escaped strings. Callers must pass plain text."""
        assert _xml_escape("&amp;") == "&amp;amp;"

    def test_mixed_content(self):
        # Realistic example: a scene name with quotes + apostrophe.
        inp = """Joe's "warm" wash & dim"""
        out = _xml_escape(inp)
        assert "&apos;" in out
        assert "&quot;" in out
        assert "&amp;" in out
        # Sanity: no raw unescaped XML chars remain.
        for ch in ["<", ">"]:
            assert ch not in out

    def test_html_tag_in_text_escaped(self):
        # A name like `<script>` should be neutralized.
        assert _xml_escape("<script>") == "&lt;script&gt;"

    def test_attribute_safe(self):
        """Text intended for an XML attribute must escape both quote types.
        QLC+ workspace uses double-quoted attributes; single quotes inside
        are technically legal but escaping defensively is correct."""
        result = _xml_escape('Path="Live"')
        assert '"' not in result
        assert "&quot;" in result

    def test_unicode_passthrough(self):
        """Non-ASCII chars don't need escaping — XML accepts UTF-8 directly."""
        assert _xml_escape("Café · 中文 · 🎭") == "Café · 中文 · 🎭"

    def test_newlines_passthrough(self):
        """Newlines inside CDATA or text content are valid XML."""
        assert _xml_escape("line1\nline2") == "line1\nline2"
