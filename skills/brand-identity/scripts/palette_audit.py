#!/usr/bin/env python3
"""Audit a brand-identity palette.json (or a bare list of role colours) and report what is measurably broken.

Checks (thresholds: references/accessibility.md and colorlib's sources; WCAG 2.x is the only contrast gate, no APCA):
  contrast     every role pair used together, both modes: text/textMuted on background/surface/surfaceAlt,
               onPrimary/primary, onAccent/accent, link on background/surface (4.5:1, gate); focus and semantic
               colours on background/surface (3:1 non-text, gate; semantic < 4.5:1 also warns: not usable as text);
               primary/accent fills on the background (3:1, warn); link vs body text (3:1 unless underlined, warn);
               border on background (3:1, info: dividers are exempt, input outlines are not).
  distinct     CIEDE2000 >= 10 between role-bearing brand colours and between semantic and brand roles (warn).
  cvd          simulated CIEDE2000 >= 10 for primary/accent, success/danger, link/text under protan, deutan
               (Machado 2009) and tritan (Brettel 1997) (warn).
  isoluminant  two saturated colours (C* > 30) of different hue with Delta L* < 12 vibrate (warn).
  structure    lightness range (one colour L* >= 85, one <= 30), hue count (> 3 brand hues warns, hue entropy),
               mid-lightness trap (a brand colour < 5:1 with both white and black), greyscale Delta L* between brand
               colours (< 15, info), scale spacing (adjacent deltaE OK >= 0.02, monotonic), anchor contract (the
               anchor step must hold a brand hex exactly, gate), tinted neutral range, dark surface (white >= 15.8:1),
               dark-mode brand derivative (>= 4.5:1), sRGB gamut edge (print risk, info), Ou-Luo harmony (advisory).
  extended     ext-1..ext-5: CIEDE2000 >= 10 to each other and to primary/accent, CVD separation, readable `on`
               text (4.5:1; < 3:1 gate), 3:1 on the light and dark background for stripes and chart series;
               the audit JSON gets an "extended" block (count, rows, closest pair). Hue count ignores them.
  grounds      (--ground kraft=#hex, repeatable) brand, primary and accent on print stocks: < 3:1 warns.
  contract     (unless --partial) every role in both modes, primary + neutral scales with 11 steps, a brand colour:
               each gap is a gate (contract.missing-role.<mode>.<role> ...), like a failed contrast pair.
Each finding: {id, severity: gate|warn|info, roles, measured, threshold, message, suggested_fix}. suggested_fix is
the minimal change: another step of the same scale when one passes, otherwise a lightness shift at the same hue.
Contexts (--context): `standalone` (default) runs everything above. `identity` is used by the identity pipeline,
whose card audit owns the card-level text contrast (it measures the colours actually rendered on the card), so the
palette audit skips the six pairs that sit on card surfaces: text, textMuted and link on surface / surfaceAlt in
both modes (their ids are listed under "skipped" in the audit object). Page-level pairs (on background), focus and
semantic colours, CVD, distinctness, structure and the contract are unchanged.
Output: stdout is a short summary (<= 1.2 KB: verdict, gate/warn/info counts, the first findings, where the full
report is); --full prints the Markdown report, --json the audit object. Diagnostics go to stderr.
Exit status: 0 = no gate failed, 1 = at least one gate failed, 2 = unreadable input.

Usage:
  python3 scripts/palette_audit.py palette.json                    # short summary on stdout
  python3 scripts/palette_audit.py palette.json --full             # Markdown report
  python3 scripts/palette_audit.py palette.json --json             # the audit object as JSON
  python3 scripts/palette_audit.py palette.json --context identity # skip card-level text contrast
  python3 scripts/palette_audit.py palette.json --write            # also store it under "audit" in the file
  python3 scripts/palette_audit.py measured.json --partial         # half-built/measured file: no contract gates
  python3 scripts/palette_audit.py palette.json --ground kraft=#c8a47e --ground white=#ffffff   # label stocks
  python3 scripts/palette_audit.py --colors "#ffffff,#1a1a1a,#e50914,#ffffff" \\
      --roles background,text,primary,onPrimary                    # audit measured site colours (light mode)
  python3 scripts/palette_audit.py --colors "#121212,#f5f5f5" --roles background,text --mode dark
"""
import argparse
import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import colorlib as cl  # noqa: E402

AUDIT_SCHEMA = "brand-identity/audit@1"
CONTEXTS = ("standalone", "identity")
# Identity context: the card audit measures text on the card surfaces, so these backgrounds are not checked here.
CARD_SURFACE_ROLES = ("surface", "surfaceAlt")
CARD_LEVEL_FG = ("text", "textMuted", "link")
TEXT = 4.5
TEXT_AAA = 7.0
UI = 3.0
DISTINCT_DE = 10.0
CVD_DE = 10.0
ISO_CHROMA = 30.0
ISO_DL = 12.0
GREY_DL = 15.0
STEP_JND = 0.02
DARK_SURFACE_WHITE = 15.8

# (fg, bg, target, severity when failing, kind, note)
CONTRAST_PAIRS = [
    ("text", "background", TEXT, "gate", "text", "body text"),
    ("text", "surface", TEXT, "gate", "text", "body text on cards"),
    ("text", "surfaceAlt", TEXT, "gate", "text", "body text on alternate surfaces"),
    ("textMuted", "background", TEXT, "gate", "text", "secondary text"),
    ("textMuted", "surface", TEXT, "gate", "text", "secondary text on cards"),
    ("textMuted", "surfaceAlt", TEXT, "gate", "text", "secondary text on alternate surfaces"),
    ("onPrimary", "primary", TEXT, "gate", "text", "label on primary buttons"),
    ("onAccent", "accent", TEXT, "gate", "text", "label on accent buttons / CTAs"),
    ("link", "background", TEXT, "gate", "text", "links"),
    ("link", "surface", TEXT, "gate", "text", "links on cards"),
    ("focus", "background", UI, "gate", "ui", "focus ring (WCAG 1.4.11 / 2.4.13)"),
    ("focus", "surface", UI, "gate", "ui", "focus ring on cards"),
    ("success", "background", UI, "gate", "ui", "success icon/border"),
    ("warning", "background", UI, "gate", "ui", "warning icon/border"),
    ("danger", "background", UI, "gate", "ui", "danger icon/border"),
    ("info", "background", UI, "gate", "ui", "info icon/border"),
    ("success", "background", TEXT, "warn", "text", "success colour used as text"),
    ("warning", "background", TEXT, "warn", "text", "warning colour used as text"),
    ("danger", "background", TEXT, "warn", "text", "danger colour used as text"),
    ("info", "background", TEXT, "warn", "text", "info colour used as text"),
    ("primary", "background", UI, "warn", "ui", "primary fill against the page (needs a label or border if lower)"),
    ("accent", "background", UI, "warn", "ui", "accent fill against the page (needs a label or border if lower)"),
    ("link", "text", UI, "warn", "ui", "links distinguishable from body text without underline (WCAG G183)"),
    ("border", "background", UI, "info", "ui", "borders: dividers are exempt, input outlines need 3:1"),
]
CVD_PAIRS = [("primary", "accent"), ("success", "danger"), ("link", "text")]
SEMANTIC = ("success", "warning", "danger", "info")


