"""Unit tests for skills/brand-identity/scripts/colorlib.py (stdlib unittest).

Run from the repo root:  python3 -m unittest discover -s tests -v
"""
import copy
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
EXAMPLE = os.path.join(ROOT, "skills", "brand-identity", "templates", "palette.example.json")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import colorlib as cl  # noqa: E402

# Sharma, Wu & Dalal 2005, Table 1: L1 a1 b1 L2 a2 b2 deltaE00 (all 34 pairs).
SHARMA = """
50.0000 2.6772 -79.7751 50.0000 0.0000 -82.7485 2.0425
50.0000 3.1571 -77.2803 50.0000 0.0000 -82.7485 2.8615
50.0000 2.8361 -74.0200 50.0000 0.0000 -82.7485 3.4412
50.0000 -1.3802 -84.2814 50.0000 0.0000 -82.7485 1.0000
50.0000 -1.1848 -84.8006 50.0000 0.0000 -82.7485 1.0000
50.0000 -0.9009 -85.5211 50.0000 0.0000 -82.7485 1.0000
50.0000 0.0000 0.0000 50.0000 -1.0000 2.0000 2.3669
50.0000 -1.0000 2.0000 50.0000 0.0000 0.0000 2.3669
50.0000 2.4900 -0.0010 50.0000 -2.4900 0.0009 7.1792
50.0000 2.4900 -0.0010 50.0000 -2.4900 0.0010 7.1792
50.0000 2.4900 -0.0010 50.0000 -2.4900 0.0011 7.2195
50.0000 2.4900 -0.0010 50.0000 -2.4900 0.0012 7.2195
50.0000 -0.0010 2.4900 50.0000 0.0009 -2.4900 4.8045
50.0000 -0.0010 2.4900 50.0000 0.0010 -2.4900 4.8045
50.0000 -0.0010 2.4900 50.0000 0.0011 -2.4900 4.7461
50.0000 2.5000 0.0000 50.0000 0.0000 -2.5000 4.3065
50.0000 2.5000 0.0000 73.0000 25.0000 -18.0000 27.1492
50.0000 2.5000 0.0000 61.0000 -5.0000 29.0000 22.8977
50.0000 2.5000 0.0000 56.0000 -27.0000 -3.0000 31.9030
50.0000 2.5000 0.0000 58.0000 24.0000 15.0000 19.4535
50.0000 2.5000 0.0000 50.0000 3.1736 0.5854 1.0000
50.0000 2.5000 0.0000 50.0000 3.2972 0.0000 1.0000
50.0000 2.5000 0.0000 50.0000 1.8634 0.5757 1.0000
50.0000 2.5000 0.0000 50.0000 3.2592 0.3350 1.0000
60.2574 -34.0099 36.2677 60.4626 -34.1751 39.4387 1.2644
63.0109 -31.0961 -5.8663 62.8187 -29.7946 -4.0864 1.2630
61.2901 3.7196 -5.3901 61.4292 2.2480 -4.9620 1.8731
35.0831 -44.1164 3.7933 35.0232 -40.0716 1.5901 1.8645
22.7233 20.0904 -46.6940 23.0331 14.9730 -42.5619 2.0373
36.4612 47.8580 18.3852 36.2715 50.5065 21.2231 1.4146
90.8027 -2.0831 1.4410 91.1528 -1.6435 0.0447 1.4441
90.9257 -0.5406 -0.9208 88.6381 -0.8985 -0.7239 1.5381
6.7747 -0.2908 -2.4247 5.8714 -0.0985 -2.2286 0.6377
2.0776 0.0795 -1.1350 0.9033 -0.0636 -0.5514 0.9082
"""


class TestDeltaE2000(unittest.TestCase):
    def test_sharma_vectors(self):
        rows = [list(map(float, line.split())) for line in SHARMA.strip().splitlines()]
        self.assertEqual(len(rows), 34)
        for i, v in enumerate(rows, 1):
            with self.subTest(pair=i):
                self.assertAlmostEqual(cl.delta_e2000_lab(v[:3], v[3:6]), v[6], places=4)
                self.assertAlmostEqual(cl.delta_e2000_lab(v[3:6], v[:3]), v[6], places=4)  # symmetric

    def test_colour_inputs(self):
        self.assertEqual(cl.delta_e2000("#1f5f7a", "#1f5f7a"), 0.0)
        self.assertAlmostEqual(cl.delta_e2000("#ff0000", (1.0, 0.0, 0.0)), 0.0, places=9)
        self.assertGreater(cl.delta_e2000("#ffffff", "#000000"), 99)


