"""Unit tests for skills/brand-identity/scripts/palette_audit.py (stdlib unittest)."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
EXAMPLE = os.path.join(ROOT, "skills", "brand-identity", "templates", "palette.example.json")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import colorlib as cl  # noqa: E402
import palette_audit as pa  # noqa: E402


def example():
    return cl.load_palette(EXAMPLE)


def ids(audit, severity=None):
    return [f["id"] for f in audit["findings"] if severity is None or f["severity"] == severity]


def finding(audit, fid):
    return next(f for f in audit["findings"] if f["id"] == fid)


def quiet_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = pa.main(argv)
    return code, out.getvalue(), err.getvalue()


class TestGates(unittest.TestCase):
    def test_example_passes(self):
        audit = pa.audit_palette(example())
        self.assertTrue(audit["passed"], ids(audit, "gate"))
        self.assertEqual(audit["schema"], "brand-identity/audit@1")
        self.assertEqual(audit["counts"]["gate"], 0)
        pairs = {(r["mode"], r["fg"], r["bg"]) for r in audit["contrast"]}
        for mode in ("light", "dark"):
            for pair in (("text", "background"), ("textMuted", "surfaceAlt"), ("onPrimary", "primary"),
                         ("onAccent", "accent"), ("link", "surface"), ("focus", "background")):
                self.assertIn((mode,) + pair, pairs)

    def test_777_on_white_fails_gate_without_rounding(self):
        code, out, err = quiet_main(["--colors", "#ffffff,#777777", "--roles", "background,text", "--format",
                                     "json"])
        self.assertEqual(code, 1)
        audit = json.loads(out)
        f = finding(audit, "contrast.light.text-on-background")
        self.assertEqual(f["severity"], "gate")
        self.assertEqual(f["measured"], 4.48)
        self.assertEqual(f["threshold"], 4.5)
        fix = f["suggested_fix"]
        self.assertGreaterEqual(cl.contrast_ratio(fix["to"], "#ffffff"), 4.5)
        self.assertLess(cl.delta_e2000(fix["to"], "#777777"), 2.0)          # minimal change
        code, _, _ = quiet_main(["--colors", "#ffffff,#767676", "--roles", "background,text"])
        self.assertEqual(code, 0)

    def test_on_primary_fix_prefers_better_text_colour(self):
        pal = cl.validate_palette(pa.palette_from_colors("#ffffff,#111111,#e50914,#000000",
                                                         "background,text,primary,onPrimary", "light"), partial=True)
        audit = pa.audit_palette(pal, partial=True)
        f = finding(audit, "contrast.light.onPrimary-on-primary")
        self.assertEqual(f["suggested_fix"]["to"], "#ffffff")              # 4.79:1 instead of 4.38:1

    def test_fix_stays_on_scale(self):
        pal = example()
        pal["modes"]["light"]["textMuted"] = pal["scales"]["neutral"]["steps"]["400"]
        audit = pa.audit_palette(pal)
        f = finding(audit, "contrast.light.textMuted-on-background")
        self.assertIn(f["suggested_fix"]["to"], pal["scales"]["neutral"]["steps"].values())
        self.assertIn("neutral-", f["suggested_fix"]["how"])

    def test_anchor_contract_is_a_gate(self):
        pal = example()
        anchor = pal["scales"]["primary"]["anchor"]
        pal["scales"]["primary"]["steps"][anchor] = "#205f7a"              # one unit off the brand hex
        audit = pa.audit_palette(pal)
        self.assertIn("anchor.primary", ids(audit, "gate"))
        self.assertFalse(audit["passed"])

    def test_exit_codes_and_write(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "palette.json")
            shutil.copy(EXAMPLE, p)
            code, out, err = quiet_main([p, "--write"])
            self.assertEqual(code, 0)
            self.assertIn("PASS", out)
            self.assertLessEqual(len(out.encode("utf-8")), 1200)
            self.assertNotIn("## Contrast", out)
            code, full, _ = quiet_main([p, "--full"])
            self.assertIn("## Contrast, light", full)
            stored = cl.load_palette(p)["audit"]
            self.assertTrue(stored["passed"])
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
            data["modes"]["dark"]["onPrimary"] = data["modes"]["dark"]["primary"]
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            code, out, _ = quiet_main([p, "--format", "json"])
            self.assertEqual(code, 1)
            self.assertIn("contrast.dark.onPrimary-on-primary", ids(json.loads(out), "gate"))

    def test_input_errors_exit_2(self):
        self.assertEqual(quiet_main([])[0], 2)
        self.assertEqual(quiet_main(["--colors", "#fff,#000", "--roles", "background"])[0], 2)
        code, _, err = quiet_main(["--colors", "#fff", "--roles", "headline"])
        self.assertEqual(code, 2)
        self.assertIn("unknown role", err)
        code, _, err = quiet_main(["--colors", "#ggg", "--roles", "text"])
        self.assertEqual(code, 2)
        self.assertEqual(quiet_main([os.path.join(ROOT, "missing.json")])[0], 2)

    def test_missing_roles_are_contract_gates(self):
        # review-1 B3: a palette with roles removed must not pass the audit (export/previews reject it)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "palette.json")
            with open(EXAMPLE, encoding="utf-8") as fh:
                data = json.load(fh)
            del data["modes"]["dark"]["text"]
            del data["modes"]["light"]["onPrimary"]
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            code, out, _ = quiet_main([p, "--format", "json"])
            self.assertEqual(code, 1)
            gates = ids(json.loads(out), "gate")
            self.assertIn("contract.missing-role.dark.text", gates)
            self.assertIn("contract.missing-role.light.onPrimary", gates)
            code, out, _ = quiet_main([p, "--partial", "--format", "json"])   # measured/half-built: explicit
            self.assertEqual(code, 0)
            self.assertFalse(any(i.startswith("contract.") for i in ids(json.loads(out))))
            self.assertTrue(json.loads(out)["partial"])

    def test_missing_scale_and_mode_are_gates(self):
        pal = example()
        del pal["scales"]["neutral"]
        del pal["modes"]["dark"]
        gates = ids(pa.audit_palette(pal), "gate")
        self.assertIn("contract.missing-scale.neutral", gates)
        self.assertIn("contract.missing-mode.dark", gates)
        self.assertEqual(ids(pa.audit_palette(example()), "gate"), [])

    def test_colors_input_is_always_partial(self):
        code, out, _ = quiet_main(["--colors", "#ffffff,#111111", "--roles", "background,text", "--format", "json"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["partial"])


class TestWarnings(unittest.TestCase):
    def test_cvd_red_green_semantic_pair(self):
        pal = cl.validate_palette(pa.palette_from_colors("#ffffff,#1a1a1a,#2e7d32,#c62828",
                                                         "background,text,success,danger", "light"), partial=True)
        audit = pa.audit_palette(pal, partial=True)
        self.assertIn("cvd.light.deutan.success-danger", ids(audit, "warn"))
        self.assertNotIn("cvd.light.tritan.success-danger", ids(audit))
        f = finding(audit, "cvd.light.deutan.success-danger")
        self.assertIn("icon", f["suggested_fix"]["how"])
        kinds = {r["kind"] for r in audit["cvd"]}
        self.assertEqual(kinds, set(cl.CVD_KINDS))

    def test_isoluminant_vibration(self):
        pal = cl.validate_palette(pa.palette_from_colors("#ffffff,#111111,#d03030,#3a8a3a",
                                                         "background,text,primary,accent", "light"), partial=True)
        audit = pa.audit_palette(pal, partial=True)
        self.assertTrue(any(i.startswith("isoluminant.") for i in ids(audit, "warn")))
        self.assertIn("harmony.primary-accent", ids(audit, "info"))       # Ou-Luo: advisory only

    def test_mid_lightness_trap_and_gamut(self):
        audit = pa.audit_palette(cl.validate_palette(pa.palette_from_colors("#e50914", "brand", "light"),
                                                     partial=True), partial=True)
        self.assertIn("structure.mid-lightness.brand-1", ids(audit, "warn"))
        self.assertIn("gamut.brand-1", ids(audit, "info"))

    def test_hue_count_and_distinct(self):
        pal = cl.validate_palette(pa.palette_from_colors("#e50914,#1f5f7a,#f5c518,#6d28d9,#e8101c",
                                                         "brand,brand,brand,brand,brand", "light"), partial=True)
        audit = pa.audit_palette(pal, partial=True)
        self.assertIn("structure.hue-count", ids(audit, "warn"))
        self.assertIn("distinct.brand-1-brand-5", ids(audit, "warn"))
        self.assertIn("structure.greyscale.brand-1-brand-5", ids(audit, "info"))

    def test_link_must_differ_from_body_text(self):
        pal = cl.validate_palette(pa.palette_from_colors("#ffffff,#111111,#1f3a5f", "background,text,link",
                                                         "light"), partial=True)
        audit = pa.audit_palette(pal, partial=True)
        f = finding(audit, "contrast.light.link-on-text")
        self.assertEqual(f["severity"], "warn")
        to = f["suggested_fix"]["to"]
        self.assertGreaterEqual(cl.contrast_ratio(to, "#111111"), 3.0)

    def test_dark_link_vs_text_is_info(self):
        pal = example()
        pal["modes"]["dark"]["link"] = "#c8d8e8"           # passes on the dark ground, 1.3:1 vs body text
        audit = pa.audit_palette(pal)
        f = finding(audit, "contrast.dark.link-on-text")
        self.assertEqual(f["severity"], "info")
        self.assertIn("underline links in dark mode", f["message"])

    def test_lightness_range_uses_brand_and_action_colours(self):
        audit = pa.audit_palette(example())                     # built palette: no noise
        self.assertNotIn("structure.lightness-range", ids(audit, "warn"))
        flat = cl.validate_palette(pa.palette_from_colors("#ffffff,#111111,#1f5f7a,#2a7291",
                                                          "background,text,primary,accent", "light"), partial=True)
        self.assertIn("structure.lightness-range", ids(pa.audit_palette(flat, partial=True), "warn"))

    def test_identical_primary_and_accent_are_not_a_collision(self):
        pal = example()
        for m in ("light", "dark"):
            pal["modes"][m]["primary"] = pal["modes"][m]["accent"]
            pal["modes"][m]["onPrimary"] = pal["modes"][m]["onAccent"]
        self.assertFalse(any(i.startswith("distinct.") and "primary-accent" in i for i in ids(pa.audit_palette(pal))))

    def test_dark_surface_rules(self):
        pal = example()
        pal["modes"]["dark"]["background"] = "#000000"
        self.assertIn("dark.surface-pure-black", ids(pa.audit_palette(pal), "info"))
        pal["modes"]["dark"]["background"] = "#333333"
        self.assertIn("dark.surface-too-light", ids(pa.audit_palette(pal), "warn"))

    def test_scale_spacing(self):
        pal = example()
        st = pal["scales"]["neutral"]["steps"]
        st["100"] = st["50"]
        self.assertIn("scale.neutral.50-100", ids(pa.audit_palette(pal), "warn"))

    def test_markdown_report(self):
        md = pa.markdown(example(), pa.audit_palette(example()))
        self.assertIn("**PASS**", md)
        self.assertIn("| onPrimary / primary |", md)
        self.assertIn("Colour-vision simulation", md)
        self.assertNotIn("APCA", md)


def built(seed, **kw):
    import palette_build as pb
    with contextlib.redirect_stderr(io.StringIO()):
        return pb.build_palette(seed, **kw)


class TestHarmonyAndGrounds(unittest.TestCase):
    def test_ou_luo_defers_to_pairing_table(self):
        pal = built("#0f766e")                                     # teal + coral scores negative
        f = finding(pa.audit_palette(pal), "harmony.primary-accent")
        self.assertEqual(f["severity"], "info")
        self.assertIn("references/pairing.md takes precedence", f["message"])
        self.assertNotIn("inharmonious", f["message"])

    def test_grounds(self):
        pal = built("#8a1c2b")                                     # claret + gold
        kraft = pa.parse_ground("kraft")
        self.assertTrue(kraft["approximate"])
        self.assertIn("8,114,486", kraft["source"])
        audit = pa.audit_palette(pal, grounds=[kraft, pa.parse_ground("white=#ffffff")])
        gold = pal["brand"][1]["id"]
        f = finding(audit, f"ground.kraft.{gold}")
        self.assertEqual(f["severity"], "warn")
        self.assertIn("white underlay", f["message"])
        self.assertIn("approximate", f["message"])
        self.assertNotIn("underlay", finding(audit, f"ground.white.{gold}")["message"])
        self.assertNotIn("ground.white.brand-1", ids(audit))     # claret on white is fine
        rows = [r for r in audit["grounds"] if r["ground"] == "kraft"]
        self.assertTrue(rows and all("delta_e2000" in r for r in rows))
        given = pa.parse_ground("kraft=#c8a47e")
        self.assertEqual(given, {"name": "kraft", "hex": "#c8a47e", "approximate": False, "source": "given"})
        for bad in ("foil", "=#fff", "kraft=#zz"):
            with self.assertRaises(pa.AuditError):
                pa.parse_ground(bad)

    def test_ground_cli_repeatable(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "p.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(built("#8a1c2b"), fh)
            code, out, _ = quiet_main([p, "--ground", "kraft=#c8a47e", "--ground", "white=#ffffff", "--full"])
            self.assertEqual(code, 0)
            self.assertIn("## Print grounds", out)
            self.assertIn("kraft #c8a47e", out)
            self.assertEqual(quiet_main([p, "--ground", "foil"])[0], 2)


class TestContextAndSummary(unittest.TestCase):
    CARD_PAIRS = {("text", "surface"), ("text", "surfaceAlt"), ("textMuted", "surface"),
                  ("textMuted", "surfaceAlt"), ("link", "surface")}

    def test_identity_context_skips_card_level_text_contrast(self):
        pal = example()
        pal["modes"]["light"]["text"] = "#8a8a8a"      # fails on every ground
        pal["modes"]["light"]["surface"] = "#f0f0f0"
        standalone = pa.audit_palette(pal)
        identity = pa.audit_palette(pal, context="identity")
        self.assertEqual(identity["context"], "identity")
        self.assertIn("contrast.light.text-on-surface", ids(standalone, "gate"))
        self.assertNotIn("contrast.light.text-on-surface", ids(identity))
        # the page-level pair stays a gate in both contexts
        self.assertIn("contrast.light.text-on-background", ids(identity, "gate"))
        pairs = {(r["fg"], r["bg"]) for r in identity["contrast"]}
        self.assertFalse(pairs & self.CARD_PAIRS)
        self.assertIn(("onPrimary", "primary"), pairs)
        self.assertIn("contrast.light.text-on-surface", identity["skipped"])
        self.assertEqual(standalone["skipped"], [])

    def test_unknown_context_is_an_error(self):
        with self.assertRaises(pa.AuditError):
            pa.audit_palette(example(), context="card")

    def test_cli_context_and_summary_size(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "p.json")
            pal = example()
            pal["modes"]["light"]["text"] = "#8a8a8a"
            pal["modes"]["light"]["textMuted"] = "#9a9a9a"
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(pal, fh)
            code, out, _ = quiet_main([p, "--context", "identity"])
            self.assertEqual(code, 1)
            self.assertLessEqual(len(out.encode("utf-8")), 1200)
            self.assertIn("[context: identity]", out)
            self.assertIn("FAIL", out)
            self.assertIn("gate", out.splitlines()[1])
            code, out, _ = quiet_main([p, "--json", "--context", "identity"])
            self.assertEqual(json.loads(out)["context"], "identity")


class TestExtendedAudit(unittest.TestCase):
    def test_hue_count_ignores_extended(self):
        extras = [("#7b4b2a", "Cocoa", ""), ("#c9a227", "Honey", ""), ("#5b8c5a", "Sage", ""), ("#8a6fb8", "Dusk", "")]
        pal = built("#1f5f7a", extras=extras)
        audit = pa.audit_palette(pal)
        self.assertNotIn("structure.hue-count", ids(audit))
        self.assertEqual(audit["metrics"]["brand_hues"], 2)
        self.assertEqual(audit["extended"]["count"], 4)

    def test_similar_extended_colours_warn(self):
        pal = built("#1f5f7a", extras=[("#7b4b2a", "Cocoa", ""), ("#7e4e2c", "Cocoa 2", "")])
        audit = pa.audit_palette(pal)
        f = finding(audit, "extended.distinct.ext-1-ext-2")
        self.assertEqual(f["severity"], "warn")
        self.assertEqual(f["roles"], ["ext-1", "ext-2"])
        self.assertLess(f["measured"], 10)
        self.assertEqual(audit["extended"]["closest"]["pair"], ["ext-1", "ext-2"])
        near_accent = built("#1f5f7a", extras=[("#ea8b2a", "Almost accent", "")])
        self.assertIn("extended.distinct.ext-1-light.accent", ids(pa.audit_palette(near_accent), "warn"))

    def test_cvd_on_and_ui_checks(self):
        pal = built("#1f5f7a", extras=[("#2e7d32", "Leaf", ""), ("#c62828", "Berry", ""), ("#e8d9b5", "Paper", "")])
        audit = pa.audit_palette(pal)
        self.assertTrue(any(i.startswith("extended.cvd.") and "ext-1-ext-2" in i for i in ids(audit, "warn")))
        self.assertIn("extended.ui.light.ext-3", ids(audit, "warn"))            # pale paper as a chart series
        pal["extended"][1]["on"] = "#000000"                                     # berry + black = 4.x
        pal["extended"][0]["on"] = pal["extended"][0]["hex"]                     # unreadable
        audit = pa.audit_palette(pal)
        self.assertIn("extended.on.light.ext-1", ids(audit, "gate"))
        self.assertEqual(finding(audit, "extended.on.light.ext-2")["severity"],
                         "warn")

    def test_contract_and_grounds(self):
        pal = built("#1f5f7a", extras=[("#c9a227", "Honey", "")])
        del pal["scales"]["ext-1"]
        self.assertIn("contract.missing-scale.ext-1", ids(pa.audit_palette(pal), "gate"))
        pal = built("#1f5f7a", extras=[("#c9a227", "Honey", "")])
        audit = pa.audit_palette(pal, grounds=[pa.parse_ground("white=#ffffff")])
        self.assertTrue(any(r["colour"] == "ext-1" for r in audit["grounds"]))
        self.assertIn("ground.white.ext-1", ids(audit, "warn"))

    def test_example_has_clean_extended(self):
        audit = pa.audit_palette(example())
        self.assertEqual(audit["extended"]["count"], 3)
        self.assertFalse([i for i in ids(audit) if i.startswith("extended.")])


class TestCLI(unittest.TestCase):
    def test_help(self):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "palette_audit.py"), "--help"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--colors", r.stdout)
        self.assertIn("exit status", r.stdout)
        self.assertIn("--ground", r.stdout)


if __name__ == "__main__":
    unittest.main()