class AuditError(Exception):
    pass


def log(msg):
    print(msg, file=sys.stderr)


def r2(x):
    return round(x, 2)


# ----------------------------------------------------------------------------- fix helpers

def scale_lookup(pal, hex_, prefer=()):
    """(scale_name, step) holding hex_, trying `prefer` scales first; None if not in any scale."""
    scales = pal.get("scales") or {}
    names = [n for n in prefer if n in scales] + [n for n in scales if n not in prefer]
    for n in names:
        for k, v in (scales[n].get("steps") or {}).items():
            if v == hex_:
                return n, k
    return None


def shift_lightness(hex_, ok, direction, step=0.005):
    """Smallest OKLCH lightness move (same hue, chroma gamut-mapped) that satisfies ok(hex); None if impossible."""
    L, C, h = cl.hex_to_oklch(hex_)
    for i in range(1, int(1 / step) + 1):
        L2 = L + direction * i * step
        if not 0.0 < L2 < 1.0:
            break
        cand = cl.oklch_to_hex(L2, C, h)
        if ok(cand):
            return cand, r2(L2 - L)
    return None


def nearest_step(pal, scale, step, ok):
    steps = pal["scales"][scale]["steps"]
    keys = list(cl.STEP_KEYS)
    i0 = keys.index(step)
    for d in range(1, len(keys)):
        for j in (i0 + d, i0 - d):
            if 0 <= j < len(keys) and ok(steps[keys[j]]):
                return keys[j], steps[keys[j]]
    return None


def fix_contrast(pal, mode, fg_role, bg_role, fg, bg, target):
    """Minimal change making fg/bg reach target. Adjusts the foreground (or the fill for on-colour pairs)."""
    def passes(c):
        return cl.contrast_ratio(c, bg) >= target
    pair_fill = fg_role in ("onPrimary", "onAccent")
    if pair_fill:
        # 1) a better on-colour; 2) another step of the fill's scale
        N = ((pal.get("scales") or {}).get("neutral") or {}).get("steps") or {}
        cands = [c for c in ("#ffffff", N.get("950"), N.get("50"), "#000000") if c]
        best = cl.best_text_on(bg, cands)
        if cl.contrast_ratio(best, bg) >= target:
            return {"change": f"modes.{mode}.{fg_role}", "from": fg, "to": best,
                    "how": "use the higher-contrast text colour", "ratio_after": r2(cl.contrast_ratio(best, bg))}
        loc = scale_lookup(pal, bg, prefer=("primary" if fg_role == "onPrimary" else "accent",))
        if loc:
            hit = nearest_step(pal, loc[0], loc[1], lambda c: cl.contrast_ratio(cl.best_text_on(c, cands), c) >= target)
            if hit:
                on = cl.best_text_on(hit[1], cands)
                return {"change": f"modes.{mode}.{bg_role}", "from": bg, "to": hit[1],
                        "how": f"use {loc[0]}-{hit[0]} (same scale) with {on} text",
                        "ratio_after": r2(cl.contrast_ratio(on, hit[1]))}
        return {"change": f"modes.{mode}.{bg_role}", "from": bg, "to": None,
                "how": "no step of this scale carries 4.5:1 text; use a darker/lighter derivative for buttons"}
    prefer = ("primary",) if fg_role in ("link", "focus", "primary") else \
        (fg_role,) if fg_role in SEMANTIC else ("neutral",) if fg_role in ("text", "textMuted", "border") else ()
    loc = scale_lookup(pal, fg, prefer)
    if loc:
        hit = nearest_step(pal, loc[0], loc[1], passes)
        if hit:
            return {"change": f"modes.{mode}.{fg_role}", "from": fg, "to": hit[1],
                    "how": f"use {loc[0]}-{hit[0]} (same scale)", "ratio_after": r2(cl.contrast_ratio(hit[1], bg))}
    direction = 1 if cl.relative_luminance(fg) > cl.relative_luminance(bg) else -1
    moved = shift_lightness(fg, passes, direction) or shift_lightness(fg, passes, -direction)
    if moved:
        return {"change": f"modes.{mode}.{fg_role}", "from": fg, "to": moved[0],
                "how": f"{'lighten' if moved[1] > 0 else 'darken'} by OKLCH L {moved[1]:+.2f}, same hue",
                "ratio_after": r2(cl.contrast_ratio(moved[0], bg))}
    return {"change": f"modes.{mode}.{bg_role}", "from": bg, "to": None,
            "how": f"{fg_role} cannot reach {target}:1 on this {bg_role}; change the {bg_role}"}


def fix_separation(a, b, ok, label):
    """Move b's lightness (same hue) until ok(a, b'); try both directions, smallest move wins."""
    best = None
    for d in (1, -1):
        r = shift_lightness(b, lambda c: ok(a, c), d)
        if r and (best is None or abs(r[1]) < abs(best[1])):
            best = r
    if best:
        return {"change": label, "from": b, "to": best[0],
                "how": f"{'lighten' if best[1] > 0 else 'darken'} by OKLCH L {best[1]:+.2f}, same hue"}
    return {"change": label, "from": b, "to": None, "how": "no lightness move separates them; change the hue"}


# ----------------------------------------------------------------------------- audit