class TestWCAG(unittest.TestCase):
    def test_known_ratios(self):
        self.assertAlmostEqual(cl.contrast_ratio("#777777", "#ffffff"), 4.478, places=3)
        self.assertLess(cl.contrast_ratio("#777", "#fff"), 4.5)          # no rounding up to 4.5
        self.assertGreaterEqual(cl.contrast_ratio("#767676", "#ffffff"), 4.5)
        self.assertAlmostEqual(cl.contrast_ratio("#000000", "#ffffff"), 21.0, places=9)
        self.assertAlmostEqual(cl.contrast_ratio("#e50914", "#ffffff"), 4.79, places=2)   # Netflix red
        self.assertAlmostEqual(cl.contrast_ratio("#e50914", "#000000"), 4.38, places=2)

    def test_symmetric_and_tuple_input(self):
        self.assertEqual(cl.contrast_ratio("#1f5f7a", "#ffffff"), cl.contrast_ratio("#ffffff", "#1f5f7a"))
        self.assertEqual(cl.contrast_ratio((1, 1, 1), "#000"), 21.0)

    def test_relative_luminance(self):
        self.assertEqual(cl.relative_luminance("#ffffff"), 1.0)
        self.assertEqual(cl.relative_luminance("#000000"), 0.0)
        self.assertAlmostEqual(cl.relative_luminance("#808080"), 0.2158605, places=6)

    def test_best_text_on(self):
        self.assertEqual(cl.best_text_on("#f5c518"), "#000000")       # yellow takes dark text
        self.assertEqual(cl.best_text_on("#183979"), "#ffffff")
        self.assertEqual(cl.best_text_on("#f5c518", ("#ffffff", "#1e1c15")), "#1e1c15")


class TestSpaces(unittest.TestCase):
    def test_ottosson_xyz_vectors(self):
        cases = [((0.950, 1.000, 1.089), (1.000, 0.000, 0.000)),
                 ((1.000, 0.000, 0.000), (0.450, 1.236, -0.019)),
                 ((0.000, 1.000, 0.000), (0.922, -0.671, 0.263)),
                 ((0.000, 0.000, 1.000), (0.153, -1.415, -0.449))]
        for xyz, lab in cases:
            got = cl.xyz_to_oklab(xyz)
            for g, e in zip(got, lab):
                self.assertAlmostEqual(g, e, places=3, msg=f"{xyz} -> {got}")

    def test_oklch_reference(self):
        L, C, h = cl.hex_to_oklch("#ff0000")              # CSS Color 4: oklch(62.796% 0.25768 29.2339)
        self.assertAlmostEqual(L, 0.62796, places=4)
        self.assertAlmostEqual(C, 0.25768, places=4)
        self.assertAlmostEqual(h, 29.2339, places=2)
        L, C, h = cl.hex_to_oklch("#ffffff")
        self.assertAlmostEqual(L, 1.0, places=4)
        self.assertLess(C, 1e-4)
        self.assertEqual(h, 0.0)                           # achromatic -> hue 0

    def test_hex_oklch_roundtrip_all_12bit_colours(self):
        for r in range(16):
            for g in range(16):
                for b in range(16):
                    hx = "#" + "".join(f"{v:x}{v:x}" for v in (r, g, b))
                    self.assertEqual(cl.oklch_to_hex(*cl.hex_to_oklch(hx)), hx)

    def test_lab_d65_d50(self):
        for got, exp in ((cl.hex_to_lab("#ff0000"), (53.2371, 80.0901, 67.2033)),
                         (cl.hex_to_lab("#ff0000", "D50"), (54.2905, 80.8049, 69.8910)),
                         (cl.hex_to_lab("#ffffff", "D50"), (100.0, 0.0, 0.0)),
                         (cl.hex_to_lab("#ffffff"), (100.0, 0.0, 0.0))):
            for g, e in zip(got, exp):
                self.assertAlmostEqual(g, e, places=3)
        with self.assertRaises(ValueError):
            cl.hex_to_lab("#ffffff", "D55")

    def test_delta_e_ok(self):
        self.assertAlmostEqual(cl.delta_e_ok("#ffffff", "#000000"), 1.0, places=3)
        self.assertEqual(cl.delta_e_ok("#123456", "#123456"), 0.0)


