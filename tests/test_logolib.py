"""Unit tests for skills/brand-identity/scripts/logolib.py (stdlib unittest; fixture fonts in tests/fixtures/fonts)."""
import contextlib
import copy
import hashlib
import io
import json
import os
import re
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
TEMPLATES = os.path.join(ROOT, "skills", "brand-identity", "templates")
FONTS = os.path.join(ROOT, "tests", "fixtures", "fonts")
OUTFIT = os.path.join(FONTS, "Outfit[wght].ttf")
ARVO = os.path.join(FONTS, "Arvo-Regular.ttf")
sys.path.insert(0, SCRIPTS)

import logolib as L  # noqa: E402


def sha_of(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def advance(svg):
    return float(re.search(r'data-advance="([^"]+)"', svg).group(1))


def ink(svg):
    _vb, _a, parts = L.parse_parts(svg)
    return L._union_bounds([L._bounds(p) for _r, _i, p in parts])


def resolved_path(svg):
    _vb, _a, parts = L.parse_parts(svg)
    return L._union([p for _r, _i, p in parts])


def example_identity():
    with open(os.path.join(TEMPLATES, "identity.example.json"), encoding="utf-8") as fh:
        ident = json.load(fh)
    disp = ident["type"]["display"]
    disp.update({"family": "Outfit", "source": "user", "file": OUTFIT, "location": {"wght": 600}, "weights": [600]})
    return ident


def example_palette():
    with open(os.path.join(TEMPLATES, "palette.example.json"), encoding="utf-8") as fh:
        return json.load(fh)


DISC_WITH_HOLE = ('<svg viewBox="0 0 100 100"><circle id="disc" cx="50" cy="50" r="40" data-color="primary"/>'
                  '<circle id="hole" cx="50" cy="50" r="15" data-op="subtract"/></svg>')


class Wordmark(unittest.TestCase):
    def test_cap_height_is_100_units(self):
        svg = L.wordmark_svg("HH", OUTFIT, {"wght": 600})
        self.assertIn('data-cap-height="100"', svg)
        self.assertIn('data-baseline="100"', svg)
        box = ink(svg)
        self.assertAlmostEqual(box[1], 0.0, delta=0.5)      # cap top
        self.assertAlmostEqual(box[3], 100.0, delta=0.5)    # baseline
        box = ink(L.wordmark_svg("HH", ARVO, {}))
        self.assertAlmostEqual(box[3] - box[1], 100.0, delta=0.5)

    def test_outlines_not_live_text(self):
        svg = L.wordmark_svg("Moonvault", OUTFIT, {"wght": 600}, tracking=-10)
        self.assertNotIn("<text", svg)
        self.assertIn('<g id="wordmark"', svg)
        self.assertNotIn("stroke", svg)

    def test_kerning_on_and_ligatures_off_unless_listed(self):
        self.assertLess(advance(L.wordmark_svg("AV", OUTFIT, {"wght": 600})),
                        advance(L.wordmark_svg("AV", OUTFIT, {"wght": 600}, ["-kern"])))
        plain = advance(L.wordmark_svg("ffi", OUTFIT, {"wght": 600}))
        liga = advance(L.wordmark_svg("ffi", OUTFIT, {"wght": 600}, ["liga"]))
        self.assertNotEqual(plain, liga)  # the font has the ligature; it is used only when listed

    def test_tracking_in_thousandths_of_an_em(self):
        a = advance(L.wordmark_svg("HHH", OUTFIT, {"wght": 600}, tracking=0))
        b = advance(L.wordmark_svg("HHH", OUTFIT, {"wght": 600}, tracking=100))
        em_units = 1000 * 100.0 / L._font(OUTFIT, {"wght": 600})["cap"]
        self.assertAlmostEqual(b - a, 2 * 0.1 * em_units, places=2)  # two gaps, not three

    def test_variable_location_changes_weight(self):
        light = resolved_path(L.wordmark_svg("H", OUTFIT, {"wght": 200}))
        heavy = resolved_path(L.wordmark_svg("H", OUTFIT, {"wght": 800}))
        self.assertGreater(abs(heavy.area), abs(light.area) * 1.5)

    def test_locale_aware_case(self):
        self.assertEqual(L.apply_case("istanbul", "upper", "tr"), "İSTANBUL")
        self.assertEqual(L.apply_case("istanbul", "upper", "en"), "ISTANBUL")
        self.assertEqual(L.apply_case("IŞIK", "lower", "tr"), "ışık")
        svg = L.wordmark_svg("ink", OUTFIT, {"wght": 600}, case="upper", lang="tr")
        self.assertIn('data-text="İNK"', svg)

    def test_missing_glyph_is_an_error(self):
        with self.assertRaises(L.LogoError):
            L.wordmark_svg("İş", ARVO, {})  # Arvo fixture lacks Turkish

    def test_wordmark_detail_ops(self):
        plain = L.wordmark_svg("HOH", OUTFIT, {"wght": 600})
        base = resolved_path(plain)
        box = ink(plain)
        # subtract on the 2nd character only: the O loses its middle band, both H keep theirs
        cut = '<svg><rect id="band" x="-100" y="45" width="2000" height="10" data-op="subtract" data-glyph="2"/></svg>'
        svg = L.wordmark_svg("HOH", OUTFIT, {"wght": 600}, detail_svg=cut)
        self.assertIn('data-detail="1"', svg)
        p = resolved_path(svg)
        self.assertLess(abs(p.area), abs(base.area))
        h_stem_x = box[0] + 3   # inside the first H's left stem
        self.assertTrue(p.contains((h_stem_x, 50)))
        self.assertEqual(advance(svg), advance(plain))
        # union: a bridge joins the letters; the ink box grows to include it
        bridge = '<svg><rect id="bridge" x="0" y="-20" width="300" height="8"/></svg>'
        joined = L.wordmark_svg("HOH", OUTFIT, {"wght": 600}, detail_svg=bridge)
        self.assertLess(ink(joined)[1], -19)
        self.assertEqual(len(L.parse_parts(joined)[2]), 1)   # still one clean path
        # a detail with its own colour stays a separate part, cut into the letters, not stacked on them
        leaf = '<svg><rect id="leaf" x="40" y="40" width="30" height="20" data-color="accent"/></svg>'
        two = L.parse_parts(L.wordmark_svg("HOH", OUTFIT, {"wght": 600}, color_role="text", detail_svg=leaf))[2]
        self.assertEqual(sorted(r for r, _i, _p in two), ["accent", "text"])
        letters = [p for r, _i, p in two if r == "text"][0]
        self.assertFalse(letters.contains((55, 50)))
        # intersect without data-glyph applies to every letter
        keep_top = '<svg><rect id="top" x="-100" y="-10" width="2000" height="60" data-op="intersect"/></svg>'
        self.assertAlmostEqual(ink(L.wordmark_svg("HOH", OUTFIT, {"wght": 600}, detail_svg=keep_top))[3], 50,
                               delta=0.01)
        with self.assertRaises(L.LogoError):
            L.wordmark_svg("HOH", OUTFIT, {"wght": 600}, detail_svg='<svg><rect width="5" height="5" '
                           'data-op="subtract" data-glyph="9"/></svg>')
        with self.assertRaises(L.LogoError):
            L.wordmark_svg("HOH", OUTFIT, {"wght": 600}, detail_svg='<svg><text>x</text></svg>')

    def test_text_path_places_letters(self):
        d = L.text_path("M", OUTFIT, {"wght": 600}, cap=40, x=50, baseline=70, anchor="middle")
        box = L._bounds(L.parse_d(d).path(close_all=True))
        self.assertAlmostEqual((box[0] + box[2]) / 2, 50, delta=0.01)
        self.assertAlmostEqual(box[3], 70, delta=0.3)
        self.assertAlmostEqual(box[3] - box[1], 40, delta=0.4)

    def test_font_optics(self):
        o = L.font_optics(OUTFIT, {"wght": 600})
        self.assertGreater(o["overshoot_top"], 0)
        self.assertLess(o["overshoot_top"], 0.05)
        self.assertGreater(o["crossbar_stem_ratio"], 0.5)


class ResolveSymbol(unittest.TestCase):
    def test_subtract_makes_a_real_hole(self):
        svg = L.resolve_symbol(DISC_WITH_HOLE)
        path = resolved_path(svg)
        self.assertFalse(path.contains((50, 50)))
        self.assertTrue(path.contains((50, 20)))
        self.assertGreater(len(list(path.contours)), 1)
        import math
        self.assertAlmostEqual(abs(path.area), math.pi * (40 ** 2 - 15 ** 2), delta=8)
        self.assertNotIn("<circle", svg)
        self.assertIn('data-color="primary"', svg)

    def test_intersect(self):
        svg = L.resolve_symbol('<svg viewBox="0 0 100 100"><rect id="a" x="10" y="10" width="50" height="50"/>'
                               '<rect id="b" x="30" y="30" width="50" height="50" data-op="intersect"/></svg>')
        self.assertAlmostEqual(abs(resolved_path(svg).area), 900, delta=0.5)

    def test_strokes_are_outlined(self):
        svg = L.resolve_symbol('<svg viewBox="0 0 100 100"><circle id="ring" cx="50" cy="50" r="30" fill="none" '
                               'stroke="currentColor" stroke-width="10" data-color="primary"/></svg>')
        self.assertNotIn("stroke", svg)
        path = resolved_path(svg)
        self.assertFalse(path.contains((50, 50)))
        self.assertTrue(path.contains((50, 20)))
        self.assertFalse(path.contains((50, 14)))

    def test_round_caps_and_joins_are_outlined(self):
        svg = L.resolve_symbol('<svg viewBox="0 0 100 100"><polyline id="zig" points="10,80 50,20 90,80" fill="none" '
                               'stroke="currentColor" stroke-width="8" stroke-linecap="round" '
                               'stroke-linejoin="round"/></svg>')
        self.assertNotIn("stroke", svg)
        self.assertTrue(resolved_path(svg).contains((50, 20)))

    def test_viewbox_normalised_to_100_grid(self):
        svg = L.resolve_symbol('<svg viewBox="0 0 24 24"><rect id="sq" x="0" y="0" width="24" height="24"/></svg>')
        box = ink(svg)
        self.assertEqual([round(v, 3) for v in box], [0, 0, 100, 100])
        self.assertIn('viewBox="0 0 100 100"', svg)

    def test_evenodd_is_honoured(self):
        d = "M10 10H90V90H10Z M30 30H70V70H30Z"  # same winding: non-zero fills the centre
        filled = resolved_path(L.resolve_symbol(f'<svg viewBox="0 0 100 100"><path id="p" d="{d}"/></svg>'))
        holed = resolved_path(L.resolve_symbol(
            f'<svg viewBox="0 0 100 100"><path id="p" d="{d}" fill-rule="evenodd"/></svg>'))
        self.assertTrue(filled.contains((50, 50)))
        self.assertFalse(holed.contains((50, 50)))

    def test_colour_parts_knock_out_and_stay_separate(self):
        svg = L.resolve_symbol('<svg viewBox="0 0 100 100"><circle id="disc" cx="50" cy="50" r="40" '
                               'data-color="primary"/><circle id="dot" cx="50" cy="50" r="10" data-color="accent"/>'
                               '</svg>')
        _vb, _a, parts = L.parse_parts(svg)
        roles = {r: p for r, _i, p in parts}
        self.assertEqual(set(roles), {"primary", "accent"})
        self.assertFalse(roles["primary"].contains((50, 50)))
        self.assertTrue(roles["accent"].contains((50, 50)))

    def test_group_scope_and_target(self):
        svg = L.resolve_symbol(
            '<svg viewBox="0 0 100 100"><rect id="bar" x="0" y="40" width="100" height="20"/>'
            '<g id="ring" data-color="accent"><circle id="o" cx="50" cy="50" r="30"/>'
            '<circle id="i" cx="50" cy="50" r="20" data-op="subtract"/></g>'
            '<rect id="notch" x="45" y="0" width="10" height="100" data-op="subtract" data-target="bar"/></svg>')
        _vb, _a, parts = L.parse_parts(svg)
        roles = {r: p for r, _i, p in parts}
        self.assertFalse(roles["primary"].contains((50, 50)))   # notch cut only the bar ...
        self.assertTrue(roles["accent"].contains((50, 23)))     # ... not the ring above it
        self.assertFalse(roles["accent"].contains((50, 50)))    # the ring's hole stayed inside its group

    def test_glyph_primitive_from_the_font(self):
        svg = L.resolve_symbol('<svg viewBox="0 0 100 100"><rect id="tile" x="0" y="0" width="100" height="100"/>'
                               '<path id="o" data-glyphs="O" data-cap="60" data-x="50" data-baseline="80" '
                               'data-op="subtract"/></svg>', font_path=OUTFIT, location={"wght": 600})
        path = resolved_path(svg)
        self.assertFalse(path.contains((50 - 26, 50)))  # the O's stroke is cut out
        with self.assertRaises(L.LogoError):
            L.resolve_symbol('<svg viewBox="0 0 100 100"><path id="o" data-glyphs="O"/></svg>')

    def test_glyph_instance_override(self):
        base = ('<svg viewBox="0 0 100 100"><path id="i" data-glyphs="H" data-cap="60" data-x="50" '
                'data-baseline="80"{extra}/></svg>')
        regular = abs(resolved_path(L.resolve_symbol(base.format(extra=""), OUTFIT, {"wght": 400})).area)
        heavy = abs(resolved_path(L.resolve_symbol(base.format(extra=' data-wght="850"'), OUTFIT,
                                                   {"wght": 400})).area)
        also = abs(resolved_path(L.resolve_symbol(base.format(extra=" data-location='{\"wght\": 850}'"), OUTFIT,
                                                  {"wght": 400})).area)
        self.assertGreater(heavy, regular * 1.4)
        self.assertAlmostEqual(heavy, also, delta=0.01)
        with self.assertRaises(L.LogoError):   # a static face cannot change weight without the resolver
            L.resolve_symbol(base.format(extra=' data-wght="850"'), ARVO, {})
        calls = []
        L.resolve_symbol(base.format(extra=' data-wght="850"'), ARVO, {}, font_resolver=lambda loc: calls.append(loc)
                         or ARVO)
        self.assertEqual(calls, [{"wght": 850.0}])

    def test_arcs_and_relative_commands(self):
        svg = L.resolve_symbol('<svg viewBox="0 0 100 100"><path id="half" d="M10 50a40 40 0 0 1 80 0z"/></svg>')
        box = ink(svg)
        self.assertAlmostEqual(box[1], 10, delta=0.05)
        self.assertAlmostEqual(box[3], 50, delta=0.05)

    def test_errors(self):
        with self.assertRaises(L.LogoError):
            L.resolve_symbol('<svg viewBox="0 0 100 100"><rect id="cut" width="10" height="10" '
                             'data-op="subtract"/></svg>')
        with self.assertRaises(L.LogoError):
            L.resolve_symbol("<svg viewBox='0 0 100 100'><circle")

    def test_inspect_reports_text_and_false_hole(self):
        rep = L.inspect_symbol('<svg viewBox="0 0 100 100"><circle id="disc" cx="50" cy="50" r="40"/>'
                               '<circle id="fake" cx="50" cy="50" r="15" data-color="background"/>'
                               '<text x="0" y="10">A</text></svg>')
        self.assertIn("text", rep["forbidden"])
        self.assertEqual(rep["overlaps"][0]["upper"], "fake")
        self.assertEqual(rep["overlaps"][0]["upper_color"], "background")


class Lockups(unittest.TestCase):
    def setUp(self):
        self.wm = L.wordmark_svg("Moonvault", OUTFIT, {"wght": 600}, tracking=-10, color_role="text")

    def band(self, svg):
        root_band = [float(x) for x in re.search(r'data-band="([^"]+)"', svg).group(1).split()]
        box = [float(x) for x in re.search(r'data-symbol-box="([^"]+)"', svg).group(1).split()]
        return root_band, box

    def test_flat_symbol_sits_on_the_cap_band(self):
        sq = L.resolve_symbol('<svg viewBox="0 0 100 100"><rect id="sq" x="10" y="10" width="80" height="80"/>'
                              '</svg>')
        (top, bottom), box = self.band(L.lockup(sq, self.wm, "horizontal", symbol_scale=1.0))
        self.assertAlmostEqual(box[1], top, delta=0.01)
        self.assertAlmostEqual(box[3], bottom, delta=0.01)

    def test_round_symbol_overshoots_by_the_fonts_amount(self):
        disc = L.resolve_symbol('<svg viewBox="0 0 100 100"><circle id="d" cx="50" cy="50" r="40"/></svg>')
        svg = L.lockup(disc, self.wm, "horizontal", symbol_scale=1.0)
        (top, bottom), box = self.band(svg)
        ot = L.font_optics(OUTFIT, {"wght": 600})["overshoot_top"] * 100
        self.assertAlmostEqual(top - box[1], ot, delta=0.05)
        self.assertAlmostEqual(box[3] - bottom, ot, delta=0.05)
        self.assertIn('data-overshoot-applied="1 1"', svg)
        # default size is centred on the band
        (top, bottom), box = self.band(L.lockup(disc, self.wm, "horizontal"))
        self.assertAlmostEqual((box[1] + box[3]) / 2, (top + bottom) / 2, delta=0.01)

    def test_stacked_symbol_above_wordmark(self):
        disc = L.resolve_symbol('<svg viewBox="0 0 100 100"><circle id="d" cx="50" cy="50" r="40"/></svg>')
        svg = L.lockup(disc, self.wm, "stacked")
        (top, _bottom), box = self.band(svg)
        wbox = ink(self.wm)
        self.assertLess(box[3], top)
        self.assertAlmostEqual((box[0] + box[2]) / 2, (wbox[0] + wbox[2]) / 2, delta=0.01)


class Raster(unittest.TestCase):
    def test_png_and_coverage(self):
        sq = L.parse_parts(L.resolve_symbol('<svg viewBox="0 0 100 100"><rect id="s" x="25" y="25" width="50" '
                                            'height="50"/></svg>'))[2][0][2]
        rgba = L.rasterize([(sq, "#ff0000")], 16, 16, (0, 0, 100, 100), None)
        self.assertEqual(len(rgba), 16 * 16 * 4)
        px = lambda x, y: rgba[4 * (y * 16 + x):4 * (y * 16 + x) + 4]  # noqa: E731
        self.assertEqual(px(8, 8), bytes((255, 0, 0, 255)))
        self.assertEqual(px(1, 1)[3], 0)
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "a.png")
            L.write_png(p, 16, 16, rgba)
            with open(p, "rb") as fh:
                data = fh.read()
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(struct.unpack(">II", data[16:24]), (16, 16))

    def test_bitmap(self):
        sq = resolved_path(L.resolve_symbol('<svg viewBox="0 0 100 100"><rect id="s" x="0" y="0" width="50" '
                                            'height="100"/></svg>'))
        rows = L.bitmap(sq, 10)
        self.assertEqual(rows[5], 0b11111)


