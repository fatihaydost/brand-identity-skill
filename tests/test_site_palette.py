"""Live-site tool (scripts/site_palette.py + scripts/site/site_engine.js + scripts/cdplib.py): unit and offline
integration tests. Ported one to one from the former Node tests (tests/site/site_palette.test.mjs).

The JavaScript engine runs in a browser, so most tests need a Chromium-based browser and are skipped without one.
"""
import http.server
import json
import math
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
TOOL = os.path.join(SCRIPTS, "site_palette.py")
HERE = os.path.join(ROOT, "tests", "site")
FIXTURES = os.path.join(HERE, "fixtures")
EXAMPLE = os.path.join(ROOT, "skills", "brand-identity", "templates", "palette.example.json")
IDENTITY_EXAMPLE = os.path.join(ROOT, "skills", "brand-identity", "templates", "identity.example.json")
FONTS = os.path.join(ROOT, "tests", "fixtures", "fonts")
LOCKUP_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 48"><circle cx="24" cy="24" r="20" fill="#c2410c"/>'
              '<rect x="56" y="12" width="176" height="24" rx="4" fill="#2b211b"/></svg>')

sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import cdplib  # noqa: E402
import site_palette as M  # noqa: E402

BROWSER = (cdplib.browser_candidates() or [None])[0]
needs_browser = unittest.skipUnless(BROWSER, "no Chromium-based browser found")


def fixture_url(name):
    return Path(os.path.join(FIXTURES, name)).as_uri()


def run_tool(*args, timeout=300):
    return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL, timeout=timeout)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def make_set(work, sid="A", logo="new", type_="new", palette="new", mono=False):
    """A set folder as the pipeline leaves it: identity.json, palette.json, type/fonts.json, logo/build/primary.svg.
    Display = Outfit (variable, pinned wght 650), text = Arvo (static, wider than the fixture site's Outfit body)."""
    d = os.path.join(work, "sets", sid)
    os.makedirs(os.path.join(d, "type"), exist_ok=True)
    os.makedirs(os.path.join(d, "logo", "build"), exist_ok=True)
    idt = read_json(IDENTITY_EXAMPLE)
    idt["set"]["id"] = sid
    idt["components"]["logo"]["mode"] = logo
    idt["components"]["type"]["mode"] = type_
    idt["components"]["palette"]["mode"] = palette
    idt["type"]["display"] = {**idt["type"]["display"], "family": "Outfit", "location": {"wght": 650}}
    idt["type"]["text"] = {**idt["type"]["text"], "family": "Arvo", "location": {}}
    if mono:
        idt["type"]["mono"] = {**idt["type"]["text"], "family": "Arvo", "fallback": "monospace"}
    with open(os.path.join(d, "identity.json"), "w", encoding="utf-8") as fh:
        json.dump(idt, fh, indent=2)
    shutil.copy(EXAMPLE, os.path.join(d, "palette.json"))
    fonts = {"display": {"family": "Outfit", "file": os.path.join(FONTS, "Outfit[wght].ttf"), "location": {"wght": 650}},
             "text": {"family": "Arvo", "file": os.path.join(FONTS, "Arvo-Regular.ttf"), "location": {}}}
    if mono:
        fonts["mono"] = {"family": "Arvo", "file": os.path.join(FONTS, "Arvo-Regular.ttf"), "location": {}}
    with open(os.path.join(d, "type", "fonts.json"), "w", encoding="utf-8") as fh:
        json.dump(fonts, fh)
    with open(os.path.join(d, "logo", "build", "primary.svg"), "w", encoding="utf-8") as fh:
        fh.write(LOCKUP_SVG)
    return os.path.join(d, "identity.json")


class _Engine:
    """One browser + engine page for the unit tests of the JavaScript engine."""
    browser = session = None

    @classmethod
    def get(cls):
        if cls.session is None:
            cls.browser, errors = cdplib.launch()
            if not cls.browser:
                raise unittest.SkipTest("browser did not start: " + "; ".join(errors))
            cls.session = M.Session(cls.browser)
        return cls.session

    @classmethod
    def close(cls):
        if cls.browser:
            cls.browser.close()
            cls.browser = cls.session = None


def tearDownModule():
    _Engine.close()


