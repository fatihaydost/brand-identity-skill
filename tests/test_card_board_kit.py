"""Presentation layer: identity card, comparison board + review image + table, guideline kit (docs/architecture.md sections 4.7, 7).

Runs on a copy of templates/demo (fictional brand "Ferrow", 3 sets). Fonts come from tests/fixtures/fonts:
typelib is switched off so nothing is downloaded. Browser tests are skipped without Chromium.
"""
import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "brand-identity")
SCRIPTS = os.path.join(SKILL, "scripts")
DEMO = os.path.join(SKILL, "templates", "demo")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import identity_board as ib  # noqa: E402
import identity_card as ic  # noqa: E402
import identitylib  # noqa: E402
import kit_build as kb  # noqa: E402
import presentlib as pl  # noqa: E402
import render_png as rp  # noqa: E402

BROWSER = rp.find_browser()
HAS_FT = importlib.util.find_spec("fontTools") is not None
_real_find_spec = importlib.util.find_spec


def _offline():
    """Fixture fonts only (no typelib downloads) and the kit's logo step as if logolib were absent."""
    pl._typelib = lambda: None

    def find_spec(name, *a, **k):
        return None if name == "logolib" else _real_find_spec(name, *a, **k)
    kb.importlib.util.find_spec = find_spec


def _restore():
    kb.importlib.util.find_spec = _real_find_spec


def _copy_demo(tmp):
    work = os.path.join(tmp, "ferrow")
    shutil.copytree(DEMO, work)
    return work


def _edit(path, fn):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    fn(d)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1)