class TestParse(unittest.TestCase):
    def test_forms(self):
        cases = {
            "#abc": "#aabbcc", "#AABBCC": "#aabbcc", "aabbcc": "#aabbcc", "#AABBCC80": "#aabbcc", "#abc8": "#aabbcc",
            "rgb(255 0 0)": "#ff0000", "rgb(255, 0, 0)": "#ff0000", "rgba(255,0,0,0.5)": "#ff0000",
            "rgb(100% 0% 0% / 0.5)": "#ff0000", "hsl(120 100% 50%)": "#00ff00", "hsl(240deg, 100%, 50%)": "#0000ff",
            "oklch(62.8% 0.2577 29.23)": "#ff0000", "oklch(0.628 0.2577 29.23)": "#ff0000", "white": "#ffffff",
        }
        for s, exp in cases.items():
            with self.subTest(s=s):
                self.assertEqual(cl.normalize_hex(s), exp)
        self.assertEqual(cl.parse_color((0.5, 0.25, 1)), (0.5, 0.25, 1.0))

    def test_errors_are_specific(self):
        for bad in ("#12", "#12345", "blue-ish", "rgb(1, 2)", "", "hsl(a b c)"):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as cm:
                cl.parse_color(bad)
            self.assertTrue(str(cm.exception))

    def test_to_hex_clips(self):
        self.assertEqual(cl.to_hex((1.2, -0.1, 0.5)), "#ff0080")


class TestGamut(unittest.TestCase):
    def test_in_gamut_unchanged(self):
        L, C, h = cl.hex_to_oklch("#1f5f7a")
        self.assertEqual(cl.oklch_to_hex(L, C, h), "#1f5f7a")

    def test_out_of_gamut_is_mapped_with_minde(self):
        for L, C, h in ((0.7, 0.4, 150), (0.5, 0.35, 265), (0.95, 0.3, 105), (0.3, 0.3, 30)):
            with self.subTest(oklch=(L, C, h)):
                rgb = cl.gamut_map_oklch(L, C, h)
                self.assertTrue(all(0.0 <= c <= 1.0 for c in rgb))
                L2, C2, h2 = cl.oklab_to_oklch(cl.rgb_to_oklab(rgb))
                self.assertAlmostEqual(L2, L, delta=0.02)          # lightness kept (within one JND)
                self.assertLess(min(abs(h2 - h), 360 - abs(h2 - h)), 3.0)
                self.assertLessEqual(C2, cl.max_chroma(L2, h2) + 0.02)       # result sits on the gamut edge
                self.assertGreater(C2, cl.max_chroma(L, h) - 0.03)   # not desaturated more than needed

    def test_extremes(self):
        self.assertEqual(cl.oklch_to_hex(1.2, 0.1, 30), "#ffffff")
        self.assertEqual(cl.oklch_to_hex(-0.1, 0.1, 30), "#000000")

    def test_max_chroma(self):
        for L, h in ((0.45, 265), (0.63, 25), (0.9, 105)):
            c = cl.max_chroma(L, h)
            self.assertTrue(cl.in_gamut(cl.oklch_to_rgb(L, c, h)))
            self.assertFalse(cl.in_gamut(cl.oklch_to_rgb(L, c + 0.003, h)))
        # Research 03 section 2.1: blue peaks dark, yellow peaks light.
        self.assertGreater(cl.max_chroma(0.45, 265), cl.max_chroma(0.9, 265))
        self.assertGreater(cl.max_chroma(0.92, 105), cl.max_chroma(0.5, 105))
        self.assertAlmostEqual(cl.max_chroma(0.45, 265), 0.309, delta=0.01)
        self.assertEqual(cl.max_chroma(1.0, 30), 0.0)


class TestCVD(unittest.TestCase):
    def test_greys_are_invariant(self):
        for kind in cl.CVD_KINDS:
            for g in ("#000000", "#808080", "#ffffff"):
                self.assertLessEqual(cl.delta_e2000(cl.simulate_cvd(g, kind), g), 0.6)

    def test_red_green_collapse_for_deutan_not_tritan(self):
        red, green = "#d03030", "#3a8a3a"
        self.assertGreater(cl.delta_e2000(red, green), 50)
        self.assertLess(cl.delta_e2000(cl.simulate_cvd(red, "deutan"), cl.simulate_cvd(green, "deutan")), 8)
        self.assertGreater(cl.delta_e2000(cl.simulate_cvd(red, "tritan"), cl.simulate_cvd(green, "tritan")), 30)

    def test_blue_green_collapse_more_for_tritan(self):
        blue, green = "#3050d0", "#30a050"
        tri = cl.delta_e2000(cl.simulate_cvd(blue, "tritan"), cl.simulate_cvd(green, "tritan"))
        deu = cl.delta_e2000(cl.simulate_cvd(blue, "deutan"), cl.simulate_cvd(green, "deutan"))
        self.assertLess(tri, deu)

    def test_linear_rgb_not_gamma(self):
        # Matrices in gamma space darken colours noticeably; in linear space pure red stays reasonably light.
        self.assertGreater(cl.hex_to_lab(cl.simulate_cvd("#ff0000", "deutan"))[0], 55)

    def test_severity_and_errors(self):
        self.assertEqual(cl.simulate_cvd("#e50914", "protan", severity=0.0), "#e50914")
        with self.assertRaises(ValueError):
            cl.simulate_cvd("#ffffff", "achromat")


