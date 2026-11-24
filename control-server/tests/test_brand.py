"""Per-install branding: the pure _brand_config helper, the swatch icon,
and the routes that expose them."""
import app as app_module
from app import DEFAULT_BRAND_ACCENT, DEFAULT_BRAND_NAME, _brand_config, _swatch_icon_svg


class TestBrandConfig:
    def test_defaults_when_unset(self):
        b = _brand_config({})
        assert b == {"name": DEFAULT_BRAND_NAME, "short_name": DEFAULT_BRAND_NAME,
                     "accent": DEFAULT_BRAND_ACCENT}

    def test_name_is_trimmed_and_collapsed(self):
        assert _brand_config({"BRAND_NAME": "  North   Studio "})["name"] == "North Studio"

    def test_name_is_capped(self):
        assert len(_brand_config({"BRAND_NAME": "x" * 100})["name"]) == 40

    def test_blank_name_falls_back(self):
        assert _brand_config({"BRAND_NAME": "   "})["name"] == DEFAULT_BRAND_NAME

    def test_short_name_defaults_to_name_when_it_fits(self):
        assert _brand_config({"BRAND_NAME": "Studio B"})["short_name"] == "Studio B"

    def test_short_name_defaults_to_first_word_when_long(self):
        assert _brand_config({"BRAND_NAME": "Harbourside Rehearsal Rooms"})["short_name"] == "Harbourside"

    def test_explicit_short_name(self):
        b = _brand_config({"BRAND_NAME": "Harbourside Rehearsal Rooms", "BRAND_SHORT_NAME": "Harbour"})
        assert b["short_name"] == "Harbour"

    def test_accent_six_digit_lowercased(self):
        assert _brand_config({"BRAND_ACCENT": "#1E50FF"})["accent"] == "#1e50ff"

    def test_accent_three_digit_expanded(self):
        assert _brand_config({"BRAND_ACCENT": "#0af"})["accent"] == "#00aaff"

    def test_bad_accent_falls_back(self):
        for bad in ("orange", "#12345", "ff6a1f", "#ggg", "red;}body{display:none"):
            assert _brand_config({"BRAND_ACCENT": bad})["accent"] == DEFAULT_BRAND_ACCENT


class TestSwatchIcon:
    def test_eight_cells_on_ink(self):
        svg = _swatch_icon_svg(192)
        assert svg.count("<rect") == 9
        assert 'viewBox="0 0 192 192"' in svg


class TestRoutes:
    def setup_method(self):
        self.client = app_module.app.test_client()

    def test_manifest_uses_brand(self, monkeypatch):
        monkeypatch.setattr(app_module, "BRAND", _brand_config({"BRAND_NAME": "Studio B"}))
        data = self.client.get("/manifest.json").get_json()
        assert data["name"] == "Studio B"
        assert data["short_name"] == "Studio B"

    def test_icon_is_swatch(self):
        r = self.client.get("/icon.svg")
        assert r.status_code == 200
        assert r.mimetype == "image/svg+xml"
        assert r.get_data(as_text=True).count("<rect") == 9

    def test_theme_css_absent_is_404(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app_module, "_THEME_CSS", tmp_path / "theme.css")
        assert self.client.get("/theme.css").status_code == 404

    def test_theme_css_served_when_present(self, monkeypatch, tmp_path):
        css = tmp_path / "theme.css"
        css.write_text(":root { --amber-tungsten: #00c8b4; }")
        monkeypatch.setattr(app_module, "_THEME_CSS", css)
        r = self.client.get("/theme.css")
        assert r.status_code == 200
        assert r.mimetype == "text/css"
        assert "#00c8b4" in r.get_data(as_text=True)
