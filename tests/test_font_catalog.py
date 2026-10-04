"""Tests for assets/fonts/catalog.json and tools/build_font_catalog.py (offline; the build itself needs network)."""
import base64
import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
ASSETS = os.path.join(ROOT, "skills", "brand-identity", "assets")
CATALOG = os.path.join(ASSETS, "fonts", "catalog.json")
CORE = os.path.join(ASSETS, "fonts", "gf-latin-core.txt")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "fonts")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import font_audit as fa  # noqa: E402
import typelib  # noqa: E402

CATEGORIES = {"sans-serif", "serif", "display", "handwriting", "monospace"}
MAX_BYTES = 1_500_000


def load_builder():
    spec = importlib.util.spec_from_file_location("build_font_catalog",
                                                  os.path.join(ROOT, "tools", "build_font_catalog.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCatalogFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(CATALOG, encoding="utf-8") as fh:
            cls.raw = fh.read()
        cls.cat = json.loads(cls.raw)
        cls.fams = {(r["family"], r["source"]): r for r in cls.cat["families"]}
        cls.google = {r["family"]: r for r in cls.cat["families"] if r["source"] == "google"}

    def test_size_and_header(self):
        self.assertLessEqual(len(self.raw.encode("utf-8")), MAX_BYTES)
        self.assertEqual(self.cat["schema"], "brand-identity/font-catalog@1")
        idx = self.cat["lang_index"]
        self.assertEqual(idx, sorted(idx))
        for lid in ("en_Latn", "tr_Latn", "de_Latn", "ar_Arab", "ja_Jpan", "hi_Deva", "ru_Cyrl"):
            self.assertIn(lid, idx)

    def test_records(self):
        self.assertGreater(len(self.google), 1500)
        self.assertEqual(len(self.fams), len(self.cat["families"]), "duplicate family+source")
        nbytes = (len(self.cat["lang_index"]) + 7) // 8
        measured = [r for r in self.google.values() if r.get("measured", True)]
        with_x = [r for r in measured if "x_cap" in r["metrics"]]  # glyphless fonts (Adobe Blank) have none
        self.assertGreater(len(with_x), 0.95 * len(measured))
        for r in self.cat["families"]:
            where = f"{r['family']} ({r['source']})"
            self.assertIn(r["source"], ("google", "fontshare"), where)
            self.assertIn(r["license"], typelib.LICENSES, where)
            self.assertIn(r["category"], CATEGORIES, where)
            self.assertIsInstance(r["ai_default"], bool, where)
            self.assertTrue(r.get("popularity") is None or isinstance(r["popularity"], int), where)
            # only computed fields and factual metadata: no Google tag scores
            for banned in ("expressive", "tags", "quality", "scores"):
                self.assertNotIn(banned, r, where)
            self.assertTrue(isinstance(r.get("class", []), list), where)
            if r["source"] == "fontshare":
                self.assertIs(r["render"], False, where)
                self.assertTrue(r["url"].startswith("https://www.fontshare.com/"), where)
                continue
            if r.get("measured", True):
                self.assertEqual(len(base64.b64decode(r["coverage"]["langs"])), nbytes, where)
                self.assertIsInstance(r["metrics"], dict, where)
                self.assertIsInstance(r["features"]["tnum"], bool, where)
            else:
                self.assertTrue(r["subsets"], where)

    def test_known_facts(self):
        idx = self.cat["lang_index"]
        arvo, inter, fraunces = self.google["Arvo"], self.google["Inter"], self.google["Fraunces"]
        self.assertFalse(fa.covers(arvo, "tr_Latn", idx))
        self.assertTrue(fa.covers(arvo, "en_Latn", idx))
        self.assertTrue(fa.covers(inter, "tr_Latn", idx))
        self.assertTrue(fa.covers(inter, "ru_Cyrl", idx))
        self.assertTrue(inter["features"]["tnum"])            # via the tnum feature
        self.assertTrue(self.google["Red Hat Mono"]["features"]["tnum"])  # tabular by default
        self.assertTrue(self.google["Exo 2"]["features"]["tnum"])  # "four.tf" 5/1000 em off: within 1%
        self.assertFalse(fraunces["features"]["tnum"])
        self.assertEqual(set(fraunces["axes"]), {"SOFT", "WONK", "opsz", "wght"})
        self.assertIn("legibility", inter)
        self.assertNotIn("legibility", self.google["Lobster"])  # display: narrow field set
        self.assertGreater(len(fa.families_covering("ja_Jpan", self.cat) or []), 0)

    def test_ai_default_flags_follow_defaults_file(self):
        names = fa._ai_default_fonts()
        if not names:
            self.skipTest("assets/ai-defaults.json not present yet")
        for r in self.cat["families"]:
            if typelib._norm(r["family"]) in names:
                self.assertTrue(r["ai_default"], r["family"])

    def test_google_dir_resolves(self):
        for r in self.google.values():
            d = r.get("dir") or typelib.default_dir(r["family"], r["license"])
            self.assertRegex(d, r"^(ofl|apache|ufl)/[a-z0-9_]+$", r["family"])


class TestCoreFile(unittest.TestCase):
    def test_vendored_core(self):
        with open(CORE, encoding="utf-8") as fh:
            head = fh.read(600)
        self.assertIn("google/glyphsets", head)
        self.assertIn("Apache License", head)
        cps = fa.gf_latin_core()
        self.assertGreater(len(cps), 300)
        self.assertIn(0x011F, cps)  # ğ
        self.assertIn(0x0130, cps)  # İ


class TestBuilder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = load_builder()

    def test_bitset_roundtrip(self):
        flags = [True, False, True, True, False, False, False, False, True]
        raw = base64.b64decode(self.b.bitset(flags))
        back = [bool(raw[i // 8] >> (7 - i % 8) & 1) for i in range(len(flags))]
        self.assertEqual(back, flags)

    def test_base_record(self):
        meta = {"family": "Demo Sans", "dir": "ofl/demosans", "license": "OFL-1.1", "category": ["SANS_SERIF"],
                "classifications": [], "stroke": None, "designer": "Ann A, Bob B and Cy C", "date_added": "2020-01-01",
                "subsets": ["latin"], "axes": {}, "fonts": [
                    {"filename": "DemoSans-Regular.ttf", "style": "normal", "weight": 400},
                    {"filename": "DemoSans-Bold.ttf", "style": "normal", "weight": 700}]}
        rec = self.b.base_record(meta, {"Demo Sans": 42})
        self.assertNotIn("dir", rec)  # derivable from licence + name
        self.assertEqual(rec["category"], "sans-serif")
        self.assertEqual(rec["weights"], [400, 700])
        self.assertEqual(rec["designers"], ["Ann A", "Bob B", "Cy C"])
        self.assertEqual(rec["popularity"], 42)
        self.assertNotIn("italic", rec)
        meta["dir"] = "ofl/demosans2"
        self.assertEqual(self.b.base_record(meta, {})["dir"], "ofl/demosans2")

    def test_measure_fixture(self):
        self.b._init_worker()
        fam, rec = self.b.measure(("Outfit", os.path.join(FIXTURES, "Outfit[wght].ttf"), "sans-serif"))
        self.assertNotIn("error", rec)
        self.assertEqual(rec["axes"], {"wght": [100, 900, 100]})
        self.assertTrue(rec["features"]["tnum"])
        self.assertIn("I/l", rec["legibility"])
        self.assertGreater(rec["woff2_kb"]["latin"], 5)
        idx = self.b.lang_index()[0]
        self.assertTrue(fa.covers({"source": "google", **rec}, "tr_Latn", idx))
        fam, rec = self.b.measure(("Arvo", os.path.join(FIXTURES, "Arvo-Regular.ttf"), "display"))
        self.assertNotIn("legibility", rec)
        self.assertFalse(fa.covers({"source": "google", **rec}, "tr_Latn", idx))


if __name__ == "__main__":
    unittest.main()
