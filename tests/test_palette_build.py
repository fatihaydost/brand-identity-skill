"""Unit tests for skills/brand-identity/scripts/palette_build.py (stdlib unittest)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import colorlib as cl  # noqa: E402
import palette_audit as pa  # noqa: E402
import palette_build as pb  # noqa: E402

SEEDS = ("#1f5f7a", "#c2410c", "#f5c518", "#6d28d9", "#0f766e")
EDGE_SEEDS = ("#e50914", "#0000ff", "#00ff00", "#fafafa", "#0a0a0a", "#808080", "#ffeb3b", "#9146ff")
_CACHE = {}


def build(seed, **kw):
    key = (seed, tuple(sorted(kw.items())))
    if key not in _CACHE:
        with contextlib.redirect_stderr(io.StringIO()):
            _CACHE[key] = pb.build_palette(seed, **kw)
    return _CACHE[key]


def scale_hexes(pal, *names):
    return {v for n in names for v in pal["scales"][n]["steps"].values()}


class TestScales(unittest.TestCase):
    def test_anchor_holds_seed_exactly(self):
        # a common ramp bug: the brand hex never appears in its own ramp. Ours must hold it verbatim at the anchor.
        for seed in SEEDS + EDGE_SEEDS:
            with self.subTest(seed=seed):
                sc = pb.build_scale(seed)
                self.assertEqual(sc["steps"][sc["anchor"]], seed)
                self.assertEqual(list(sc["steps"].values()).count(seed), 1)

    def test_anchor_is_nearest_ladder_step(self):
        self.assertEqual(pb.build_scale("#f5c518")["anchor"], "300")    # light yellow sits high on the ladder
        self.assertEqual(pb.build_scale("#c2410c")["anchor"], "600")
        self.assertEqual(pb.build_scale("#0a0a0a")["anchor"], "950")
        self.assertEqual(pb.build_scale("#fafafa")["anchor"], "50")

    def test_monotonic_and_spaced(self):
        for seed in SEEDS + EDGE_SEEDS:
            with self.subTest(seed=seed):
                steps = pb.build_scale(seed)["steps"]
                Ls = [cl.hex_to_oklch(steps[k])[0] for k in cl.STEP_KEYS]
                self.assertEqual(Ls, sorted(Ls, reverse=True))
                for a, b in zip(cl.STEP_KEYS, cl.STEP_KEYS[1:]):
                    self.assertGreaterEqual(cl.delta_e_ok(steps[a], steps[b]), 0.02, f"{seed} {a}->{b}")

    def test_hue_is_kept_except_yellow_drift(self):
        steps = pb.build_scale("#6d28d9")["steps"]
        h0 = cl.hex_to_oklch("#6d28d9")[2]
        for k in ("200", "400", "600", "800", "900"):
            L, C, h = cl.hex_to_oklch(steps[k])
            self.assertLess(min(abs(h - h0), 360 - abs(h - h0)), 6, k)
        # Yellow darks turn amber instead of olive (drift), and the switch turns it off.
        dark = cl.hex_to_oklch(pb.build_scale("#f5c518")["steps"]["800"])[2]
        flat = cl.hex_to_oklch(pb.build_scale("#f5c518", hue_drift=False)["steps"]["800"])[2]
        self.assertLess(dark, flat - 15)

    def test_chroma_peaks_follow_the_hue(self):
        # Research 03 section 2.1: the most chromatic step differs per hue family.
        def peak(seed):
            st = pb.build_scale(seed)["steps"]
            return max(cl.STEP_KEYS, key=lambda k: cl.hex_to_oklch(st[k])[1])
        self.assertIn(peak("#f5c518"), ("200", "300", "400"))
        self.assertIn(peak("#6d28d9"), ("600", "700", "800"))

    def test_ladder_rescales_when_crowded(self):
        anchor, lad = pb.lightness_ladder(0.96)
        self.assertEqual(lad[anchor], 0.96)
        self.assertGreaterEqual(lad["50"] - lad["100"], pb.MIN_STEP_GAP - 1e-9)

    def test_neutral_tint(self):
        n = pb.build_neutral(230.0, 0.022)["steps"]
        for k, v in n.items():
            L, C, h = cl.hex_to_oklch(v)
            self.assertLessEqual(C, 0.046, k)
            if C > 0.006:
                self.assertLess(min(abs(h - 230), 360 - abs(h - 230)), 12, k)
        grey = pb.build_neutral(None, 0.0)["steps"]
        for v in grey.values():
            self.assertLess(cl.hex_to_oklch(v)[1], 1e-3)


class TestRoles(unittest.TestCase):
    def test_full_palettes_validate_and_pass_gates(self):
        for seed in SEEDS + EDGE_SEEDS:
            with self.subTest(seed=seed):
                pal = build(seed)
                cl.validate_palette(json.loads(json.dumps(pal)))
                audit = pa.audit_palette(pal)
                gates = [f["id"] for f in audit["findings"] if f["severity"] == "gate"]
                self.assertEqual(gates, [], f"{seed}: {gates}")

    def test_yellow_takes_dark_on_primary(self):
        pal = build("#f5c518")
        for mode in ("light", "dark"):
            m = pal["modes"][mode]
            self.assertEqual(m["primary"], "#f5c518")                       # brand yellow kept as the fill
            self.assertLess(cl.hex_to_oklch(m["onPrimary"])[0], 0.3)        # ... with near-black text
            self.assertGreaterEqual(cl.contrast_ratio(m["onPrimary"], m["primary"]), 7.0)
        # the yellow cannot be a link on white; the derived navy accent takes the job instead of a brown step
        light = pal["modes"]["light"]
        self.assertEqual(light["link"], light["accent"])
        self.assertGreaterEqual(cl.contrast_ratio(light["link"], "#ffffff"), 4.5)

    def test_dark_primary_link_keeps_3_to_1_against_body_text(self):
        # A near-black brand colour used to become the link itself (1.0-1.2:1 against the text). The link now takes
        # the nearest primary-scale step that clears 4.5:1 on the surfaces and 3:1 against body text (G183).
        for seed in ("#1a2b3c", "#3b2314"):
            pal = build(seed)
            light = pal["modes"]["light"]
            with self.subTest(seed=seed):
                self.assertNotEqual(light["link"], light["primary"])
                self.assertIn(light["link"], pal["scales"]["primary"]["steps"].values())
                for surface in ("background", "surface"):
                    self.assertGreaterEqual(cl.contrast_ratio(light["link"], light[surface]), 4.5)
                self.assertGreaterEqual(cl.contrast_ratio(light["link"], light["text"]), 3.0)
                self.assertLess(cl.contrast_ratio(light["primary"], light["text"]), 1.3)   # brand fill unchanged
                moves = [a for a in pal["build"]["adjustments"] if a["role"] == "link" and a["mode"] == "light"]
                self.assertEqual(moves[0]["scale"], "primary")

    def test_link_falls_back_to_accent_scale_then_to_old_behaviour(self):
        # near-black grey primary: the primary scale has no hue to offer, the accent scale supplies the link
        pal = build("#18181b", accent="#ea580c")
        light = pal["modes"]["light"]
        self.assertIn(light["link"], pal["scales"]["accent"]["steps"].values())
        self.assertGreaterEqual(cl.contrast_ratio(light["link"], light["text"]), 3.0)
        # no primary or accent step reaches both targets: the previous choice stays (the audit keeps its note)
        pal = build("#2b1a10")
        light = pal["modes"]["light"]
        steps = pal["scales"]["primary"]["steps"].values()
        self.assertIn(light["link"], steps)
        passing = [c for c in steps if cl.contrast_ratio(c, light["background"]) >= 4.5
                   and cl.contrast_ratio(c, light["surface"]) >= 4.5
                   and cl.contrast_ratio(c, light["text"]) >= 3.0]
        self.assertEqual(passing, [])
        self.assertLess(cl.contrast_ratio(light["link"], light["text"]), 3.0)

    def test_link_choice_never_worse_than_a_passing_step(self):
        # whenever a primary-scale step satisfies both link targets, the link role satisfies them too
        for seed in SEEDS + EDGE_SEEDS:
            pal = build(seed)
            for mode, r in pal["modes"].items():
                bgs = (r["background"], r["surface"])
                exists = any(min(cl.contrast_ratio(c, b) for b in bgs) >= 4.5 and cl.contrast_ratio(c, r["text"]) >= 3.0
                             and abs((cl.hex_to_oklch(c)[2] - cl.hex_to_oklch(pal["brand"][0]["hex"])[2] + 180) % 360 - 180)
                             <= pb.LINK_HUE_TOLERANCE
                             for c in pal["scales"]["primary"]["steps"].values())
                if exists and cl.hex_to_oklch(pal["brand"][0]["hex"])[1] >= 0.03:
                    with self.subTest(seed=seed, mode=mode):
                        self.assertGreaterEqual(cl.contrast_ratio(r["link"], r["text"]), 3.0)
                        self.assertGreaterEqual(min(cl.contrast_ratio(r["link"], b) for b in bgs), 4.5)

    def test_dark_mode_uses_lightened_step_of_same_scale(self):
        pal = build("#1f5f7a")
        dark = pal["modes"]["dark"]
        self.assertNotEqual(dark["primary"], "#1f5f7a")
        self.assertIn(dark["primary"], pal["scales"]["primary"]["steps"].values())
        self.assertGreater(cl.hex_to_oklch(dark["primary"])[0], cl.hex_to_oklch("#1f5f7a")[0])
        self.assertGreaterEqual(cl.contrast_ratio(dark["primary"], dark["background"]), 4.5)
        self.assertEqual(pal["modes"]["light"]["primary"], "#1f5f7a")
        self.assertTrue(any(a["role"] == "primary" and a["mode"] == "dark" for a in pal["build"]["adjustments"]))

    def test_auto_fix_never_invents_a_hue(self):
        for seed in SEEDS + EDGE_SEEDS:
            pal = build(seed)
            brand_scales = scale_hexes(pal, "primary", "accent")
            neutral = scale_hexes(pal, "neutral") | {"#ffffff"}
            for mode, r in pal["modes"].items():
                with self.subTest(seed=seed, mode=mode):
                    for role in ("primary", "accent", "link", "focus"):
                        self.assertIn(r[role], brand_scales, role)
                    for role in ("background", "surface", "surfaceAlt", "border", "text", "textMuted"):
                        self.assertIn(r[role], neutral, role)
                    for role in ("onPrimary", "onAccent"):
                        self.assertIn(r[role], neutral, role)
                    for role in ("success", "warning", "danger", "info"):
                        self.assertIn(r[role], pal["scales"][role]["steps"].values(), role)

    def test_dark_surface_rule(self):
        for seed in SEEDS:
            bg = build(seed)["modes"]["dark"]["background"]
            self.assertNotEqual(bg, "#000000")
            self.assertGreaterEqual(cl.contrast_ratio("#ffffff", bg), 15.8)

    def test_semantic_hues_move_away_from_brand(self):
        pal = build("#e50914")                          # a red brand: danger must not read as the brand
        for mode in ("light", "dark"):
            r = pal["modes"][mode]
            self.assertGreaterEqual(cl.delta_e2000(r["danger"], r["primary"]), 10, mode)
        self.assertTrue(any(n["scale"] == "danger" for n in pal["build"]["semantic_shifts"]))

    def test_success_and_danger_survive_cvd_when_possible(self):
        pal = build("#6d28d9")
        for mode in ("light", "dark"):
            r = pal["modes"][mode]
            for kind in cl.CVD_KINDS:
                d = cl.delta_e2000(cl.simulate_cvd(r["success"], kind), cl.simulate_cvd(r["danger"], kind))
                self.assertGreaterEqual(d, 10, f"{mode} {kind}")

    def test_accent_strategies(self):
        L, C, h = cl.hex_to_oklch("#1f5f7a")
        for strategy in ("family", "analogous", "complement", "mono"):
            acc, why = pb.derive_accent("#1f5f7a", strategy)
            self.assertTrue(cl.is_hex(acc) and why)
        fam = cl.hex_to_oklch(pb.derive_accent("#1f5f7a", "family")[0])[2]
        self.assertTrue(30 <= fam <= 90, fam)          # blue -> warm amber/orange accent
        navy = cl.hex_to_oklch(pb.derive_accent("#f5c518", "family")[0])
        self.assertLess(navy[0], 0.45)                 # yellow -> deep navy accent
        with self.assertRaises(pb.BuildError):
            pb.derive_accent("#1f5f7a", "triadic")

    def test_deterministic(self):
        with contextlib.redirect_stderr(io.StringIO()):
            a = pb.build_palette("#0f766e", name="X")
            b = pb.build_palette("#0f766e", name="X")
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_achromatic_primary_routes_actions_to_accent(self):
        # review-1 polish 0: near-black/near-white greys cannot be buttons or links
        for seed in ("#18181b", "#f5f5f5", "#ffffff"):
            pal = build(seed)
            acc = set(pal["scales"]["accent"]["steps"].values())
            for mode in ("light", "dark"):
                r = pal["modes"][mode]
                with self.subTest(seed=seed, mode=mode):
                    if seed == "#18181b" and mode == "light":
                        self.assertEqual(r["primary"], seed)          # review-2: charcoal button stays on light
                        self.assertEqual(r["onPrimary"], "#ffffff")
                    else:
                        self.assertEqual(r["primary"], r["accent"])
                        self.assertEqual(r["onPrimary"], r["onAccent"])
                    self.assertIn(r["link"], acc)
                    self.assertIn(r["focus"], acc)
                    self.assertGreaterEqual(cl.contrast_ratio(r["primary"], r["background"]), 1.5)
            self.assertTrue(any(a["role"] == "primary" and a.get("to_step") == "accent"
                                for a in pal["build"]["adjustments"]))
            self.assertEqual(pal["scales"]["primary"]["steps"][pal["scales"]["primary"]["anchor"]], seed)
            self.assertEqual(pa.audit_palette(pal)["counts"]["gate"], 0)
        # a chromatic dark brand is untouched
        self.assertEqual(build("#1f5f7a")["modes"]["light"]["primary"], "#1f5f7a")

    def test_near_black_keeps_its_button_in_light_mode(self):
        # review-2 polish 0 (Kavruk): charcoal + gold on cream must not give gold buttons at 1.77:1
        pal = build("#27201a", accent="#d2b373", light_bg="#f3f0ea")
        light, dark = pal["modes"]["light"], pal["modes"]["dark"]
        self.assertEqual(light["primary"], "#27201a")
        self.assertGreaterEqual(cl.contrast_ratio(light["onPrimary"], light["primary"]), 7)
        self.assertGreaterEqual(cl.contrast_ratio(light["primary"], light["background"]), 3)
        acc = set(pal["scales"]["accent"]["steps"].values())
        self.assertIn(light["link"], acc)                         # a charcoal link would read as body text
        self.assertIn(light["focus"], acc)
        self.assertEqual(dark["primary"], dark["accent"])        # on a dark ground the fill moves
        self.assertEqual(pa.audit_palette(pal)["counts"]["gate"], 0)

    def test_tinted_ground_border_and_alt_follow_the_ground(self):
        # review-2 polish 7: neutral-200 on cream was 1.10:1 and the alt surface read cold
        pal = build("#7a5c3e", light_bg="#f3f0ea")
        m = pal["modes"]["light"]
        gL, gC, gh = cl.hex_to_oklch(m["background"])
        self.assertGreaterEqual(cl.contrast_ratio(m["border"], m["background"]), 1.25)
        for role in ("surfaceAlt", "border"):
            L, C, h = cl.hex_to_oklch(m[role])
            self.assertLess(L, gL, role)
            self.assertLess(min(abs(h - gh), 360 - abs(h - gh)), 15, role)       # warm like the ground
        for bg in ("background", "surface", "surfaceAlt"):
            self.assertGreaterEqual(cl.contrast_ratio(m["textMuted"], m[bg]), 4.5)
        self.assertEqual(build("#7a5c3e")["modes"]["light"]["border"],          # white ground unchanged
                         build("#7a5c3e")["scales"]["neutral"]["steps"]["200"])

    def test_mid_tone_fill_prefers_white_label_one_step_darker(self):
        # review-1 polish 4: hazelnut + near-black label reads muddy
        pal = build("#2b6cb0", accent="#b8743a")
        light = pal["modes"]["light"]
        steps = pal["scales"]["accent"]["steps"]
        anchor = pal["scales"]["accent"]["anchor"]
        self.assertEqual(light["accent"], steps[cl.STEP_KEYS[cl.STEP_KEYS.index(anchor) + 1]])
        self.assertEqual(light["onAccent"], "#ffffff")
        self.assertTrue(any("muddy" in a.get("reason", "") for a in pal["build"]["adjustments"]))
        self.assertEqual(build("#f5c518")["modes"]["light"]["primary"], "#f5c518")   # light fills keep dark text

    def test_light_and_dark_grounds(self):
        cream = build("#7a5c3e", light_bg="#f7f1e8")
        light = cream["modes"]["light"]
        self.assertEqual(light["background"], "#f7f1e8")
        self.assertEqual(light["surface"], "#ffffff")
        for bg in ("background", "surface", "surfaceAlt"):
            self.assertGreaterEqual(cl.contrast_ratio(light["text"], light[bg]), 7.0)
            self.assertGreaterEqual(cl.contrast_ratio(light["textMuted"], light[bg]), 4.5)
        self.assertEqual(cream["build"]["grounds"]["light"], "#f7f1e8")
        self.assertEqual(pa.audit_palette(cream)["counts"]["gate"], 0)
        n50 = build("#1f5f7a", light_bg="neutral-50")
        self.assertEqual(n50["modes"]["light"]["background"], n50["scales"]["neutral"]["steps"]["50"])
        d9 = build("#1f5f7a", dark_bg="neutral-900")
        dark = d9["modes"]["dark"]
        self.assertEqual(dark["background"], d9["scales"]["neutral"]["steps"]["900"])
        self.assertGreater(cl.hex_to_oklch(dark["surface"])[0], cl.hex_to_oklch(dark["background"])[0])
        self.assertEqual(pa.audit_palette(d9)["counts"]["gate"], 0)
        with self.assertRaises(pb.BuildError):
            pb.build_palette("#1f5f7a", light_bg="neutral-55")

    def test_success_base_hue_needs_no_rotation(self):
        # review-1 polish 15: no success shift logged on ordinary palettes
        for seed in ("#1f5f7a", "#6d28d9", "#f5c518", "#2b6cb0"):
            self.assertFalse(any(n["scale"] == "success" for n in build(seed)["build"]["semantic_shifts"]), seed)




class TestAchromaticNeutral(unittest.TestCase):
    def test_achromatic_primary_keeps_grey_neutrals(self):
        # seen in an end-to-end run: a black primary + yellow accent tinted the greys to cream
        pal = build("#18181b", accent="#f5c518")
        cs = [cl.hex_to_oklch(v)[1] for v in pal["scales"]["neutral"]["steps"].values()]
        self.assertLess(max(cs), 0.002)
        self.assertEqual(pal["scales"]["neutral"]["tint_of"], "none")
        self.assertTrue(any(a.get("role") == "neutral" for a in pal["build"]["adjustments"]))
        tinted = build("#18181b", accent="#f5c518", neutral_tint="accent")
        self.assertGreater(max(cl.hex_to_oklch(v)[1] for v in tinted["scales"]["neutral"]["steps"].values()), 0.008)
        chroma = build("#1f5f7a")                                    # chromatic primary still tints
        self.assertEqual(chroma["scales"]["neutral"]["tint_of"], "primary")

class TestExtended(unittest.TestCase):
    EXTRAS = [("#7b4b2a", "Cocoa", "product variant"), ("#c9a227", "Honey", ""), ("#5b8c5a", "Sage", "")]

    def test_contract_fields(self):
        pal = build("#1f5f7a", extras=tuple(self.EXTRAS), feels=("calm", " local", "honest "))
        self.assertEqual(pal["feels"], ["calm", "local", "honest"])
        ext = pal["extended"]
        self.assertEqual([e["id"] for e in ext], ["ext-1", "ext-2", "ext-3"])
        dark = pal["modes"]["dark"]
        for e, (hx, name, usage) in zip(ext, self.EXTRAS):
            self.assertEqual((e["hex"], e["name"]), (hx, name))
            self.assertEqual(e.get("usage", ""), usage)
            sc = pal["scales"][e["id"]]
            self.assertEqual(sc["steps"][sc["anchor"]], hx)                       # anchor holds the hex exactly
            self.assertGreaterEqual(cl.contrast_ratio(e["on"], hx), 4.5)
            self.assertGreaterEqual(cl.contrast_ratio(e["dark"]["hex"], dark["background"]), 3.0)
            self.assertGreaterEqual(cl.contrast_ratio(e["dark"]["hex"], dark["surface"]), 3.0)
            self.assertIn(e["dark"]["hex"], sc["steps"].values())
            self.assertGreaterEqual(cl.contrast_ratio(e["dark"]["on"], e["dark"]["hex"]), 4.5)
            self.assertTrue(any(b["hex"] == hx and b.get("role") == "extended" for b in pal["brand"]))
        self.assertGreater(cl.contrast_ratio(ext[0]["dark"]["hex"], dark["background"]),
                           cl.contrast_ratio("#7b4b2a", dark["background"]))         # cocoa lightened for dark
        cl.validate_palette(json.loads(json.dumps(pal)))
        self.assertEqual(pa.audit_palette(pal)["counts"]["gate"], 0)

    def test_core_roles_are_untouched(self):
        plain, rich = build("#1f5f7a"), build("#1f5f7a", extras=tuple(self.EXTRAS))
        self.assertEqual(plain["modes"], rich["modes"])
        for k in ("primary", "accent", "neutral", "success", "warning", "danger", "info"):
            self.assertEqual(plain["scales"][k], rich["scales"][k])
        self.assertNotIn("extended", plain)
        self.assertNotIn("feels", plain)

    def test_limit_and_errors(self):
        six = tuple((f"#{c}", f"E{i}", "") for i, c in enumerate(
            ("7b4b2a", "c9a227", "5b8c5a", "8a6fb8", "2f5d46", "a0527a")))
        with self.assertRaises(pb.BuildError) as cm:
            pb.build_palette("#1f5f7a", extras=list(six))
        self.assertIn("at most 5", str(cm.exception))
        self.assertEqual(len(build("#1f5f7a", extras=six[:5])["extended"]), 5)
        for bad in (("calm",), ("a", "b", "c", "d", "e")):
            with self.assertRaises(pb.BuildError):
                pb.build_palette("#1f5f7a", feels=list(bad))
        self.assertEqual(pb.parse_extra("#7b4b2a:Cocoa:product variant"), ("#7b4b2a", "Cocoa", "product variant"))
        self.assertEqual(pb.parse_extra("rgb(123 75 42):Cocoa"), ("#7b4b2a", "Cocoa", ""))
        for bad in ("#7b4b2a", "#7b4b2a:", "#zzz:X"):
            with self.assertRaises(pb.BuildError):
                pb.parse_extra(bad)

    def test_from_partial_reads_extended(self):
        partial = cl.validate_palette({"schema": cl.SCHEMA_ID, "name": "P", "brand": [
            {"id": "brand-1", "hex": "#1f5f7a", "locked": True},
            {"id": "brand-2", "hex": "#c9a227", "role": "extended", "name": "Honey"},
            {"id": "brand-3", "hex": "#c2410c", "locked": True}],
            "extended": [{"id": "ext-1", "name": "Cocoa", "hex": "#7b4b2a"}], "feels": ["warm", "plain"]},
            partial=True)
        with contextlib.redirect_stderr(io.StringIO()):
            pal = pb.build_palette(None, partial=partial, extras=[("#5b8c5a", "Sage", "")])
        self.assertEqual([e["hex"] for e in pal["extended"]], ["#7b4b2a", "#c9a227", "#5b8c5a"])
        self.assertEqual(pal["modes"]["light"]["primary"], "#1f5f7a")
        self.assertEqual(pal["brand"][1]["hex"], "#c2410c")           # the role:extended entry is not the accent
        self.assertEqual(pal["scales"]["accent"]["steps"][pal["scales"]["accent"]["anchor"]], "#c2410c")
        self.assertEqual(pal["feels"], ["warm", "plain"])

    def test_cli(self):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "palette_build.py"), "#1f5f7a", "--json",
                            "--extra", "#7b4b2a:Cocoa:product variant", "--feels", "calm, local, honest"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        pal = json.loads(r.stdout)
        self.assertEqual(pal["extended"][0]["name"], "Cocoa")
        self.assertEqual(pal["feels"], ["calm", "local", "honest"])
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "palette_build.py"), "#1f5f7a",
                            "--extra", "#7b4b2a"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--extra", r.stderr)


class TestPartialAndLocked(unittest.TestCase):
    def test_locked_brand_and_given_roles_are_kept(self):
        partial = {"schema": cl.SCHEMA_ID, "name": "Measured",
                   "brand": [{"id": "brand-1", "hex": "#E50914", "source": "measured", "locked": True},
                             {"id": "brand-2", "hex": "#221f1f", "source": "measured", "locked": True}],
                   "modes": {"light": {"background": "#fffaf5"}}}
        partial = cl.validate_palette(partial, partial=True)
        with contextlib.redirect_stderr(io.StringIO()):
            pal = pb.build_palette(None, partial=partial)
        self.assertEqual([b["hex"] for b in pal["brand"]], ["#e50914", "#221f1f"])
        self.assertTrue(all(b["locked"] for b in pal["brand"]))
        self.assertEqual(pal["modes"]["light"]["background"], "#fffaf5")
        # roles were derived against the given background
        self.assertGreaterEqual(cl.contrast_ratio(pal["modes"]["light"]["text"], "#fffaf5"), 7.0)
        self.assertEqual(pal["scales"]["primary"]["steps"][pal["scales"]["primary"]["anchor"]], "#e50914")
        self.assertEqual(pal["scales"]["accent"]["steps"][pal["scales"]["accent"]["anchor"]], "#221f1f")
        self.assertIsNotNone(pal["brand"][0]["print"]["lab_d50"])

    def test_orphaned_locked_brand_colour_becomes_extended(self):
        # review-1 polish 7: a seed given together with --from must not silently orphan a locked colour; it keeps
        # its id (logo colours may name it) and gets the extended colour's scale and dark version
        partial = cl.validate_palette({"schema": cl.SCHEMA_ID, "name": "M", "brand": [
            {"id": "brand-1", "hex": "#e50914", "name": "Signal", "source": "measured", "locked": True}]},
            partial=True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            pal = pb.build_palette("#1f5f7a", accent="#f5c518", partial=partial)
        b1 = next(b for b in pal["brand"] if b["id"] == "brand-1")
        self.assertEqual((b1["hex"], b1.get("role"), b1["name"]), ("#e50914", "extended", "Signal"))
        self.assertEqual([b["hex"] for b in pal["brand"][:2]], ["#1f5f7a", "#f5c518"])
        self.assertEqual([e["hex"] for e in pal["extended"]], ["#e50914"])
        self.assertEqual(pal["build"]["warnings"], [])
        self.assertIn("brand-1 #e50914 is neither seed, accent nor a ground", err.getvalue())
        self.assertEqual(build("#1f5f7a")["build"]["warnings"], [])
        # no extended slot left: the old warning stays
        extras = [(h, f"E{i}", "") for i, h in enumerate(("#111827", "#7b4b2a", "#c9a227", "#0f766e", "#6d28d9"))]
        with contextlib.redirect_stderr(io.StringIO()):
            full = pb.build_palette("#1f5f7a", accent="#f5c518", partial=partial, extras=extras)
        self.assertTrue(any("brand-1" in w and "#e50914" in w for w in full["build"]["warnings"]))

    def test_brand_colour_equal_to_dark_ground_is_the_ground(self):
        # Tessellate C: a dark-first brand lists its night slate first; it is the ground, the seed is the next colour
        partial = cl.validate_palette({"schema": cl.SCHEMA_ID, "name": "Night", "brand": [
            {"id": "brand-1", "name": "Night Slate", "hex": "#10161d", "source": "chosen", "locked": True},
            {"id": "brand-2", "name": "Tile Yellow", "hex": "#f6d84a", "source": "chosen", "locked": True},
            {"id": "brand-3", "name": "Tile Blue", "hex": "#4f8cff", "source": "chosen", "locked": True}]},
            partial=True)
        with contextlib.redirect_stderr(io.StringIO()):
            pal = pb.build_palette(None, partial=partial, dark_bg="#10161d")
        self.assertEqual(pal["build"]["seed"], "#f6d84a")
        self.assertEqual(pal["build"]["accent"]["hex"], "#4f8cff")
        self.assertEqual(pal["modes"]["dark"]["background"], "#10161d")
        self.assertEqual(pal["build"]["warnings"], [])
        # brand[0]/brand[1] are primary/accent for readers; ids are unchanged
        self.assertEqual([(b["id"], b["hex"]) for b in pal["brand"]],
                         [("brand-2", "#f6d84a"), ("brand-3", "#4f8cff"), ("brand-1", "#10161d")])
        self.assertFalse(any(b.get("role") == "extended" for b in pal["brand"]))

    def test_ground_leaving_one_colour_invents_no_accent_hue(self):
        # Fernhill C: whinstone ground + one lichen green; the family rule would add a gold the designer never chose
        partial = cl.validate_palette({"schema": cl.SCHEMA_ID, "name": "Close", "brand": [
            {"id": "brand-1", "name": "Whinstone", "hex": "#1c2421", "source": "chosen", "locked": True},
            {"id": "brand-2", "name": "Lichen", "hex": "#bccb5c", "source": "chosen", "locked": True}]},
            partial=True)
        with contextlib.redirect_stderr(io.StringIO()):
            pal = pb.build_palette(None, partial=partial, dark_bg="#1c2421")
            family = pb.build_palette(None, partial=partial, dark_bg="#1c2421", strategy="family")
        self.assertEqual(pal["build"]["seed"], "#bccb5c")
        self.assertEqual(pal["build"]["accent"]["strategy"], "mono")
        lichen_h = cl.hex_to_oklch("#bccb5c")[2]
        accent_h = cl.hex_to_oklch(pal["build"]["accent"]["hex"])[2]
        self.assertLess(abs((accent_h - lichen_h + 180) % 360 - 180), 10)
        # an explicit strategy still wins
        self.assertEqual(family["build"]["accent"]["strategy"], "family")
        # a plain seed with no ground taken keeps the family default
        self.assertEqual(build("#1f5f7a")["build"]["accent"]["strategy"], "family")

    def test_neutral_tint_follows_a_dark_ground_colour(self):
        def hue_gap(a, b):
            return abs((a - b + 180) % 360 - 180)
        slate_h = cl.hex_to_oklch("#10161d")[2]
        auto = build("#f6d84a", accent="#4f8cff", dark_bg="#10161d")
        surf = auto["modes"]["dark"]["surface"]
        self.assertLess(hue_gap(cl.hex_to_oklch(surf)[2], slate_h), 20)      # cool surface on the slate ground
        self.assertEqual(auto["scales"]["neutral"]["tint_of"], "#10161d")
        warm = build("#f6d84a", accent="#4f8cff", dark_bg="#10161d", neutral_tint="primary")
        self.assertGreater(hue_gap(cl.hex_to_oklch(warm["modes"]["dark"]["surface"])[2], slate_h), 60)
        grey = build("#f6d84a", accent="#4f8cff", dark_bg="#111111")
        self.assertEqual(grey["scales"]["neutral"]["tint_of"], "none")
        self.assertEqual(build("#1f5f7a")["scales"]["neutral"]["tint_of"], "primary")   # no dark ground: as before

    def test_given_primary_or_accent_roles(self):
        # review-2 blocker 0: a given modes.<mode>.primary/accent crashed with KeyError
        for strategy in ("family", "complement"):
            for given in ({"primary": "#1f5f7a"}, {"accent": "#c2410c"}, {"primary": "#1f5f7a", "accent": "#c2410c"}):
                with self.subTest(strategy=strategy, given=given):
                    partial = cl.validate_palette({"schema": cl.SCHEMA_ID, "name": "Existing", "brand": [
                        {"id": "brand-1", "hex": "#1f5f7a", "source": "chosen", "locked": True}],
                        "modes": {"light": dict(given)}}, partial=True)
                    with contextlib.redirect_stderr(io.StringIO()):
                        pal = pb.build_palette(None, strategy=strategy, partial=partial)
                    light = pal["modes"]["light"]
                    for role, v in given.items():
                        self.assertEqual(light[role], v)
                    self.assertGreaterEqual(cl.contrast_ratio(light["onPrimary"], light["primary"]), 4.5)
                    self.assertGreaterEqual(cl.contrast_ratio(light["onAccent"], light["accent"]), 4.5)
                    cl.validate_palette(json.loads(json.dumps(pal)))

    def test_summary_measures_against_the_ground(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pb.main(["#274454", "--light-bg", "#f3f0ea", "--full", "-o", os.devnull])
        out = buf.getvalue()
        self.assertIn("light ground #f3f0ea", out)
        self.assertIn("with label", out)
        self.assertIn("fill vs ground", out)

    def test_derived_accent_is_not_locked(self):
        pal = build("#1f5f7a")
        self.assertTrue(pal["brand"][0]["locked"])
        self.assertEqual(pal["brand"][1]["source"], "derived")
        self.assertFalse(pal["brand"][1]["locked"])
        pal2 = build("#1f5f7a", accent="#c2410c")
        self.assertEqual(pal2["brand"][1]["hex"], "#c2410c")
        self.assertTrue(pal2["brand"][1]["locked"])


class TestCLI(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "palette_build.py"), *args],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)

    def test_help(self):
        r = self.run_cli("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("palette_build.py \"#1f5f7a\"", r.stdout)

    def test_stdout_json_and_file(self):
        r = self.run_cli("#0f766e", "--name", "Lagoon", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        pal = json.loads(r.stdout)
        self.assertEqual(pal["name"], "Lagoon")
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "p.json")
            r = self.run_cli("#0f766e", "--accent", "#f97316", "-o", out, "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout), json.load(open(out, encoding="utf-8")))
            cl.load_palette(out)

    def test_default_stdout_is_a_short_summary(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "p.json")
            r = self.run_cli("#0f766e", "--accent", "#f97316", "--name", "Lagoon", "-o", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLessEqual(len(r.stdout.encode("utf-8")), 1200)
            self.assertIn("palette 'Lagoon'", r.stdout)
            self.assertIn("-> " + out, r.stdout)
            self.assertNotIn('"scales"', r.stdout)
            full = self.run_cli("#0f766e", "--accent", "#f97316", "--name", "Lagoon", "--full")
            self.assertGreater(len(full.stdout), len(r.stdout))
            self.assertIn("neutral", full.stdout)
        r = self.run_cli("#0f766e", "-q")
        self.assertEqual(r.stdout, "")

    def test_ground_flags(self):
        r = self.run_cli("--help")
        self.assertIn("--light-bg", r.stdout)
        self.assertIn("--dark-bg", r.stdout)
        r = self.run_cli("#1f5f7a", "--light-bg", "#f7f1e8", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["modes"]["light"]["background"], "#f7f1e8")
        r = self.run_cli("#1f5f7a", "--dark-bg", "neutral-42")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--dark-bg", r.stderr)

    def test_errors(self):
        r = self.run_cli("#12345")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a colour", r.stderr)
        r = self.run_cli()
        self.assertEqual(r.returncode, 2)
        self.assertIn("seed", r.stderr)
        r = self.run_cli("#1f5f7a", "--neutral-chroma", "0.2")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