class TestAdvisory(unittest.TestCase):
    def test_hue_entropy(self):
        self.assertAlmostEqual(cl.hue_entropy([30]), 4.6, delta=0.05)              # O'Donovan: one hue 4.62
        self.assertAlmostEqual(cl.hue_entropy([0, 72, 144, 216, 288]), 5.87, delta=0.02)
        self.assertIsNone(cl.hue_entropy(["#808080", "#ffffff"]))
        self.assertLess(cl.hue_entropy(["#1f5f7a", "#2a7291"]), cl.hue_entropy(["#1f5f7a", "#ea8a28"]))

    def test_ou_luo(self):
        self.assertAlmostEqual(cl.ou_luo_harmony("#000000", "#ffffff"), 0.55, delta=0.02)   # Ou & Luo 2006 model, black + white
        self.assertLess(cl.ou_luo_harmony("#d03030", "#3a8a3a"), -0.8)                      # isoluminant red/green


class TestPaletteContract(unittest.TestCase):
    def setUp(self):
        with open(EXAMPLE, encoding="utf-8") as fh:
            self.good = json.load(fh)

    def test_role_keys(self):
        self.assertEqual(cl.ROLE_KEYS, ("background", "surface", "surfaceAlt", "border", "text", "textMuted",
                                        "primary", "onPrimary", "accent", "onAccent", "link", "focus", "success",
                                        "warning", "danger", "info"))

    def test_example_loads(self):
        pal = cl.load_palette(EXAMPLE)
        self.assertEqual(pal["schema"], "brand-identity/palette@1")

    def _expect(self, mutate, fragment, partial=False):
        data = copy.deepcopy(self.good)
        mutate(data)
        with self.assertRaises(cl.PaletteError) as cm:
            cl.validate_palette(data, partial=partial)
        self.assertIn(fragment, str(cm.exception))

    def test_precise_errors(self):
        self._expect(lambda d: d.update(schema="palette@0"), "schema")
        self._expect(lambda d: d["modes"]["dark"].pop("onPrimary"), "modes.dark.onPrimary")
        self._expect(lambda d: d["modes"]["light"].update(text="#12345"), "modes.light.text")
        self._expect(lambda d: d["scales"]["primary"]["steps"].pop("950"), "scales.primary.steps")
        self._expect(lambda d: d["brand"].append(dict(d["brand"][0])), "duplicate id")
        self._expect(lambda d: d["modes"]["light"].update(onSurface="#000000"), "unknown role")
        self._expect(lambda d: d["brand"][0].update(source="guessed"), "brand[0].source")
        self._expect(lambda d: d["rationale"].append({"claim": "x", "evidence": "vibes"}), "rationale[3].evidence")

    def test_extended_and_feels_contract(self):
        self.assertEqual(len(self.good["extended"]), 3)
        self._expect(lambda d: d["extended"][1].update(id="ext-7"), "extended[1].id")
        self._expect(lambda d: d["extended"][0].update(on="#12"), "extended[0].on")
        self._expect(lambda d: d["extended"][0]["dark"].pop("on"), "extended[0].dark.on")
        self._expect(lambda d: d["scales"].pop("ext-2"), "scales")
        self._expect(lambda d: d["extended"].extend(
            [dict(d["extended"][0], id=f"ext-{i}") for i in range(4, 7)]), "at most 5")
        self._expect(lambda d: d.update(feels=["calm"]), "feels")
        self._expect(lambda d: d.update(feels="calm, local"), "feels")
        self.assertEqual(self.good["feels"], ["calm", "dependable", "maritime"])

    def test_partial_allows_missing(self):
        data = {"schema": cl.SCHEMA_ID, "brand": [{"id": "b", "hex": "#E50914"}], "modes": {"light": {"text": "#111"
                                                                                                       "111"}}}
        out = cl.validate_palette(data, partial=True)
        self.assertEqual(out["brand"][0]["hex"], "#e50914")             # normalised

    def test_load_errors(self):
        with self.assertRaises(cl.PaletteError) as cm:
            cl.load_palette(os.path.join(ROOT, "no-such-palette.json"))
        self.assertIn("file not found", str(cm.exception))
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{ not json")
        try:
            with self.assertRaises(cl.PaletteError) as cm:
                cl.load_palette(fh.name)
            self.assertIn("line 1", str(cm.exception))
        finally:
            os.unlink(fh.name)


if __name__ == "__main__":
    unittest.main()
