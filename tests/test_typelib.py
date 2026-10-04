"""Unit tests for skills/brand-identity/scripts/typelib.py. Network tests run only with BI_NETWORK_TESTS=1."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "fonts")
ARVO = os.path.join(FIXTURES, "Arvo-Regular.ttf")
OUTFIT = os.path.join(FIXTURES, "Outfit[wght].ttf")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import typelib as tl  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

NETWORK = os.environ.get("BI_NETWORK_TESTS") == "1"


class TempCache(unittest.TestCase):
    """Every test gets its own XDG cache so nothing touches ~/.cache."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bi-typelib-")
        self.env = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": self.tmp})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestLanguages(unittest.TestCase):
    def test_default_script(self):
        cases = {"tr": "tr_Latn", "tr-TR": "tr_Latn", "en": "en_Latn", "de": "de_Latn", "az": "az_Latn",
                 "sr": "sr_Cyrl", "sr-Latn": "sr_Latn", "zh": "zh_Hans", "zh-Hant": "zh_Hant", "ar": "ar_Arab",
                 "ja": "ja_Jpan", "pa": "pa_Guru", "tr_Latn": "tr_Latn", "pt-BR": "pt_Latn"}
        for code, want in cases.items():
            self.assertEqual(tl.lang_id(code), want, code)

    def test_unknown_language(self):
        with self.assertRaises(ValueError):
            tl.lang_id("qqq")
        with self.assertRaises(ValueError):
            tl.lang_id("")

    def test_bcp47(self):
        self.assertEqual(tl.bcp47("tr"), "tr")
        self.assertEqual(tl.bcp47("tr_Latn"), "tr")
        self.assertEqual(tl.bcp47("sr_Latn"), "sr-Latn")
        self.assertEqual(tl.bcp47("sr"), "sr")


class TestCasing(unittest.TestCase):
    def test_turkish(self):
        self.assertEqual(tl.case_text("istanbul ılık", "upper", "tr"), "İSTANBUL ILIK")
        self.assertEqual(tl.case_text("İSTANBUL ILIK", "lower", "tr"), "istanbul ılık")
        self.assertEqual(tl.case_text("bilgi", "upper", "az"), "BİLGİ")
        self.assertEqual(tl.case_text("bilgi", "upper", "tr-TR"), "BİLGİ")

    def test_other_languages(self):
        self.assertEqual(tl.case_text("editorial", "upper", "en"), "EDITORIAL")
        self.assertEqual(tl.case_text("İSTANBUL", "lower", "en"), "i̇stanbul")  # no Turkish rule outside tr/az
        self.assertEqual(tl.case_text("straße", "upper", "de"), "STRASSE")
        self.assertEqual(tl.case_text("Moonvault", "as-is", "tr"), "Moonvault")
        with self.assertRaises(ValueError):
            tl.case_text("x", "title", "en")


class TestTextproto(unittest.TestCase):
    SAMPLE = '''# comment
name: "Demo Sans"
designer: "A, B"
license: "OFL"
category: "SANS_SERIF"
fonts {
  name: "Demo Sans"
  style: "normal"
  weight: 400
  filename: "DemoSans[wght].ttf"
  copyright: "Copyright \\"Demo\\" " "Authors"
}
fonts {
  style: "italic"
  weight: 400
  filename: "DemoSans-Italic[wght].ttf"
}
subsets: "latin"
subsets: "menu"
axes {
  tag: "wght"
  min_value: 100.0
  max_value: 900.0
}
'''

    def test_parse(self):
        pb = tl.parse_textproto(self.SAMPLE)
        self.assertEqual(pb["name"], ["Demo Sans"])
        self.assertEqual(len(pb["fonts"]), 2)
        self.assertEqual(pb["fonts"][0]["weight"], [400])
        self.assertEqual(pb["fonts"][0]["copyright"], ['Copyright "Demo" Authors'])
        self.assertEqual(pb["axes"][0]["min_value"], [100.0])
        meta = tl._family_meta(pb, "ofl/demosans")
        self.assertEqual(meta["license"], "OFL-1.1")
        self.assertEqual(meta["subsets"], ["latin"])
        self.assertEqual(meta["axes"], {"wght": [100.0, 900.0]})
        self.assertEqual(tl._pick_file(meta, {}), "DemoSans[wght].ttf")
        self.assertEqual(tl._pick_file(meta, {"ital": 1}), "DemoSans-Italic[wght].ttf")

    def test_static_pick_closest_weight(self):
        meta = {"family": "X", "fonts": [{"filename": f"X-{w}.ttf", "style": "normal", "weight": w}
                                         for w in (300, 400, 700)]}
        self.assertEqual(tl._pick_file(meta, {"wght": 650}), "X-700.ttf")
        self.assertEqual(tl._pick_file(meta, {}), "X-400.ttf")


