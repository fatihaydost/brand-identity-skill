"""Unit tests for the brand palette library: build_catalog.py and search_library.py (stdlib unittest).

Synthetic fixtures exercise the build pipeline in a temporary folder; the shipped library is checked for schema,
consistency and the v1 coverage targets (about 200 brands, about 20 industries, >= 25 Turkish brands).
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
LIBRARY = os.path.join(ROOT, "skills", "brand-identity", "assets", "library")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import build_catalog as bc  # noqa: E402
import search_library as sl  # noqa: E402


def cls(brand, url, industry="fintech", country="GB", price="mid", tone=("friendly", "bold"), **extra):
    d = {"brand": brand, "url": url, "country": country, "industry": industry,
         "positioning": {"price": price, "tone": list(tone)},
         "strategy_note": f"{brand} test note for the fixture.", "exemplary": False}
    d.update(extra)
    return d


def site(host, roles, top=(), conf=None):
    return {"url": f"https://{host}/", "host": host, "roles": roles,
            "confidence": conf or {k: 0.8 for k in roles},
            "topColors": [{"hex": h, "oklch": "", "score": 1, "areaShare": 0.01, "roles": []} for h in top],
            "lightnessRange": [0.2, 0.98], "nHues": 1, "hues": []}


def run(fn, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = fn(argv)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class TestHueFamily(unittest.TestCase):
    def test_reference_colours(self):
        cases = {"#e50914": "red", "#ff4f40": "red", "#ff8000": "orange", "#ffbc0d": "yellow", "#00a651": "green",
                 "#008080": "teal", "#1f2a5c": "blue", "#0000ff": "blue", "#7c3aed": "purple", "#e6007e": "pink",
                 "#8b4513": "brown", "#000000": "neutral", "#f5f5f7": "neutral", "#64748b": "neutral",
                 "#6d1a36": "red"}
        for hex_, fam in cases.items():
            self.assertEqual(bc.hue_family(hex_), fam, hex_)

    def test_merge_hues(self):
        self.assertEqual(len(bc.merge_hues(["#e50914", "#ff4f40", "#0000ff", "#000000"])), 2)

    def test_hue_gaps_wrap(self):
        gaps = bc.hue_gaps([10, 30, 250])
        self.assertEqual(gaps[0]["width"], 220)
        self.assertEqual((gaps[0]["from"], gaps[0]["to"]), (30, 250))
        self.assertTrue(any(g["from"] == 250 and g["to"] == 10 for g in gaps))
        self.assertEqual(bc.hue_gaps([])[0]["width"], 360)


class TestValidation(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(bc.validate_classifications([cls("A", "https://a.com")]), [])

    def test_errors_are_specific(self):
        bad = cls("A", "https://a.com", industry="crypto", country="tr", price="cheap", tone=("x",))
        bad["exemplary"] = "yes"
        bad["role_overrides"] = {"primary": "#FFF", "logo": "#000000"}
        errs = "\n".join(bc.validate_classifications([bad, cls("A", "https://www.a.com/x")]))
        for needle in ("industry 'crypto'", "ISO 3166", "positioning.price", "2-3 adjectives", "'exemplary'",
                       "role_overrides.primary", "role_overrides.logo is not a role key", "duplicate brand",
                       "duplicates classifications[0]"):
            self.assertIn(needle, errs)

    def test_missing_keys(self):
        errs = bc.validate_classifications([{"brand": "A"}])
        self.assertTrue(any("missing 'url'" in e for e in errs))


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lib = os.path.join(self.tmp, "library")
        os.makedirs(self.lib)
        classes = [
            cls("Coral Bank", "https://coral.example", exemplary=True),
            cls("Blue One", "https://www.blue1.example"),
            cls("Blue Two", "https://blue2.example", country="TR"),
            cls("Night", "https://night.example", role_overrides={"primary": "#7c3aed"}, role_note="CTA was grey."),
            cls("Burger", "https://burger.example", industry="restaurant-delivery", country="US"),
        ]
        with open(os.path.join(self.lib, "classifications.json"), "w", encoding="utf-8") as fh:
            json.dump(classes, fh)
        doc = {"schema": "brand-identity/competitors@1", "measuredAt": "2026-10-01T12:00:00Z", "sites": [
            site("coral.example", {"background": "#ffffff", "text": "#111111", "primary": "#ff4f40",
                                   "accent": "#1f2a5c"}, top=["#ffffff", "#ff4f40", "#00a651"]),
            site("blue1.example", {"background": "#ffffff", "text": "#222222", "primary": "#0050d0"},
                 conf={"background": 0.9, "text": 0.9, "primary": 0.3}),
            site("blue2.example", {"background": "#f7f7f7", "text": "#222222", "primary": "#1f2a5c"}),
            site("night.example", {"background": "#0b0b0f", "text": "#f0f0f0", "primary": "#6c757d"}),
            site("burger.example", {"background": "#ffffff", "text": "#222222", "primary": "#d62300",
                                    "accent": "#ffbc0d"}),
            {"url": "https://broken.example/", "host": "broken.example", "error": "could not load"},
        ]}
        self.raw = os.path.join(self.tmp, "raw", "fintech")
        os.makedirs(self.raw)
        with open(os.path.join(self.raw, "competitors.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def build(self, *extra):
        return run(bc.main, ["--library", self.lib, "--raw", os.path.join(self.tmp, "raw"), *extra])

    def catalog(self):
        with open(os.path.join(self.lib, "catalog.json"), encoding="utf-8") as fh:
            return {r["brand"]: r for r in json.load(fh)}

    def test_build_and_check(self):
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        cat = self.catalog()
        self.assertEqual(len(cat), 5)
        coral = cat["Coral Bank"]
        self.assertEqual(coral["brand_colors"][:2], ["#ff4f40", "#1f2a5c"])
        self.assertIn("#00a651", coral["brand_colors"])
        self.assertEqual(coral["n_hues"], 3)
        self.assertEqual(coral["primary_family"], "red")
        self.assertEqual(coral["category_fit"], "differentiate")
        self.assertEqual(cat["Blue One"]["category_fit"], "conform")
        night = cat["Night"]
        self.assertTrue(night["dark_ground"])
        self.assertEqual(night["roles"]["primary"], "#7c3aed")
        self.assertEqual(night["measured_roles"]["primary"], "#6c757d")
        self.assertEqual(night["role_confidence"]["primary"], 1.0)
        self.assertEqual(night["roles_overridden"], ["primary"])
        self.assertEqual(night["role_note"], "CTA was grey.")
        self.assertIn("low-confidence", err)  # Blue One primary 0.3 without a role_note
        with open(os.path.join(self.lib, "stats.json"), encoding="utf-8") as fh:
            stats = json.load(fh)
        self.assertEqual(stats["count"], 5)
        self.assertEqual(stats["turkish_brands"], 1)
        fin = stats["by_industry"]["fintech"]
        self.assertEqual(fin["n"], 4)
        self.assertAlmostEqual(fin["primary_family"]["blue"], 0.5)
        self.assertAlmostEqual(fin["dark_ground_share"], 0.25)
        self.assertIn("yellow", fin["unused_families"])
        with open(os.path.join(self.lib, "gallery.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('id="fintech--coral-bank"', html)
        self.assertNotRegex(html, r"(src|href)=\"https?://")
        code, out, err = run(bc.main, ["--library", self.lib, "--check"])
        self.assertEqual(code, 0, out)

    def test_relabel_without_raw_keeps_measurements(self):
        self.build()
        path = os.path.join(self.lib, "classifications.json")
        with open(path, encoding="utf-8") as fh:
            classes = json.load(fh)
        classes[3].pop("role_overrides")
        classes[0]["exemplary"] = False
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(classes, fh)
        code, out, err = run(bc.main, ["--library", self.lib])
        self.assertEqual(code, 0, err)
        cat = self.catalog()
        self.assertEqual(cat["Night"]["roles"]["primary"], "#6c757d")  # override undone, measurement kept
        self.assertFalse(cat["Coral Bank"]["exemplary"])

    def test_check_catches_stale_catalog(self):
        self.build()
        path = os.path.join(self.lib, "classifications.json")
        with open(path, encoding="utf-8") as fh:
            classes = json.load(fh)
        classes[1]["industry"] = "banking"
        classes.append(cls("Ghost", "https://ghost.example"))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(classes, fh)
        code, out, _ = run(bc.main, ["--library", self.lib, "--check"])
        self.assertEqual(code, 1)
        self.assertIn("'industry' differs", out)
        self.assertIn("Ghost", out)

    def test_missing_measurement_exit_2(self):
        path = os.path.join(self.lib, "classifications.json")
        with open(path, encoding="utf-8") as fh:
            classes = json.load(fh)
        classes.append(cls("Ghost", "https://ghost.example"))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(classes, fh)
        code, _, err = self.build()
        self.assertEqual(code, 2)
        self.assertIn("Ghost", err)

    def test_search(self):
        self.build()
        code, out, _ = run(sl.main, ["--library", self.lib, "--industry", "fin", "--summary"])
        self.assertEqual(code, 0)
        self.assertIn("4 brands match", out)
        self.assertIn("blue", out)
        self.assertIn("unused families", out)
        code, out, _ = run(sl.main, ["--library", self.lib, "--hue", "red", "--format", "json"])
        self.assertEqual({r["brand"] for r in json.loads(out)}, {"Coral Bank", "Burger"})
        code, out, _ = run(sl.main, ["--library", self.lib, "--hue", "60-120", "--any-colour", "--format", "json"])
        self.assertEqual([r["brand"] for r in json.loads(out)], ["Burger"])
        code, out, _ = run(sl.main, ["--library", self.lib, "--country", "tr", "--format", "paths"])
        self.assertTrue(out.strip().endswith("gallery.html#fintech--blue-two"))
        code, out, _ = run(sl.main, ["--library", self.lib, "--strategy", "differentiate", "--industry", "fintech"])
        self.assertIn("Coral Bank", out)
        self.assertNotIn("Blue One", out)
        code, out, _ = run(sl.main, ["--library", self.lib, "--exemplary", "--format", "json"])
        self.assertEqual([r["brand"] for r in json.loads(out)], ["Coral Bank"])
        code, _, err = run(sl.main, ["--library", self.lib, "--industry", "crypto"])
        self.assertNotEqual(code, 0)
        code, _, _ = run(sl.main, ["--library", self.lib, "--hue", "bluish"])
        self.assertNotEqual(code, 0)


@unittest.skipUnless(os.path.exists(os.path.join(LIBRARY, "catalog.json")), "library not built")
class TestShippedLibrary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(LIBRARY, "catalog.json"), encoding="utf-8") as fh:
            cls.cat = json.load(fh)

    def test_check_passes(self):
        code, out, _ = run(bc.main, ["--check"])
        self.assertEqual(code, 0, out)

    def test_coverage_targets(self):
        inds = {}
        for r in self.cat:
            inds[r["industry"]] = inds.get(r["industry"], 0) + 1
        self.assertGreaterEqual(len(self.cat), 180)
        self.assertGreaterEqual(len(inds), 18)
        self.assertTrue(all(n >= 6 for n in inds.values()), inds)
        tr = [r for r in self.cat if r["country"] == "TR"]
        self.assertGreaterEqual(len(tr), 25)
        self.assertGreaterEqual(len({r["industry"] for r in tr}), 8)

    def test_low_confidence_primaries_reviewed(self):
        open_ = [r["brand"] for r in self.cat if r["role_confidence"].get("primary", 1) < 0.5
                 and not r.get("role_note") and "primary" not in r["roles_overridden"]]
        self.assertEqual(open_, [])

    def test_fintech_summary(self):
        code, out, _ = run(sl.main, ["--industry", "fintech", "--summary"])
        self.assertEqual(code, 0)
        self.assertIn("Primary colour family", out)
        self.assertIn("Open space", out)


if __name__ == "__main__":
    unittest.main()
