"""End-to-end tests for skills/brand-identity/scripts/pipeline.py (`brand.py build`, `mix`) with offline fixture fonts.

Needs a Chromium-based browser for the card render; skipped when none is installed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
EXAMPLE = os.path.join(ROOT, "skills", "brand-identity", "templates", "sets.example.json")
FONTS = os.path.join(ROOT, "tests", "fixtures", "fonts")
sys.path.insert(0, SCRIPTS)

import render_png  # noqa: E402
import pipeline  # noqa: E402

HAS_BROWSER = bool(render_png.find_browser())


def offline_sets(n=2):
    """sets.example.json with every face pointed at a fixture file (no downloads)."""
    with open(EXAMPLE, encoding="utf-8") as fh:
        draft = json.load(fh)
    draft["sets"] = draft["sets"][:n]
    files = {"Outfit": os.path.join(FONTS, "Outfit[wght].ttf"), "Arvo": os.path.join(FONTS, "Arvo-Regular.ttf")}
    for s in draft["sets"]:
        for role in ("display", "text"):
            face = s["identity"]["type"][role]
            face["file"] = files.get(face["family"], files["Outfit"])
            face["source"] = "user"
            face["license"] = "OFL-1.1"
            if face["family"] == "Arvo":
                face["location"] = {}
                face["weights"] = [400]
    return draft


@unittest.skipUnless(HAS_BROWSER, "no Chromium-based browser")
class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "brand.py"), "init", "Ferrow", "--sets", "2",
                            "--langs", "en", "--numbers"], cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.work = os.path.join(self.tmp, "brand-identity", "ferrow")
        with open(os.path.join(self.work, "sets.json"), "w", encoding="utf-8") as fh:
            json.dump(offline_sets(), fh)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_writes_audited_identities_and_board(self):
        states, board, _ = pipeline.run_build(self.work, site=False)
        self.assertEqual(sorted(states), ["A", "B"])
        for sid in ("A", "B"):
            with open(os.path.join(self.work, "sets", sid, "identity.json"), encoding="utf-8") as fh:
                ident = json.load(fh)
            self.assertEqual(ident["audit"]["schema"], "brand-identity/audit@1")
            self.assertTrue(ident["derived"][0]["inputs_sha256"])
            self.assertTrue(os.path.isfile(os.path.join(self.work, "sets", sid, "palette.json")))
            self.assertTrue(os.path.isfile(os.path.join(self.work, "sets", sid, "type", "fonts.json")))
            self.assertTrue(os.path.isfile(os.path.join(self.work, "sets", sid, "card.png")))
        for key in ("board", "review", "table"):
            self.assertTrue(os.path.isfile(board[key]), key)
        # brief fields reach the identity
        self.assertEqual(states["A"]["ident"]["brand"]["name"], "Ferrow")

    def test_text_in_symbol_is_a_gate(self):
        draft = offline_sets(1)
        draft["sets"][0]["symbol_svg"] = '<svg viewBox="0 0 100 100"><text x="10" y="50">F</text></svg>'
        with open(os.path.join(self.work, "sets.json"), "w", encoding="utf-8") as fh:
            json.dump(draft, fh)
        states, _, _ = pipeline.run_build(self.work, sets=["A"], site=False)
        gates = [f for f in states["A"]["final"] if f["severity"] == "gate"]
        self.assertTrue(gates, "a <text> element in the symbol must fail the set")

    def test_mix_adds_a_set(self):
        pipeline.run_build(self.work, site=False)
        import argparse
        code = pipeline.cli_mix(argparse.Namespace(work=self.work, parts=["A:logo", "A:type", "B:palette"],
                                                   as_id="D"))
        self.assertIn(code, (0, 1))
        with open(os.path.join(self.work, "sets", "D", "identity.json"), encoding="utf-8") as fh:
            ident = json.load(fh)
        self.assertTrue(any(f["id"] == "cohesion.mixed" for f in ident["audit"]["findings"]) or
                        "mix" in ident["set"]["name"])


class Critique(unittest.TestCase):
    def test_logo_with_text_is_a_gate_and_cwd_untouched(self):
        import argparse
        with tempfile.TemporaryDirectory() as tmp:
            logo = os.path.join(tmp, "logo.svg")
            with open(logo, "w") as fh:
                fh.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text x="5" y="50">X</text></svg>')
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                code = pipeline.cli_critique(argparse.Namespace(site=None, logo=logo, fonts=None, palette=None,
                                                                langs="en", full=False))
            finally:
                os.chdir(cwd)
            self.assertEqual(code, 1)
            self.assertFalse(os.path.exists(os.path.join(tmp, "site")))

    def test_rel_falls_back_to_file_url_across_drives(self):
        import logo_audit
        from unittest import mock
        with mock.patch("os.path.relpath", side_effect=ValueError("path is on mount 'C:', start on mount 'D:'")):
            self.assertTrue(logo_audit._rel("/x/logo.svg", "/y").startswith("file:///"))

    def test_palette_audit_runs(self):
        import argparse
        ex = os.path.join(ROOT, "skills", "brand-identity", "templates", "palette.example.json")
        code = pipeline.cli_critique(argparse.Namespace(site=None, logo=None, fonts=None, palette=ex, langs="en",
                                                        full=False))
        self.assertIn(code, (0, 1))


class Slug(unittest.TestCase):
    def test_turkish_and_accents_fold(self):
        sys.path.insert(0, SCRIPTS)
        import brand
        self.assertEqual(brand.slugify("Şişdağı Çay"), "sisdagi-cay")
        self.assertEqual(brand.slugify("Café Øresund"), "cafe-oresund")


class Defaults(unittest.TestCase):
    def test_font_and_tag_matches(self):
        ident = {"type": {"display": {"family": "Fraunces"}, "text": {"family": "Inter"}},
                 "logo": {"tags": ["spark"]}, "defaults_used": [{"id": "inter-text", "why": "user asked for Inter"}]}
        hits = pipeline.defaults_matches(ident, None)
        self.assertIn("fraunces-display", hits)
        self.assertIn("spark", hits)
        ids = {f["id"] for f in pipeline.defaults_findings(ident, hits)}
        self.assertIn("default.fraunces-display", ids)
        self.assertNotIn("default.inter-text", ids)

    def test_competitor_colour_too_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "site"))
            with open(os.path.join(tmp, "site", "competitors.json"), "w") as fh:
                json.dump({"sites": [{"host": "pando.ai", "status": "ok", "roles": {"primary": "#003d31"}},
                                     {"host": "parked.example", "status": "parked", "roles": {"primary": "#255032"}}]},
                          fh)
            ident = {"components": {"palette": {"mode": "new"}}}
            near = pipeline.competitor_findings(tmp, ident, {"brand": [{"name": "Ink", "hex": "#255032"}]})
            far = pipeline.competitor_findings(tmp, ident, {"brand": [{"name": "Ink", "hex": "#3d5d2a"}]})
            kept = pipeline.competitor_findings(tmp, {"components": {"palette": {"mode": "keep"}}},
                                                {"brand": [{"name": "Ink", "hex": "#255032"}]})
        self.assertEqual([f["id"] for f in near], ["cohesion.competitor-colour"])
        self.assertEqual(far, [])
        self.assertEqual(kept, [])

    def test_cohesion_close_sets(self):
        out = pipeline.cohesion_findings({"A": {"axes": {"warm_cool": 1, "quiet_loud": 0}, "hits": []},
                                          "B": {"axes": {"warm_cool": 0, "quiet_loud": 1}, "hits": ["x"]}})
        self.assertEqual([f["id"] for f in out], ["cohesion.sets-close"])


if __name__ == "__main__":
    unittest.main()