class TestFontInfo(unittest.TestCase):
    def test_static(self):
        info = tl.font_info(ARVO)
        self.assertFalse(info["variable"])
        self.assertEqual(info["names"]["family"], "Arvo")
        self.assertAlmostEqual(info["metrics"]["x_over_cap"], 0.683, places=2)
        self.assertGreater(info["cmap_size"], 100)
        self.assertFalse(info["tnum"])

    def test_variable(self):
        info = tl.font_info(OUTFIT)
        self.assertTrue(info["variable"])
        self.assertEqual([(a["tag"], a["min"], a["max"]) for a in info["axes"]], [("wght", 100.0, 900.0)])
        self.assertIn("kern", info["features"])

    def test_tabular_figures(self):
        self.assertEqual(tl.tabular_figures(TTFont(OUTFIT)), "tnum")
        self.assertIsNone(tl.tabular_figures(TTFont(ARVO)))
        self.assertEqual(tl.font_info(OUTFIT)["tabular"], "tnum")

    def test_tabular_by_default(self):
        f = TTFont(ARVO)
        cmap, hmtx = f.getBestCmap(), f["hmtx"]
        for d in "0123456789":
            g = cmap[ord(d)]
            hmtx[g] = (1200, hmtx[g][1])  # monospaced-style figures
        self.assertEqual(tl.tabular_figures(f), "default")

    def test_tnum_that_leaves_digits_unequal_does_not_count(self):
        f = TTFont(OUTFIT)
        lk = tl._feature_lookups(f["GSUB"].table, "tnum")
        mapping = next(st.mapping for l in lk for st in tl._subtables(l) if getattr(st, "mapping", None))
        nine = mapping[f.getBestCmap()[ord("9")]]
        adv, lsb = f["hmtx"][nine]
        f["hmtx"][nine] = (adv + 5, lsb)   # 5/1000 em off: a defect that does not show
        self.assertEqual(tl.tabular_figures(f), "tnum-approx")
        f["hmtx"][nine] = (adv + 10, lsb)  # exactly 1% of the em: still tabular
        self.assertEqual(tl.tabular_figures(f), "tnum-approx")
        f["hmtx"][nine] = (adv + 11, lsb)  # beyond 1%
        self.assertIsNone(tl.tabular_figures(f))

    def test_not_a_font(self):
        with tempfile.NamedTemporaryFile(suffix=".ttf") as fh:
            fh.write(b"not a font")
            fh.flush()
            with self.assertRaises(tl.FontError):
                tl.font_info(fh.name)


