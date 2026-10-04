"""render_png.render(): one CDP session with probe, per-page screenshots and PDF font checks (docs/architecture.md section 4.7).

Pure-Python parts (PDF parsing, probe -> findings) always run; browser parts are skipped without Chromium.
Fixture fonts: tests/fixtures/fonts/ (Outfit[wght] is pinned to static instances first).
"""
import inspect
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
FONTS = os.path.join(ROOT, "tests", "fixtures", "fonts")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import presentlib as pl  # noqa: E402
import render_png as rp  # noqa: E402

BROWSER = rp.find_browser()


def _has_fonttools():
    try:
        import fontTools  # noqa: F401
        return True
    except ImportError:
        return False


class TestSignatures(unittest.TestCase):
    def test_screenshot_html_unchanged(self):
        params = list(inspect.signature(rp.screenshot_html).parameters)
        self.assertEqual(params, ["html_path", "png_path", "width", "height", "scale", "full_page", "browser",
                                  "timeout", "mobile", "color_scheme"])

    def test_render_signature(self):
        params = list(inspect.signature(rp.render).parameters)
        self.assertEqual(params[:7], ["html", "out", "width", "height", "pdf", "pages", "probe"])


class TestPdfReport(unittest.TestCase):
    PDF = (b"%PDF-1.4\n1 0 obj\n<</Type /Font /Subtype /TrueType /BaseFont /ABCDEF+Outfit-Bold>>\nendobj\n"
           b"2 0 obj\n<</Type /FontDescriptor /FontName /ABCDEF+Outfit-Bold /Flags 4 /FontFile2 9 0 R>>\nendobj\n"
           b"3 0 obj\n<</Type /FontDescriptor /FontName /QWERTY+Arvo /Flags 4>>\nendobj\n")

    def test_embedded_fonts(self):
        r = rp.pdf_font_report(self.PDF)
        names = {f["name"]: f["embedded"] for f in r["fonts"]}
        self.assertEqual(names["Outfit-Bold"], "FontFile2")
        self.assertIsNone(names["Arvo"])
        self.assertEqual(r["type3"], 0)
        self.assertIn("Outfit-Bold", r["base_fonts"])

    def test_findings(self):
        r = rp.pdf_font_report(self.PDF)
        ids = [f["id"] for f in rp.pdf_font_findings(r, {"Outfit"})]
        self.assertEqual(ids, [])
        f = rp.pdf_font_findings(r, {"Outfit", "Arvo"})
        self.assertEqual([x["id"] for x in f], ["pdf-font-not-embedded"])
        self.assertEqual(f[0]["severity"], "gate")

    def test_type3_is_a_gate(self):
        r = rp.pdf_font_report(self.PDF + b"4 0 obj\n<</Type /Font /Subtype /Type3>>\nendobj\n")
        self.assertEqual(r["type3"], 1)
        self.assertIn("pdf-type3-font", [x["id"] for x in rp.pdf_font_findings(r, {"Outfit"})])


class TestProbeFindings(unittest.TestCase):
    BASE = {"fonts": [{"family": "Outfit", "weight": "700", "status": "loaded"},
                      {"family": "Outfit", "weight": "400", "status": "loaded"},
                      {"family": "Arvo", "weight": "400", "status": "error"}],
            "roles": [{"selector": "h1", "role": "display", "family": "Outfit", "weight": "700"},
                      {"selector": "p", "role": "text", "family": "Outfit", "weight": "400"}],
            "overflow": [], "text_contrast": [{"selector": "p", "text": "x", "ratio": 7.0, "required": 4.5, "ok": True}],
            "logo_px_detail": [{"selector": "div", "name": "hero", "px": 80, "min": 24}]}

    def test_clean(self):
        f = rp.probe_findings(self.BASE, [("Outfit", 700), ("Outfit", 400)], {"display": "Outfit", "text": "Outfit"})
        self.assertEqual(f, [])

    def test_each_gate(self):
        bad = dict(self.BASE, overflow=["div.x"],
                   text_contrast=[{"selector": "p", "text": "x", "ratio": 2.1, "required": 4.5, "ok": False}],
                   logo_px_detail=[{"selector": "div", "name": "hero", "px": 18, "min": 24}],
                   roles=[{"selector": "h1", "role": "display", "family": "Georgia", "weight": "700"}])
        f = rp.probe_findings(bad, [("Arvo", 400)], {"display": "Outfit"})
        ids = sorted(x["id"] for x in f)
        self.assertEqual(ids, ["font-fallback", "font-not-loaded", "logo-too-small", "text-contrast", "text-overflow"])
        self.assertTrue(all(x["severity"] == "gate" for x in f))

    def test_logo_main_part_gates_small_parts_warn(self):
        res = {"logo_ground": [{"selector": "x", "ground": "#ffffff", "min_ratio": 1.4, "main_ratio": 9.1,
                                "colours": ["#111111", "#ffd84a"], "one_colour": False, "raster": False}]}
        f = rp.probe_findings(res)
        self.assertEqual([(x["id"], x["severity"]) for x in f], [("logo-part-on-ground", "warn")])
        res["logo_ground"][0]["main_ratio"] = 1.4
        self.assertEqual([x["id"] for x in rp.probe_findings(res)], ["logo-on-ground"])

    def test_weight_range_counts(self):
        res = {"fonts": [{"family": "Outfit", "weight": "100 900", "status": "loaded"}], "roles": []}
        self.assertEqual(rp.probe_findings(res, [("Outfit", 600)]), [])