@needs_browser
class EngineUnits(unittest.TestCase):
    """Pure functions of site_engine.js, evaluated in the engine page."""

    @classmethod
    def setUpClass(cls):
        cls.S = _Engine.get()
        cls.eng = cls.S.eng

    def js(self, expr):
        r = self.eng.page.conn.call("Runtime.evaluate", {"expression": f"(() => {{ const C = SiteEngine.C, F = SiteEngine.F, "
                                                                        f"A = SiteEngine.API; return ({expr}); }})()",
                                                         "returnByValue": True}, self.eng.page.session)
        if r.get("exceptionDetails"):
            raise AssertionError(r["exceptionDetails"])
        return r["result"].get("value")

    def test_parse_handles_hex_rgb_hsl_oklch_and_names(self):
        self.assertEqual(self.js("C.toHex(C.parse('#abc'))"), "#aabbcc")
        self.assertEqual(self.js("C.toHex(C.parse('rgb(31 95 122)'))"), "#1f5f7a")
        self.assertEqual(self.js("C.toHex(C.parse('hsl(0, 100%, 50%)'))"), "#ff0000")
        self.assertEqual(self.js("C.toHex(C.parse('white'))"), "#ffffff")
        self.assertEqual(self.js("C.toHex(C.parse('oklch(1 0 0)'))"), "#ffffff")
        self.assertEqual(self.js("C.parse('rgba(0,0,0,.5)').a"), 0.5)
        self.assertIsNone(self.js("C.parse('var(--x)')"))

    def test_contrast_matches_wcag2(self):
        self.assertLess(abs(self.js("C.contrast(C.parse('#000'), C.parse('#fff'))") - 21), 1e-9)

    def test_mapper_sends_old_role_anchors_to_the_new_roles(self):
        r = self.js("""(() => {
          const old = { background: '#ffffff', text: '#222222', primary: '#1f5f7a', textMuted: '#666666' };
          const neu = { background: '#fbf7f2', text: '#2b211b', primary: '#c2410c', textMuted: '#6e5f53' };
          const map = C.makeMapper(old, neu);
          const near = (a, b) => C.deltaE(map(C.parse(a)), C.parse(b)) < 0.01;
          return [near('#ffffff', '#fbf7f2'), near('#222222', '#2b211b'), near('#1f5f7a', '#c2410c'),
                  near('#666666', '#6e5f53'), C.toHex(map(C.parse('#d11a2a')))];
        })()""")
        self.assertEqual(r[:4], [True, True, True, True])  # background, text, primary, muted
        self.assertEqual(r[4], "#d11a2a")  # an unrelated hue (status red far from every anchor) is left alone

    def test_pale_accent_tints_follow_the_accent(self):
        r = self.js("""(() => {
          const old = { background: '#ffffff', text: '#191c20', primary: '#106579', accent: '#106579', textMuted: '#494d54' };
          const neu = { background: '#fbf7f2', text: '#2b211b', primary: '#c2410c', accent: '#c2410c', textMuted: '#6e5f53' };
          const tintHex = C.toHex(C.oklchToRgb({ L: 0.945, C: 0.022, h: 218 }));
          const plain = C.makeMapper(old, neu)(C.parse(tintHex));
          const steps = { 50: '#fff4ef', 100: '#ffe6da', 200: '#fdc9b1', 500: '#e0601f', 600: '#c2410c', 900: '#4a1806' };
          const scaled = C.makeMapper(old, neu, { scales: { accent: steps } })(C.parse(tintHex));
          return [C.toOklch(plain).C, C.hueDist(C.toOklch(plain).h, C.toOklch(C.parse('#c2410c')).h),
                  Object.values(steps).includes(C.toHex(scaled)),
                  C.toHex(C.makeMapper(old, neu, { scales: { accent: steps } })(C.parse('#ffffff')))];
        })()""")
        self.assertGreater(r[0], 0.015, "tint went grey")
        self.assertLess(r[1], 25)
        self.assertTrue(r[2])
        self.assertEqual(r[3], "#fbf7f2")  # the page background (a role anchor) still follows the neutral ramp

    def test_named_tokens_prefixes_stripped_framework_internals_ignored(self):
        vars_ = {"--vp-c-text-1": {"hex": "#3c3c43", "alpha": 1}, "--vp-c-text-2": {"hex": "#67676c", "alpha": 1},
                 "--kds-surface": {"hex": "#fcfdfe", "alpha": 1}, "--accent-bg": {"hex": "#e8f3f6", "alpha": 1},
                 "--tw-ring-offset-color": {"hex": "#ffffff", "alpha": 1}, "--bs-body-bg": {"hex": "#fafafa", "alpha": 1},
                 "--data-context": {"hex": "#83878b", "alpha": 1}, "--color-primary": {"hex": "#106579", "alpha": 1}}
        t = self.eng.call("namedTokenRoles", vars_, {})
        self.assertEqual(t["text"]["name"], "--vp-c-text-1")
        self.assertEqual(t["textMuted"]["name"], "--vp-c-text-2")
        self.assertEqual(t["surface"]["name"], "--kds-surface")
        self.assertEqual(t["background"]["name"], "--bs-body-bg")  # not --accent-bg
        self.assertEqual(t["primary"]["name"], "--color-primary")
        self.assertNotIn("focus", t)  # --tw-ring-* is a Tailwind internal

    def test_hue_families_and_the_compact_competitors_table(self):
        h = lambda x: self.eng.call("hueFamily", x)  # noqa: E731
        self.assertEqual([h("#c00015"), h("#ffd343"), h("#1f5f7a"), h("#0f766e"), h("#777777")],
                         ["red", "yellow", "blue", "teal", "neutral"])
        d = {"sites": [
            {"host": "a.test", "roles": {"background": "#ffffff", "primary": "#c00015", "accent": "#ffd343"},
             "confidence": {"primary": 0.83}, "nHues": 2},
            {"host": "b.test", "roles": {"background": "#111111", "primary": "#1f5f7a"}, "confidence": {"primary": 0.5},
             "nHues": 1}],
             "errors": [{"url": "https://c.test/", "host": "c.test", "blocked": True, "reason": 'page title "Just a moment..."'}]}
        d["sites"][0]["fonts"] = {"heading": '"Söhne", sans-serif', "body": "Inter, sans-serif"}
        d["errors"].append({"url": "https://p.test/", "host": "p.test", "status": "parked",
                            "reason": "redirected to hugedomains.com"})
        t = M.competitors_summary(self.S, d, "out", 2)
        self.assertRegex(t, r"^competitors: 2 ok, 1 parked, 1 blocked, 0 failed, 2 skipped")
        self.assertRegex(t, r"\n2 of 4 competitors measured; positioning falls back to the brief")
        self.assertRegex(t, r"a\.test +ok +#c00015 red \+ #ffd343 yellow · light · Söhne / Inter")
        self.assertRegex(t, r"b\.test +ok +#1f5f7a blue · dark · - / -")
        self.assertRegex(t, r'c\.test +blocked +blocked, skipped: page title "Just a moment')
        self.assertRegex(t, r"p\.test +parked +parked, skipped: redirected to hugedomains\.com")
        self.assertRegex(t, r"empty families: .*green")
        self.assertLess(len(t.encode("utf-8")), 1500)

    def test_parked_domains_by_redirect_title_or_short_page_text(self):
        p = lambda **k: self.eng.call("parkedReason", k)  # noqa: E731
        self.assertEqual(p(requested="https://privatocafe.com/",
                           final="https://www.hugedomains.com/domain_profile.cfm?d=privatocafe.com", title="x", text="x"),
                         "redirected to hugedomains.com")
        self.assertRegex(p(requested="https://privatocafe.com/", final="https://privatocafe.com/",
                           title="PrivatoCafe.com is for sale | HugeDomains", text=""), "for sale")
        for text in ("The domain name privatocafe.com is for sale", "Buy this domain today",
                     "This domain is parked free, courtesy of GoDaddy.com", "Make an offer on this domain",
                     "Sedo domain parking"):
            self.assertTrue(p(requested="https://x.test/", final="https://x.test/", title="x", text=text), text)
        self.assertIsNone(p(requested="https://cafe.test/", final="https://cafe.test/menu", title="Cafe Privato",
                            text="Espresso, cakes and a garden. Open daily."))
        # a long real page that mentions a sale is not a parking page; the marketplace's own site is not parked
        self.assertIsNone(p(requested="https://shop.test/", final="https://shop.test/", title="Shop",
                            text="Our cafe domain is for sale soon. " + "menu " * 5000))
        self.assertIsNone(p(requested="https://www.godaddy.com/", final="https://www.godaddy.com/", title="GoDaddy",
                            text="Buy this domain"))

    def test_empty_page_reason(self):
        e = lambda n, pal: self.eng.call("emptyPageReason", n, pal)  # noqa: E731
        self.assertRegex(e(0, [{"hex": "#ffffff", "share": 1}]), "only 0 visible characters")
        self.assertRegex(e(400, [{"hex": "#ffffff", "share": 0.99}]), "one colour")
        self.assertIsNone(e(400, [{"hex": "#ffffff", "share": 0.7}]))

    def test_http_bot_protection_challenge_header_and_access_statuses(self):
        b = lambda st, h: self.eng.call("httpBlockReason", st, h)  # noqa: E731
        self.assertRegex(b(200, {"cf-mitigated": "challenge"}), "Cloudflare challenge")
        self.assertEqual(b(403, {}), "HTTP 403")
        self.assertEqual(b(403, {"Server": "cloudflare"}), "HTTP 403 from cloudflare")
        self.assertEqual(b(429, {}), "HTTP 429")
        self.assertEqual(b(503, {"server": "cloudflare"}), "HTTP 503 from cloudflare")
        self.assertIsNone(b(503, {}))
        self.assertIsNone(b(404, {}))
        self.assertIsNone(b(200, {}))

    def test_rewrite_css_changes_colour_declarations_only(self):
        out = self.js("C.rewriteCss('.white{color:#fff;background:url(a#fff.png) #000;width:10px}:root{--x:rgba(0,0,0,.5);}', "
                      "() => ({ r: 1, g: 2, b: 3 }))")
        self.assertRegex(out, r"^\.white\{color:#010203;background:url\(a#fff\.png\) #010203;width:10px\}")
        self.assertRegex(out, r"--x:rgba\(1, 2, 3, 0\.5\)")

    def test_to_hex_clamps_channels(self):
        self.assertEqual(self.js("C.toHex({ r: 348, g: -5, b: 40.4 })"), "#ff0028")
        self.assertEqual(self.js("C.toHex({ r: NaN, g: 255, b: 255 })"), "#00ffff")

    def test_slugify_and_repeated_palette_flags(self):
        self.assertEqual(self.eng.call("slugify", "Midnight Teal"), "midnight-teal")
        self.assertEqual(self.eng.call("slugify", "Çınar Yeşili"), "cinar-yesili")
        a = M.parse_args(["apply", "https://x.test", "--palette", "a.json", "--palette", "b.json", "c.json", "--out", "o"])
        self.assertEqual(a["palette"], ["a.json", "b.json", "c.json"])
        self.assertEqual(a["out"], "o")
        self.assertEqual(M.parse_args(["x", "--palettes=a.json,b.json"])["palette"], ["a.json", "b.json"])

    def test_confidence_stays_in_range_and_fallbacks_are_capped(self):
        c = lambda **k: self.eng.call("confidence", k)  # noqa: E731
        self.assertLessEqual(c(dominance=1, n=100), 0.97)
        self.assertGreaterEqual(c(dominance=0, n=0), 0.05)
        self.assertLessEqual(c(dominance=1, n=100, fallback=True), 0.35)

    def test_font_rewrite_prepends_set_faces_and_leaves_font_face_alone(self):
        r = self.js("""(() => {
          const famFor = F.makeFamilyMapper({ display: { name: '__bi_display_a', old: 'site slab' }, text: { name: '__bi_text_a', old: 'site sans' } });
          const css = '@font-face{font-family:"Site Sans";src:url(a.woff2)}h1{font-family:"Site Slab",serif}body{font:400 17px/1.5 "Site Sans",Arial,sans-serif}code{font-family:ui-monospace,monospace}:root{--font-body:"Site Sans", sans-serif;--gap:12px}';
          const out = F.rewriteFontCss(css, famFor);
          const one = F.makeFamilyMapper({ display: { name: 'D', old: 'inter' }, text: { name: 'T', old: 'inter' } });
          return [out, F.rewriteFontCss(out, famFor) === out, one('Inter, sans-serif', '.hero-title'),
                  one('Inter, sans-serif', '--font-heading :root'), one('Inter, sans-serif', 'p'), one('"Font Awesome 6 Free"', 'i')];
        })()""")
        out = r[0]
        self.assertRegex(out, r'@font-face\{font-family:"Site Sans";src:url\(a\.woff2\)\}')
        self.assertRegex(out, r'h1\{font-family:"__bi_display_a", "Site Slab",serif\}')
        self.assertRegex(out, r'font:400 17px/1\.5 "__bi_text_a", "Site Sans",Arial,sans-serif')
        self.assertRegex(out, r"code\{font-family:ui-monospace,monospace\}")
        self.assertRegex(out, r'--font-body:"__bi_text_a", "Site Sans", sans-serif')
        self.assertRegex(out, r"--gap:12px")
        self.assertTrue(r[1], "idempotent")
        # one family for both roles: heading-like selectors and custom properties go to display
        self.assertEqual(r[2:], ["D", "D", "T", None])

    def test_font_axes_svg_sizes_and_the_layout_stress_diff(self):
        with open(os.path.join(FONTS, "Outfit[wght].ttf"), "rb") as fh:
            ax = M.font_axes(fh.read())
        self.assertTrue(ax["wght"] and ax["wght"]["min"] <= 100 and ax["wght"]["max"] >= 900, ax)
        with open(os.path.join(FONTS, "Arvo-Regular.ttf"), "rb") as fh:
            self.assertEqual(M.font_axes(fh.read()), {})
        self.assertIsNone(M.font_axes(b"wOF2xxxxxxxxxxxx"))
        self.assertEqual(self.eng.call("svgSize", LOCKUP_SVG), {"w": 240, "h": 48})
        self.assertEqual(self.eng.call("svgSize", '<svg width="30" height="10"></svg>'), {"w": 30, "h": 10})
        before = {"1": {"o": False, "l": 1, "t": "a", "tag": "p"}, "2": {"o": False, "l": 1, "t": "b", "tag": "span"},
                  "3": {"o": True, "l": 1, "t": "c", "tag": "div"}, "4": {"o": False, "l": 2, "t": "d", "tag": "h1"}, "5": None}
        after = {"1": {"o": False, "l": 2, "t": "a", "tag": "p"}, "2": {"o": True, "l": 1, "t": "b", "tag": "span"},
                 "3": {"o": True, "l": 3, "t": "c", "tag": "div"}, "4": {"o": False, "l": 1, "t": "d", "tag": "h1"},
                 "5": {"o": True, "l": 9}}
        st = self.eng.call("layoutStress", before, after)
        self.assertEqual(st["count"], 3)  # p gains a line, span starts overflowing, div gains lines
        self.assertEqual(st["overflow"], 1)
        self.assertEqual(st["gainedLines"], 2)

    def test_load_identity_set_reads_palette_faces_and_logo_variants(self):
        work = tempfile.mkdtemp(prefix="bi-set-")
        try:
            s = M.load_identity_set(self.S, make_set(work), "light")
            self.assertEqual(s["id"], "A")
            self.assertTrue(s["palette"]["roles"]["primary"])
            self.assertEqual([[f["role"], f["weight"]] for f in s["faces"]], [["display", "100 900"], ["text", "1 1000"]])
            self.assertEqual(s["roleStyle"]["display"]["fvs"], '"wght" 650')
            self.assertTrue(s["roleStyle"]["text"]["static"])
            self.assertEqual([[v["name"], v["w"], v["h"], v["primary"]] for v in s["variants"]], [["primary.svg", 240, 48, True]])
            kept = M.load_identity_set(self.S, make_set(work, sid="B", logo="keep", type_="none", palette="none"), "light")
            self.assertIsNone(kept["palette"])
            self.assertEqual(kept["faces"], [])
            self.assertEqual(kept["variants"], [])
            self.assertTrue(any("logo: keep" in n for n in kept["notes"]))
            # a logolib build: painted versions are used, role-tagged masters and small/one-colour files are not;
            # the manifest's mode marks the version for dark grounds
            b = os.path.join(work, "sets", "A", "logo", "build")
            os.remove(os.path.join(b, "primary.svg"))
            for n in ("full-color", "full-color-dark", "symbol-only", "master-primary", "one-color-dark", "favicon-32",
                      "on-light-primary"):
                with open(os.path.join(b, f"{n}.svg"), "w", encoding="utf-8") as fh:
                    fh.write(LOCKUP_SVG)
            with open(os.path.join(b, "manifest.json"), "w", encoding="utf-8") as fh:
                json.dump({"primary": "lockup-horizontal", "versions": {"full-color": {"mode": "light"},
                           "full-color-dark": {"mode": "dark"}, "symbol-only": {"mode": "light"}}}, fh)
            lg = M.load_identity_set(self.S, os.path.join(work, "sets", "A", "identity.json"), "light")
            self.assertEqual([[v["name"], v["primary"], v["reversed"]] for v in lg["variants"]],
                             [["full-color.svg", True, False], ["full-color-dark.svg", False, True],
                              ["symbol-only.svg", False, False]])
            # fonts.json pointing at a missing file: noted, not applied, no crash
            with open(os.path.join(work, "sets", "A", "type", "fonts.json"), "w", encoding="utf-8") as fh:
                json.dump({"display": {"family": "X", "file": "nope.ttf"}}, fh)
            miss = M.load_identity_set(self.S, os.path.join(work, "sets", "A", "identity.json"), "light")
            self.assertEqual(len(miss["faces"]), 0)
            self.assertTrue(any("display font: file not found" in n for n in miss["notes"]), miss["notes"])
        finally:
            shutil.rmtree(work, ignore_errors=True)

    @staticmethod
    def raw_page(hero_share):
        """Synthetic page reading: a large blue brand surface vs a small yellow CTA (the python.org case)."""
        vw, vh, doc_h = 1440, 900, 3000
        hero = vw * doc_h * hero_share
        stats = [
            {"hex": "#f9f9f9", "bg": vw * doc_h - hero, "text": 0, "border": 0, "svg": 0, "gradient": 0, "fold": 0, "n": 1},
            {"hex": "#2b5b84", "bg": hero, "text": 0, "border": 0, "svg": 0, "gradient": 0, "fold": min(hero, vw * vh), "n": 3},
            {"hex": "#ffd343", "bg": 160 * 44, "text": 0, "border": 0, "svg": 0, "gradient": 0, "fold": 160 * 44, "n": 1},
            {"hex": "#444444", "bg": 0, "text": 900, "border": 0, "svg": 0, "gradient": 0, "fold": 0, "n": 30},
            {"hex": "#e6e8ea", "bg": 0, "text": 300, "border": 0, "svg": 0, "gradient": 0, "fold": 0, "n": 8},
            {"hex": "#caccce", "bg": 0, "text": 0, "border": 5000, "svg": 0, "gradient": 0, "fold": 0, "n": 12}]
        return {"url": "https://x.test/", "viewport": {"w": vw, "h": vh, "docH": doc_h}, "pageBg": "#f9f9f9", "stats": stats,
                "votes": {"primary": [["#ffd343", 160 * 44 * 3, 1]], "onPrimary": [["#ffd343|#333333", 160 * 44 * 3, 1]],
                          "link": [["#1f5f7a", 50, 6]], "heading": [], "text": [["#444444", 900, 30]],
                          "border": [["#caccce", 5000, 12]],
                          "textOn": [["#2b5b84|#e6e8ea", 300, 8], ["#f9f9f9|#444444", 900, 30], ["#ffd343|#333333", 10, 1]]},
                "vars": {}, "inlineCss": "", "fonts": {}, "logo": None, "favicon": None,
                "signals": {"title": "x", "themeColor": None, "darkModeSupport": False}}

    def test_build_roles_large_chromatic_surface_wins_primary(self):
        b = self.eng.call("buildRoles", self.raw_page(0.2), [])
        self.assertEqual(b["roles"]["primary"], "#2b5b84")
        self.assertEqual(b["roles"]["accent"], "#ffd343")
        self.assertEqual(b["roles"]["background"], "#f9f9f9")
        self.assertEqual(b["roles"]["text"], "#444444")
        self.assertLessEqual(b["roleDetails"]["primary"]["confidence"], 0.5)
        self.assertTrue(any(n.startswith("primary/accent ambiguous") for n in b["notes"]))
        self.assertTrue(any(c.startswith("primary") for c in b["confirm"]))
        for d in b["roleDetails"].values():
            self.assertTrue(0 <= d["confidence"] <= 1)
            self.assertIn(d["level"], ("high", "medium", "low"))
            self.assertIsInstance(d["method"], str)
            self.assertIn("elements", d)
            self.assertIsInstance(d["alternatives"], list)
        # role keys are the palette.json contract keys, in contract order
        keys = list(b["roles"])
        self.assertEqual(keys, [k for k in M.ROLE_KEYS if k in keys])

    def test_build_roles_small_coloured_area_keeps_the_cta_primary(self):
        self.assertEqual(self.eng.call("buildRoles", self.raw_page(0.0), [])["roles"]["primary"], "#ffd343")

    def test_values_cross_the_bridge_unchanged(self):
        """NaN, Infinity, -0 and undefined survive the trip page -> Python -> engine, as with Playwright."""
        v = self.S.eng.page.evaluate("() => ({ a: NaN, b: -0, c: Infinity, d: undefined, e: [1, undefined, 'x'] })")
        self.assertTrue(math.isnan(v["a"]))
        self.assertEqual(math.copysign(1, v["b"]), -1)
        self.assertEqual(v["c"], float("inf"))
        self.assertIs(v["d"], cdplib.UNDEFINED)
        back = self.S.eng.page.evaluate("(x) => [Number.isNaN(x.a), Object.is(x.b, -0), x.c === Infinity, 'd' in x && x.d === undefined, x.e[1] === undefined]", v)
        self.assertEqual(back, [True, True, True, True, True])

    def test_numbers_print_like_javascript(self):
        xs = [0.1, 1e21, 1e-7, 123.0, 1.5, 0.000001, 1.2345e-7, 1.2345678901234568e20, 5e-324, 1.7976931348623157e308,
              0.30000000000000004, 100.0, 1e20, 12.5, 255.0, -3.75, 2 ** 53 + 2.0, 1 / 3, 65536 / 3]
        want = self.js("[" + ",".join(repr(x) for x in xs) + "].map(String)")
        self.assertEqual([cdplib.js_number(x) for x in xs], want)