class TestResolveFont(TempCache):
    def test_variable_file_is_instanced_with_every_axis_pinned(self):
        p = tl.resolve_font("Outfit", {"wght": 600}, file=OUTFIT)
        self.assertTrue(str(p).startswith(self.tmp), p)
        f = TTFont(str(p))
        for table in ("fvar", "gvar", "HVAR", "avar"):
            self.assertNotIn(table, f)
        self.assertEqual(f["OS/2"].usWeightClass, 600)
        # overlaps removed: no glyph keeps the OVERLAP_SIMPLE flag
        glyf = f["glyf"]
        for name in ("A", "B", "o"):
            g = glyf[name]
            if g.isComposite() or not g.numberOfContours:
                continue
            self.assertFalse(any(flag & 0x40 for flag in g.flags), name)

    def test_cache_reused(self):
        p1 = tl.resolve_font("Outfit", {"wght": 500}, file=OUTFIT)
        mtime = os.stat(p1).st_mtime_ns
        p2 = tl.resolve_font("Outfit", {"wght": 500}, file=OUTFIT)
        self.assertEqual(p1, p2)
        self.assertEqual(os.stat(p2).st_mtime_ns, mtime)
        p3 = tl.resolve_font("Outfit", {"wght": 700}, file=OUTFIT)
        self.assertNotEqual(p1, p3)

    def test_missing_axes_pinned_at_default(self):
        p = tl.resolve_font("Outfit", None, file=OUTFIT)
        self.assertNotIn("fvar", TTFont(str(p)))

    def test_static_file_returned_as_is(self):
        self.assertEqual(tl.resolve_font("Arvo", {"wght": 400}, file=ARVO), Path(ARVO))

    def test_bad_locations(self):
        with self.assertRaisesRegex(tl.FontError, "wght 100..900"):
            tl.resolve_font("Outfit", {"opsz": 14}, file=OUTFIT)
        with self.assertRaisesRegex(tl.FontError, "outside"):
            tl.resolve_font("Outfit", {"wght": 1000}, file=OUTFIT)
        with self.assertRaisesRegex(tl.FontError, "not found"):
            tl.resolve_font("X", file=os.path.join(self.tmp, "missing.ttf"))

    def test_sources_that_are_not_downloaded(self):
        for src in ("fontshare", "commercial", "user"):
            with self.assertRaisesRegex(tl.FontError, "file="):
                tl.resolve_font("General Sans", {"wght": 500}, source=src)
        with self.assertRaises(tl.FontError):
            tl.resolve_font("X", source="adobe")

    def test_google_family_from_cache_without_network(self):
        d = Path(self.tmp) / "brand-identity" / "fonts" / "google" / "ofl" / "outfit"
        d.mkdir(parents=True)
        shutil.copy(OUTFIT, d / "Outfit[wght].ttf")
        (d / "METADATA.pb").write_text('name: "Outfit"\nlicense: "OFL"\ncategory: "SANS_SERIF"\n'
                                       'fonts {\n  name: "Outfit"\n  style: "normal"\n  weight: 400\n'
                                       '  filename: "Outfit[wght].ttf"\n}\nsubsets: "latin"\n'
                                       'axes {\n  tag: "wght"\n  min_value: 100.0\n  max_value: 900.0\n}\n',
                                       encoding="utf-8")
        with mock.patch.object(tl, "_fetch", side_effect=AssertionError("network used")):
            p = tl.resolve_font("Outfit", {"wght": 700})
        self.assertEqual(TTFont(str(p))["OS/2"].usWeightClass, 700)

    def test_unknown_google_family(self):
        err = tl.FontError("x: HTTP 404 from y")
        with mock.patch.object(tl, "_fetch", side_effect=err):
            with self.assertRaisesRegex(tl.FontError, "not a Google Fonts family"):
                tl.resolve_font("Definitely Not A Font Family")


@unittest.skipUnless(NETWORK, "network test (BI_NETWORK_TESTS=1)")
class TestNetwork(TempCache):
    def test_fraunces_every_axis_pinned(self):
        p = tl.resolve_font("Fraunces", {"wght": 600, "opsz": 144})
        f = TTFont(str(p))
        self.assertNotIn("fvar", f)
        self.assertEqual(f["OS/2"].usWeightClass, 600)
        meta = tl.google_family("Fraunces")
        self.assertEqual(meta["license"], "OFL-1.1")
        self.assertIn("SOFT", meta["axes"])

    def test_static_google_family(self):
        p = tl.resolve_font("Arvo", {"wght": 700})
        self.assertEqual(TTFont(str(p))["OS/2"].usWeightClass, 700)


if __name__ == "__main__":
    unittest.main()