class Auditor:
    def __init__(self, pal, grounds=None, context="standalone"):
        self.pal = pal
        self.context = context
        self.skipped = []
        self.grounds = grounds or []
        self.ground_rows = []
        self.extended_summary = None
        self.findings = []
        self.contrast = []
        self.cvd = []
        self.metrics = {}

    def add(self, fid, severity, roles, measured, threshold, message, fix=None):
        self.findings.append({"id": fid, "severity": severity, "roles": roles, "measured": measured,
                              "threshold": threshold, "message": message, "suggested_fix": fix})

    def modes(self):
        return [(m, r) for m, r in (self.pal.get("modes") or {}).items() if isinstance(r, dict) and r]

    def brand(self, core=True):
        """Brand colours with a valid hex. core=True leaves out role "extended" entries: the core checks (hue
        count, distinctness, lightness structure) are about the identity colours; extended colours have their own
        extended.* checks."""
        return [b for b in self.pal.get("brand") or [] if cl.is_hex(b.get("hex"))
                and not (core and b.get("role") == "extended")]

    def extended(self):
        return [e for e in self.pal.get("extended") or [] if isinstance(e, dict) and cl.is_hex(e.get("hex"))]

    # --- contrast
    def check_contrast(self):
        for mode, r in self.modes():
            for fg_role, bg_role, target, sev, kind, note in CONTRAST_PAIRS:
                if fg_role not in r or bg_role not in r:
                    continue
                if fg_role == "link" and bg_role == "text" and r["link"] == r["text"]:
                    continue
                if (self.context == "identity" and fg_role in CARD_LEVEL_FG and bg_role in CARD_SURFACE_ROLES):
                    self.skipped.append(f"contrast.{mode}.{fg_role}-on-{bg_role}")
                    continue
                fg, bg = r[fg_role], r[bg_role]
                ratio = cl.contrast_ratio(fg, bg)
                ok = ratio >= target
                dl = abs(cl.hex_to_lab(fg)[0] - cl.hex_to_lab(bg)[0])
                self.contrast.append({"mode": mode, "fg": fg_role, "bg": bg_role, "fg_hex": fg, "bg_hex": bg,
                                      "ratio": r2(ratio), "target": target, "kind": kind, "severity": sev,
                                      "pass": ok, "delta_l": round(dl, 1)})
                if fg_role == "link" and bg_role == "text" and mode == "dark":
                    # On dark grounds a link step that clears both 4.5:1 on the background and 3:1 against
                    # near-white text rarely exists; underlining is the fix, so this is information, not risk.
                    sev, note = "info", "underline links in dark mode (WCAG G183: without underline a link needs " \
                                        "3:1 against body text)"
                    self.contrast[-1]["severity"] = sev
                if not ok:
                    self.add(f"contrast.{mode}.{fg_role}-on-{bg_role}" + ("" if sev == "gate" or target == UI
                                                                          else "-text"),
                             sev, [f"{mode}.{fg_role}", f"{mode}.{bg_role}"], r2(ratio), target,
                             f"{mode}: {fg_role} {fg} on {bg_role} {bg} is {ratio:.2f}:1, below {target}:1 "
                             f"({note}; WCAG 2, no rounding)",
                             fix_contrast(self.pal, mode, fg_role, bg_role, fg, bg, target))
        dark = (self.pal.get("modes") or {}).get("dark") or {}
        if "background" in dark:
            bg = dark["background"]
            w = cl.contrast_ratio("#ffffff", bg)
            self.metrics["dark_surface_white_ratio"] = r2(w)
            if bg == "#000000":
                self.add("dark.surface-pure-black", "info", ["dark.background"], bg, "#121212-ish",
                         "dark background is pure #000000: fine for OLED/battery reasons, otherwise a dark grey "
                         "(#121212-ish) reduces halation of white text (Material)")
            elif w < DARK_SURFACE_WHITE:
                self.add("dark.surface-too-light", "warn", ["dark.background"], r2(w), DARK_SURFACE_WHITE,
                         f"dark background {bg} gives white only {w:.2f}:1; Material asks >= 15.8:1 so raised "
                         "surfaces still keep 4.5:1", fix_separation("#ffffff", bg,
                                                                      lambda a, c: cl.contrast_ratio(a, c) >=
                                                                      DARK_SURFACE_WHITE, "modes.dark.background"))
            if "primary" in dark:
                pr = cl.contrast_ratio(dark["primary"], bg)
                if pr < TEXT:
                    self.add("dark.brand-derivative", "warn", ["dark.primary", "dark.background"], r2(pr), TEXT,
                             f"dark-mode primary {dark['primary']} is {pr:.2f}:1 on the dark background; use a "
                             "lightened derivative (OKLCH L ~0.80-0.88, lower chroma)",
                             fix_contrast(self.pal, "dark", "primary", "background", dark["primary"], bg, TEXT))

    # --- distinguishability
    def check_distinct(self):
        brand = self.brand()
        for i in range(len(brand)):
            for j in range(i + 1, len(brand)):
                a, b = brand[i], brand[j]
                de = cl.delta_e2000(a["hex"], b["hex"])
                if de < DISTINCT_DE:
                    adj, keep = (a, b) if b.get("locked") and not a.get("locked") else (b, a)
                    self.add(f"distinct.{a['id']}-{b['id']}", "warn", [a["id"], b["id"]], r2(de), DISTINCT_DE,
                             f"brand colours {a['hex']} and {b['hex']} are only CIEDE2000 {de:.1f} apart; "
                             "two role-bearing colours should differ by >= 10",
                             fix_separation(keep["hex"], adj["hex"],
                                            lambda x, y: cl.delta_e2000(x, y) >= DISTINCT_DE, f"brand {adj['id']}"))
        for mode, r in self.modes():
            pairs = [("primary", "accent")] + [(s, b) for s in SEMANTIC for b in ("primary", "accent")]
            for x, y in pairs:
                if x in r and y in r and r[x] != r[y]:   # identical = one deliberate colour, not a confusion
                    de = cl.delta_e2000(r[x], r[y])
                    if de < DISTINCT_DE:
                        self.add(f"distinct.{mode}.{x}-{y}", "warn", [f"{mode}.{x}", f"{mode}.{y}"], r2(de),
                                 DISTINCT_DE,
                                 f"{mode}: {x} {r[x]} and {y} {r[y]} are only CIEDE2000 {de:.1f} apart; users may "
                                 f"read one as the other",
                                 fix_separation(r[y], r[x], lambda p, q: cl.delta_e2000(p, q) >= DISTINCT_DE,
                                                f"modes.{mode}.{x}"))

    # --- colour vision deficiency
    def check_cvd(self):
        for mode, r in self.modes():
            for x, y in CVD_PAIRS:
                if x not in r or y not in r or r[x] == r[y]:
                    continue
                for kind in cl.CVD_KINDS:
                    sx, sy = cl.simulate_cvd(r[x], kind), cl.simulate_cvd(r[y], kind)
                    de = cl.delta_e2000(sx, sy)
                    ok = de >= CVD_DE
                    self.cvd.append({"mode": mode, "pair": [x, y], "kind": kind, "hex": [r[x], r[y]],
                                     "sim": [sx, sy], "delta_e2000": r2(de), "pass": ok})
                    if not ok:
                        def sep(a, c, kind=kind):
                            return cl.delta_e2000(cl.simulate_cvd(a, kind), cl.simulate_cvd(c, kind)) >= CVD_DE
                        fix = fix_separation(r[x], r[y], sep, f"modes.{mode}.{y}")
                        fix["how"] = "never carry meaning by colour alone (icon/label); or " + fix["how"]
                        self.add(f"cvd.{mode}.{kind}.{x}-{y}", "warn", [f"{mode}.{x}", f"{mode}.{y}"], r2(de),
                                 CVD_DE, f"{mode}: {x} and {y} collapse to CIEDE2000 {de:.1f} for {kind}opia "
                                         f"({sx} vs {sy})", fix)

    # --- isoluminance
    def check_isoluminance(self):
        pairs = []
        for mode, r in self.modes():
            if "primary" in r and "accent" in r:
                pairs.append((f"{mode}.primary", r["primary"], f"{mode}.accent", r["accent"]))
        brand = self.brand()
        for i in range(len(brand)):
            for j in range(i + 1, len(brand)):
                pairs.append((brand[i]["id"], brand[i]["hex"], brand[j]["id"], brand[j]["hex"]))
        seen = set()
        for na, a, nb, b in pairs:
            if (a, b) in seen:
                continue
            seen.add((a, b))
            La, Ca, ha = cl.lab_to_lch(cl.hex_to_lab(a))
            Lb, Cb, hb = cl.lab_to_lch(cl.hex_to_lab(b))
            dh = min(abs(ha - hb), 360 - abs(ha - hb))
            if Ca > ISO_CHROMA and Cb > ISO_CHROMA and dh > 30 and abs(La - Lb) < ISO_DL:
                self.add(f"isoluminant.{na}-{nb}", "warn", [na, nb], round(abs(La - Lb), 1), ISO_DL,
                         f"{a} and {b} are both saturated (C* {Ca:.0f}/{Cb:.0f}) with almost equal lightness "
                         f"(Delta L* {abs(La - Lb):.1f}); edges between them vibrate and text on either is unreadable",
                         fix_separation(a, b, lambda p, q: abs(cl.hex_to_lab(p)[0] - cl.hex_to_lab(q)[0]) >= ISO_DL,
                                        nb))

    # --- structure
    def check_structure(self):
        brand = self.brand()
        light = (self.pal.get("modes") or {}).get("light") or {}
        # Brand colours plus the action roles only: page white and body-text black would always satisfy it.
        pool = list(dict.fromkeys([b["hex"] for b in brand] + [light[k] for k in ("primary", "accent") if k in light]))
        if len(pool) >= 2:
            Ls = [cl.hex_to_lab(c)[0] for c in pool]
            lo, hi = min(Ls), max(Ls)
            self.metrics["lightness_range"] = [round(lo, 1), round(hi, 1)]
            if hi - lo < 25:
                self.add("structure.lightness-range", "warn", ["brand", "light.primary", "light.accent"],
                         round(hi - lo, 1), 25,
                         f"brand and action colours span only Delta L* {hi - lo:.1f}; without a clear light/dark "
                         "step between them the palette reads flat (lightness contrast is the strongest harmony "
                         "predictor, O'Donovan 2011)",
                         {"change": "brand/accent", "how": "move the accent (not a locked brand colour) at least "
                                                           "25 L* away from the primary"})
            elif hi < 85 and lo > 30:
                self.add("structure.lightness-range", "info", ["brand", "light.primary", "light.accent"],
                         [round(lo, 1), round(hi, 1)], [30, 85],
                         "no brand/action colour is very light (L* >= 85) and none is very dark (L* <= 30); the neutrals "
                         "carry the extremes, which is fine for most identities", None)
        chroma_pool = [b["hex"] for b in brand] + [light[k] for k in ("primary", "accent") if k in light]
        hues = []
        for c in chroma_pool:
            L, C, h = cl.hex_to_oklch(c)
            if C >= 0.04:
                hues.append(h)
        clusters = []
        for h in sorted(hues):
            if not any(min(abs(h - c), 360 - abs(h - c)) <= 30 for c in clusters):
                clusters.append(h)
        ent = cl.hue_entropy(hues) if hues else None
        self.metrics["brand_hues"] = len(clusters)
        self.metrics["hue_entropy"] = None if ent is None else r2(ent)
        if len(clusters) > 3:
            self.add("structure.hue-count", "warn", ["brand"], len(clusters), 3,
                     f"{len(clusters)} distinct brand hues; 2-3 hues is the sweet spot (O'Donovan 2011), semantic "
                     "colours not counted", {"change": "brand", "how": "drop or neutralise the least used hue"})
        for b in brand:
            w, k = cl.contrast_ratio(b["hex"], "#ffffff"), cl.contrast_ratio(b["hex"], "#000000")
            if max(w, k) < 5.0:
                self.add(f"structure.mid-lightness.{b['id']}", "warn", [b["id"]], [r2(w), r2(k)], 5.0,
                         f"{b['hex']} is a mid-lightness trap: white {w:.2f}:1, black {k:.2f}:1; body text on it "
                         "needs a darker or lighter derivative",
                         {"change": b["id"], "how": "keep the brand colour for large areas/logo; put text on a "
                                                    "derivative step of its scale"})
            L, C, h = cl.hex_to_oklch(b["hex"])
            cmax = cl.max_chroma(L, h)
            if C >= 0.1 and cmax > 0 and C / cmax >= 0.9:
                self.add(f"gamut.{b['id']}", "info", [b["id"]], r2(C / cmax), 0.9,
                         f"{b['hex']} sits at the sRGB gamut edge ({C / cmax:.0%} of max chroma); vivid colours "
                         "like this often fall outside CMYK process gamut: specify Lab D50 + tolerance, check with "
                         "the printer, consider a spot colour", None)
        for i in range(len(brand)):
            for j in range(i + 1, len(brand)):
                a, b = brand[i], brand[j]
                dl = abs(cl.hex_to_lab(a["hex"])[0] - cl.hex_to_lab(b["hex"])[0])
                if dl < GREY_DL:
                    self.add(f"structure.greyscale.{a['id']}-{b['id']}", "info", [a["id"], b["id"]], round(dl, 1),
                             GREY_DL, f"{a['hex']} and {b['hex']} differ by only Delta L* {dl:.1f}; side by side in "
                                      "a logo they merge in greyscale/one-colour print", None)
        light_pa = [light.get("primary"), light.get("accent")]
        if all(light_pa):
            ch = cl.ou_luo_harmony(*light_pa)
            self.metrics["ou_luo_primary_accent"] = r2(ch)
            if ch < -0.3:
                self.add("harmony.primary-accent", "info", ["light.primary", "light.accent"], r2(ch), -0.3,
                         f"Ou-Luo two-colour model scores {light_pa[0]} + {light_pa[1]} at {ch:+.2f} (negative = "
                         "less harmonious on a grey CRT test, no context): model signal only; the pairing table in "
                         "references/pairing.md takes precedence", None)

    def check_scales(self):
        brand_hexes = {b["hex"] for b in self.brand(core=False)}
        for name, sc in (self.pal.get("scales") or {}).items():
            steps = sc.get("steps") or {}
            if not all(k in steps for k in cl.STEP_KEYS):
                continue
            Ls = [cl.hex_to_oklch(steps[k])[0] for k in cl.STEP_KEYS]
            for (ka, kb), (La, Lb) in zip(zip(cl.STEP_KEYS, cl.STEP_KEYS[1:]), zip(Ls, Ls[1:])):
                d = cl.delta_e_ok(steps[ka], steps[kb])
                if d < STEP_JND:
                    self.add(f"scale.{name}.{ka}-{kb}", "warn", [f"scales.{name}"], round(d, 4), STEP_JND,
                             f"scales.{name} steps {ka} and {kb} are only deltaE OK {d:.3f} apart (JND 0.02); "
                             "they look identical", None)
                if Lb >= La:
                    self.add(f"scale.{name}.order-{ka}-{kb}", "warn", [f"scales.{name}"], [round(La, 3),
                                                                                         round(Lb, 3)], "L decreasing",
                             f"scales.{name}: step {kb} is not darker than {ka}", None)
            anchor = sc.get("anchor")
            if anchor is not None and brand_hexes and steps.get(str(anchor)) not in brand_hexes:
                self.add(f"anchor.{name}", "gate", [f"scales.{name}"], steps.get(str(anchor)), "a brand hex",
                         f"scales.{name}.steps.{anchor} = {steps.get(str(anchor))} is not one of the brand colours; "
                         "the anchor step must hold the brand hex exactly (palette contract)",
                         {"change": f"scales.{name}", "how": "rebuild the scale with palette_build.py"})
            if name == "neutral":
                cs = [cl.hex_to_oklch(steps[k])[1] for k in cl.STEP_KEYS]
                self.metrics["neutral_chroma"] = [round(min(cs), 4), round(max(cs), 4)]
                if max(cs) > 0.045:
                    self.add("neutral.too-colourful", "info", ["scales.neutral"], round(max(cs), 4), 0.045,
                             "neutral scale chroma exceeds 0.045; it reads as a colour, not a tinted grey", None)

    def check_contract(self):
        """Full palette@1 contract (skipped with partial=True): every role in both modes, required scales with all
        11 steps, a name and at least one brand colour. Each gap is a gate: downstream scripts reject such files."""
        pal = self.pal
        if not pal.get("brand"):
            self.add("contract.missing-brand", "gate", ["brand"], 0, ">= 1", "brand[] is empty; the palette has "
                     "no identity colour", {"change": "brand", "how": "add the brand colour(s) and rebuild"})
        scales = pal.get("scales") or {}
        for name in ("primary", "neutral"):
            if name not in scales:
                self.add(f"contract.missing-scale.{name}", "gate", [f"scales.{name}"], None, "11 steps",
                         f"scales.{name} is missing", {"change": "scales", "how": "rebuild with palette_build.py"})
        for name, sc in scales.items():
            missing = [k for k in cl.STEP_KEYS if k not in ((sc or {}).get("steps") or {})]
            if missing:
                self.add(f"contract.incomplete-scale.{name}", "gate", [f"scales.{name}"], missing, "11 steps",
                         f"scales.{name} lacks step(s) {', '.join(missing)}",
                         {"change": f"scales.{name}", "how": "rebuild with palette_build.py"})
        modes = pal.get("modes") or {}
        for mode in ("light", "dark"):
            if not isinstance(modes.get(mode), dict):
                self.add(f"contract.missing-mode.{mode}", "gate", [f"modes.{mode}"], None, "all role keys",
                         f"modes.{mode} is missing", {"change": f"modes.{mode}", "how": "rebuild with "
                                                      "palette_build.py --from this file"})
                continue
            for role in cl.ROLE_KEYS:
                if role not in modes[mode]:
                    self.add(f"contract.missing-role.{mode}.{role}", "gate", [f"{mode}.{role}"], None, "present",
                             f"modes.{mode}.{role} is missing; every role key is required in both modes (palette "
                             "contract; export_tokens.py and the previews reject the file)",
                             {"change": f"modes.{mode}.{role}", "how": "rebuild with palette_build.py --from this "
                                                                       "file, which keeps the roles you set"})
        for e in self.extended():
            if e.get("id") not in scales:
                self.add(f"contract.missing-scale.{e.get('id')}", "gate", [str(e.get("id"))], None, "11 steps",
                         f"scales.{e.get('id')} is missing for extended colour {e['hex']}",
                         {"change": "scales", "how": "rebuild with palette_build.py --from this file"})
        if not any(f["id"].startswith("contract.") for f in self.findings):
            try:
                cl.validate_palette(json.loads(json.dumps(pal)), partial=False)
            except cl.PaletteError as e:
                self.add("contract.invalid", "gate", ["palette"], None, "brand-identity/palette@1", str(e), None)

    # --- extended colours (ext-1..ext-5)
    def check_extended(self):
        """Extended colours must stay apart from each other and from the core action colours (CIEDE2000 >= 10,
        also after CVD simulation between extended colours), carry readable `on` text (4.5:1; 3:1 = large labels
        only, warn; below 3 = gate) and work as stripes/chart series on the page (3:1 on light and dark grounds)."""
        ext = self.extended()
        modes = dict(self.modes())
        light, dark = modes.get("light") or {}, modes.get("dark") or {}
        rows, pairs = [], []
        core = [(f"light.{k}", light[k]) for k in ("primary", "accent") if k in light]
        for i, a in enumerate(ext):
            for b in ext[i + 1:]:
                pairs.append((a["id"], a["hex"], b["id"], b["hex"], True))
            for name, hx in core:
                pairs.append((a["id"], a["hex"], name, hx, False))
        closest = None
        for na, a, nb, b, both_ext in pairs:
            de = cl.delta_e2000(a, b)
            if closest is None or de < closest[2]:
                closest = (na, nb, de)
            if de < DISTINCT_DE:
                self.add(f"extended.distinct.{na}-{nb}", "warn", [na, nb], r2(de), DISTINCT_DE,
                         f"{na} {a} and {nb} {b} are only CIEDE2000 {de:.1f} apart; as variants or chart series "
                         "they will be confused", fix_separation(b if not both_ext else a, a if not both_ext else b,
                                                                  lambda p, q: cl.delta_e2000(p, q) >= DISTINCT_DE,
                                                                  na))
            if both_ext:
                for kind in cl.CVD_KINDS:
                    sd = cl.delta_e2000(cl.simulate_cvd(a, kind), cl.simulate_cvd(b, kind))
                    if sd < CVD_DE and de >= DISTINCT_DE:
                        self.add(f"extended.cvd.{kind}.{na}-{nb}", "warn", [na, nb], r2(sd), CVD_DE,
                                 f"{na} and {nb} collapse to CIEDE2000 {sd:.1f} for {kind}opia; label variants and "
                                 "chart series, don't rely on colour alone", None)
        for e in ext:
            eid = e["id"]
            for mode, fill, on, bg in (("light", e["hex"], e.get("on"), light.get("background")),
                                       ("dark", (e.get("dark") or {}).get("hex"), (e.get("dark") or {}).get("on"),
                                        dark.get("background"))):
                if not fill:
                    continue
                row = {"id": eid, "mode": mode, "hex": fill}
                if on:
                    ratio = cl.contrast_ratio(on, fill)
                    row.update(on=on, on_ratio=r2(ratio))
                    if ratio < UI:
                        self.add(f"extended.on.{mode}.{eid}", "gate", [eid], r2(ratio), TEXT,
                                 f"{mode}: text {on} on {eid} {fill} is {ratio:.2f}:1, unreadable even as a large "
                                 "label", {"change": f"extended.{eid}.on", "from": on,
                                           "to": cl.best_text_on(fill, ("#ffffff", "#000000")),
                                           "how": "use the higher-contrast text colour"})
                    elif ratio < TEXT:
                        self.add(f"extended.on.{mode}.{eid}", "warn", [eid], r2(ratio), TEXT,
                                 f"{mode}: text {on} on {eid} {fill} is {ratio:.2f}:1: large labels only (>= 24 px "
                                 "or 18.5 px bold), not body text", None)
                if bg:
                    ui = cl.contrast_ratio(fill, bg)
                    row.update(ground=bg, ui_ratio=r2(ui))
                    if ui < UI:
                        loc = scale_lookup(self.pal, fill, prefer=(eid,))
                        fix = None
                        if loc:
                            hit = nearest_step(self.pal, loc[0], loc[1], lambda c, bg=bg: cl.contrast_ratio(c, bg) >= UI)
                            if hit:
                                fix = {"change": f"extended.{eid}" + (".dark.hex" if mode == "dark" else ""),
                                       "from": fill, "to": hit[1], "how": f"use {loc[0]}-{hit[0]} for stripes, "
                                                                          "chart series and thin lines"}
                        self.add(f"extended.ui.{mode}.{eid}", "warn", [eid], r2(ui), UI,
                                 f"{mode}: {eid} {fill} is {ui:.2f}:1 on the {mode} background {bg}; as a stripe, "
                                 "chart series or thin line it needs 3:1 (WCAG 1.4.11)", fix)
                rows.append(row)
        self.extended_summary = {
            "count": len(ext), "rows": rows,
            "closest": None if closest is None else {"pair": [closest[0], closest[1]],
                                                     "delta_e2000": r2(closest[2])}}

    # --- print / paper grounds (e.g. --ground kraft=#c8a47e)
    def check_grounds(self):
        light = (self.pal.get("modes") or {}).get("light") or {}
        items = [(b["id"], b["hex"]) for b in self.brand()]
        for role in ("primary", "accent"):
            if role in light and all(light[role] != hx for _, hx in items):
                items.append((f"light.{role}", light[role]))
        items += [(e["id"], e["hex"]) for e in self.extended() if all(e["hex"] != hx for _, hx in items)]
        for g in self.grounds:
            name, ghex = g["name"], g["hex"]
            for label, hx in items:
                ratio = cl.contrast_ratio(hx, ghex)
                de = cl.delta_e2000(hx, ghex)
                row = {"ground": name, "ground_hex": ghex, "approximate": g.get("approximate", False),
                       "colour": label, "hex": hx, "ratio": r2(ratio), "delta_e2000": r2(de), "pass": ratio >= UI}
                self.ground_rows.append(row)
                if ratio < UI:
                    approx = (f" ({name} {ghex} is an approximate published value, {g['source']}; measure your own "
                              "stock)") if g.get("approximate") else ""
                    light_stock = cl.hex_to_oklch(ghex)[0] >= 0.95
                    remedy = "a darker shade" if light_stock else "a darker shade or a white underlay"
                    self.add(f"ground.{slug_id(name)}.{label}", "warn", [label], r2(ratio), UI,
                             f"{label} {hx} on {name} {ghex} is {ratio:.2f}:1 (CIEDE2000 {de:.1f}); on {name} use "
                             f"{remedy}. Screen estimate: on uncoated or brown stock the ink is absorbed and "
                             f"darkens, proof on the real paper{approx}",
                             {"change": label, "how": f"use a darker step of its scale on {name}"
                                                      + ("" if light_stock else ", or print a white underlay below it")})


    def run(self, partial=False):
        if not partial:
            self.check_contract()
        self.check_contrast()
        self.check_distinct()
        self.check_cvd()
        self.check_isoluminance()
        self.check_structure()
        self.check_scales()
        if self.extended():
            self.check_extended()
        if self.grounds:
            self.check_grounds()
        order = {"gate": 0, "warn": 1, "info": 2}
        self.findings.sort(key=lambda f: order[f["severity"]])
        counts = {s: sum(1 for f in self.findings if f["severity"] == s) for s in ("gate", "warn", "info")}
        return {"schema": AUDIT_SCHEMA, "tool": "brand-identity/palette_audit.py", "passed": counts["gate"] == 0,
                "context": self.context, "skipped": self.skipped,
                "counts": counts, "metrics": self.metrics, "contrast": self.contrast, "cvd": self.cvd,
                "grounds": self.ground_rows, "extended": self.extended_summary,
                "findings": self.findings}


