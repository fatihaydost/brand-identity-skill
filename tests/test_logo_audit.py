"""Unit tests for skills/brand-identity/scripts/logo_audit.py (stdlib unittest; fixture fonts in tests/fixtures)."""
import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
TEMPLATES = os.path.join(ROOT, "skills", "brand-identity", "templates")
FONTS = os.path.join(ROOT, "tests", "fixtures", "fonts")
OUTFIT = os.path.join(FONTS, "Outfit[wght].ttf")
sys.path.insert(0, SCRIPTS)

import logo_audit as A  # noqa: E402
import logolib as L  # noqa: E402


def example_identity():
    with open(os.path.join(TEMPLATES, "identity.example.json"), encoding="utf-8") as fh:
        ident = json.load(fh)
    ident["type"]["display"].update({"family": "Outfit", "source": "user", "file": OUTFIT,
                                     "location": {"wght": 600}, "weights": [600]})
    return ident


def example_palette():
    with open(os.path.join(TEMPLATES, "palette.example.json"), encoding="utf-8") as fh:
        return json.load(fh)


CLEAN = ('<svg viewBox="0 0 100 100"><circle id="disc" cx="50" cy="50" r="40" data-color="primary"/>'
         '<rect id="slot" x="0" y="55" width="100" height="8" data-op="subtract"/></svg>')


class AuditCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._px = L.DELIVERY_PX
        L.DELIVERY_PX = 96  # fast; sizes do not change what the audit measures

    @classmethod
    def tearDownClass(cls):
        L.DELIVERY_PX = cls._px

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_audit(self, symbol, small=None, mutate=None, palette=None):
        set_dir = os.path.join(self.tmp.name, "sets", "A")
        os.makedirs(os.path.join(set_dir, "logo"), exist_ok=True)
        with open(os.path.join(set_dir, "logo", "symbol.svg"), "w", encoding="utf-8") as fh:
            fh.write(symbol)
        if small:
            with open(os.path.join(set_dir, "logo", "symbol-small.svg"), "w", encoding="utf-8") as fh:
                fh.write(small)
        ident = example_identity()
        if mutate:
            mutate(ident)
        pal = palette or example_palette()
        build = os.path.join(set_dir, "logo", "build")
        L.variants(ident, pal, build)
        return A.audit(ident, pal, build)

    @staticmethod
    def ids(findings, severity=None):
        return {f["id"] for f in findings if severity is None or f["severity"] == severity}

    def test_dark_brand_id_is_reversed_in_dark_versions_not_gated(self):
        # Tessellate B: wordmark brand-1 (a blue-black ink) on the dark ground was a 1.23:1 gate. A brand id keeps its
        # exact hex where it reads; in dark versions it takes the dark text colour, a passing brand id stays
        import colorlib
        pal = example_palette()
        b1, b2, dbg = pal["brand"][0]["hex"], pal["brand"][4]["hex"], pal["modes"]["dark"]["background"]
        self.assertLess(colorlib.contrast_ratio(b1, dbg), 3.0)
        self.assertGreaterEqual(colorlib.contrast_ratio(b2, dbg), 3.0)

        def mutate(ident):
            ident["logo"]["colors"] = {"symbol": "brand-5", "wordmark": "brand-1"}
        fs = self.run_audit(CLEAN.replace('data-color="primary"', 'data-color="brand-5"'), mutate=mutate)
        self.assertEqual({f["message"] for f in fs if f["id"] == "logo.contrast" and f["severity"] == "gate"}, set())
        with open(os.path.join(self.tmp.name, "sets", "A", "logo", "build", "manifest.json"), encoding="utf-8") as fh:
            man = json.load(fh)
        light = {p["role"]: p["hex"] for p in man["versions"]["full-color"]["parts"]}
        dark = {p["role"]: p["hex"] for p in man["versions"]["full-color-dark"]["parts"]}
        self.assertEqual((light["brand-1"], light["brand-5"]), (b1, b2))
        self.assertEqual((dark["brand-1"], dark["brand-5"]), (pal["modes"]["dark"]["text"], b2))
        swaps = {(s["version"], s["role"]) for s in man["dark_swaps"]}
        self.assertEqual(swaps, {("full-color-dark", "brand-1"), ("on-dark-background", "brand-1")})
        self.assertTrue(any(f["id"] == "logo.dark-swap" for f in fs))

    def test_light_part_is_drawn_in_ink_on_light_grounds(self):
        # Fernhill C: a lichen primary block (1.77:1 on white) is the dark-ground idea; on light grounds the block is
        # drawn in the dark ink and the wordmark keeps its colour, no gate
        pal = example_palette()
        pal["modes"]["light"].update(primary="#bccb5c", onPrimary=pal["modes"]["light"]["text"])
        fs = self.run_audit(CLEAN, palette=pal)
        self.assertEqual({f["message"] for f in fs if f["id"] == "logo.contrast" and f["severity"] == "gate"}, set())
        with open(os.path.join(self.tmp.name, "sets", "A", "logo", "build", "manifest.json"), encoding="utf-8") as fh:
            man = json.load(fh)
        ink = pal["modes"]["light"]["text"]
        swapped = {s["version"] for s in man["light_swaps"]}
        self.assertIn("symbol-only", swapped)      # judged by the whole logo: its wordmark keeps its colour
        for name in ("full-color", "on-light-background", "symbol-only"):
            parts = man["versions"][name]["parts"]
            main = max(parts, key=lambda p: p["area"])
            primary = {p["hex"] for p in parts if p["role"] == "primary"}
            if main["role"] == "primary":           # the part the gate judges is drawn in ink
                self.assertEqual(primary, {ink}, name)
                self.assertIn(name, swapped)
            else:                                   # a smaller weak part keeps its colour (a warn, not a gate)
                self.assertEqual(primary, {"#bccb5c"}, name)
        for px in (16, 32, 48):                     # favicons on a light tab: the same rule, no device
            fav = {p["hex"] for p in man["versions"][f"favicon-{px}"]["parts"] if p["role"] == "primary"}
            self.assertEqual(fav, {ink}, px)
            self.assertIn(f"favicon-{px}", swapped)
        dark = {p["role"]: p["hex"] for p in man["versions"]["full-color-dark"]["parts"]}
        self.assertEqual(dark["primary"], pal["modes"]["dark"]["primary"])     # the dark versions keep the colour
        self.assertTrue(any(f["id"] == "logo.light-swap" for f in fs))

    def test_all_parts_weak_are_drawn_in_ink_with_a_warn(self):
        # Kovan A, Ridgeline A: a one-colour mark in a dark brand ink gated on the dark ground and agents recoloured the
        # design to pass. The mark is drawn in the ground's ink like any reversed logo; the audit warns that no brand
        # colour is left in that version instead of blocking the build
        pal = example_palette()
        pal["modes"]["light"].update(primary="#bccb5c", onPrimary=pal["modes"]["light"]["text"])

        def mutate(ident):
            ident["logo"]["colors"] = {"symbol": "primary", "wordmark": "primary"}
        fs = self.run_audit(CLEAN, palette=pal, mutate=mutate)
        self.assertFalse([f for f in fs if f["id"] == "logo.contrast" and f["severity"] == "gate"
                          and f["message"].startswith(("full-color:", "on-light-background:"))])
        with open(os.path.join(self.tmp.name, "sets", "A", "logo", "build", "manifest.json"), encoding="utf-8") as fh:
            man = json.load(fh)
        ink = pal["modes"]["light"]["text"]
        self.assertEqual({p["hex"] for p in man["versions"]["full-color"]["parts"]}, {ink})
        lost = [f for f in fs if f["id"] == "logo.light-swap" and f["severity"] == "warn"]
        self.assertTrue(lost and "no brand colour is left" in lost[0]["message"])

    def test_clean_mark_has_no_gates(self):
        fs = self.run_audit(CLEAN)
        self.assertEqual(self.ids(fs, "gate"), set(), [f["message"] for f in fs])
        for f in fs:
            self.assertEqual(f["component"], "logo")
            self.assertIn(f["severity"], ("gate", "warn", "info"))

    def test_text_element_is_a_gate(self):
        fs = self.run_audit(CLEAN.replace("</svg>", '<text x="10" y="90">MV</text></svg>'))
        self.assertIn("logo.forbidden-element", self.ids(fs, "gate"))

    def test_image_and_filter_are_gates(self):
        fs = self.run_audit(CLEAN.replace('data-color="primary"/>', 'data-color="primary" filter="url(#f)"/>')
                            .replace("</svg>", '<image href="x.png" width="10" height="10"/></svg>'))
        msgs = " ".join(f["message"] for f in fs if f["id"] == "logo.forbidden-element")
        self.assertIn("<image>", msgs)
        self.assertIn("<filter>", msgs)

    def test_missing_viewbox_is_a_gate(self):
        fs = self.run_audit(CLEAN.replace('viewBox="0 0 100 100"', 'width="100" height="100"'))
        self.assertIn("logo.viewbox", self.ids(fs, "gate"))

    def test_false_hole_is_a_gate_and_real_hole_is_not(self):
        fake = ('<svg viewBox="0 0 100 100"><circle id="ring" cx="50" cy="50" r="40" data-color="primary"/>'
                '<circle id="fake-hole" cx="50" cy="50" r="20" data-color="background"/></svg>')
        self.assertIn("logo.false-hole", self.ids(self.run_audit(fake), "gate"))
        white = fake.replace('data-color="background"', 'fill="#ffffff"')
        gates = self.ids(self.run_audit(white), "gate")
        self.assertIn("logo.false-hole", gates)
        self.assertIn("logo.color-role", gates)
        stroke = ('<svg viewBox="0 0 100 100"><circle id="disc" cx="50" cy="50" r="40" data-color="primary"/>'
                  '<line id="fake-cut" x1="0" y1="50" x2="100" y2="50" stroke="white" stroke-width="6"/></svg>')
        self.assertIn("logo.false-hole", self.ids(self.run_audit(stroke), "gate"))
        real = fake.replace('data-color="background"', 'data-op="subtract"')
        self.assertNotIn("logo.false-hole", self.ids(self.run_audit(real)))

    def test_dark_ink_detail_is_not_a_false_hole(self):
        # Petal A, Tomo A: a text-ink detail over a coloured part was called ground-coloured because its light-mode
        # hex (#111e24) was compared with the dark grounds (dark surface is #111e24); each mode uses its own colour
        detail = ('<svg viewBox="0 0 100 100"><circle id="disc" cx="50" cy="50" r="40" data-color="primary"/>'
                  '<circle id="eye" cx="50" cy="50" r="10" data-color="text"/></svg>')
        self.assertNotIn("logo.false-hole", self.ids(self.run_audit(detail), "gate"))

    def test_raw_colour_and_unknown_role(self):
        fs = self.run_audit(CLEAN.replace('data-color="primary"', 'fill="#1f5f7a"'))
        self.assertIn("logo.color-role", self.ids(fs, "gate"))
        fs = self.run_audit(CLEAN.replace('data-color="primary"', 'data-color="sparkle"'))
        self.assertIn("logo.color-role", self.ids(fs, "gate"))
        fs = self.run_audit(CLEAN.replace('data-color="primary"', 'data-color="brand-2"'))
        self.assertNotIn("logo.color-role", self.ids(fs))

    def test_low_contrast_main_part_is_drawn_in_ink_or_tiled(self):
        pal = example_palette()
        pal["modes"]["light"]["primary"] = "#f2f4f5"
        pal["modes"]["light"]["onPrimary"] = "#111e24"   # keep the palette itself consistent

        def all_primary(ident):   # no part reads on white: the light versions are drawn in ink, the swap warns
            ident["logo"]["colors"] = {"symbol": "primary", "wordmark": "primary"}
        fs = self.run_audit(CLEAN, palette=pal, mutate=all_primary)
        self.assertTrue([f for f in fs if f["id"] == "logo.light-swap" and f["severity"] == "warn"])
        self.assertFalse([f for f in fs if f["id"] == "logo.contrast" and f["severity"] == "gate"
                          and f["message"].startswith("full-color:")])

        def tile(ident):   # the symbol on a tile; the wordmark in the text colour
            ident["logo"]["device"] = "tile"
        fs = self.run_audit(CLEAN, palette=pal, mutate=tile)
        self.assertFalse([f for f in fs if f["id"] == "logo.contrast" and f["severity"] == "gate"])

    def test_thin_feature_warn_and_small_variant(self):
        hairline = ('<svg viewBox="0 0 100 100"><rect id="left" x="10" y="10" width="30" height="80"/>'
                    '<rect id="right" x="60" y="10" width="30" height="80"/>'
                    '<rect id="hair" x="40" y="48" width="20" height="2"/></svg>')
        fs = self.run_audit(hairline)
        self.assertIn("logo.thin-feature", self.ids(fs, "warn"))
        self.assertNotIn("logo.thin-feature", self.ids(fs, "gate"))   # uncalibrated threshold: warn only
        small = ('<svg viewBox="0 0 100 100"><rect id="block" x="10" y="10" width="80" height="80"/></svg>')
        fs = self.run_audit(hairline, small=small)
        self.assertNotIn("logo.thin-feature", self.ids(fs, "gate"))
        self.assertIn("logo.thin-feature", self.ids(fs, "info"))

    def test_thin_features_measure(self):
        bar = L.parse_d("M0 45H100V55H0Z").path(close_all=True)    # 10 units = 2.4 px at 24 px
        self.assertLess(A.thin_features(bar, 24)["stroke_px2"], A.THIN_AREA_PX2)  # only corner rounding
        hair = L.parse_d("M0 49H100V51H0Z").path(close_all=True)   # 2 units = 0.48 px
        self.assertGreater(A.thin_features(hair, 24)["stroke_px2"], A.THIN_AREA_PX2)
        gap = L.parse_d("M0 0H100V49H0Z M0 51H100V100H0Z").path(close_all=True)
        self.assertGreater(A.thin_features(gap, 24)["gap_px2"], A.THIN_AREA_PX2)

    def test_font_licence(self):
        def commercial(ident):
            ident["type"]["display"]["license"] = "commercial"
        self.assertIn("logo.font-license", self.ids(self.run_audit(CLEAN, mutate=commercial), "gate"))

        def permitted(ident):
            ident["type"]["display"]["license"] = "commercial"
            ident["logo"]["font_permission"] = "written permission from the foundry, 2026-09-30"
        self.assertNotIn("logo.font-license", self.ids(self.run_audit(CLEAN, mutate=permitted)))

    def test_cliche_flag_and_justification(self):
        import math
        pts = " ".join(f"{50 + 40 * math.cos(math.radians(60 * i)):.3f},{50 + 40 * math.sin(math.radians(60 * i)):.3f}"
                       for i in range(6))
        hexagon = f'<svg viewBox="0 0 100 100"><polygon id="hex" points="{pts}"/></svg>'
        fs = self.run_audit(hexagon)
        self.assertIn("logo.cliche", self.ids(fs, "warn"))

        def justified(ident):
            ident["defaults_used"] = [{"id": "hexagon", "why": "the brand is named Hexwell; the user asked for it"}]
        fs = self.run_audit(hexagon, mutate=justified)
        self.assertNotIn("logo.cliche", self.ids(fs, "warn"))
        self.assertIn("logo.cliche", self.ids(fs, "info"))

        def empty_why(ident):
            ident["defaults_used"] = [{"id": "hexagon", "why": ""}]
        self.assertIn("logo.cliche", self.ids(self.run_audit(hexagon, mutate=empty_why), "gate"))

    def test_spark_detector(self):
        import math
        pts = " ".join(f"{50 + (40 if i % 2 == 0 else 12) * math.cos(math.radians(45 * i)):.3f},"
                       f"{50 + (40 if i % 2 == 0 else 12) * math.sin(math.radians(45 * i)):.3f}" for i in range(8))
        rep = L.inspect_symbol(f'<svg viewBox="0 0 100 100"><polygon id="spark" points="{pts}"/></svg>')
        self.assertIn("spark", [t for t, _e in A.cliches(rep)])

    def test_near_equal_and_fill_rule_warnings(self):
        near = ('<svg viewBox="0 0 100 100"><circle id="a" cx="30" cy="50" r="20"/>'
                '<circle id="b" cx="72" cy="50" r="20.5"/></svg>')
        self.assertIn("logo.near-equal", self.ids(self.run_audit(near), "warn"))
        same = near.replace('r="20.5"', 'r="20"')
        self.assertNotIn("logo.near-equal", self.ids(self.run_audit(same)))
        ambiguous = '<svg viewBox="0 0 100 100"><path id="frame" d="M10 10H90V90H10Z M30 30H70V70H30Z"/></svg>'
        self.assertIn("logo.fill-rule", self.ids(self.run_audit(ambiguous), "warn"))

    def test_device_tile_and_outline_are_drawn_and_verified(self):
        pal = example_palette()
        pal["modes"]["light"]["primary"] = "#f2f4f5"   # symbol nearly invisible on the light background
        pal["modes"]["light"]["onPrimary"] = "#111e24"
        for kind in ("tile", "outline"):
            def dev(ident, kind=kind):
                ident["logo"]["device"] = kind
            fs = self.run_audit(CLEAN, palette=pal, mutate=dev)
            self.assertFalse([f for f in fs if f["id"] == "logo.contrast" and f["severity"] == "gate"], kind)
            self.assertTrue([f for f in fs if f["id"] == "logo.contrast" and f["severity"] == "info"], kind)
            build = os.path.join(self.tmp.name, "sets", "A", "logo", "build")
            with open(os.path.join(build, "full-color.svg"), encoding="utf-8") as fh:
                svg = fh.read()
            self.assertIn(f'id="device-{kind}"', svg)
            man = json.load(open(os.path.join(build, "manifest.json"), encoding="utf-8"))
            self.assertEqual(man["versions"]["full-color"]["device"]["kind"], kind)
            self.assertIsNone(man["versions"]["on-light-primary"]["device"])  # enough contrast there: no device
            # a declared device that is not in the file does not excuse the contrast
            with open(os.path.join(build, "full-color.svg"), "w", encoding="utf-8") as fh:
                fh.write(svg.replace(f'id="device-{kind}"', 'id="something-else"'))
            ident = example_identity()
            ident["logo"]["device"] = kind
            fs = A.audit(ident, pal, build)
            bad = [f for f in fs if f["id"] == "logo.contrast" and f["severity"] in ("gate", "warn")
                   and f["message"].startswith("full-color:")]
            self.assertTrue(bad, kind)                       # the symbol is no longer carried: back to warn/gate
            self.assertIn("declared but no", bad[0]["message"])

    def test_wordmark_only_needs_a_small_mark(self):
        self.assertEqual(L.favicon_letter("Domaine Fauvel"), "F")
        self.assertEqual(L.favicon_letter("The Studio Nine"), "N")
        self.assertEqual(L.favicon_letter("Moonvault"), "M")
        self.assertEqual(L.favicon_letter("The Studio"), "T")

        def wordmark_only(ident):
            ident["logo"].update({"type": "wordmark", "symbol": None, "monogram": None})
            ident["logo"]["wordmark"]["text"] = "Domaine Fauvel"
        fs = self.run_audit(CLEAN, mutate=wordmark_only)
        warn = [f for f in fs if f["id"] == "logo.small-mark-missing"]
        self.assertEqual(len(warn), 1)
        self.assertEqual(warn[0]["severity"], "warn")
        self.assertIn("'F'", warn[0]["message"])
        self.assertIn("symbol_small_svg or monogram", warn[0]["message"])
        build = os.path.join(self.tmp.name, "sets", "A", "logo", "build")
        man = json.load(open(os.path.join(build, "manifest.json"), encoding="utf-8"))
        self.assertTrue(man["favicon_fallback"])
        self.assertEqual(man["favicon_source"], "fallback-letter")
        self.assertTrue(os.path.isfile(os.path.join(build, "favicon-16.png")))   # the fallback is still built

        def with_monogram(ident):
            wordmark_only(ident)
            ident["logo"]["monogram"] = {"letters": "DF", "role": "display"}
        fs = self.run_audit(CLEAN, mutate=with_monogram)
        self.assertNotIn("logo.small-mark-missing", self.ids(fs))
        man = json.load(open(os.path.join(build, "manifest.json"), encoding="utf-8"))
        self.assertEqual(man["favicon_source"], "monogram")
        small = '<svg viewBox="0 0 100 100"><rect id="block" x="20" y="20" width="60" height="60"/></svg>'
        fs = self.run_audit(CLEAN, small=small, mutate=wordmark_only)
        self.assertNotIn("logo.small-mark-missing", self.ids(fs))
        self.assertNotIn("logo.small-mark-missing", self.ids(self.run_audit(CLEAN)))   # sets with a symbol

    def test_wordmark_detail_is_built_and_checked(self):
        set_dir = os.path.join(self.tmp.name, "sets", "A")
        os.makedirs(os.path.join(set_dir, "logo"), exist_ok=True)
        with open(os.path.join(set_dir, "logo", "wordmark-detail.svg"), "w", encoding="utf-8") as fh:
            fh.write('<svg><rect id="cut" x="-50" y="45" width="2000" height="10" data-op="subtract"/></svg>')
        fs = self.run_audit(CLEAN)
        self.assertEqual(self.ids(fs, "gate"), set())
        build = os.path.join(set_dir, "logo", "build")
        with open(os.path.join(build, "master-wordmark.svg"), encoding="utf-8") as fh:
            self.assertIn('data-detail="1"', fh.read())
        with open(os.path.join(set_dir, "logo", "wordmark-detail.svg"), "w", encoding="utf-8") as fh:
            fh.write('<svg><text x="0" y="90">x</text></svg>')
        with self.assertRaises(L.LogoError):
            self.run_audit(CLEAN)

    def test_near_equal_ignores_rounding_noise(self):
        self.assertEqual(A._near_equal([("a", 158.58), ("b", 158.57)], deg=A.NEAR_EQUAL_DEG), [])
        self.assertEqual(len(A._near_equal([("a", 158.5), ("b", 157.5)], deg=A.NEAR_EQUAL_DEG)), 1)
        self.assertEqual(A._near_equal([("a", 20.0), ("b", 20.04)], rel=A.NEAR_EQUAL_REL), [])
        self.assertEqual(len(A._near_equal([("a", 20.0), ("b", 20.5)], rel=A.NEAR_EQUAL_REL)), 1)

    def test_thin_feature_fix_suggests_small_symbol_first(self):
        hairline = ('<svg viewBox="0 0 100 100"><rect id="left" x="10" y="10" width="30" height="80"/>'
                    '<rect id="right" x="60" y="10" width="30" height="80"/>'
                    '<rect id="hair" x="40" y="48" width="20" height="2"/></svg>')
        f = [f for f in self.run_audit(hairline) if f["id"] == "logo.thin-feature"][0]
        self.assertTrue(f["suggested_fix"].startswith("add symbol_small_svg for small sizes, or raise"))

    def test_glyph_tables_are_written(self):
        small = ('<svg viewBox="0 0 100 100"><path id="initial" data-glyphs="Hi" data-cap="60" data-x="50" '
                 'data-baseline="80" data-color="primary"/></svg>')
        self.run_audit(CLEAN, small=small)
        build = os.path.join(self.tmp.name, "sets", "A", "logo", "build")
        wg = json.load(open(os.path.join(build, "wordmark-glyphs.json"), encoding="utf-8"))
        self.assertEqual([g["char"] for g in wg["glyphs"]], list("Moonvault"))
        self.assertEqual(wg["glyphs"][0]["index"], 1)
        g1, g2 = wg["glyphs"][0], wg["glyphs"][1]
        self.assertLess(g1["x0"], g1["x1"])
        self.assertLessEqual(g1["x1"], g2["x0"] + 1)        # letters in order along x
        self.assertTrue(g1["stems"])                          # M has vertical stems
        self.assertEqual((g1["cap_top"], g1["baseline"]), (0, 100))
        sg = json.load(open(os.path.join(build, "small-glyphs.json"), encoding="utf-8"))
        tab = sg["symbol-small"][0]
        self.assertEqual(tab["id"], "initial")
        self.assertEqual([g["char"] for g in tab["glyphs"]], ["H", "i"])
        self.assertAlmostEqual(tab["glyphs"][0]["baseline"], 80, delta=0.01)
        self.assertAlmostEqual(tab["glyphs"][0]["cap_top"], 20, delta=0.01)

    def test_coloured_wordmark_detail_stays_a_separate_part(self):
        set_dir = os.path.join(self.tmp.name, "sets", "A")
        os.makedirs(os.path.join(set_dir, "logo"), exist_ok=True)
        with open(os.path.join(set_dir, "logo", "wordmark-detail.svg"), "w", encoding="utf-8") as fh:
            fh.write('<svg><circle id="leaf" cx="20" cy="-20" r="12" data-color="accent"/></svg>')
        self.run_audit(CLEAN)
        build = os.path.join(set_dir, "logo", "build")
        man = json.load(open(os.path.join(build, "manifest.json"), encoding="utf-8"))
        roles = {p["role"]: p["hex"] for p in man["versions"]["full-color"]["parts"]}
        self.assertEqual(roles["accent"], example_palette()["modes"]["light"]["accent"])
        self.assertIn("text", roles)
        with open(os.path.join(build, "master-lockup-horizontal.svg"), encoding="utf-8") as fh:
            self.assertIn('data-color="accent" data-parts="wordmark-detail-leaf"', fh.read())

    def test_app_icon_svg_and_safe_area(self):
        set_dir = os.path.join(self.tmp.name, "sets", "A")
        os.makedirs(os.path.join(set_dir, "logo"), exist_ok=True)
        icon = os.path.join(set_dir, "logo", "app-icon.svg")
        with open(icon, "w", encoding="utf-8") as fh:
            fh.write('<svg viewBox="0 0 512 512"><rect id="tile" width="512" height="512" data-color="primary"/>'
                     '<circle id="mark" cx="256" cy="256" r="150" data-color="onPrimary"/></svg>')
        fs = self.run_audit(CLEAN)
        build = os.path.join(set_dir, "logo", "build")
        man = json.load(open(os.path.join(build, "manifest.json"), encoding="utf-8"))
        self.assertEqual(man["app_icon"]["source"], "app-icon.svg")
        self.assertEqual(man["versions"]["app-icon-512"]["size"], [512, 512])
        self.assertNotIn("logo.app-icon-safe-area", self.ids(fs))
        with open(icon, "w", encoding="utf-8") as fh:
            fh.write('<svg viewBox="0 0 512 512"><rect id="tile" width="512" height="512" data-color="primary"/>'
                     '<circle id="mark" cx="256" cy="256" r="250" data-color="onPrimary"/></svg>')
        self.assertIn("logo.app-icon-safe-area", self.ids(self.run_audit(CLEAN), "warn"))

    def test_segment_lightness_only_between_touching_symbol_parts(self):
        pal = example_palette()
        pal["modes"]["light"]["accent"] = pal["modes"]["light"]["text"]     # same L as the wordmark colour
        pal["modes"]["light"]["link"] = "#1f6f7a"                             # nearly the primary's L, other hue
        apart = ('<svg viewBox="0 0 100 100"><circle id="a" cx="25" cy="50" r="20" data-color="primary"/>'
                 '<circle id="b" cx="75" cy="50" r="20" data-color="accent"/></svg>')
        self.assertNotIn("logo.segment-lightness", self.ids(self.run_audit(apart, palette=pal)))
        touching = ('<svg viewBox="0 0 100 100"><circle id="a" cx="35" cy="50" r="30" data-color="primary"/>'
                    '<circle id="b" cx="65" cy="50" r="30" data-color="link"/></svg>')
        self.assertIn("logo.segment-lightness", self.ids(self.run_audit(touching, palette=pal), "warn"))

    def test_role_tone_info(self):
        pal = example_palette()
        self.assertNotIn("logo.role-tone", self.ids(self.run_audit(CLEAN, palette=pal)))  # primary == brand-1
        pal["modes"]["light"]["primary"] = "#1a4f66"
        fs = [f for f in self.run_audit(CLEAN, palette=pal) if f["id"] == "logo.role-tone"]
        self.assertEqual(fs[0]["severity"], "info")
        self.assertEqual(fs[0]["message"], "primary resolves to #1a4f66 (UI tone); use brand-1 for #1f5f7a")

    def test_kept_logo_is_shown_and_measured_not_rebuilt(self):
        work = self.tmp.name
        set_dir = os.path.join(work, "sets", "A")
        os.makedirs(os.path.join(work, "site"))
        os.makedirs(set_dir)
        src = os.path.join(work, "site", "logo.svg")
        body = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                '<rect width="10" height="10" fill="#1f5f7a"/></svg>')
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(body)
        ident = example_identity()
        ident["components"]["logo"] = {"mode": "keep", "status": "fixed", "source": "site/logo.svg", "sha256": None}
        rec = {"id": "A", "set_dir": set_dir, "identity": ident, "palette": example_palette(), "errors": []}
        man, fs = A.build_set(rec)
        self.assertTrue(man["kept"])
        self.assertEqual(man["dark_from"], "derived one-colour white")
        with open(man["dark"], encoding="utf-8") as fh:
            self.assertIn(example_palette()["modes"]["dark"]["text"], fh.read())   # one light colour
        with open(src, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), body)                 # the kept source is never written
        self.assertIn("logo.kept", self.ids(fs, "info"))
        self.assertGreaterEqual(man["measured"]["kept-dark"]["best_contrast"], 3)
        # the source's own dark-mode rules win over a derived white
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(body.replace("<rect", "<style>@media (prefers-color-scheme: dark){rect{fill:#5da6c8}}</style>"
                                           "<rect"))
        man, _fs = A.build_set(rec)
        self.assertEqual(man["dark_from"], "prefers-color-scheme rules in the source")
        self.assertIn("#5da6c8", man["measured"]["kept-dark"]["colours"])
        # a logo-dark.svg next to it wins over both
        with open(os.path.join(work, "site", "logo-dark.svg"), "w", encoding="utf-8") as fh:
            fh.write(body.replace("#1f5f7a", "#f1f3f5"))
        man, _fs = A.build_set(rec)
        self.assertEqual(man["dark_from"], "logo-dark.svg")

    def test_glyph_weight_override_through_the_logos_path(self):
        # the face resolves to a static instance (typelib), so data-wght needs the set's resolver everywhere
        small = ('<svg viewBox="0 0 100 100"><path id="initial" data-glyphs="M" data-wght="800" data-cap="60" '
                 'data-x="50" data-baseline="80" data-color="primary"/></svg>')
        set_dir = os.path.join(self.tmp.name, "sets", "A")
        os.makedirs(os.path.join(set_dir, "logo"), exist_ok=True)
        with open(os.path.join(set_dir, "logo", "symbol.svg"), "w", encoding="utf-8") as fh:
            fh.write(CLEAN)
        with open(os.path.join(set_dir, "logo", "symbol-small.svg"), "w", encoding="utf-8") as fh:
            fh.write(small)
        ident = example_identity()
        man, fs = A.build_set({"id": "A", "set_dir": set_dir, "identity": ident, "palette": example_palette(),
                               "errors": []})
        self.assertIsNotNone(man)
        self.assertNotIn("logo.symbol-invalid", self.ids(fs))
        self.assertEqual(self.ids(fs, "gate"), set(), [f["message"] for f in fs if f["severity"] == "gate"])
        heavy = A.logolib._union([p for _r, _i, p in A.logolib.parse_parts(
            open(os.path.join(set_dir, "logo", "build", "master-symbol-small.svg"), encoding="utf-8").read())[2]])
        light = A.logolib._union([p for _r, _i, p in A.logolib.parse_parts(
            A.logolib.resolve_symbol(small.replace(' data-wght="800"', ""), OUTFIT, {"wght": 600}))[2]])
        self.assertGreater(abs(heavy.area), abs(light.area) * 1.1)

    def test_kept_logo_variants_are_recoloured_for_every_ground(self):
        work = self.tmp.name
        set_dir = os.path.join(work, "sets", "A")
        os.makedirs(os.path.join(work, "site"))
        os.makedirs(set_dir)
        pal = example_palette()
        teal = pal["modes"]["light"]["primary"]
        body = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 100"><style>.mark{fill:%s}'
                '.word{fill:#111e24}</style><rect width="400" height="100" fill="#ffffff"/>'
                '<g id="logomark"><circle class="mark" cx="50" cy="50" r="40"/></g>'
                '<g id="wordmark"><rect class="word" x="120" y="30" width="260" height="40"/></g></svg>') % teal
        src = os.path.join(work, "site", "logo.svg")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(body)
        ident = example_identity()
        ident["components"]["logo"] = {"mode": "keep", "status": "fixed", "source": "site/logo.svg", "sha256": None}
        build = os.path.join(set_dir, "logo", "build")
        man = L.variants(ident, pal, build)
        self.assertTrue(man["kept"])
        for name in ("full-color", "full-color-dark", "one-color-dark", "one-color-light", "on-light-background",
                     "on-dark-background", "on-light-primary", "favicon-16", "favicon-32", "app-icon-512",
                     "symbol-only"):
            for ext in ("svg", "png"):
                self.assertTrue(os.path.isfile(os.path.join(build, man["versions"][name][ext])), (name, ext))
        import colorlib
        for name, v in man["versions"].items():
            if name.startswith("on-"):
                for p in v["parts"]:
                    self.assertGreaterEqual(colorlib.contrast_ratio(p["hex"], v["ground"]), 3.0, (name, p))
        full = {p["hex"] for p in man["versions"]["full-color"]["parts"]}
        self.assertEqual(full, {teal, "#111e24"})                       # source colours, background rect dropped
        self.assertEqual(len({p["hex"] for p in man["versions"]["one-color-dark"]["parts"]}), 1)
        self.assertEqual(man["icon_from"], "kept symbol parts")         # id="logomark" picked as the symbol
        with open(os.path.join(build, "master-primary.svg"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), body)
        with open(src, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), body)                            # the source is never written

    def test_kept_dark_file_with_another_aspect_is_skipped(self):
        work = self.tmp.name
        set_dir = os.path.join(work, "sets", "A")
        os.makedirs(os.path.join(work, "site"))
        os.makedirs(set_dir)
        wide = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1319 256">'
                '<rect width="1319" height="256" fill="#1f5f7a"/></svg>')
        with open(os.path.join(work, "site", "logo.svg"), "w", encoding="utf-8") as fh:
            fh.write(wide)
        with open(os.path.join(work, "site", "logo-dark.svg"), "w", encoding="utf-8") as fh:
            fh.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" '
                     'fill="#f1f3f5"/></svg>')
        ident = example_identity()
        ident["components"]["logo"] = {"mode": "keep", "status": "fixed", "source": "site/logo.svg", "sha256": None}
        man, _fs = A.build_set({"id": "A", "set_dir": set_dir, "identity": ident, "palette": example_palette(),
                                "errors": []})
        self.assertEqual(man["dark_from"], "derived one-colour white")
        self.assertTrue(man["dark_skipped"])
        with open(os.path.join(work, "site", "logo-dark.svg"), "w", encoding="utf-8") as fh:
            fh.write(wide.replace("#1f5f7a", "#f1f3f5"))
        man, _fs = A.build_set({"id": "A", "set_dir": set_dir, "identity": ident, "palette": example_palette(),
                                "errors": []})
        self.assertEqual(man["dark_from"], "logo-dark.svg")

    def test_missing_build_is_a_gate(self):
        fs = A.audit(example_identity(), example_palette(), os.path.join(self.tmp.name, "nowhere"))
        self.assertEqual(self.ids(fs, "gate"), {"logo.build-missing"})


