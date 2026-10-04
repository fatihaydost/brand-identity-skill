"""Unit tests for the site/mock preview tools: render_png.py, site_preview.py, palette_board.py (stdlib unittest).

Browser-dependent tests are skipped when no Chromium-based browser is found.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
EXAMPLE = os.path.join(ROOT, "skills", "brand-identity", "templates", "palette.example.json")
TEMPLATES = os.path.join(ROOT, "skills", "brand-identity", "templates", "mock-sites")
IDENTITY_EXAMPLE = os.path.join(ROOT, "skills", "brand-identity", "templates", "identity.example.json")
FONTS = os.path.join(ROOT, "tests", "fixtures", "fonts")
SITE_FIXTURE = os.path.join(ROOT, "tests", "site", "fixtures", "identity-site.html")
BRAND = os.path.join(SCRIPTS, "brand.py")
LOCKUP = ('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="48" viewBox="0 0 240 48"><circle cx="24" cy="24" '
          'r="20" fill="#c2410c"/><rect x="56" y="12" width="176" height="24" rx="4" fill="#2b211b"/></svg>')


def read(path, as_json=False):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh) if as_json else fh.read()


def make_set(work, sid="A", logo="new", type_="new", logo_type="symbol+wordmark", fonts=True):
    """A set folder as the pipeline leaves it (identity, palette, type/fonts.json, logo/build/primary.svg)."""
    d = os.path.join(work, "sets", sid)
    os.makedirs(os.path.join(d, "type"), exist_ok=True)
    os.makedirs(os.path.join(d, "logo", "build"), exist_ok=True)
    with open(IDENTITY_EXAMPLE, encoding="utf-8") as fh:
        idt = json.load(fh)
    idt["set"]["id"] = sid
    idt["components"]["logo"]["mode"] = logo
    idt["components"]["type"]["mode"] = type_
    idt["logo"]["type"] = logo_type
    idt["type"]["display"].update(family="Outfit", location={"wght": 650}, tracking=-10)
    idt["type"]["text"].update(family="Arvo", location={})
    with open(os.path.join(d, "identity.json"), "w", encoding="utf-8") as fh:
        json.dump(idt, fh)
    shutil.copy(EXAMPLE, os.path.join(d, "palette.json"))
    if fonts:
        with open(os.path.join(d, "type", "fonts.json"), "w", encoding="utf-8") as fh:
            json.dump({"display": {"family": "Outfit", "file": os.path.join(FONTS, "Outfit[wght].ttf"), "location": {"wght": 650}},
                       "text": {"family": "Arvo", "file": os.path.join(FONTS, "Arvo-Regular.ttf"), "location": {}}}, fh)
    with open(os.path.join(d, "logo", "build", "primary.svg"), "w", encoding="utf-8") as fh:
        fh.write(LOCKUP)
    return d
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import colorlib as cl  # noqa: E402
import palette_audit as pa  # noqa: E402
import palette_board as pb  # noqa: E402
import render_png as rp  # noqa: E402
import site_preview as sp  # noqa: E402

BROWSER = rp.find_browser()


class TestRenderPng(unittest.TestCase):
    def test_env_override_wins(self):
        tmp = tempfile.mkdtemp()
        try:
            fake = os.path.join(tmp, "my-chrome")
            open(fake, "w").close()
            old = os.environ.get("BRAND_IDENTITY_BROWSER")
            os.environ["BRAND_IDENTITY_BROWSER"] = fake
            try:
                self.assertEqual(rp.browser_candidates()[0], fake)
            finally:
                if old is None:
                    os.environ.pop("BRAND_IDENTITY_BROWSER")
                else:
                    os.environ["BRAND_IDENTITY_BROWSER"] = old
        finally:
            shutil.rmtree(tmp)

    def test_file_url_quotes_spaces_and_unicode(self):
        path = os.path.join(tempfile.gettempdir(), "a b", "çay.html")
        url = rp._file_url(path)
        self.assertTrue(url.startswith("file:///"), url)
        self.assertIn("a%20b/", url)
        self.assertNotIn(" ", url)

    def test_missing_file_is_a_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            rp.screenshot_html("/nonexistent/page.html", "/tmp/x.png")

    @unittest.skipUnless(BROWSER, "no Chromium-based browser")
    def test_viewport_scale_and_full_page(self):
        tmp = tempfile.mkdtemp()
        try:
            page = os.path.join(tmp, "p.html")
            with open(page, "w", encoding="utf-8") as fh:
                fh.write("<!doctype html><body style='margin:0'><div style='height:1500px;background:#c2410c'></div>")
            rp.screenshot_html(page, os.path.join(tmp, "a.png"), 400, 300, 2.0)
            self.assertEqual(rp.png_size(os.path.join(tmp, "a.png")), (800, 600))
            rp.screenshot_html(page, os.path.join(tmp, "b.png"), 400, 300, 1.0, full_page=True)
            self.assertEqual(rp.png_size(os.path.join(tmp, "b.png")), (400, 1500))
        finally:
            shutil.rmtree(tmp)


class TestFill(unittest.TestCase):
    TPL = ('<!doctype html><html lang="en" data-theme="light"><head><style id="bi-tokens">{{TOKENS_CSS}}</style>'
           '</head><body><!-- {{TOKENS_CSS}} --><b>{{BRAND_NAME}}</b><i>{{TAGLINE}}</i>'
           '<span class="logo">{{LOGO_SVG}}</span></body></html>')

    def setUp(self):
        self.pal = cl.load_palette(EXAMPLE)

    def test_tokens_css_uses_contract_names(self):
        css = sp.tokens_css(self.pal, "light")
        self.assertIn(f"--bi-on-primary: {self.pal['modes']['light']['onPrimary']};", css)
        self.assertIn("--bi-text-muted:", css)
        self.assertIn("--bi-surface-alt:", css)
        self.assertIn("--bi-primary-500:", css)
        self.assertTrue(css.startswith(":root {"))

    def test_fill_sets_theme_and_placeholders(self):
        html = sp.fill_template(self.TPL, self.pal, "dark", "Kahve & Co", "Small <batch>", "")
        self.assertIn('data-theme="dark"', html)
        self.assertIn(self.pal["modes"]["dark"]["background"], html)
        self.assertIn("<b>Kahve &amp; Co</b>", html)
        self.assertIn("Small &lt;batch&gt;", html)
        self.assertIn('<span class="logo"></span>', html)  # empty, so :empty selectors work
        self.assertNotIn("{{", html)

    def test_copy_json_and_kraft(self):
        tpl = ('<html data-theme="light"><head><style id="bi-tokens">{{TOKENS_CSS}}</style></head><body>'
               '<script type="application/json" id="bi-copy">{{COPY_JSON}}</script><!-- fill {{COPY_JSON}} --></body></html>')
        copy = {"product.name": "Fındık </script> Ezmesi", "_lang": "tr"}
        html_ = sp.fill_template(tpl, self.pal, "light", "X", "Y", "", copy, "#a99083")
        self.assertIn('"product.name": "Fındık <\\/script> Ezmesi"', html_)
        self.assertIn("--bi-kraft: #a99083;", html_)
        self.assertNotIn("{{", html_)

    def test_load_copy_merges_user_keys_over_english(self):
        tmp = tempfile.mkdtemp()
        try:
            user = os.path.join(tmp, "c.json")
            with open(user, "w", encoding="utf-8") as fh:
                json.dump({"product.weight": "300 g"}, fh)
            merged = sp.load_copy(user)
            self.assertEqual(merged["product.weight"], "300 g")
            en = os.path.join(sp.COPY_DIR, "en.json")
            if os.path.isfile(en):
                with open(en, encoding="utf-8") as fh:
                    defaults = json.load(fh)
                other = next(k for k in defaults if k != "product.weight" and not k.startswith("_"))
                self.assertEqual(merged[other], defaults[other])
        finally:
            shutil.rmtree(tmp)

    def test_logo_svg_is_inlined(self):
        svg = '<svg viewBox="0 0 2 2" fill="currentColor"><rect width="2" height="2"/></svg>'
        html = sp.fill_template(self.TPL, self.pal, "light", "X", "Y", svg)
        self.assertIn(f'<span class="logo">{svg}</span>', html)

    def test_font_and_mark_slots(self):
        tpl = ('<html data-theme="light"><head><style id="bi-tokens">{{TOKENS_CSS}}</style>'
               '<style id="bi-fonts">{{FONTS_CSS}}</style></head><body><!-- {{FONTS_CSS}} -->'
               '<i class="a">{{LOGO_SVG}}</i><i class="m">{{LOGO_MARK_SVG}}</i></body></html>')
        html = sp.fill_template(tpl, self.pal, "light", "X", "Y", "<svg/>")
        self.assertIn('<style id="bi-fonts"></style>', html)
        self.assertIn('<i class="m"><svg/></i>', html)  # the mark slot defaults to the logo
        self.assertNotIn("data-logo", html)
        html = sp.fill_template(tpl, self.pal, "light", "X", "Y", "<svg id=l/>", fonts_css=":root{--bi-font-text:x}",
                                logo_mark_svg="", logo_mode="lockup")
        self.assertIn('<style id="bi-fonts">:root{--bi-font-text:x}</style>', html)
        self.assertIn('<i class="m"></i>', html)
        self.assertIn('<html data-logo="lockup" data-theme="light">', html)
        self.assertNotIn("{{", html)

    def test_every_template_fills_without_identity(self):
        for name in sp.TEMPLATES:
            with open(os.path.join(TEMPLATES, f"{name}.html"), encoding="utf-8") as fh:
                tpl = fh.read()
            self.assertIn('<style id="bi-fonts">{{FONTS_CSS}}</style>', tpl, name)
            self.assertIn("--bi-font-display", tpl, name)
            self.assertIn("--bi-primary", tpl, name)  # colour variables stay
            html = sp.fill_template(tpl, self.pal, "light", "X", "Y", "")
            self.assertEqual(re.findall(r"\{\{[A-Z_]+\}\}", html), [], name)

    def test_slugify(self):
        self.assertEqual(sp.slugify("Midnight Teal"), "midnight-teal")
        self.assertEqual(sp.slugify("Işık Mavisi"), "isik-mavisi")


class TestIdentityInputs(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.work)

    def ident(self, d):
        with open(os.path.join(d, "identity.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_fonts_css_variable_and_static(self):
        d = make_set(self.work)
        css, notes = sp.identity_fonts_css(d, self.ident(d))
        self.assertEqual(notes, [])
        self.assertIn("font-family:'BI display a'", css)
        self.assertRegex(css, r"font-family:'BI display a';font-style:normal;font-display:block;font-weight:100 900;")
        self.assertIn("font-family:'BI text a';font-style:normal;font-display:block;font-weight:1 1000;", css)
        self.assertIn('--bi-fvs-display: "wght" 650;', css)
        self.assertIn("--bi-fvs-text: normal;", css)  # static file: nothing to pin
        self.assertIn("letter-spacing: -0.01em;", css)  # tracking -10 / 1000 em
        self.assertIn("data:font/ttf;base64,", css)

    def test_fonts_missing_or_kept(self):
        d = make_set(self.work, fonts=False)
        css, notes = sp.identity_fonts_css(d, self.ident(d))
        self.assertEqual(css, "")
        self.assertTrue(any("no file in type/fonts.json" in n for n in notes), notes)
        d = make_set(self.work, sid="B", type_="keep")
        self.assertEqual(sp.identity_fonts(d, self.ident(d))[0], {})

    def test_logo_lockup_symbol_and_keep(self):
        d = make_set(self.work)
        logo, mark, mode = sp.identity_logo(d, self.ident(d))
        self.assertEqual(mode, "lockup")
        self.assertTrue(logo.startswith("<svg"))
        self.assertNotRegex(logo.split(">")[0], r"\s(width|height)=")  # CSS sizes it, viewBox keeps the aspect
        self.assertIn('viewBox="0 0 240 48"', logo)
        self.assertEqual(mark, "")  # no symbol drawn: square slots fall back to the letter
        # logolib build: painted full-color / symbol-only versions, the manifest says what the primary is
        b = os.path.join(d, "logo", "build")
        os.remove(os.path.join(b, "primary.svg"))
        for n in ("full-color", "symbol-only"):
            with open(os.path.join(b, f"{n}.svg"), "w", encoding="utf-8") as fh:
                fh.write(LOCKUP.replace("<circle", f'<circle data-v="{n}"'))
        with open(os.path.join(b, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"primary": "lockup-horizontal"}, fh)
        logo, mark, mode = sp.identity_logo(d, self.ident(d))
        self.assertEqual(mode, "lockup")
        self.assertIn('data-v="full-color"', logo)
        self.assertIn('data-v="symbol-only"', mark)
        with open(os.path.join(b, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"primary": "symbol"}, fh)
        self.assertIsNone(sp.identity_logo(d, self.ident(d))[2])
        html = sp.fill_template("<html><body>{{LOGO_SVG}}</body></html>", cl.load_palette(EXAMPLE), "light", "X", "Y",
                                logo, trim_logo=True)
        self.assertIn("getBBox", html)  # clear-space padding is cropped in the page
        d = make_set(self.work, sid="B", logo_type="symbol")
        self.assertEqual(sp.identity_logo(d, self.ident(d))[2], None)
        d = make_set(self.work, sid="C", logo="keep")
        self.assertEqual(sp.identity_logo(d, self.ident(d)), ("", "", None))

    def test_site_findings(self):
        meta = {"fonts": {"display": {}}, "fontsLoaded": False, "logoVariants": ["primary.svg"],
                "layoutStress": {"desktop": 2, "mobile": 0},
                "viewports": {"desktop": {"layoutStress": {"samples": ['p "x" 1->2 lines']},
                                          "fonts": {"roles": {"text": {"ok": True, "applied": False}}},
                                          "logo": {"replaced": True, "fill": 0.24}},
                              "mobile": {"layoutStress": {"samples": []}, "logo": {"replaced": False, "reason": "no logo"}}}}
        fs = {f["id"]: f for f in sp._site_findings(meta)}
        self.assertEqual(fs["site-font-not-loaded"]["severity"], "gate")
        self.assertEqual(fs["site-layout-stress"]["severity"], "warn")
        self.assertIn("uncalibrated", fs["site-layout-stress"]["message"])
        self.assertEqual(fs["site-font-unused-text"]["severity"], "warn")
        self.assertEqual(fs["site-logo-small-desktop"]["severity"], "info")
        self.assertEqual(fs["site-logo-kept-mobile"]["severity"], "info")
        for f in fs.values():
            self.assertEqual(f["component"], "site")
            self.assertTrue({"id", "severity", "measured", "threshold", "message", "suggested_fix"} <= set(f))

    def test_competitor_lines_name_status_colour_fonts(self):
        d = {"sites": [{"host": "wise.com", "status": "ok", "roles": {"primary": "#9fe870"},
                        "fonts": {"heading": '"Wise Sans", sans-serif', "body": "Inter, sans-serif"}}],
             "errors": [{"host": "privatocafe.com", "status": "parked", "reason": "page says \"PrivatoCafe.com is for sale\""},
                        {"host": "zengo.com", "status": "blocked", "blocked": True, "reason": "Cloudflare challenge (cf-mitigated)"},
                        {"host": "old.test", "blocked": True, "reason": "HTTP 403"},  # record without status
                        {"host": "gone.test", "status": "error", "error": "HTTP 404"}]}
        lines = sp.competitor_lines(d, "/w/site")
        self.assertTrue(lines[0].startswith("competitors: 1 ok, 1 parked, 2 blocked, 1 failed"), lines[0])
        self.assertEqual(lines[1], "1 of 5 competitors measured; positioning falls back to the brief")
        self.assertRegex(lines[2], r"wise\.com +· ok · #9fe870 · Wise Sans / Inter")
        self.assertRegex(lines[3], r"privatocafe\.com +· parked, skipped: page says")
        self.assertRegex(lines[4], r"zengo\.com +· blocked, skipped: Cloudflare")
        self.assertRegex(lines[5], r"old\.test +· blocked, skipped: HTTP 403")
        self.assertRegex(lines[6], r"gone\.test +· error, skipped: HTTP 404")
        self.assertNotIn("measured;", "\n".join(sp.competitor_lines({"sites": d["sites"], "errors": []}, "/w/site")))

    def test_apply_json_merges_per_set(self):
        path = os.path.join(self.work, "apply.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"source": "site", "url": "https://x.test/", "sets": {"A": {"v": 1}, "B": {"v": 1}, "C": {"v": 1}}}, fh)
        body = {"source": "site", "url": "https://x.test/", "sets": {"B": {"v": 2}}}
        self.assertEqual(sp.merge_site_apply(path, body), ["A", "C"])
        self.assertEqual(body["sets"], {"A": {"v": 1}, "B": {"v": 2}, "C": {"v": 1}})
        other = {"source": "site", "url": "https://y.test/", "sets": {"B": {"v": 3}}}  # another site: no merge
        self.assertEqual(sp.merge_site_apply(path, other), [])
        self.assertEqual(other["sets"], {"B": {"v": 3}})
        mock = {"source": "mock", "templates": ["app"], "sets": {"A": {}}}
        self.assertEqual(sp.merge_site_apply(path, mock), [])
        self.assertEqual(sp.merge_site_apply(os.path.join(self.work, "none.json"), body), [])

    def test_cli_site_bad_targets(self):
        for argv in (["site", "extract", "https://x.test/"], ["site", "competitors", self.work],
                     ["site", "extract", "notaurl", self.work], ["site", "apply", "ftp://x", self.work]):
            make_set(self.work)
            r = subprocess.run([sys.executable, BRAND, *argv], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 2, (argv, r.stderr))
        r = subprocess.run([sys.executable, BRAND, "site", "apply", "mock", tempfile.mkdtemp()], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no sets found", r.stderr)


class TestSiteToolChecks(unittest.TestCase):
    def test_missing_tool_names_the_mock_fallback(self):
        saved = sp.SITE_TOOL
        sp.SITE_TOOL = os.path.join(tempfile.mkdtemp(), "nosuch.py")
        try:
            self.assertTrue(any("missing" in p for p in sp.site_problems()))
            with self.assertRaises(SystemExit) as cm:
                sp.require_site()
            self.assertEqual(cm.exception.code, 3)
        finally:
            sp.SITE_TOOL = saved

    def test_silent_site_tool_is_a_clear_error(self):
        """A tool that exits 0 and writes nothing (the symlink bug) must not end in a traceback."""
        tmp = tempfile.mkdtemp()
        saved = sp.SITE_TOOL
        try:
            stub = os.path.join(tmp, "stub.py")
            with open(stub, "w") as fh:
                fh.write("# does nothing\n")
            sp.SITE_TOOL = stub
            with self.assertRaises(SystemExit) as cm:
                sp.run_site(["extract", "https://x.test/", "--out", tmp], expect=[os.path.join(tmp, "extract.json")])
            self.assertEqual(cm.exception.code, 1)
        finally:
            sp.SITE_TOOL = saved
            shutil.rmtree(tmp)

    @unittest.skipUnless(BROWSER and hasattr(os, "symlink") and os.name != "nt", "needs a browser and symlinks")
    def test_check_through_a_symlinked_install(self):
        tmp = tempfile.mkdtemp()
        try:
            link = os.path.join(tmp, "linked-skill")
            os.symlink(os.path.join(ROOT, "skills", "brand-identity"), link)
            r = subprocess.run([sys.executable, os.path.join(link, "scripts", "site_preview.py"), "--check"],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            info = json.loads(r.stdout)
            self.assertTrue(info["livePreviewReady"], r.stderr)
            self.assertTrue(info["siteCheck"]["selfTest"]["ok"])
        finally:
            shutil.rmtree(tmp)

    def test_bad_input_exit_codes(self):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "site_preview.py"), "--mock", "nosuch",
                            "--palettes", EXAMPLE, "--out", tempfile.gettempdir()],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not found", r.stderr)


class TestBoard(unittest.TestCase):
    JARGON = ("ΔE", "CIEDE", "Lc ", "isolumin", "gate", "onPrimary", "surfaceAlt", "textMuted", "WCAG")

    def setUp(self):
        self.pal = cl.load_palette(EXAMPLE)
        self.roles = self.pal["modes"]["light"]

    def test_plain_sentences_for_common_findings(self):
        f = lambda i, m="": {"id": i, "severity": "warn", "message": m}  # noqa: E731
        self.assertEqual(pb.plain_sentence(f("contrast.light.onPrimary-on-primary", "light: onPrimary #ffffff on primary #e0a526 ...")),
                         "White text on the main buttons is hard to read — use a darker shade of the button colour.")
        self.assertEqual(pb.plain_sentence(f("cvd.light.deutan.link-text")),
                         "Links look like body text for some colour-blind readers — underline them.")
        self.assertEqual(pb.plain_sentence(f("ground.kraft.brand-2", "brand-2 #d2b373 on kraft #a99083 is 1.49:1")),
                         "Gold disappears on kraft — print it on a white label or use a darker gold.")
        self.assertIsNone(pb.plain_sentence(f("contrast.light.border-on-background")))
        self.assertIsNone(pb.plain_sentence(f("harmony.primary-accent")))
        for fid in ("contrast.dark.link-on-text", "structure.lightness-range", "isoluminant.a-b"):
            s = pb.plain_sentence(f(fid, "#111111 #eeeeee"))
            self.assertTrue(s and not any(j in s for j in self.JARGON), s)

    def test_colour_names(self):
        for hx, name in (("#ffffff", "white"), ("#274454", "navy"), ("#d2b373", "gold"), ("#ab5d2b", "copper"),
                         ("#7d222a", "burgundy"), ("#8da1b3", "slate blue"), ("#f2cf3b", "yellow")):
            self.assertEqual(pb.colour_name(hx), name, hx)

    def test_verdicts_pick_plain_cells_and_watch_out(self):
        audit = {"findings": [
            {"id": "ground.kraft.brand-2", "severity": "warn", "message": "brand-2 #d2b373 on kraft #a99083"},
            {"id": "cvd.light.protan.link-text", "severity": "warn", "message": ""},
            {"id": "contrast.dark.text-on-background", "severity": "warn", "message": ""}]}
        v = pb.verdicts(audit, ["light"], True)
        self.assertEqual(v["read"], (True, "Clear"))  # the dark finding is not shown on a light-only board
        self.assertFalse(v["cvd"][0])
        self.assertEqual(v["print"], (False, "Gold fades on kraft"))
        self.assertTrue(v["watch"].startswith("Gold disappears on kraft"))
        both = pb.verdicts(audit, ["light", "dark"], False)
        self.assertEqual(both["read"], (False, "Dark mode: body text too faint"))
        self.assertNotIn("print", both)
        clean = pb.verdicts({"findings": []}, ["light"], False)
        self.assertEqual(clean["watch"], "Nothing major.")

    def test_bands_skip_repeated_colours(self):
        html_ = pb.bands_html({"background": "#ffffff", "surface": "#ffffff", "primary": "#1f5f7a",
                               "accent": "#1f5f7a", "text": "#111111"})
        self.assertEqual(html_.count("<div style="), 3)

    def test_feels_like_prefers_palette_words(self):
        self.assertEqual(pb.feels_like({"feels": ["calm", "local"]}, self.roles), ["calm", "local"])
        words = pb.feels_like({}, self.roles)
        self.assertTrue(2 <= len(words) <= 4)

    def test_build_board_and_audit_md(self):
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmp, "harbor"))
            man = {"schema": "brand-identity/preview@2", "source": "mock", "themes": ["light", "dark"],
                   "brandName": "Kahve", "tagline": "Small batch", "grounds": [{"name": "kraft", "hex": "#a99083"}],
                   "recommend": "A", "columns": [
                       {"kind": "current", "name": "Old", "slug": "current",
                        "roles": {"light": {"background": "#ffffff", "text": "#000000", "primary": "#e0a526"}},
                        "shots": {"light": {"desktop": "current/landing-desktop.png"}}},
                       {"kind": "direction", "name": "Harbor <A>", "slug": "harbor", "direction": "Deep blue.",
                        "palette": EXAMPLE, "roles": {"light": self.roles, "dark": self.pal["modes"]["dark"]},
                        "shots": {"light": {}, "dark": {}}}]}
            with open(os.path.join(tmp, "preview.json"), "w", encoding="utf-8") as fh:
                json.dump(man, fh)
            html_path, png = pb.build(tmp)
            self.assertIsNone(png)
            with open(html_path, encoding="utf-8") as fh:
                doc = fh.read()
            self.assertIn("Kahve — colour directions", doc)
            self.assertIn("Harbor &lt;A&gt;", doc)
            self.assertIn("RECOMMENDED", doc)
            self.assertIn('class="card rec"', doc)
            self.assertIn("Print (kraft)", doc)
            self.assertIn("Technical checks: audit.md", doc)
            self.assertEqual(doc.count(">dark<"), 2)  # both themes: a dark shot per card
            visible = re.sub(r"<[^>]+>|<style>.*?</style>", " ", doc, flags=re.S)
            for j in self.JARGON:
                self.assertNotIn(j, visible.split("</style>")[-1], j)
            with open(os.path.join(tmp, "audit.md"), encoding="utf-8") as fh:
                md = fh.read()
            self.assertIn("## Current", md)
            self.assertIn("Contrast, light", md)
        finally:
            shutil.rmtree(tmp)

    EXT = [{"id": "ext-1", "name": "Plain", "hex": "#c9a46a", "on": "#1a1208"},
           {"id": "ext-2", "name": "Cocoa", "hex": "#6b3f2a", "on": "#ffffff", "dark": {"hex": "#b07a5e", "on": "#120a06"}},
           {"id": "ext-3", "name": "Roast", "hex": "#6e4130", "on": "#ffffff"}]

    def test_extended_strip_and_column(self):
        pal = dict(self.pal, extended=self.EXT)
        strip = pb.ext_strip_html(pal, "light")
        self.assertEqual(strip.count("<i style="), 3)
        self.assertIn("<b>Cocoa</b><code>#6b3f2a</code>", strip)
        self.assertIn("#b07a5e", pb.ext_strip_html(pal, "dark"))
        no_ext = {k: v for k, v in self.pal.items() if k != "extended"}
        self.assertEqual(pb.ext_strip_html(no_ext, "light"), "")
        self.assertEqual(pb.extended_cell(pal, {"findings": []}), (True, "3 — easy to tell apart"))
        alike = {"findings": [{"id": "extended.distinct.ext-2-ext-3", "severity": "warn", "roles": ["ext-2", "ext-3"],
                               "message": "ext-2 #6b3f2a and ext-3 #6e4130 are close"}]}
        self.assertEqual(pb.extended_cell(pal, alike), (False, "Cocoa and Roast look alike"))
        self.assertIsNone(pb.extended_cell(no_ext, {"findings": []}))

    def test_feels_from_palette_then_guess_without_opposites(self):
        self.assertEqual(pb.feels_like({"feels": ["calm", "local", "honest"]}, self.roles), ["calm", "local", "honest"])
        samples = [{"background": "#ffffff", "primary": p, "accent": a}
                   for p in ("#274454", "#c2410c", "#f2cf3b", "#6b7280", "#0f766e", "#ff2d55")
                   for a in ("#ab5d2b", "#1f5f7a", "#d2b373")]
        for r in samples:
            words = pb.feels_like({}, r)
            self.assertLessEqual(len(words), 3, (r, words))
            for pair in pb.OPPOSITES:
                self.assertFalse(pair <= set(words), (r, words))

    def test_board_with_extended_colours(self):
        tmp = tempfile.mkdtemp()
        try:
            pal = dict(self.pal, extended=self.EXT, feels=["calm", "local"])
            path = os.path.join(tmp, "p.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(pal, fh)
            man = {"schema": "brand-identity/preview@2", "source": "mock", "themes": ["light"], "brandName": "X",
                   "columns": [{"kind": "direction", "name": "Sis", "slug": "sis", "palette": path,
                                "roles": {"light": self.roles}, "shots": {"light": {}}}]}
            with open(os.path.join(tmp, "preview.json"), "w", encoding="utf-8") as fh:
                json.dump(man, fh)
            html_path, _ = pb.build(tmp)
            with open(html_path, encoding="utf-8") as fh:
                doc = fh.read()
            self.assertIn("<th>Extra colours</th>", doc)
            self.assertIn('class="ext"', doc)
            self.assertIn("<td>calm, local</td>", doc)
            self.assertNotIn('class="verdict"', doc)
        finally:
            shutil.rmtree(tmp)

    def test_ext_tokens_in_mock_css(self):
        pal = dict(self.pal, extended=self.EXT)
        css = sp.tokens_css(pal, "light")
        self.assertIn("--bi-ext-2: #6b3f2a;", css)
        self.assertIn("--bi-on-ext-2: #ffffff;", css)
        dark = sp.tokens_css(pal, "dark")
        self.assertIn("--bi-ext-2: #b07a5e;", dark)
        self.assertIn("--bi-ext-1: #c9a46a;", dark)  # no dark variant: the light value is kept

    def test_card_sections(self):
        roles = dict(self.roles)
        both = {"light": roles, "dark": self.pal["modes"]["dark"]}
        self.assertEqual(pb.in_use_html(roles, "Kahve").count('class="use"'), 1)
        chips = pb.status_html(roles)
        for name in ("Success", "Warning", "Error", "Info"):
            self.assertIn(f"<b>{name}</b>", chips)
        self.assertIn(roles["danger"], chips)
        read = pb.readability_html(both, ["light", "dark"], {"findings": []})
        self.assertEqual(read.count('class="rr"'), 3)
        self.assertEqual(read.count('class="ok"'), 3)
        self.assertRegex(read, r"Body text</span><small>[\d.]+:1 · [\d.]+:1</small>")
        flagged = pb.readability_html(both, ["light"], {"findings": [
            {"id": "contrast.light.link-on-text", "severity": "warn"},
            {"id": "contrast.dark.onPrimary-on-primary", "severity": "warn"}]})
        self.assertIn("look like body text", flagged)
        self.assertEqual(flagged.count('class="no"'), 1)  # the dark finding is not shown on a light board

    def test_buttons_row_judges_label_and_fill(self):
        r = {"light": {"background": "#f9fafc", "primary": "#f2cf3b", "onPrimary": "#1a1c1f", "text": "#1a1c1f"}}
        audit = {"findings": [{"id": "contrast.light.primary-on-background", "severity": "warn"}]}
        row = [x for x in pb.readability_html(r, ["light"], audit).split('<div class="rr">') if "Buttons" in x][0]
        self.assertIn("blend into the page", row)
        self.assertIn('class="no"', row)
        self.assertIn(f"{cl.contrast_ratio('#f2cf3b', '#f9fafc'):.1f}:1", row)  # the failing ratio, not the label's
        v = pb.verdicts(audit, ["light"], False)
        self.assertEqual(v["read"], (False, "Buttons blend into the page"))  # table cell from the same finding
        label = {"findings": [{"id": "contrast.light.onPrimary-on-primary", "severity": "warn"}]}
        self.assertIn("label hard to read", pb.readability_html(r, ["light"], label))

    def test_colour_vision_rows_match_the_audit(self):
        cv = pb.cvd_html({}, {"light": self.roles}, ["light"])
        for name in ("Typical", "Red-blind", "Green-blind", "Blue-blind", "protanopia", "deuteranopia", "tritanopia"):
            self.assertIn(name, cv)
        for col in ("Main", "Accent", "Link", "Text", "Success", "Error"):
            self.assertIn(f"<em>{col}</em>", cv)
        clash = pb.cvd_html({}, {"light": {"primary": "#1f5f7a", "accent": "#1f5f7a", "text": "#111111",
                                           "link": "#1f5f7a", "success": "#2e7d32", "danger": "#a33f00"}}, ["light"])
        self.assertIn("Success ≈ Error", clash)  # same simulation and threshold as palette_audit
        self.assertNotIn("Main ≈ Accent", clash)   # identical colours are not a pair
        ext = pb.cvd_html({"extended": [{"id": "ext-1", "hex": "#c9a46a"}]}, {"light": self.roles}, ["light"])
        self.assertIn('<em class="x">Extra</em>', ext)

    def test_mobile_shot_beside_desktop(self):
        self.assertEqual(pb.pick_mobile({"mobile": "a/m.png"}), "a/m.png")
        prod = {"template": "product", "mobile": "a/product-mobile.png",
                "extra": [{"label": "landing", "mobile": "a/landing-mobile.png"}]}
        self.assertEqual(pb.pick_mobile(prod), "a/landing-mobile.png")
        self.assertIsNone(pb.pick_mobile({"template": "product", "mobile": "x.png", "extra": []}))
        row = pb.shots_row("d.png", "m.png", "", 400)
        w = [float(x) for x in re.findall(r"width:([\d.]+)px", row)]
        self.assertLessEqual(sum(w) + 10, 401)  # side by side, nothing overlaps

    def test_missing_manifest(self):
        with self.assertRaises(FileNotFoundError):
            pb.build(tempfile.mkdtemp())


@unittest.skipUnless(BROWSER and os.path.isfile(os.path.join(TEMPLATES, "landing.html")),
                     "needs a browser and the landing mock template")
class TestMockEndToEnd(unittest.TestCase):
    def pal_light_bg(self):
        return cl.load_palette(EXAMPLE)["modes"]["light"]["background"]

    def test_mock_preview_and_board(self):
        tmp = tempfile.mkdtemp()
        try:
            copy_file = os.path.join(tmp, "copy.json")
            with open(copy_file, "w", encoding="utf-8") as fh:
                json.dump({"_lang": "tr", "product.name": "Fındık Ezmesi"}, fh, ensure_ascii=False)
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "site_preview.py"), "--mock", "landing,product",
                                "--palettes", EXAMPLE, "--out", tmp, "--board", "--copy", copy_file,
                                "--ground", "kraft", "--theme", "both"],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(tmp, "preview.json"), encoding="utf-8") as fh:
                man = json.load(fh)
            col = man["columns"][0]
            self.assertEqual(man["source"], "mock")
            self.assertEqual(man["themes"], ["light", "dark"])
            self.assertEqual(man["grounds"], [{"name": "kraft", "hex": "#a99083"}])
            self.assertEqual(rp.png_size(os.path.join(tmp, col["shots"]["light"]["desktop"])), (1440, 900))
            self.assertEqual(rp.png_size(os.path.join(tmp, col["shots"]["dark"]["mobile"])), (780, 1688))
            self.assertTrue(os.path.getsize(os.path.join(tmp, "board.png")) > 10000)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "audit.md")))
            self.assertFalse(os.path.exists(os.path.join(tmp, "board-dark.png")))
            slug = col["slug"]
            with open(os.path.join(tmp, slug, "product.html"), encoding="utf-8") as fh:
                page = fh.read()
            self.assertIn('"_lang": "tr"', page)  # the user's own copy file, any language
            self.assertIn("--bi-kraft: #a99083;", page)  # bare --ground kraft = palette_audit's KNOWN_GROUNDS
            with open(os.path.join(tmp, "board.html"), encoding="utf-8") as fh:
                self.assertIn('lang="en"', fh.read())  # the board itself is always English
            # the dark theme of a print scene keeps the light roles
            with open(os.path.join(tmp, slug, "product-dark.html"), encoding="utf-8") as fh:
                page = fh.read()
            self.assertIn('data-theme="light"', page)
            self.assertIn(self.pal_light_bg(), page)
        finally:
            shutil.rmtree(tmp)


@unittest.skipUnless(BROWSER, "needs a browser")
class TestSiteCommand(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp()
        make_set(self.work)

    def tearDown(self):
        shutil.rmtree(self.work)

    def brand(self, *argv):
        return subprocess.run([sys.executable, BRAND, *argv], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=600)

    def test_apply_one_set_keeps_the_others(self):
        make_set(self.work, sid="B")
        r = self.brand("site", "apply", "mock:landing", self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.brand("site", "apply", "mock:landing", self.work, "--sets", "B")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("kept from the earlier apply: set A", r.stdout)
        man = read(os.path.join(self.work, "site", "apply.json"), True)
        self.assertEqual(sorted(man["sets"]), ["A", "B"])

    def test_mock_apply_fills_fonts_and_logo(self):
        r = self.brand("site", "apply", "mock:landing,card", self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLessEqual(len(r.stdout.strip().splitlines()), 15)
        man = read(os.path.join(self.work, "site", "apply.json"), True)
        self.assertEqual(man["source"], "mock")
        st = man["sets"]["A"]
        self.assertEqual(st["logo"], "lockup")
        self.assertEqual(rp.png_size(os.path.join(self.work, st["shots"]["landing-desktop"])), (1440, 900))
        page = read(os.path.join(self.work, st["dir"], "landing.html"))
        self.assertIn('data-logo="lockup"', page)
        self.assertIn("--bi-font-display: 'BI display a', var(--font);", page)
        self.assertIn('viewBox="0 0 240 48"', page)
        self.assertNotIn("{{", page)

    @unittest.skipUnless(BROWSER, "needs a Chromium-based browser")
    def test_live_apply_on_local_fixture(self):
        url = Path(SITE_FIXTURE).as_uri()
        r = self.brand("site", "extract", url, self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLessEqual(len(r.stdout.strip().splitlines()), 15)
        self.assertIn("fonts: heading Site Slab · body Site Sans", r.stdout)
        self.assertRegex(r.stdout, r"logo: img 144x36 -> .*site/logo\.svg \(kept-logo source\)")
        r = self.brand("site", "apply", url, self.work, "--sets", "A")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLessEqual(len(r.stdout.strip().splitlines()), 15)
        self.assertRegex(r.stdout, r"layoutStress [1-9]\d* desktop")
        man = read(os.path.join(self.work, "site", "apply.json"), True)
        st = man["sets"]["A"]
        self.assertTrue(st["fontsLoaded"])
        self.assertEqual(st["logo"]["desktop"], "primary.svg")
        self.assertIn("site-layout-stress", [f["id"] for f in st["findings"]])
        for k in ("desktop", "mobile", "full", "header"):
            self.assertTrue(os.path.isfile(os.path.join(self.work, st["shots"][k])), k)
        self.assertTrue(os.path.isfile(os.path.join(self.work, man["current"]["header"])))


if __name__ == "__main__":
    unittest.main()