@unittest.skipUnless(BROWSER and _has_fonttools(), "needs a Chromium-based browser and fontTools")
class TestRenderBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bi-probe-")
        cache = os.path.join(cls.tmp, "fonts")
        cls.bold = pl.static_instance(os.path.join(FONTS, "Outfit[wght].ttf"), {"wght": 700}, cache)
        cls.reg = pl.static_instance(os.path.join(FONTS, "Outfit[wght].ttf"), {"wght": 400}, cache)
        faces = [{"family": "Outfit", "weight": 700, "path": cls.bold}, {"family": "Outfit", "weight": 400, "path": cls.reg}]
        css = pl.font_face_css(faces, cls.tmp)
        body = """
<style>%s
@page{size:800px 500px;margin:0}
body{margin:0;font-family:Outfit,sans-serif;background:#fff}
.page{width:800px;height:500px;position:relative;break-after:page;background:#fafafa}
h1{font-weight:700;font-size:48px;margin:0}
.clip{width:120px;height:20px;overflow:hidden;white-space:nowrap;font-size:16px}
.low{color:#bbbbbb;font-size:14px}
.logo{height:40px;width:80px;background:#222}
.fb{font-family:"NoSuchFamily",serif}
</style>
<section class="page" data-name="one"><h1 data-role="display">Probe</h1><p data-role="text">Body text</p>
<div class="clip">This text is far too long for its clipped box</div><p class="low">faint text</p>
<div class="logo" data-logo="hero" data-min="24"></div><p class="fb" data-role="display">fallback</p></section>
<div class="tall" style="height:40px;overflow:hidden;font-size:50px;line-height:.8;font-family:Outfit;width:700px">
<span id="tallok">Tight line height</span></div>
<div style="height:30px;overflow:hidden;font-size:20px;line-height:30px;width:300px"><span id="cut">first line of
text and a second line that the box clips away completely</span></div>
<nav style="width:200px;overflow:hidden;display:flex;gap:10px;font-size:16px"><a>Menu</a>
<a id="btn" style="white-space:nowrap">A very long call to action button</a></nav>
</section>
<div data-mock style="width:120px;overflow:hidden"><span id="mockcut" style="white-space:nowrap">a mock line far too long</span></div>
<div style="width:150px;overflow:hidden;display:flex"><a id="boxcut" data-probe-box style="flex:none;padding:0 90px">Go</a><a style="flex:none;padding:0 40px">x</a></div>
<div style="background:#888888;width:60px;height:30px"><div id="lowlogo" data-logo-ground style="height:20px">
<svg viewBox="0 0 10 10" style="height:100%%"><path d="M0 0h10v10z" fill="#7a7a7a"/></svg></div></div>
<div style="background:#ffffff;width:60px;height:30px"><div id="twofill" data-logo-ground data-one-colour style="height:20px">
<svg viewBox="0 0 10 10" style="height:100%%"><path d="M0 0h5v10z" fill="#000000"/><path d="M5 0h5v10z" fill="#0044aa"/></svg></div></div>
<style>.force-mono{display:contents}.force-mono svg *{fill:var(--mono)!important}</style>
<div style="background:#111111;width:60px;height:30px"><div id="forced" data-logo-ground data-one-colour style="height:20px">
<span class="force-mono" style="--mono:#ffffff"><svg viewBox="0 0 10 10" style="height:100%%"><path d="M0 0h5v10z" fill="#000000"/>
<path d="M5 0h5v10z" fill="#0044aa"/></svg></span></div></div>
<section class="page" data-name="two"><p data-role="text">Second page</p></section>""" % css
        cls.html = os.path.join(cls.tmp, "t.html")
        with open(cls.html, "w", encoding="utf-8") as fh:
            fh.write("<!doctype html><html><head><meta charset=utf-8></head><body>" + body + "</body></html>")
        cls.res = rp.render(cls.html, os.path.join(cls.tmp, "pages"), 800, 500, pdf=True, pages=True, scale=1)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_pages_and_pdf(self):
        self.assertEqual(len(self.res["pages"]), 2)
        self.assertTrue(self.res["pages"][0].endswith("01-one.png"))
        self.assertEqual(rp.png_size(self.res["pages"][1]), (800, 500))
        self.assertTrue(os.path.getsize(self.res["pdf"]) > 1000)
        rep = self.res["pdf_fonts"]
        self.assertEqual(rep["type3"], 0)
        self.assertEqual(rp.pdf_font_findings(rep, {"Outfit"}), [])

    def test_fonts_loaded_and_roles(self):
        st = {(f["family"], f["weight"]): f["status"] for f in self.res["fonts"]}
        self.assertEqual(st[("Outfit", "700")], "loaded")
        self.assertEqual(st[("Outfit", "400")], "loaded")
        fams = {r["family"] for r in self.res["roles"]}
        self.assertIn("NoSuchFamily", fams)  # the fallback element is reported, not hidden

    def test_gates_found(self):
        f = rp.probe_findings(self.res, [("Outfit", 700), ("Outfit", 400)], {"display": "Outfit", "text": "Outfit"})
        ids = {x["id"] for x in f}
        self.assertIn("font-fallback", ids)
        self.assertIn("text-overflow", ids)
        self.assertIn("text-contrast", ids)
        self.assertNotIn("font-not-loaded", ids)
        self.assertNotIn("logo-too-small", ids)
        self.assertEqual(self.res["logo_px"]["hero"], 40)
        self.assertTrue(any("div.clip" in s for s in self.res["overflow"]))

    def test_overflow_is_glyphs_vs_clip_boxes(self):
        ov = " ".join(self.res["overflow"])
        self.assertNotIn("#tallok", ov)  # tall ascent at line-height < 1 is not a defect
        self.assertIn("#cut", ov)        # a clipped line is
        self.assertIn("#btn", ov)        # a nowrap button pushed out of its clipping row is

    def test_mock_overflow_is_a_warn_and_boxes_are_checked(self):
        self.assertTrue(any("#mockcut" in x for x in self.res["overflow_mock"]))
        self.assertFalse(any("#mockcut" in x for x in self.res["overflow"]))
        self.assertTrue(any("#boxcut" in x for x in self.res["overflow"]))  # a clipped button, text still visible
        f = rp.probe_findings(self.res)
        self.assertEqual([x["severity"] for x in f if x["id"] == "mock-overflow"], ["warn"])

    def test_logo_on_ground(self):
        by = {x["selector"].split(" > ")[-1]: x for x in self.res["logo_ground"]}
        self.assertLess(by["#lowlogo"]["min_ratio"], 3)
        self.assertEqual(len(by["#twofill"]["colours"]), 2)
        self.assertEqual(by["#forced"]["colours"], ["#ffffff"])  # a forced one colour really is one colour
        ids = [f["id"] for f in rp.probe_findings(self.res)]
        self.assertIn("logo-on-ground", ids)
        self.assertIn("logo-not-one-colour", ids)

    def test_single_png(self):
        out = os.path.join(self.tmp, "one.png")
        r = rp.render(self.html, out, 800, 500, probe=False, scale=1)
        self.assertEqual(r["png"], out)
        self.assertEqual(rp.png_size(out), (800, 500))
        self.assertNotIn("fonts", r)


if __name__ == "__main__":
    unittest.main()
