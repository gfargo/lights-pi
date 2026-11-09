#!/usr/bin/env python3
"""The landing page template's placeholders are all filled by BOTH renderers.

`lightsctl landing-deploy` and `landing-setup` (scripts/services/landing.sh) each render
landing/index.html with sed. landing-deploy once lacked __CONTROL_PORT__/__QLC_PORT__, so a deploy
left literal placeholders in the page's links.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / "landing" / "index.html").read_text()
LIGHTSCTL = (ROOT / "lightsctl.sh").read_text()
LANDING_SH = (ROOT / "scripts" / "services" / "landing.sh").read_text()


def _deploy_block():
    start = LIGHTSCTL.index("function command_landing_deploy()")
    return LIGHTSCTL[start:LIGHTSCTL.index("\nfunction ", start + 10)]


class TestLandingTemplate(unittest.TestCase):
    def test_every_placeholder_is_filled_by_landing_deploy(self):
        block = _deploy_block()
        for name in sorted(set(re.findall(r"__[A-Z_]+__", TEMPLATE))):
            self.assertIn(name, block, f"lightsctl landing-deploy never fills {name}")

    def test_every_placeholder_is_filled_by_landing_setup(self):
        for name in sorted(set(re.findall(r"__[A-Z_]+__", TEMPLATE))):
            if name in ("__QLC_URL__", "__CONTROL_URL__", "__LANDING_TITLE__"):
                continue  # only used by the deploy path / head, not part of the link script
            self.assertIn(name, LANDING_SH, f"landing.sh never fills {name}")

    def test_links_use_https_only_on_the_tailscale_name(self):
        script = TEMPLATE[TEMPLATE.index("var tailnet"):]
        self.assertIn(r"\.ts\.net$", script)
        # plain-HTTP branch for LAN/IP hosts, HTTPS branch only for the tailnet name
        self.assertRegex(script, r"tailnet \? 'https://'.*: 'http://'")


if __name__ == "__main__":
    unittest.main()