class TestDemoData(unittest.TestCase):
    def test_demo_sets_valid(self):
        dirs = identitylib.set_dirs(DEMO)
        self.assertEqual([os.path.basename(d) for d in dirs], ["A", "B", "C"])
        for d in dirs:
            ident = identitylib.load_identity(os.path.join(d, "identity.json"))
            with open(os.path.join(d, "palette.json"), encoding="utf-8") as fh:
                pal = json.load(fh)
            self.assertIn("modes", pal)
            self.assertIn("scales", pal)
            for svg in pl.logo_svgs(pl.load_set(d)).values():
                self.assertNotIn("<text", svg)
            self.assertEqual(ident["brand"]["name"], "Ferrow")

    def test_table_columns(self):
        infos = [pl.load_set(d) for d in identitylib.set_dirs(DEMO)]
        md = ib.table_md(infos, {i["dir"]: [] for i in infos})
        head = md.splitlines()[0]
        self.assertEqual(head, "| Set | Mechanism | Logo | Type | Palette | Where it differs | Risks / flags |")
        self.assertEqual(len(md.strip().splitlines()), 5)
        # keep/none components get no column; a single-component request keeps set · mechanism · it · differs · risks
        for i in infos:
            i["identity"] = copy.deepcopy(i["identity"])
            i["identity"]["components"]["logo"]["mode"] = "keep"
            i["identity"]["components"]["palette"]["mode"] = "none"
        head = ib.table_md(infos, {i["dir"]: [] for i in infos}).splitlines()[0]
        self.assertEqual(head, "| Set | Mechanism | Type | Where it differs | Risks / flags |")

    def test_test_row_ladder(self):
        self.assertEqual(ic.test_sizes(1.0), (64, 32, 16))
        wide = ic.test_sizes(9.0)  # "Parallax Labs"-wide wordmark
        self.assertLess(wide[0], 64)
        self.assertLessEqual(9.0 * sum(wide), ic.TEST_ROW_PX)
        self.assertEqual(wide[-1], 16)
        self.assertLessEqual(40.0 * sum(ic.test_sizes(40.0)), ic.TEST_ROW_PX + 1e-6)

    def test_brand_hex_in_band(self):
        info = pl.load_set(os.path.join(DEMO, "sets", "A"))
        html = ic.palette_block(info, pl.theme(info["palette"]))
        self.assertIn("Signal Orange", html)
        self.assertIn("#e4572e", html)       # the locked brand colour
        self.assertIn("UI #bf4019", html)    # the adjusted role step, as a note

    def test_dark_card_strip_shows_dark_roles_and_every_brand_colour(self):
        # Tessellate C: card.ground dark showed the light Ground (#ffffff) and left out the night slate brand colour
        info = copy.deepcopy(pl.load_set(os.path.join(DEMO, "sets", "A")))
        pal = info["palette"]
        dk = pal["modes"]["dark"]
        pal["brand"].append({"id": "brand-9", "name": "Night Slate", "hex": "#10161d", "source": "chosen",
                             "locked": True})
        t = pl.theme(pal)
        dark = ic.palette_block(info, t, gt=pl.ground_theme(pal, t, "dark"))
        self.assertIn(dk["background"], dark)
        self.assertNotIn("background:" + pal["modes"]["light"]["background"], dark)
        self.assertIn("Night Slate", dark)
        self.assertIn("#10161d", dark)
        light = ic.palette_block(info, t)
        self.assertIn("background:" + pal["modes"]["light"]["background"], light)
        self.assertIn("Night Slate", light)     # a brand colour no role shows still gets its band

    def test_dark_strip_does_not_repeat_the_accent(self):
        # Fernhill C: the accent band shows its dark UI step; the accent's own hex got a second "Accent" band
        info = copy.deepcopy(pl.load_set(os.path.join(DEMO, "sets", "A")))
        pal = info["palette"]
        acc = pal["brand"][1]
        pal["modes"]["dark"]["accent"] = "#97a758"   # a dark step far from the brand accent's hex
        acc["locked"], acc["source"] = False, "derived"
        t = pl.theme(pal)
        dark = ic.palette_block(info, t, gt=pl.ground_theme(pal, t, "dark"))
        self.assertEqual(dark.count('class="bn"'), len({s for s in dark.split('class="bn">')[1:]}))
        self.assertNotIn("background:" + acc["hex"].lower() + ";", dark.lower())

    def test_findings_dedupe_and_fresh_card(self):
        g = identitylib.finding("text-overflow", "card", "gate", "1 text element(s) overflow: x")
        t = identitylib.finding("coverage", "type", "gate", "misses ğ")
        ident = {"audit": {"findings": [g, t, dict(t)]}}
        self.assertEqual(len(pl.merged_findings(ident)), 2)
        self.assertEqual([f["id"] for f in pl.merged_findings(ident, [])], ["coverage"])  # fresh probe wins
        self.assertEqual(len(pl.merged_findings(ident, [dict(g)])), 2)
        html = ic.checks_html(dict(ident, components={}), [], None)
        self.assertIn("Type · 1 gate", html)

    def test_copy_header_lang(self):
        info = copy.deepcopy(pl.load_set(os.path.join(DEMO, "sets", "B")))
        logos = pl.logo_svgs(info)
        html = ic.app_block(info, logos)
        self.assertIn("<a>Menu</a>", html)
        self.assertIn(">Contact</a>", html)
        self.assertNotIn("We answer every message", html)
        info["identity"]["brand"]["copy"] = {"sentence": {"en": "Bikes fixed while you wait."},
                                             "nav": ["Repairs", "Rentals"], "cta": "Book a repair"}
        info["identity"]["brand"]["languages"] = ["tr", "en"]
        info["identity"]["logo"]["min_size"]["px"] = 36
        html = ic.app_block(info, logos)
        self.assertIn("<a>Repairs</a><a>Rentals</a>", html)
        self.assertIn("Book a repair", html)
        self.assertIn('lang="tr"', html)
        self.assertIn('style="height:36px"', html)  # header logo never below logo.min_size.px
        mark, h = ic.header_logo(info["identity"], logos)
        self.assertEqual(mark, logos["wordmark"])  # wordmark-only identity: no symbol in the header
        page = ic.build_html(info, [], DEMO)  # no doc_lang: the tool text follows languages[0] (tr), root lang too
        self.assertIn('<html lang="tr">', page)
        self.assertIn("Kimlik yönleri", page)
        info["identity"]["brand"]["doc_lang"] = "en"  # English labels on a Turkish brand: never Turkish casing
        page = ic.build_html(info, [], DEMO)
        self.assertIn('<html lang="en">', page)
        self.assertIn("Identity directions", page)
        self.assertIn('class="m-brand" data-role="display" lang="tr"', page)
        self.assertIn("--font-ui:", page)

    def test_board_type_mono_and_justified_defaults(self):
        info = copy.deepcopy(pl.load_set(os.path.join(DEMO, "sets", "A")))
        info["identity"]["type"]["mono"] = {"family": "Azeret Mono", "weights": [400, 500]}
        self.assertIn("mono Azeret Mono 400/500", ib.cell_component(info, "type"))
        info["identity"]["defaults_used"] = [{"id": "outfit-display", "why": "the user asked for Outfit"},
                                             {"id": "not-matched", "why": "declared but never flagged"}]
        info["identity"]["audit"] = {"findings": [], "defaults": {"matched": ["outfit-display", "teal-primary"]}}
        txt = " ".join(t for _k, t in ib.risks(info, []))
        self.assertIn("AI-default (justified): outfit-display — the user asked for Outfit", txt)
        self.assertIn("AI-default without a reason: teal-primary", txt)
        self.assertNotIn("not-matched", txt)
        self.assertNotIn("none found", txt)
        info["identity"]["audit"] = {"findings": []}  # nothing matched: declarations are not shown
        self.assertNotIn("AI-default", " ".join(t for _k, t in ib.risks(info, [])))

    def test_kept_component_notes_once(self):
        infos = [copy.deepcopy(pl.load_set(d)) for d in identitylib.set_dirs(DEMO)]
        note = identitylib.finding("pal-link", "palette", "warn", "link colour is close to text")
        for i in infos:
            i["identity"]["components"]["palette"]["mode"] = "keep"
            i["identity"]["audit"] = {"findings": [dict(note)]}
        self.assertEqual(pl.shown_findings(infos[0]["identity"]), [])
        self.assertNotIn("link colour", " ".join(t for _k, t in ib.risks(infos[0], [])))
        self.assertEqual(len(pl.kept_notes([i["identity"] for i in infos])["palette"]), 1)
        self.assertEqual(ib.board_warnings(infos).count("Kept palette: 1 note"), 1)
        self.assertIn("Kept palette: 1 note(s)", ib.table_md(infos, {i["dir"]: [] for i in infos}))

    def test_number_format_and_tracking_scope(self):
        self.assertEqual(pl.format_number(3815.07, "en"), "3,815.07")
        self.assertEqual(pl.format_number(3815.07, "tr"), "3.815,07")
        self.assertEqual(pl.format_number(3815.07, "fr"), "3\u202f815,07")
        self.assertEqual(pl.format_number(12.4, "pt-BR"), "12,40")
        with open(os.path.join(SKILL, "templates", "card", "card.css"), encoding="utf-8") as fh:
            css = fh.read()
        tracked = [ln for ln in css.splitlines() if "var(--display-track)" in ln]
        self.assertEqual(len(tracked), 1)
        self.assertTrue(tracked[0].startswith(".spec .disp"))

    def test_logolib_version_crop(self):
        svg = ('<svg viewBox="-296 -114 937 328" data-ink="-196 -14 541 114"><rect id="ground" x="0" y="0" '
               'width="9" height="9" fill="#fff"/><path d="M0 0"/></svg>')
        out = pl._crop_clear_space(svg, {}, False)
        self.assertNotIn('id="ground"', out)
        vb = pl.viewbox(out)  # data-ink is x0 y0 x1 y1: the crop is 737 x 128 plus a 2% pad
        self.assertAlmostEqual(vb[2], 737 * 1.04, delta=1)
        self.assertAlmostEqual(vb[3], 128 + 0.04 * 737, delta=1)

    def test_brand_id_ground(self):
        info = copy.deepcopy(pl.load_set(os.path.join(DEMO, "sets", "A")))
        pal = info["palette"]
        b1 = pal["brand"][0]["hex"]
        gt = pl.ground_theme(pal, pl.theme(pal), "brand-1")
        self.assertEqual((gt["bg"], gt["ref"]), (b1, "brand-1"))
        logos = pl.logo_svgs(info)
        _html, how = ic.ground_mark(info, logos, gt)
        self.assertEqual(how, "one colour")  # no logolib version: one colour in the ground's ink, never a role tint
        tmp = tempfile.mkdtemp(prefix="bi-ground-")
        try:
            sd = os.path.join(tmp, "sets", "A")
            shutil.copytree(os.path.join(DEMO, "sets", "A"), sd)
            os.makedirs(os.path.join(sd, "logo", "build"), exist_ok=True)
            with open(os.path.join(sd, "logo", "build", "on-light-primary.svg"), "w", encoding="utf-8") as fh:
                fh.write('<svg viewBox="0 0 10 10" data-kind="on-light-primary"><path d="M0 0h9v9z" fill="#fff"/></svg>')
            with open(os.path.join(sd, "logo", "build", "manifest.json"), "w", encoding="utf-8") as fh:
                json.dump({"versions": {"on-light-primary": {"svg": "on-light-primary.svg", "ground": b1,
                                                             "ground_role": "primary"}}}, fh)
            self.assertIn('data-kind="on-light-primary"', pl.logo_version(sd, gt))  # found by ground colour
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_url_falls_back_across_drives(self):
        from unittest import mock
        with mock.patch.object(pl.os.path, "relpath", side_effect=ValueError("path is on mount 'C:', start on 'D:'")):
            url = pl._url(os.path.join(DEMO, "brief.json"), "/somewhere/else")
        self.assertTrue(url.startswith("file://"))
        self.assertTrue(url.endswith("brief.json"))

    def test_ground_theme(self):
        info = pl.load_set(os.path.join(DEMO, "sets", "C"))
        t = pl.theme(info["palette"])
        for g in ("light", "dark", "primary", "accent"):
            gt = pl.ground_theme(info["palette"], t, g)
            cr = __import__("colorlib").contrast_ratio
            self.assertGreaterEqual(cr(gt["ink"], gt["bg"]), 4.5, g)
            self.assertGreaterEqual(cr(gt["btn"], gt["bg"]), 3, g)
            self.assertGreaterEqual(cr(gt["onBtn"], gt["btn"]), 4.5, g)
        self.assertEqual(pl.ground_theme(info["palette"], t, "primary")["bg"],
                         info["palette"]["modes"]["light"]["primary"])
        self.assertEqual(pl.ground_theme(info["palette"], t, "dark")["mode"], "dark")

    def test_theme_paints_from_palette(self):
        b = pl.load_set(os.path.join(DEMO, "sets", "B"))
        t = pl.theme(b["palette"])
        self.assertEqual(t["paper"], b["palette"]["modes"]["light"]["background"])  # kraft ground is the paper
        self.assertEqual(pl.theme(None)["paper"], pl.NEUTRAL_THEME["paper"])