def audit_palette(pal, partial=False, grounds=None, context="standalone"):
    """Audit a palette dict; returns the audit object.
    partial=False (default) also gates the palette@1 contract (all roles, both modes, full scales);
    partial=True audits only what is present (measured site colours, --colors input).
    grounds: [{"name", "hex", ...}] print stocks to check brand, primary and accent against.
    context: "standalone" (everything) or "identity" (skips the card-level text contrast pairs, see the docstring)."""
    if context not in CONTEXTS:
        raise AuditError(f"unknown context {context!r}; choose from {', '.join(CONTEXTS)}")
    audit = Auditor(pal, grounds, context).run(partial)
    audit["partial"] = bool(partial)
    return audit


def slug_id(name):
    return "".join(ch if ch.isalnum() else "-" for ch in str(name).lower()).strip("-") or "ground"


# Approximate published grounds for `--ground NAME` without a value. Users should measure their own stock.
KNOWN_GROUNDS = {
    "kraft": {"hex": "#a99083", "approximate": True,
              "source": "brown kraft liner, uncoated: CIELAB L* 61.9 a* 8.0 b* 10.6 (mean of three readings, "
                        "US 8,114,486 B2 Table 1; illuminant not stated, read as D50 print convention)"},
    "white": {"hex": "#ffffff", "approximate": False, "source": "paper white as screen white"},
}