class Variants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._px = L.DELIVERY_PX
        L.DELIVERY_PX = 128  # keep the test fast; the size does not change what is built
        cls.tmp = tempfile.TemporaryDirectory()
        cls.set_dir = os.path.join(cls.tmp.name, "sets", "A")
        os.makedirs(os.path.join(cls.set_dir, "logo"))
        with open(os.path.join(cls.set_dir, "logo", "symbol.svg"), "w", encoding="utf-8") as fh:
            fh.write(DISC_WITH_HOLE)
        cls.identity = example_identity()
        cls.palette = example_palette()
        cls.build = os.path.join(cls.set_dir, "logo", "build")
        cls.man = L.variants(cls.identity, cls.palette, cls.build)

    @classmethod
    def tearDownClass(cls):
        L.DELIVERY_PX = cls._px
        cls.tmp.cleanup()

    def test_every_version_has_svg_and_png(self):
        names = set(self.man["versions"])
        for n in ("full-color", "full-color-dark", "one-color-dark", "one-color-light", "on-light-background",
                  "on-dark-background", "on-light-primary", "symbol-only", "favicon-16", "favicon-32",
                  "favicon-48", "app-icon-512"):
            self.assertIn(n, names)
            for ext in ("svg", "png"):
                self.assertTrue(os.path.isfile(os.path.join(self.build, self.man["versions"][n][ext])), (n, ext))
        self.assertEqual(self.man["versions"]["favicon-16"]["size"], [16, 16])
        self.assertEqual(self.man["primary"], "lockup-horizontal")

    def test_roles_resolve_in_the_mode_of_the_ground(self):
        light = self.palette["modes"]["light"]
        dark = self.palette["modes"]["dark"]
        full = {p["role"]: p["hex"] for p in self.man["versions"]["full-color"]["parts"]}
        self.assertEqual(full["primary"], light["primary"])
        on_dark = {p["role"]: p["hex"] for p in self.man["versions"]["on-dark-background"]["parts"]}
        self.assertEqual(on_dark["primary"], dark["primary"])
        mono = {p["hex"] for p in self.man["versions"]["one-color-light"]["parts"]}
        self.assertEqual(mono, {dark["text"]})
        on_primary = {p["hex"] for p in self.man["versions"]["on-light-primary"]["parts"]}
        self.assertEqual(on_primary, {light["onPrimary"]})

    def test_masters_are_clean(self):
        for name in self.man["masters"].values():
            with open(os.path.join(self.build, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("<text", text)
            self.assertNotIn("stroke", text)
            self.assertIn("viewBox", text)


class Sync(unittest.TestCase):
    def test_logos_reuse_the_built_palette_when_the_build_reordered_brand(self):
        # palette_build puts seed and accent first (a dark ground listed first moves back); `logos` must still use
        # palette.json instead of a second in-memory build with other neutrals
        import colorlib
        import palette_build
        partial = {"schema": colorlib.SCHEMA_ID, "name": "Close", "brand": [
            {"id": "brand-1", "name": "Whinstone", "hex": "#1c2421", "source": "chosen", "locked": True},
            {"id": "brand-2", "name": "Lichen", "hex": "#bccb5c", "source": "chosen", "locked": True}]}
        with contextlib.redirect_stderr(io.StringIO()):
            built = palette_build.build_palette(None, partial=colorlib.validate_palette(
                json.loads(json.dumps(partial)), partial=True), dark_bg="#1c2421")
        self.assertEqual(built["brand"][0]["hex"], "#bccb5c")
        with tempfile.TemporaryDirectory() as sd:
            with open(os.path.join(sd, "palette.json"), "w", encoding="utf-8") as fh:
                json.dump(built, fh)
            pal, err = L._palette_for(partial, sd, {"palette_build": {"dark_bg": "#1c2421"}})
        self.assertIsNone(err)
        self.assertEqual(pal["modes"], built["modes"])

    def test_sync_writes_symbols_only(self):
        with tempfile.TemporaryDirectory() as work:
            sdir = os.path.join(work, "sets", "A")
            os.makedirs(sdir)
            ident = example_identity()
            ip = os.path.join(sdir, "identity.json")
            with open(ip, "w", encoding="utf-8") as fh:
                json.dump(ident, fh)
            before = sha_of(ip)
            draft_ident = {k: copy.deepcopy(v) for k, v in ident.items()
                           if k not in ("schema", "brand", "components", "derived", "audit")}
            draft_ident["set"]["name"] = "Changed in the draft"
            draft = {"schema": "brand-identity/sets@1",
                     "sets": [{"identity": draft_ident, "palette": example_palette(),
                               "symbol_svg": DISC_WITH_HOLE, "symbol_small_svg": None,
                               "wordmark_detail_svg": '<svg><rect id="cut" width="5" height="5"/></svg>'}]}
            with open(os.path.join(work, "sets.json"), "w", encoding="utf-8") as fh:
                json.dump(draft, fh)
            recs = L.sync_symbols(work, write=False)
            self.assertFalse(os.path.exists(os.path.join(sdir, "logo", "symbol.svg")))
            recs = L.sync_symbols(work)
            self.assertEqual(recs[0]["identity"]["set"]["name"], "Changed in the draft")
            self.assertEqual(recs[0]["identity"]["brand"], ident["brand"])
            self.assertTrue(os.path.isfile(os.path.join(sdir, "logo", "symbol.svg")))
            self.assertEqual(sha_of(ip), before)
            self.assertFalse(os.path.exists(os.path.join(sdir, "palette.json")))
            self.assertEqual(L.sync_symbols(work)[0]["written"], [])  # unchanged -> not rewritten
            detail = os.path.join(sdir, "logo", "wordmark-detail.svg")
            self.assertTrue(os.path.isfile(detail))
            draft["sets"][0]["wordmark_detail_svg"] = None
            with open(os.path.join(work, "sets.json"), "w", encoding="utf-8") as fh:
                json.dump(draft, fh)
            L.sync_symbols(work)
            self.assertFalse(os.path.exists(detail))                  # dropped from the draft -> dropped


if __name__ == "__main__":
    unittest.main()