class PythonUnits(unittest.TestCase):
    """Driver-side functions that need no browser."""

    def test_logo_downloads_http_and_small_data_only_never_file_from_a_remote_page(self):
        a = M.logo_source_allowed
        self.assertTrue(a("https://x.test/logo.svg", "https://x.test/")["ok"])
        self.assertTrue(a("data:image/svg+xml;base64,PHN2Zy8+", "https://x.test/")["ok"])
        self.assertFalse(a("data:image/png;base64," + "A" * 4000000, "https://x.test/")["ok"])
        self.assertEqual(a("file:///etc/passwd", "https://evil.test/"), {"ok": False, "reason": "skipped: unsupported scheme file:"})
        self.assertRegex(a("ftp://x.test/l.svg", "https://x.test/")["reason"], "unsupported scheme ftp:")
        self.assertRegex(a("blob:https://x.test/1", "https://x.test/")["reason"], "unsupported scheme")
        # a local page the user pointed at may use files next to it, not elsewhere
        site = Path(tempfile.gettempdir()) / "u" / "site"
        page = (site / "index.html").as_uri()
        self.assertTrue(a((site / "img" / "logo.svg").as_uri(), page)["ok"])
        self.assertFalse(a(Path(os.path.abspath(os.sep)).joinpath("etc", "passwd").as_uri(), page)["ok"])
        self.assertFalse(a(page.rsplit("/", 1)[0] + "/../.ssh/id", page)["ok"])

    def test_load_palette_reads_modes_light_and_dark(self):
        p = M.load_palette(EXAMPLE, "light")
        self.assertTrue(p["roles"]["background"] and p["roles"]["text"] and p["roles"]["primary"])
        for k in p["roles"]:
            self.assertIn(k, M.ROLE_KEYS)
        self.assertTrue(M.load_palette(EXAMPLE, "dark")["roles"]["background"])

    def test_json_is_written_like_json_stringify(self):
        self.assertEqual(cdplib.js_json({"a": [1, 2.5, {"b": None, "c": cdplib.UNDEFINED}], "d": {}, "e": [],
                                         "f": float("nan"), "g": -0.0, "h": 1e-7}),
                         '{\n  "a": [\n    1,\n    2.5,\n    {\n      "b": null\n    }\n  ],\n  "d": {},\n  "e": [],\n'
                         '  "f": null,\n  "g": 0,\n  "h": 1e-7\n}')
        self.assertEqual(cdplib.js_json("x\"\\\n \ud800é"), '"x\\"\\\\\\n \\ud800é"')

    def test_runs_through_a_symlinked_install(self):
        if not hasattr(os, "symlink"):
            self.skipTest("no symlinks")
        d = tempfile.mkdtemp(prefix="bi-link-")
        try:
            try:
                os.symlink(os.path.join(ROOT, "skills", "brand-identity"), os.path.join(d, "linked-skill"))
            except OSError as exc:
                self.skipTest(f"cannot symlink: {exc}")
            linked = os.path.join(d, "linked-skill", "scripts", "site_palette.py")
            r = subprocess.run([sys.executable, linked, "--help"], capture_output=True, text=True, timeout=60,
                               stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("extract <url>", r.stdout)
            bad = subprocess.run([sys.executable, linked, "nosuch"], capture_output=True, text=True, timeout=60,
                                 stdin=subprocess.DEVNULL)
            self.assertNotEqual(bad.returncode, 0)  # main() really ran
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---- offline integration: local fixture pages, needs a browser (skipped otherwise)

class _Server:
    def __init__(self, handler):
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _send(h, code, body, headers=None):
    h.send_response(code)
    h.send_header("content-type", "text/html")
    for k, v in (headers or {}).items():
        h.send_header(k, v)
    h.send_header("content-length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


@needs_browser
class Integration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bi-site-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extract_and_apply_on_a_local_fixture_page(self):
        out, url = self.tmp, fixture_url("cafe.html")
        r = run_tool("extract", url, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ex = read_json(os.path.join(out, "extract.json"))
        self.assertEqual(ex["schema"], "brand-identity/extract@1")
        self.assertEqual(ex["roles"]["primary"], "#1f5f7a", ex["roles"])
        self.assertEqual(ex["roles"]["accent"], "#e0a526")
        self.assertEqual(ex["roles"]["background"], "#fbfaf7")
        self.assertTrue(any("theme-color" in s for s in ex["roleDetails"]["primary"]["corroboration"]))
        for f in ("desktop", "mobile", "full"):
            self.assertTrue(os.path.exists(os.path.join(out, "current", f"{f}.png")))
        r = run_tool("apply", url, "--palette", EXAMPLE, "--out", out, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        slug = json.loads(r.stdout)["palettes"][0]["slug"]
        meta = read_json(os.path.join(out, slug, "apply.json"))
        self.assertEqual(meta["mode"], "light")
        for f in ("desktop", "mobile", "full"):
            self.assertGreater(os.path.getsize(os.path.join(out, slug, f"{f}.png")), 1000)

    def test_contrast_guard_leaves_light_text_on_an_image_gradient_hero_alone(self):
        out, url = self.tmp, fixture_url("topo-hero.html")
        self.assertEqual(run_tool("extract", url, "--out", out).returncode, 0)
        r = run_tool("apply", url, "--palette", EXAMPLE, "--out", out, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        meta = read_json(os.path.join(out, json.loads(r.stdout)["palettes"][0]["slug"], "apply.json"))
        for kind in ("desktop", "mobile"):
            g = meta["contrastGuard"][kind]
            hit = [x for x in g["samples"] if "Topo hero" in x or "light words" in x]
            self.assertEqual(hit, [], f"{kind}: hero text was repaired")
            self.assertGreaterEqual(g["skippedUnmeasurable"], 2, f"{kind}: hero text should be unmeasurable: {g}")

    def test_translucent_colours_via_the_canvas_fallback(self):
        out = self.tmp
        r = run_tool("extract", fixture_url("alpha.html"), "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ex = read_json(os.path.join(out, "extract.json"))
        hexes = list(ex["roles"].values()) + [p["hex"] for p in ex["palette"]] + [d["hex"] for d in ex["roleDetails"].values()]
        self.assertGreater(len(hexes), 4)
        for h in hexes:
            self.assertRegex(h, r"^#[0-9a-f]{6}$")
        # the opaque color(srgb 0.85 0.3 0.05) button resolves through the canvas to its real value
        S = _Engine.get()
        self.assertTrue(any(S.eng.page.evaluate(f"() => SiteEngine.C.deltaE(SiteEngine.C.parse('{p['hex']}'), "
                                                "SiteEngine.C.parse('#d94d0d')) < 0.02") for p in ex["palette"]),
                        [p["hex"] for p in ex["palette"]])

    def test_bot_walls_fail_extract_with_exit_5_and_land_in_competitor_errors(self):
        out = self.tmp
        blocked = fixture_url("blocked.html")
        r = run_tool("extract", blocked, "--out", out)
        self.assertEqual(r.returncode, 5, r.stderr)
        self.assertIn("blocks automated browsers", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(out, "extract.json")))
        self.assertTrue(read_json(os.path.join(out, "blocked.json"))["blocked"])
        denied, cafe = fixture_url("access-denied.html"), fixture_url("cafe.html")
        r = run_tool("competitors", blocked, denied, cafe, "--out", out, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = read_json(os.path.join(out, "competitors.json"))
        self.assertEqual([s["url"] for s in doc["sites"]], [cafe])
        self.assertTrue(isinstance(doc["sites"][0]["fonts"]["loaded"], list) and "heading" in doc["sites"][0]["fonts"])
        self.assertEqual([[e["url"], e["blocked"], e["status"]] for e in doc["errors"]],
                         [[blocked, True, "blocked"], [denied, True, "blocked"]])
        self.assertEqual(doc["sites"][0]["status"], "ok")
        self.assertEqual(json.loads(r.stdout)["blocked"], [blocked, denied])

    def test_third_party_widgets_and_unreadable_hero_text_do_not_vote(self):
        out = self.tmp
        r = run_tool("extract", fixture_url("widgets.html"), "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ex = read_json(os.path.join(out, "extract.json"))
        self.assertEqual(ex["roles"]["primary"], "#b5532f", ex["roles"])
        self.assertEqual(ex["roles"]["text"], "#222222")
        for v in ex["roles"].values():
            self.assertNotIn(v, ("#25d366", "#1972f5"), f"widget colour in roles: {v}")
        self.assertTrue(all(p["hex"] != "#25d366" for p in ex["palette"]))

    def test_re_extract_keeps_corrected_roles_and_dark_apply_reads_the_dark_scheme(self):
        out, url = self.tmp, fixture_url("darkable.html")
        r = run_tool("extract", url, "--out", out, "--no-dark")
        self.assertEqual(r.returncode, 0, r.stderr)
        file = os.path.join(out, "extract.json")
        ex = read_json(file)
        self.assertIsNone(ex["rolesDark"])
        self.assertEqual(ex["roles"]["text"], "#1c1c1a")
        ex["roles"]["text"] = "#000000"  # the user's correction
        with open(file, "w", encoding="utf-8") as fh:
            json.dump(ex, fh, indent=2)
        r = run_tool("extract", url, "--out", out, "--no-dark")
        self.assertEqual(r.returncode, 0, r.stderr)
        ex = read_json(file)
        self.assertEqual(ex["roles"]["text"], "#000000")
        self.assertIn("text", ex["preservedRoles"]["light"])
        r = run_tool("apply", url, "--palette", EXAMPLE, "--mode", "dark", "--out", out, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("reading the site's dark scheme first", r.stderr)
        ex = read_json(file)
        self.assertEqual(ex["rolesDark"]["background"], "#121412")
        self.assertEqual(ex["roles"]["text"], "#000000")  # light roles untouched by the dark reading
        meta = read_json(os.path.join(out, json.loads(r.stdout)["palettes"][0]["slug"], "apply-dark.json"))
        self.assertEqual(meta["pageScheme"], "dark")

    def test_apply_rejects_a_palette_without_modes(self):
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            json.dump({"name": "x", "roles": {}}, fh)
        with open(os.path.join(self.tmp, "extract.json"), "w", encoding="utf-8") as fh:
            json.dump({"roles": {"background": "#fff", "text": "#000"}}, fh)
        r = run_tool("apply", "https://x.invalid/", "--palette", bad, "--out", self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("modes.light is missing", r.stderr)

    def test_apply_identity_fonts_size_adjust_header_logo_layout_stress(self):
        work = self.tmp
        out, url = os.path.join(work, "site"), fixture_url("identity-site.html")
        r = run_tool("extract", url, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ex = read_json(os.path.join(out, "extract.json"))
        self.assertEqual(ex["fonts"]["roles"]["display"]["family"], "Site Slab")
        self.assertEqual(ex["fonts"]["roles"]["text"]["family"], "Site Sans")
        self.assertIn("h1", ex["fonts"]["roles"]["display"]["selectors"])
        self.assertEqual(ex["logo"]["kind"], "img")
        self.assertEqual(ex["logo"]["source"], "logo.svg")  # the <img> file itself, for a kept logo
        with open(os.path.join(out, "logo.svg"), encoding="utf-8") as fh:
            self.assertRegex(fh.read(), r'^<svg[^>]*viewBox="0 0 160 40"')
        self.assertEqual(ex["status"], "ok")
        self.assertGreater(os.path.getsize(os.path.join(out, "current", "header.png")), 1000)
        r = run_tool("apply", url, "--identity", make_set(work, mono=True), "--out", out, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        item = json.loads(r.stdout)["sets"][0]
        self.assertEqual(item["set"], "A")
        self.assertTrue(item["fontsLoaded"])
        meta = read_json(os.path.join(item["dir"], "apply.json"))
        self.assertEqual(meta["schema"], "brand-identity/site-apply@1")
        for kind in ("desktop", "mobile"):
            v = meta["viewports"][kind]
            for role in ("display", "text"):
                f = v["fonts"]["roles"][role]
                self.assertTrue(f["ok"] and f["applied"], f"{kind} {role}: {f}")
                self.assertTrue(0.7 < f["sizeAdjust"] < 1.4)
            self.assertGreaterEqual(v["fonts"]["displayMarked"], 3)  # h1 + two h2
            self.assertTrue(v["fonts"]["roles"]["mono"]["ok"] and v["fonts"]["roles"]["mono"]["applied"])
            self.assertEqual(v["fonts"]["monoMarked"], 3)  # <code> + the two figure cells, not the labels
            self.assertTrue(any('svg text "Thickness 0.2599" clipped' in x for x in v["layoutStress"]["samples"]),
                            v["layoutStress"]["samples"])
            # the fixture's .fit / .chip fit the site's Outfit body but not the wider Arvo text face
            self.assertGreaterEqual(v["layoutStress"]["count"], 3)
            self.assertTrue(any("Woven Mountain Mix" in x and "lines" in x for x in v["layoutStress"]["samples"]))
            self.assertTrue(any("Warm Monsoon Malt" in x and "overflows" in x for x in v["layoutStress"]["samples"]))
            self.assertTrue(v["logo"]["replaced"])
            self.assertEqual(v["logo"]["copies"], 2)  # header + footer copy of the same image
            self.assertTrue(v["logo"]["newBox"]["w"] <= v["logo"]["oldBox"]["w"] * 1.1 + 0.5 and
                            v["logo"]["newBox"]["h"] <= v["logo"]["oldBox"]["h"] * 1.1 + 0.5, v["logo"])
        self.assertEqual(meta["layoutStress"]["desktop"], meta["viewports"]["desktop"]["layoutStress"]["count"])
        for f in ("desktop", "mobile", "full", "header"):
            self.assertGreater(os.path.getsize(os.path.join(item["dir"], f"{f}.png")), 1000, f)
        # header close-up is @2x of the full viewport width
        with open(os.path.join(item["dir"], "header.png"), "rb") as fh:
            self.assertEqual(int.from_bytes(fh.read()[16:20], "big"), 2880)
        # a text wordmark is replaced by the logo image, its words kept as the link's aria-label
        tout = os.path.join(work, "text")
        r = run_tool("apply", fixture_url("text-logo.html"), "--identity", os.path.join(work, "sets", "A", "identity.json"),
                     "--out", tout, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        tex = read_json(os.path.join(tout, "extract.json"))
        self.assertEqual([tex["logo"]["kind"], tex["logo"]["text"]], ["text", "Kettle Works"])
        tmeta = read_json(os.path.join(json.loads(r.stdout)["sets"][0]["dir"], "apply.json"))
        self.assertEqual(tmeta["viewports"]["desktop"]["logo"]["kind"], "text")
        self.assertTrue(tmeta["viewports"]["desktop"]["logo"]["replaced"])

    def test_apply_identity_with_kept_type_and_logo_changes_colours_only(self):
        work = self.tmp
        r = run_tool("apply", fixture_url("identity-site.html"), "--identity", make_set(work, logo="keep", type_="keep"),
                     "--out", os.path.join(work, "site"), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        meta = read_json(os.path.join(json.loads(r.stdout)["sets"][0]["dir"], "apply.json"))
        self.assertIsNone(meta["viewports"]["desktop"]["fonts"])
        self.assertFalse(meta["viewports"]["desktop"]["logo"]["replaced"])
        self.assertEqual(meta["layoutStress"]["desktop"], 0)
        self.assertTrue(meta["roles"]["primary"])

    def test_competitors_parked_challenge_and_403_sites_are_kept_out_of_the_map(self):
        def page(name):
            with open(os.path.join(FIXTURES, name), "rb") as fh:
                return fh.read()
        long_page = ('<!doctype html><title>Fieldfisher</title><body style="font-family:Georgia"><h1>Law firm</h1>'
                     + "<p>Real-looking content behind a block. </p>" * 60).encode()

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):  # noqa: N802
                if self.path == "/parked":
                    return _send(self, 200, page("parked.html"))
                if self.path == "/challenge":
                    return _send(self, 403, b"<!doctype html><title>Just a moment...</title><body>Checking your browser</body>",
                                 {"cf-mitigated": "challenge", "server": "cloudflare"})
                if self.path == "/forbidden":
                    return _send(self, 403, long_page)
                if self.path == "/missing":
                    return _send(self, 404, long_page)
                return _send(self, 200, page("cafe.html"))

        srv = _Server(H)
        try:
            urls = [srv.base + p for p in ("/ok", "/parked", "/challenge", "/forbidden", "/missing")]
            r = run_tool("competitors", *urls, "--out", self.tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = read_json(os.path.join(self.tmp, "competitors.json"))
            self.assertEqual([[s["url"], s["status"]] for s in doc["sites"]], [[srv.base + "/ok", "ok"]])
            st = {e["url"].replace(srv.base, ""): e["status"] for e in doc["errors"]}
            self.assertEqual(st, {"/parked": "parked", "/challenge": "blocked", "/forbidden": "blocked", "/missing": "error"})
            out = r.stdout
            self.assertRegex(out, r"^competitors: 1 ok, 1 parked, 2 blocked, 1 failed")
            self.assertRegex(out, r"parked +parked, skipped: page says")
            self.assertRegex(out, r'blocked +blocked, skipped: (page title "Just a moment|Cloudflare challenge)')
            self.assertRegex(out, r"blocked +blocked, skipped: HTTP 403")
            self.assertRegex(out, r"error +failed, skipped: HTTP 404")
            self.assertRegex(out, r"1 of 5 competitors measured; positioning falls back to the brief")
            self.assertNotRegex(out, r"mock|site_preview")  # an unreadable competitor is no reason to preview on mocks
            self.assertRegex(out, r"127\.0\.0\.1 +ok +#1f5f7a blue")
        finally:
            srv.close()

    def test_extract_saves_an_inline_svg_logo_safely_and_warns_on_an_empty_page(self):
        out = self.tmp
        r = run_tool("extract", fixture_url("svg-logo.html"), "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"logo: svg → .*logo\.svg \(\+ logo-dark\.svg\)")
        with open(os.path.join(out, "logo.svg"), encoding="utf-8") as fh:
            light = fh.read()
        with open(os.path.join(out, "logo-dark.svg"), encoding="utf-8") as fh:
            dark = fh.read()
        for svg in (light, dark):
            self.assertNotRegex(svg, r"(?i)<style|class=|style=|data-bi-|currentColor|rgb\(|oklch\(")
            self.assertRegex(svg, r'^<svg[^>]*xmlns="http://www\.w3\.org/2000/svg"')
            self.assertRegex(svg, r'<rect[^>]*fill="#e0a526"')  # currentColor resolved
        self.assertRegex(light, r'<circle[^>]*fill="#30674c"')  # oklch(0.4693 0.0738 160.4) painted as hex
        self.assertNotRegex(light, r'fill-(opacity|rule)="#')
        self.assertRegex(dark, r'<circle[^>]*fill="#9fd4b8"')
        ex = read_json(os.path.join(out, "extract.json"))
        self.assertEqual([ex["logo"]["source"], ex["logo"]["sourceDark"]], ["logo.svg", "logo-dark.svg"])
        blank = os.path.join(out, "blank")
        r = run_tool("extract", fixture_url("blank.html"), "--out", blank)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"WARNING: page looks empty .*sub-path, pass the full URL")
        self.assertEqual(read_json(os.path.join(blank, "extract.json"))["status"], "empty")

    def test_a_remote_page_cannot_make_extract_copy_a_local_file(self):
        secret_dir = tempfile.mkdtemp(prefix="bi-secret-")
        secret = os.path.join(secret_dir, "secret.svg")
        with open(secret, "w", encoding="utf-8") as fh:
            fh.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><title>SECRET</title></svg>')
        html = (f'<!doctype html><title>Evil Co</title><style>body{{font-family:Arial;margin:0}}header{{display:flex;gap:20px;'
                f'padding:16px 40px;align-items:center}}</style>\n<header><a href="/" class="logo"><img src="{Path(secret).as_uri()}" '
                f'alt="Evil Co" width="120" height="32"></a><nav><a href="#a">About</a> <a href="#b">Shop</a></nav></header>\n'
                f'<h1 style="margin:40px">Evil Co sells things</h1><p style="margin:0 40px">'
                f'{"Plenty of words on the page so it is not empty. " * 6}</p>').encode()

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):  # noqa: N802
                _send(self, 200, html)

        srv = _Server(H)
        try:
            r = run_tool("extract", srv.base + "/", "--out", self.tmp, "--no-dark")
            self.assertEqual(r.returncode, 0, r.stderr)
            ex = read_json(os.path.join(self.tmp, "extract.json"))
            self.assertEqual(ex["logo"]["kind"], "img")
            self.assertNotIn("source", ex["logo"])
            self.assertEqual(ex["logo"]["sourceSkipped"], "skipped: unsupported scheme file:")
            for f in os.listdir(self.tmp):
                if f.startswith("logo.") and f.endswith((".svg", ".png", ".jpg", ".webp")) and f != "logo.png":
                    self.fail(f"saved {f}")
            for f in os.listdir(self.tmp):
                if f.endswith(".svg"):
                    with open(os.path.join(self.tmp, f), encoding="utf-8") as fh:
                        self.assertNotIn("SECRET", fh.read())
        finally:
            srv.close()
            shutil.rmtree(secret_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