def parse_ground(spec):
    """'kraft=#c8a47e' -> {"name": "kraft", "hex": "#c8a47e"}; 'kraft' -> the documented approximation."""
    name, _, value = spec.partition("=")
    name = name.strip()
    if not name:
        raise AuditError(f"--ground {spec!r}: give name=#hex, e.g. kraft=#c8a47e")
    if value.strip():
        try:
            return {"name": name, "hex": cl.normalize_hex(value.strip()), "approximate": False, "source": "given"}
        except ValueError as e:
            raise AuditError(f"--ground {spec!r}: {e}") from None
    known = KNOWN_GROUNDS.get(name.lower())
    if not known:
        raise AuditError(f"--ground {name!r} has no value; give {name}=#hex (measure the paper), or use one of "
                         f"{', '.join(KNOWN_GROUNDS)} for a documented approximation")
    return dict(known, name=name)


# ----------------------------------------------------------------------------- report

def markdown(pal, audit):
    out = [f"# Palette audit: {pal.get('name', 'colours')}", ""]
    c = audit["counts"]
    out.append(f"**{'PASS' if audit['passed'] else 'FAIL'}**: {c['gate']} gate, {c['warn']} warn, {c['info']} info. "
               "Gates are WCAG 2.x contrast and the palette contract; warnings are measured risks, not taste.")
    out.append("")
    for mode in ("light", "dark"):
        rows = [r for r in audit["contrast"] if r["mode"] == mode]
        if not rows:
            continue
        out += [f"## Contrast, {mode}", "", "| pair | colours | ratio | target | result |", "|---|---|---|---|---|"]
        for r in rows:
            res = "pass" if r["pass"] else ("FAIL" if r["severity"] == "gate" else r["severity"])
            out.append(f"| {r['fg']} / {r['bg']} | {r['fg_hex']} / {r['bg_hex']} | {r['ratio']:.2f} | "
                       f"{r['target']:g} | {res} |")
        out.append("")
    if audit["cvd"]:
        out += ["## Colour-vision simulation (CIEDE2000 after simulation, target >= 10)", "",
                "| mode | pair | protan | deutan | tritan |", "|---|---|---|---|---|"]
        keyed = {}
        for r in audit["cvd"]:
            keyed.setdefault((r["mode"], "/".join(r["pair"])), {})[r["kind"]] = r
        for (mode, pair), kinds in keyed.items():
            cells = []
            for k in cl.CVD_KINDS:
                r = kinds.get(k)
                cells.append("-" if r is None else f"{r['delta_e2000']:.1f}{'' if r['pass'] else ' !'}")
            out.append(f"| {mode} | {pair} | " + " | ".join(cells) + " |")
        out.append("")
    ex = audit.get("extended")
    if ex and ex.get("count"):
        out += [f"## Extended colours ({ex['count']})", "", "| colour | mode | hex | label | label ratio | on page |",
                "|---|---|---|---|---|---|"]
        for r in ex["rows"]:
            out.append(f"| {r['id']} | {r['mode']} | {r['hex']} | {r.get('on', '-')} | "
                       f"{r.get('on_ratio', 0):.2f} | {r.get('ui_ratio', 0):.2f} |")
        if ex.get("closest"):
            out.append(f"\nClosest pair: {' / '.join(ex['closest']['pair'])}, CIEDE2000 "
                       f"{ex['closest']['delta_e2000']:.1f}.")
        out.append("")
    if audit.get("grounds"):
        out += ["## Print grounds (screen estimate; proof on the real stock)", "",
                "| ground | colour | ratio | CIEDE2000 | result |", "|---|---|---|---|---|"]
        for r in audit["grounds"]:
            g = f"{r['ground']} {r['ground_hex']}" + (" (approx.)" if r["approximate"] else "")
            out.append(f"| {g} | {r['colour']} {r['hex']} | {r['ratio']:.2f} | {r['delta_e2000']:.1f} | "
                       f"{'pass' if r['pass'] else 'warn'} |")
        out.append("")
    if audit["metrics"]:
        out += ["## Metrics", ""]
        for k, v in audit["metrics"].items():
            out.append(f"- {k}: {v}")
        out.append("")
    out += ["## Findings", ""]
    if not audit["findings"]:
        out.append("None.")
    for f in audit["findings"]:
        out.append(f"- **{f['severity']}** `{f['id']}`: {f['message']}")
        fx = f.get("suggested_fix")
        if fx:
            to = f" -> {fx['to']}" if fx.get("to") else ""
            frm = f" {fx['from']}" if fx.get("from") else ""
            after = f" ({fx['ratio_after']:.2f}:1)" if fx.get("ratio_after") is not None else ""
            out.append(f"  - fix: {fx.get('change', '')}{frm}{to}: {fx['how']}{after}")
    return "\n".join(out) + "\n"