@unittest.skipUnless(BROWSER and HAS_FT, "needs a Chromium-based browser and fontTools")
class TestCardBoard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _offline()
        cls.tmp = tempfile.mkdtemp(prefix="bi-card-")
        cls.work = _copy_demo(cls.tmp)
        cls.board = ib.render(cls.work, scale=1.0, date_str="03 OCT 2026")

    @classmethod
    def tearDownClass(cls):
        _restore()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_cards_pass_gates(self):
        for sid, fs in self.board["card_findings"].items():
            self.assertEqual([f["message"] for f in fs if f["severity"] == "gate"], [], sid)
            png = os.path.join(self.work, "sets", sid, "card.png")
            self.assertEqual(rp.png_size(png), (3200, 2000))

    def test_board_review_table(self):
        self.assertTrue(os.path.isfile(self.board["board"]))
        w, h = rp.png_size(self.board["review"])
        self.assertLessEqual(max(w, h), 2000)
        with open(self.board["table"], encoding="utf-8") as fh:
            self.assertIn("**A · Spoke Line** (recommended)", fh.read())
        with open(self.board["board_html"], encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("Recommended", html)
        self.assertIn("sets/A/card.png", html)

    def test_card_html_uses_set_fonts_and_palette(self):
        with open(os.path.join(self.work, "sets", "B", "card.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('font-family:"Arvo"', html)
        self.assertIn("#f3ecdf", html.lower())
        self.assertNotIn("https://", html)

    def test_states_keep_none_failed(self):
        sd = os.path.join(self.work, "sets", "A")

        def change(d):
            d["components"]["logo"] = {"mode": "keep", "status": "fixed", "source": "sets/A/logo/build/horizontal.svg",
                                       "sha256": None}
            d["components"]["palette"] = {"mode": "none", "status": "not_applicable", "source": None, "sha256": None}
            d["palette"] = None
            d["audit"] = {"schema": "brand-identity/audit@1", "passed": False, "counts": {"gate": 1},
                          "findings": [identitylib.finding("coverage", "type", "gate", "Outfit misses 2 letters")]}
        _edit(os.path.join(sd, "identity.json"), change)
        os.remove(os.path.join(sd, "palette.json"))
        r = ic.render(sd)
        with open(r["html"], encoding="utf-8") as fh:
            html = fh.read()
        self.assertFalse(r["passed"])
        self.assertIn('class="kept"', html)
        self.assertIn("FAILED — Outfit misses 2 letters", html)
        self.assertNotIn('data-block="palette"', html)
        self.assertEqual([f for f in r["findings"] if f["severity"] == "gate"], [])

    def test_dark_ground_and_tile_device(self):
        sd = os.path.join(self.work, "sets", "A")

        def change(d):
            d["card"] = {"ground": "primary"}
            d["logo"]["device"] = "tile"
            d["brand"]["copy"] = {"sentence": "Bikes fixed while you wait, and a loaner if it takes longer than "
                                              "a day, with every part listed on the receipt before we start.",
                                  "nav": ["Repairs", "Rentals", "Workshop hours"], "cta": "Book a repair slot today"}
        _edit(os.path.join(sd, "identity.json"), change)
        r = ic.render(sd)
        with open(r["html"], encoding="utf-8") as fh:
            html = fh.read()
        self.assertEqual([f["message"] for f in r["findings"] if f["severity"] == "gate"], [])
        with open(os.path.join(sd, "palette.json"), encoding="utf-8") as fh:
            prim = json.load(fh)["modes"]["light"]["primary"]
        self.assertIn(f"--g-bg:{prim}", html)
        self.assertIn('class="m dev tile"', html)  # one-colour rows draw the tile device

    def test_long_idea_text_is_clamped_as_warn(self):
        sd = os.path.join(self.work, "sets", "B")
        long = ("A deliberately long paragraph that keeps going well past what the card's idea column can hold, "
                "repeating its point about ledgers, rules, kraft paper and rust ink so that even at three quarters of "
                "the size it still cannot fit, which is exactly the case the template must survive. ") * 3

        def change(d):
            d["set"]["differs_by"] = long
            d["set"]["mechanism"] = long[:420]
        _edit(os.path.join(sd, "identity.json"), change)
        r = ic.render(sd)
        with open(r["html"], encoding="utf-8") as fh:
            html = fh.read()
        self.assertEqual([f["message"] for f in r["findings"] if f["severity"] == "gate"], [])
        self.assertIn("content-clamped", [f["id"] for f in r["findings"] if f["severity"] == "warn"])
        self.assertIn("A deliberately long paragraph that keeps going well past what the", html)  # table.md: full

    def test_logo_none_collapses_and_bad_font_fails(self):
        sd = os.path.join(self.work, "sets", "C")

        def change(d):
            d["components"]["logo"] = {"mode": "none", "status": "not_applicable", "source": None, "sha256": None}
            d["type"]["display"]["family"] = "No Such Grotesk"
        _edit(os.path.join(sd, "identity.json"), change)
        shutil.rmtree(os.path.join(sd, "logo"))
        r = ic.render(sd)
        with open(r["html"], encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("No logo in this brief", html)
        ids = {f["id"] for f in r["findings"] if f["severity"] == "gate"}
        self.assertIn("font-file-missing", ids)
        self.assertIn("font-fallback", ids)
        self.assertIn("is-failed", html)


class TestKitApplications(unittest.TestCase):
    """The applications page as HTML (no browser): which mark the big boxes show and the business card text."""

    def setUp(self):
        _offline()
        self.addCleanup(_restore)
        self.tmp = tempfile.mkdtemp(prefix="bi-apps-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = _copy_demo(self.tmp)
        self.sd = os.path.join(self.work, "sets", "A")

    def page(self):
        return kb.page_applications(kb.Kit(self.sd))["content"]

    def test_icon_key_uses_the_full_symbol_from_64_px(self):
        both = {"small": "<svg/>", "symbol": "<svg/>", "primary": "<svg/>"}
        self.assertEqual(kb.icon_key(both, 32), "small")
        self.assertEqual(kb.icon_key(both, 72), "symbol")
        self.assertEqual(kb.icon_key(both, 150), "symbol")
        self.assertEqual(kb.icon_key({"small": "<svg/>", "primary": "<svg/>"}, 150), "small")  # wordmark-only
        self.assertEqual(kb.icon_key({"primary": "<svg/>"}, 150), "primary")

    def test_avatar_and_app_icon_show_the_full_symbol(self):
        # Fernhill: the 150 px avatar and app icon showed the 16-48 px small cut
        bd = os.path.join(self.sd, "logo", "build")
        with open(os.path.join(bd, "master-symbol-small.svg"), "w", encoding="utf-8") as fh:
            fh.write('<svg viewBox="0 0 100 100" data-kind="small-cut"><rect width="100" height="100" '
                     'data-color="primary"/></svg>')
        html = self.page()
        soc = html[html.index('class="soc"'):]
        self.assertNotIn("small-cut", soc)
        with open(os.path.join(bd, "symbol.svg"), encoding="utf-8") as fh:
            sym_d = fh.read().split(' d="', 1)[1].split('"', 1)[0]
        self.assertIn(sym_d[:40], soc)

    def test_own_app_icon_is_shown_as_built(self):
        bd = os.path.join(self.sd, "logo", "build")
        for name in ("master-app-icon.svg", "app-icon-512.svg"):
            with open(os.path.join(bd, name), "w", encoding="utf-8") as fh:
                fh.write('<svg viewBox="0 0 100 100" data-kind="own-icon"><rect width="100" height="100" '
                         'fill="#123456"/></svg>')
        html = self.page()
        self.assertIn('class="av sq app"', html)
        self.assertIn('data-kind="own-icon"', html)

    def test_business_card_has_no_invented_contact(self):
        html = self.page()
        self.assertNotIn("hello@", html)
        self.assertIn("name@example.com", html)
        self.assertIn("Sample details", html)
        _edit(os.path.join(self.work, "brief.json"), lambda d: d.update(site="https://www.ferrow-bikes.co.uk/shop"))
        html = self.page()
        self.assertIn("ferrow-bikes.co.uk", html)
        self.assertNotIn("example.com", html)
        self.assertIn("Sample details", html)   # name and title stay samples


@unittest.skipUnless(BROWSER and HAS_FT, "needs a Chromium-based browser and fontTools")
class TestKit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _offline()
        cls.tmp = tempfile.mkdtemp(prefix="bi-kit-")
        cls.work = _copy_demo(cls.tmp)
        cls.res = kb.build(os.path.join(cls.work, "sets", "A"))

    @classmethod
    def tearDownClass(cls):
        _restore()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_eight_pages_and_pdf(self):
        r = self.res
        self.assertEqual([f["message"] for f in r["findings"] if f["severity"] == "gate"], [])
        self.assertTrue(r["passed"])
        names = [os.path.basename(p) for p in r["pages"]]
        self.assertEqual(names, ["01-cover.png", "02-idea.png", "03-logo.png", "04-logo-variations.png",
                                 "05-logo-on-colour.png", "06-colours.png", "07-typography.png",
                                 "08-applications.png"])
        self.assertEqual(rp.png_size(r["pages"][0]), (1920, 1080))
        self.assertTrue(r["pdf"].endswith("ferrow-guidelines.pdf"))
        self.assertEqual(r["pdf_fonts"]["type3"], 0)
        self.assertTrue(any(f["name"].startswith("Outfit") and f["embedded"] == "FontFile2"
                            for f in r["pdf_fonts"]["fonts"]))

    def test_logo_boxes_are_measured(self):
        with open(self.res["html"], encoding="utf-8") as fh:
            html = fh.read()
        self.assertGreaterEqual(html.count("data-logo-ground"), 10)  # cover, construction, tiles, grounds, uses
        self.assertNotIn("logo-on-ground", [f["id"] for f in self.res["findings"]])
        # the misuse tiles are deliberately wrong and are not measured
        mis = html[html.index('class="mis"'):html.index('class="mis"') + 6000]
        self.assertNotIn("data-logo-ground", mis)

    def test_side_outputs(self):
        kit = os.path.join(self.work, "kit", "A")
        self.assertEqual(self.res["kit"], kit)
        self.assertEqual(self.res["logo"], "skipped: logolib not installed")
        with open(os.path.join(kit, "fonts.md"), encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("https://fonts.google.com/specimen/Outfit", md)
        self.assertIn("OFL-1.1", md)
        self.assertTrue(os.path.isfile(os.path.join(kit, "tokens", "fonts.css")))
        for root, _d, files in os.walk(kit):
            self.assertFalse([f for f in files if f.lower().endswith((".ttf", ".otf", ".woff", ".woff2"))], root)
        with open(self.res["html"], encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("ask your printer for CMYK/Pantone", html)
        self.assertNotIn("CMYK ", html.replace("CMYK/Pantone", ""))

    def test_kits_per_set_do_not_mix_and_set_b_passes(self):
        marker = os.path.join(self.work, "kit", "A", "tokens", "only-in-a.txt")
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("x")
        rb = kb.build(os.path.join(self.work, "sets", "B"))
        self.assertEqual(rb["kit"], os.path.join(self.work, "kit", "B"))
        self.assertTrue(os.path.isfile(marker))  # A's kit untouched by B's build
        self.assertFalse(os.path.exists(os.path.join(rb["kit"], "tokens", "only-in-a.txt")))
        self.assertEqual([f["message"] for f in rb["findings"] if f["severity"] == "gate"], [])
        ra = kb.build(os.path.join(self.work, "sets", "A"))
        self.assertFalse(os.path.exists(marker))  # a rebuild of A starts clean
        self.assertTrue(ra["passed"])

    def test_none_component_pages_omitted(self):
        sd = os.path.join(self.work, "sets", "B")

        def change(d):
            d["components"]["palette"] = {"mode": "none", "status": "not_applicable", "source": None, "sha256": None}
            d["palette"] = None
        _edit(os.path.join(sd, "identity.json"), change)
        os.remove(os.path.join(sd, "palette.json"))
        k = kb.Kit(sd)
        names = [fn(k)["name"] for _g, fn in kb.plan(k)]
        self.assertNotIn("colours", names)
        self.assertEqual(len(names), 7)
        full = [fn(k)["name"] for _g, fn in kb.plan(k, full=True)]
        self.assertIn("grid", full)
        self.assertNotIn("dark-mode", full)


if __name__ == "__main__":
    unittest.main()
