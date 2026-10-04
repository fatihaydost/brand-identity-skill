"""Unit tests for skills/brand-identity/scripts/font_audit.py. Network tests run only with BI_NETWORK_TESTS=1."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "fonts")
ARVO = os.path.join(FIXTURES, "Arvo-Regular.ttf")
OUTFIT = os.path.join(FIXTURES, "Outfit[wght].ttf")
BRAND = os.path.join(SCRIPTS, "brand.py")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import font_audit as fa  # noqa: E402
import identitylib  # noqa: E402
import typelib  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

NETWORK = os.environ.get("BI_NETWORK_TESTS") == "1"
KEYS = {"id", "component", "severity", "measured", "threshold", "message", "suggested_fix"}


def by_id(findings):
    return {f["id"]: f for f in findings}


class TempCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bi-fontaudit-")
        self.env = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": self.tmp})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestArvo(TempCache):
    """Arvo (static, one weight) lacks Ğ ğ İ Ş ş: the Turkish coverage gate must fail."""

    def test_turkish_coverage_gate(self):
        fs = fa.audit(ARVO, ["tr"])
        f = by_id(fs)["type.coverage.tr_Latn"]
        self.assertEqual(f["severity"], "gate")
        for ch in "Ğ ğ İ Ş ş".split():
            self.assertIn(ch, f["message"])
        self.assertEqual(f["threshold"], "100% base exemplars")
        self.assertTrue(f["measured"].endswith("/58"), f["measured"])

    def test_english_passes_coverage(self):
        ids = by_id(fa.audit(ARVO, ["en"]))
        self.assertNotIn("type.coverage.en_Latn", ids)
        self.assertEqual(ids["type.coverage.gf-latin-core"]["severity"], "info")

    def test_licence_from_sibling_ofl_text(self):
        self.assertNotIn("type.license", by_id(fa.audit(ARVO, ["en"])))

    def test_licence_unknown_is_a_gate(self):
        d = os.path.join(self.tmp, "lonely")
        os.makedirs(d)
        shutil.copy(ARVO, d)
        f = by_id(fa.audit(os.path.join(d, "Arvo-Regular.ttf"), ["en"]))["type.license"]
        self.assertEqual(f["severity"], "gate")

    def test_declared_commercial_licence_is_a_note(self):
        f = by_id(fa.audit(ARVO, ["en"], license="commercial"))["type.license"]
        self.assertEqual(f["severity"], "info")
        self.assertIn("Commercial", f["message"])

    def test_declared_licence_mismatch_warns(self):
        ids = by_id(fa.audit(ARVO, ["en"], license="Apache-2.0"))
        f = ids["type.license-mismatch"]
        self.assertEqual((f["severity"], f["measured"], f["threshold"]), ("warn", "OFL-1.1", "Apache-2.0"))
        for same in ("OFL", "SIL OFL 1.1", "ofl-1.1"):
            ids = by_id(fa.audit(ARVO, ["en"], license=same))
            self.assertNotIn("type.license-mismatch", ids, same)
            self.assertNotIn("type.license", ids, same)  # canonicalised: no commercial note for "OFL"

    def test_tnum_gate_only_with_numbers(self):
        self.assertNotIn("type.tnum", by_id(fa.audit(ARVO, ["en"])))
        self.assertEqual(by_id(fa.audit(ARVO, ["en"], numbers=True))["type.tnum"]["severity"], "gate")

    def test_tabular_by_default_passes_numbers_gate(self):
        f = TTFont(ARVO)
        cmap, hmtx = f.getBestCmap(), f["hmtx"]
        for d in "0123456789":
            hmtx[cmap[ord(d)]] = (1200, hmtx[cmap[ord(d)]][1])
        d = os.path.join(self.tmp, "mono")
        os.makedirs(d)
        f.save(os.path.join(d, "ArvoTab.ttf"))
        shutil.copy(os.path.join(FIXTURES, "OFL-Arvo.txt"), d)
        self.assertNotIn("type.tnum", by_id(fa.audit(os.path.join(d, "ArvoTab.ttf"), ["en"], numbers=True)))

    def test_single_weight_warns_for_text_only(self):
        self.assertEqual(by_id(fa.audit(ARVO, ["en"]))["type.weights"]["severity"], "warn")
        self.assertNotIn("type.weights", by_id(fa.audit(ARVO, ["en"], role="display")))

    def test_finding_shape(self):
        for f in fa.audit(ARVO, ["en", "tr"], numbers=True):
            self.assertTrue(KEYS <= set(f), f)
            self.assertEqual(f["component"], "type")
            self.assertIn(f["severity"], identitylib.SEVERITIES)
            json.dumps(f)

    def test_bad_arguments(self):
        with self.assertRaises(ValueError):
            fa.audit(ARVO, ["en"], uses=("print-on-mars",))
        with self.assertRaises(ValueError):
            fa.audit(ARVO, ["qqq"])


class TestOutfit(TempCache):
    def test_no_gates_en_tr_numbers(self):
        fs = fa.audit(OUTFIT, ["en", "tr"], numbers=True, uses=("web", "logo", "app", "pdf"))
        gates = [f["id"] for f in fs if f["severity"] == "gate"]
        self.assertEqual(gates, [])
        ids = by_id(fs)
        self.assertNotIn("type.weights", ids)  # wght 100-900 spans 400-700
        self.assertIn("type.web-weight", ids)

    def test_confusables_consistent_with_threshold(self):
        f = TTFont(OUTFIT)
        iou = fa.confusable_iou(f, {"wght": 400})
        self.assertEqual(set(iou), {"I/l", "I/1", "l/1"})
        self.assertTrue(all(0 <= v <= 1 for v in iou.values()))
        flagged = "type.confusables" in by_id(fa.audit(OUTFIT, ["en"]))
        self.assertEqual(flagged, max(iou.values()) > fa.IOU_WARN)

    def test_confusables_severity_by_role(self):
        with mock.patch.object(fa, "IOU_WARN", 0.1):  # force the finding whatever the measured value
            self.assertEqual(by_id(fa.audit(OUTFIT, ["en"], role="text"))["type.confusables"]["severity"], "warn")
            self.assertEqual(by_id(fa.audit(OUTFIT, ["en"], role="display"))["type.confusables"]["severity"],
                             "info")

    def test_extended_coverage_is_info(self):
        for f in fa.audit(OUTFIT, ["en", "tr"]):
            if f["id"].endswith(".extended"):
                self.assertEqual(f["severity"], "info")

    def test_optional_capital_sharp_s_is_a_warning(self):
        cov = fa.coverage(TTFont(ARVO).getBestCmap(), "de_Latn")
        self.assertEqual(cov["base"], [])
        self.assertEqual(cov["optional"], ["\u1E9E"])
        ids = by_id(fa.audit(ARVO, ["de"]))
        self.assertNotIn("type.coverage.de_Latn", ids)
        self.assertEqual(ids["type.coverage.de_Latn.optional"]["severity"], "warn")

    def test_identical_glyphs_iou_is_one(self):
        f = TTFont(OUTFIT)
        self.assertEqual(fa.confusable_iou(f, {"wght": 400}, pairs=(("l", "l"),))["l/l"], 1.0)

    def test_low_contrast_sans(self):
        self.assertGreater(fa.stroke_contrast(TTFont(OUTFIT), {"wght": 400}), 0.8)

    def test_coverage_helper(self):
        cmap = TTFont(ARVO).getBestCmap()
        cov = fa.coverage(cmap, "tr_Latn")
        self.assertEqual(sorted(cov["base"]), sorted("Ğ İ Ş ğ ş".split()))
        self.assertEqual(fa._tokens("a {ch} ◌̂ é"), ["a", "ch", "é"])


class TestWoff2Timeout(unittest.TestCase):
    def test_timeout_any_thread(self):
        import threading
        import time

        def slow(*a):
            time.sleep(2)
            return 1
        out = {}

        def run():
            try:
                fa.woff2_kb(OUTFIT, fa.LATIN, timeout=0.2)
            except TimeoutError as e:
                out["e"] = e
        with mock.patch.object(fa, "_woff2_kb", side_effect=slow):
            t0 = time.time()
            with self.assertRaises(TimeoutError):
                fa.woff2_kb(OUTFIT, fa.LATIN, timeout=0.2)
            th = threading.Thread(target=run)  # not the main thread: a signal timer would not work here
            th.start()
            th.join()
            self.assertIn("e", out)
            self.assertLess(time.time() - t0, 1.5)

    def test_errors_propagate_and_result_returns(self):
        self.assertGreater(fa.woff2_kb(OUTFIT, fa.LATIN, timeout=60), 5)
        with mock.patch.object(fa, "_woff2_kb", side_effect=ValueError("bad")):
            with self.assertRaises(ValueError):
                fa.woff2_kb(OUTFIT, fa.LATIN)

    def test_audit_reports_skipped_timeout(self):
        with mock.patch.object(fa, "woff2_kb", side_effect=TimeoutError("took longer than 20 s")):
            f = by_id(fa.audit(OUTFIT, ["en"]))["type.web-weight"]
        self.assertEqual(f["severity"], "info")
        self.assertIn("skipped: timeout", f["message"])


class TestCatalogueHelpers(unittest.TestCase):
    CAT = {"lang_index": ["en_Latn", "ja_Jpan", "tr_Latn"], "families": [
        {"family": "Alpha", "source": "google", "category": "sans-serif", "popularity": 3,
         "coverage": {"langs": "oA=="}, "features": {"tnum": True}},                       # bits 101: en, tr
        {"family": "Beta", "source": "google", "category": "serif", "popularity": 1,
         "coverage": {"langs": "gA=="}, "features": {"tnum": False}, "ai_default": True},   # bits 100: en only
        {"family": "Gamma JP", "source": "google", "category": "sans-serif", "popularity": 2, "measured": False,
         "subsets": ["japanese", "latin", "latin-ext"]},
        {"family": "Delta", "source": "fontshare", "license": "ITF-FFL-2.0", "render": False,
         "category": "sans-serif", "script": "latin"},
    ]}

    def test_families_covering(self):
        self.assertEqual(fa.families_covering("tr_Latn", self.CAT), ["Gamma JP", "Alpha"])
        self.assertEqual(fa.families_covering("ja_Jpan", self.CAT), ["Gamma JP"])
        self.assertIsNone(fa.families_covering("de_Latn", self.CAT))

    def test_search_filters(self):
        rows, _ = fa.search(langs=["tr"], catalog=self.CAT)
        self.assertEqual([r["family"] for r in rows], ["Gamma JP", "Alpha", "Delta"])
        rows, _ = fa.search(langs=["ja"], catalog=self.CAT)
        self.assertEqual([r["family"] for r in rows], ["Gamma JP"])
        rows, _ = fa.search(klass="serif", langs=["en"], catalog=self.CAT)
        self.assertEqual([r["family"] for r in rows], ["Beta"])
        rows, _ = fa.search(numbers=True, langs=["en"], catalog=self.CAT)
        self.assertEqual([r["family"] for r in rows], ["Alpha"])
        rows, _ = fa.search(exclude_defaults=True, langs=["en"], catalog=self.CAT)
        self.assertNotIn("Beta", [r["family"] for r in rows])
        rows, notes = fa.search(langs=["de"], catalog=self.CAT)
        self.assertEqual(len(rows), 4)
        self.assertTrue(any("not indexed" in n for n in notes))


class TestSearchRankingAndClasses(unittest.TestCase):
    CAT = {"lang_index": ["en_Latn"], "families": [
        {"family": n, "source": "google", "category": c, "popularity": p, "coverage": {"langs": "gA=="},
         "metrics": m, "features": {}, **({"stroke": st} if st else {})}
        for n, c, p, m, st in [
            ("Archivo", "sans-serif", 10, {"o_aspect": 0.89, "O_aspect": 0.96, "contrast": 0.81}, "sans"),
            ("Archivo Black", "sans-serif", 20, {"o_aspect": 0.9, "O_aspect": 0.9, "contrast": 0.8}, "sans"),
            ("Chivo Mono", "monospace", 40, {"o_aspect": 0.9, "O_aspect": 0.9}, None),
            ("Chivo", "sans-serif", 50, {"o_aspect": 0.89, "O_aspect": 0.81, "contrast": 0.68}, "sans"),
            ("Jost", "sans-serif", 30, {"o_aspect": 0.99, "O_aspect": 0.99, "contrast": 0.93}, "sans"),
            ("Bodoni Moda", "serif", 60, {"o_aspect": 0.95, "O_aspect": 0.83, "contrast": 0.15}, "serif"),
            ("Lora", "serif", 5, {"o_aspect": 0.95, "O_aspect": 0.96, "contrast": 0.37}, "serif"),
        ]]}

    def names(self, **kw):
        return [r["family"] for r in fa.search(catalog=self.CAT, langs=["en"], **kw)[0]]

    def test_exact_then_word_start_then_substring(self):
        self.assertEqual(self.names(query="Chivo"), ["Chivo", "Chivo Mono", "Archivo", "Archivo Black"])
        self.assertEqual(self.names(query="black")[0], "Archivo Black")

    def test_class_aliases(self):
        self.assertEqual(fa.class_def("grotesk")[0], "grotesque")
        self.assertEqual(fa.class_def("Neo Grotesk")[0], "neo-grotesque")
        self.assertEqual(fa.class_def("garalde")[0], "old-style")
        self.assertEqual(fa.class_def("script")[0], "handwriting")
        with self.assertRaisesRegex(ValueError, "geometric.*didone"):
            fa.class_def("blobby")

    def test_measured_proxy_when_tags_unavailable(self):
        with mock.patch.object(fa, "_google_tags", side_effect=typelib.FontError("offline")):
            self.assertEqual(self.names(klass="geometric"), ["Jost"])
            self.assertEqual(self.names(klass="grotesk"), ["Archivo", "Archivo Black", "Chivo"])
            self.assertEqual(self.names(klass="didone"), ["Bodoni Moda"])
            rows, notes = fa.search(klass="grotesk", catalog=self.CAT, langs=["en"])
            self.assertTrue(any("proxy" in n for n in notes), notes)
            with self.assertRaises(typelib.FontError):
                fa.search(klass="rounded", catalog=self.CAT, langs=["en"])

    def test_google_class_tags_when_available(self):
        tags = {"Chivo": {"/Sans/Grotesque": 100}, "Archivo": {"/Sans/Grotesque": 30},
                "Jost": {"/Sans/Geometric": 90}}
        with mock.patch.object(fa, "_google_tags", return_value=tags):
            self.assertEqual(self.names(klass="grotesk"), ["Chivo"])
            self.assertEqual(self.names(klass="sans", query="jost"), ["Jost"])

    def test_suggestions_same_category_without_defaults(self):
        cat = {"lang_index": ["tr_Latn"], "families": [
            {"family": "Roboto", "source": "google", "category": "sans-serif", "popularity": 1,
             "coverage": {"langs": "gA=="}, "ai_default": True},
            {"family": "Merriweather", "source": "google", "category": "serif", "popularity": 3,
             "coverage": {"langs": "gA=="}, "ai_default": False},
            {"family": "Playfair Display", "source": "google", "category": "serif", "popularity": 2,
             "coverage": {"langs": "gA=="}, "ai_default": True},
            {"family": "Arvo", "source": "google", "category": "serif", "popularity": 4,
             "coverage": {"langs": "gA=="}, "ai_default": False}]}
        self.assertEqual(fa.families_covering("tr_Latn", cat, category="serif", exclude=["Arvo"],
                                              skip_defaults=True), ["Merriweather"])


class TestCLI(TempCache):
    def run_cli(self, *argv):
        env = dict(os.environ, XDG_CACHE_HOME=self.tmp, PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, BRAND, "fonts", *argv], capture_output=True, text=True, env=env,
                           stdin=subprocess.DEVNULL, timeout=120)
        return r.returncode, r.stdout, r.stderr

    def test_audit_gate_exit_and_budget(self):
        code, out, err = self.run_cli("audit", ARVO, "--langs", "tr")
        self.assertEqual(code, 1, err)
        self.assertLessEqual(len(out.encode("utf-8")), 1200)
        self.assertIn("GATE  type.coverage.tr_Latn", out)
        self.assertIn("figures proportional only", out)
        self.assertIn("result: FAIL", out)
        code, out, _ = self.run_cli("audit", OUTFIT, "--langs", "en", "--numbers")
        self.assertIn("figures tabular via tnum feature", out)

    def test_audit_json(self):
        code, out, _ = self.run_cli("audit", OUTFIT, "--langs", "en,tr", "--numbers", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertTrue(data["passed"])
        self.assertEqual(data["schema"], identitylib.AUDIT_SCHEMA)
        self.assertEqual(data["counts"]["gate"], 0)

    def test_audit_needs_a_target(self):
        code, _, err = self.run_cli("audit")
        self.assertEqual(code, 2)
        self.assertIn("FAMILY", err)

    def test_class_list_and_unknown_class(self):
        code, out, _ = self.run_cli("search", "--class", "list")
        self.assertEqual(code, 0)
        self.assertIn("grotesque (grotesk", out)
        self.assertLessEqual(len(out.encode("utf-8")), 1200)
        code, _, err = self.run_cli("search", "--class", "blobby")
        self.assertEqual(code, 2)
        self.assertIn("valid classes", err)

    @unittest.skipUnless(os.path.isfile(typelib.CATALOG), "catalogue not built")
    def test_coverage_suggestion_avoids_ai_defaults(self):
        f = by_id(fa.audit(ARVO, ["tr"]))["type.coverage.tr_Latn"]
        names = f["suggested_fix"].split("e.g. ")[1].split(", ")
        self.assertEqual(len(names), 3)
        for n in names:
            rec = typelib.catalog_entry(n)
            self.assertEqual(rec["category"], "serif", n)
            self.assertFalse(rec["ai_default"], n)
            self.assertNotIn(typelib._norm(n), fa._ai_default_fonts())

    @unittest.skipUnless(os.path.isfile(typelib.CATALOG), "catalogue not built")
    def test_search_exact_name_first(self):
        code, out, _ = self.run_cli("search", "Chivo")
        self.assertEqual(code, 0)
        self.assertTrue(out.splitlines()[1].startswith("Chivo · "), out)

    @unittest.skipUnless(os.path.isfile(typelib.CATALOG), "catalogue not built")
    def test_search_budget(self):
        code, out, _ = self.run_cli("search", "--class", "serif", "--langs", "en,tr", "--numbers")
        self.assertEqual(code, 0)
        self.assertLessEqual(len(out.encode("utf-8")), 1200)
        rows = out.strip().splitlines()[1:]
        self.assertTrue(0 < len(rows) <= 13)
        self.assertTrue(all("tnum" in r for r in rows if " · " in r))


@unittest.skipUnless(NETWORK, "network test (BI_NETWORK_TESTS=1)")
class TestNetwork(TempCache):
    def test_fraunces_en_tr(self):
        fs = fa.audit("Fraunces", ["en", "tr"])
        self.assertEqual([f["id"] for f in fs if f["severity"] == "gate"], [])
        self.assertNotIn("type.license", by_id(fs))

    def test_google_licence_mismatch(self):
        f = by_id(fa.audit("Fraunces", ["en"], license="Apache-2.0"))["type.license-mismatch"]
        self.assertEqual(f["measured"], "OFL-1.1")
        self.assertIn("Google Fonts catalogue", f["message"])

    def test_fraunces_numbers_gate(self):
        self.assertEqual(by_id(fa.audit("Fraunces", ["en"], numbers=True))["type.tnum"]["severity"], "gate")

    def test_unknown_family(self):
        with self.assertRaises(typelib.FontError):
            fa.audit("Definitely Not A Font Family", ["en"])

    @unittest.skipUnless(os.path.isfile(typelib.CATALOG), "catalogue not built")
    def test_mood_uses_runtime_tags_in_cache(self):
        rows, notes = fa.search(mood="calm", langs=["en"])
        self.assertTrue(rows)
        self.assertTrue((typelib.cache_root() / "gf-tags" / "families.csv").is_file())
        self.assertTrue(any("limited evidence" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