def summary_text(pal, audit, source=None, shown=4):
    """The default stdout: verdict, counts, the first findings (gates first), pointers. <= 1.2 KB."""
    c = audit["counts"]
    ctx = "" if audit.get("context", "standalone") == "standalone" else f" [context: {audit['context']}]"
    lines = [f"palette audit '{pal.get('name', 'colours')}': {'PASS' if audit['passed'] else 'FAIL'} - "
             f"{c['gate']} gate, {c['warn']} warn, {c['info']} info{ctx}"]
    for f in audit["findings"][:shown]:
        fx = f.get("suggested_fix") or {}
        hint = f" | fix: {fx['change']}" + (f" -> {fx['to']}" if fx.get("to") else "") if fx.get("change") else ""
        lines.append(f"{f['severity']:<4} {f['id']}: {f['message']}{hint}")
    if len(audit["findings"]) > shown:
        lines.append(f"... {len(audit['findings']) - shown} more finding(s)")
    if audit.get("skipped"):
        lines.append(f"skipped by context: {len(audit['skipped'])} card-level pair(s)")
    lines.append("full report: --full (Markdown) or --json" + (f"; stored in {source}" if source else ""))
    return cl.clip_summary(lines)


def palette_from_colors(colors, roles, mode):
    cols = [c.strip() for c in colors.split(",") if c.strip()]
    rls = [r.strip() for r in roles.split(",") if r.strip()]
    if len(cols) != len(rls):
        raise AuditError(f"--colors has {len(cols)} colour(s) but --roles has {len(rls)}; give one role per colour")
    m, brand = {}, []
    for c, r in zip(cols, rls):
        try:
            hx = cl.normalize_hex(c)
        except ValueError as e:
            raise AuditError(str(e)) from None
        if r == "brand":
            brand.append({"id": f"brand-{len(brand) + 1}", "hex": hx, "source": "measured"})
            continue
        if r not in cl.ROLE_KEYS:
            raise AuditError(f"unknown role {r!r}; roles: {', '.join(cl.ROLE_KEYS)}, brand")
        m[r] = hx
        if r in ("primary", "accent") and all(b["hex"] != hx for b in brand):
            brand.append({"id": f"brand-{len(brand) + 1}", "hex": hx, "source": "measured"})
    return {"schema": cl.SCHEMA_ID, "name": "measured colours", "brand": brand, "modes": {mode: m}}