class CliLogos(unittest.TestCase):
    def test_sets_rebuild_keeps_every_set_on_the_sheet(self):
        # Fernhill: `logos --sets A` must not leave a contact sheet with set A only
        from unittest import mock
        import render_png
        px = L.DELIVERY_PX
        L.DELIVERY_PX = 96
        self.addCleanup(setattr, L, "DELIVERY_PX", px)
        sheets = []

        def fake_shot(html_path, out_png, **_kw):
            with open(html_path, encoding="utf-8") as fh:
                sheets.append(fh.read())
            with open(out_png, "wb") as fh:
                fh.write(b"png")
        with tempfile.TemporaryDirectory() as work, mock.patch.object(render_png, "screenshot_html", fake_shot):
            ident = example_identity()
            drafts = []
            for sid, name in (("A", "Alpha Route"), ("B", "Beta Route")):
                os.makedirs(os.path.join(work, "sets", sid))
                base = copy.deepcopy(ident)
                base["set"].update(id=sid, name=name)
                with open(os.path.join(work, "sets", sid, "identity.json"), "w", encoding="utf-8") as fh:
                    json.dump(base, fh)
                di = {k: copy.deepcopy(v) for k, v in base.items()
                      if k not in ("schema", "brand", "components", "derived", "audit")}
                drafts.append({"identity": di, "palette": example_palette(), "symbol_svg": CLEAN})
            with open(os.path.join(work, "sets.json"), "w", encoding="utf-8") as fh:
                json.dump({"schema": "brand-identity/sets@1", "sets": drafts}, fh)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(A.cli_logos(types.SimpleNamespace(work=work, sets=None, full=False)), 0)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(A.cli_logos(types.SimpleNamespace(work=work, sets="a", full=False)), 0)
        self.assertNotIn("B Beta Route", out.getvalue())          # only A was rebuilt
        self.assertEqual(len(sheets), 2)
        for html in sheets:
            self.assertIn("Alpha Route", html)
            self.assertIn("Beta Route", html)                       # B from its last build

    def test_end_to_end_contact_sheet(self):
        import render_png
        if not render_png.find_browser():
            self.skipTest("no Chromium-based browser for the contact sheet")
        px = L.DELIVERY_PX
        L.DELIVERY_PX = 96
        self.addCleanup(setattr, L, "DELIVERY_PX", px)
        with tempfile.TemporaryDirectory() as work:
            ident = example_identity()
            draft_ident = {k: copy.deepcopy(v) for k, v in ident.items()
                           if k not in ("schema", "brand", "components", "derived", "audit")}
            draft_ident["rationale"] = [{"claim": "x", "evidence": "bogus-evidence", "source": "y"}]
            draft_ident["logo"]["ideas"] = ["A wheel with one spoke.", "A ledger rule as horizon."]
            second = copy.deepcopy(draft_ident)
            second["rationale"] = []
            second["set"]["id"] = "B"
            for sid in ("A", "B"):
                os.makedirs(os.path.join(work, "sets", sid))
                base = copy.deepcopy(ident)
                base["set"]["id"] = sid
                with open(os.path.join(work, "sets", sid, "identity.json"), "w", encoding="utf-8") as fh:
                    json.dump(base, fh)
            draft = {"schema": "brand-identity/sets@1", "sets": [
                {"identity": draft_ident, "palette": example_palette(), "symbol_svg": CLEAN,
                 "symbol_small_svg": '<svg viewBox="0 0 100 100"><path id="m" data-glyphs="M" data-wght="800" '
                                     'data-color="primary"/></svg>'},
                {"identity": second, "palette": example_palette(),
                 "symbol_svg": CLEAN.replace("</svg>", "<text>X</text></svg>"), "symbol_small_svg": None}]}
            with open(os.path.join(work, "sets.json"), "w", encoding="utf-8") as fh:
                json.dump(draft, fh)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = A.cli_logos(types.SimpleNamespace(work=work, sets=None, full=False))
            text = out.getvalue()
            self.assertEqual(code, 1)                       # set B has a gate
            self.assertLessEqual(len(text.encode("utf-8")), 1200)
            self.assertIn("sheet:", text)
            self.assertIn("GATE logo.forbidden-element", text)
            self.assertIn("warn sync: identity:", text)              # the sync warning says what it is
            self.assertIn("rationale[0].evidence", text)
            self.assertNotIn("variable font", text)                   # data-wght resolved through the set's face
            with open(os.path.join(work, ".cache", "logo-sheet.html"), encoding="utf-8") as fh:
                self.assertIn("A ledger rule as horizon.", fh.read())   # logo.ideas under the sheet
            sheet = os.path.join(work, A.SHEET_NAME)
            self.assertTrue(os.path.isfile(sheet), text)
            with open(sheet, "rb") as fh:
                head = fh.read(24)
            import struct
            w, h = struct.unpack(">II", head[16:24])
            self.assertLessEqual(max(w, h), 1600)
            self.assertTrue(os.path.isfile(os.path.join(work, "sets", "A", "logo", "build", "audit.json")))


if __name__ == "__main__":
    unittest.main()