def main(argv=None):
    cl.setup_utf8_console()
    ap = argparse.ArgumentParser(description="Audit a brand-identity palette: WCAG 2 contrast gates, CVD, "
                                             "distinguishability, lightness structure.",
                                 epilog="exit status: 0 = no gate failed, 1 = a gate failed, 2 = unreadable input\n\nexamples:"
                                 + __doc__.split("Usage:")[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("palette", nargs="?", help="palette.json (full contract unless --partial)")
    ap.add_argument("--colors", help="comma-separated colours instead of a palette file")
    ap.add_argument("--roles", help="comma-separated role per --colors entry (role keys, or 'brand')")
    ap.add_argument("--mode", default="light", choices=("light", "dark"), help="mode of --colors (default light)")
    ap.add_argument("--full", action="store_true", help="print the full Markdown report instead of the summary")
    ap.add_argument("--json", action="store_true", help="print the audit object as JSON instead of the summary")
    ap.add_argument("--format", choices=("summary", "md", "json"), help="report format (md = --full, json = --json)")
    ap.add_argument("--context", default="standalone", choices=CONTEXTS,
                    help="identity: skip the card-level text contrast pairs the identity audit owns (default: "
                         "standalone)")
    ap.add_argument("--write", action="store_true", help="store the audit object under \"audit\" in palette.json")
    ap.add_argument("--ground", action="append", default=[], metavar="NAME[=#hex]",
                    help="print/paper ground to check brand, primary and accent against (repeatable), e.g. "
                         "--ground kraft=#c8a47e --ground white=#ffffff; 'kraft' alone uses a documented, "
                         "approximate kraft-liner colour (measure your own stock)")
    ap.add_argument("--partial", action="store_true",
                    help="audit only what is present (measured or half-built palettes); without it every missing "
                         "role/scale is a contract gate. --colors input is always partial")
    args = ap.parse_args(argv)
    try:
        if args.colors:
            if args.palette:
                raise AuditError("give either a palette file or --colors/--roles, not both")
            if not args.roles:
                raise AuditError("--colors needs --roles (one role per colour)")
            if args.write:
                raise AuditError("--write needs a palette file")
            pal = cl.validate_palette(palette_from_colors(args.colors, args.roles, args.mode), partial=True,
                                      source="--colors")
        elif args.palette:
            pal = cl.load_palette(args.palette, partial=True)
        else:
            raise AuditError("give a palette.json or --colors with --roles (see --help)")
    except (AuditError, cl.PaletteError) as e:
        log(f"error: {e}")
        return 2
    try:
        grounds = [parse_ground(g) for g in args.ground]
    except AuditError as e:
        log(f"error: {e}")
        return 2
    audit = audit_palette(pal, partial=bool(args.colors) or args.partial, grounds=grounds, context=args.context)
    if args.write:
        with open(args.palette, encoding="utf-8") as fh:
            raw = json.load(fh)
        raw["audit"] = audit
        with open(args.palette, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        log(f"audit written to {args.palette}")
    fmt = "json" if args.json else "md" if args.full else args.format or "summary"
    if fmt == "json":
        sys.stdout.write(json.dumps(audit, indent=2, ensure_ascii=False) + "\n")
    elif fmt == "md":
        sys.stdout.write(markdown(pal, audit))
    else:
        sys.stdout.write(summary_text(pal, audit, args.palette if args.write else None))
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
