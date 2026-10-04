#!/usr/bin/env python3
"""Build a complete brand-identity palette.json from one or two seed colours (or a partial palette.json).

What it produces (docs/architecture.md section 3 and 4.2):
  * 11-step OKLCH scales (50..950) for primary, accent, a tinted neutral and the four semantic colours.
    The scale's anchor step holds the seed hex EXACTLY; the lightness ladder bends around it, chroma follows the
    hue's own sRGB max-chroma curve (gamut-mapped, CSS Color 4) and adjacent steps stay >= 0.02 deltaE OK apart.
  * Light and dark role colours (background, surface, text, primary, onPrimary, link, focus, success, ...).
    A role that misses its WCAG 2 target is moved to another step of the SAME scale (never a new hue); every move
    is reported on stderr and recorded under "build.adjustments".
  * Semantic colours whose hue would collide with a brand role (CIEDE2000 < 10) are rotated within their family.
Brand colours are never rewritten (locked or not); only their print.lab_d50 is filled when empty.

Accent strategies when no accent is given (--strategy):
  family      default: the pairing that tends to work for the primary's hue family (Radix Colors
              natural pairings plus practice knowledge, not a controlled finding), e.g. blue -> amber/orange, yellow -> deep navy.
  analogous   35 degrees round the wheel, same mood.
  complement  opposite hue; weak evidence (O'Donovan 2011: templates do not predict harmony), offered, not advised.
  mono        same hue, different lightness/chroma; no separate accent hue. Also the default when a brand
              colour is the page ground and leaves no accent: no hue the designer did not choose.

Usage:
  python3 scripts/palette_build.py "#1f5f7a" --name Harbor -o harbor.json
  python3 scripts/palette_build.py "#1f5f7a" "#c2410c" --name Harbor --direction "Deep sea blue, ember accent"
  python3 scripts/palette_build.py "#f5c518" --strategy family --json > yellow.json
  python3 scripts/palette_build.py --from measured.json -o full.json        # complete a partial palette
  python3 scripts/palette_build.py "#6d28d9" --neutral-tint none --neutral-chroma 0  # pure grey neutrals
  python3 scripts/palette_build.py "#7a5c3e" --light-bg "#f7f1e8"                  # cream ground, white cards
  python3 scripts/palette_build.py "#18181b" --accent "#ea580c"   # black brand: buttons/links use the accent
  python3 scripts/palette_build.py "#1f5f7a" --extra "#7b4b2a:Cocoa:product variant" --extra "#c9a227:Honey" \
      --feels "calm, local, honest"                                  # extended colours + feel words
Output: stdout is a short summary (<= 1.2 KB: headline, role contrast, the first adjustments, the output path).
--full prints the detailed summary instead, --json prints the palette JSON itself, -o FILE writes it to a file
(diagnostics always go to stderr).
"""
import argparse
import copy
import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import colorlib as cl  # noqa: E402

STEPS = cl.STEP_KEYS
# Lightness ladder for chromatic scales: medians of Tailwind v4 chromatic families (tailwindcss.com/docs/colors).
LADDER = dict(zip(STEPS, (0.977, 0.951, 0.902, 0.833, 0.746, 0.685, 0.592, 0.514, 0.448, 0.396, 0.279)))
# Neutral ladder: darker mid-tones than the chromatic one (text greys), 950 near Material's #121212 dark surface.
NEUTRAL_LADDER = dict(zip(STEPS, (0.986, 0.964, 0.925, 0.870, 0.710, 0.555, 0.450, 0.375, 0.290, 0.225, 0.170)))
# Share of the neutral tint chroma per step (light end almost grey, mid/dark carry the tint; Tailwind gray shape).
NEUTRAL_CHROMA_SHAPE = dict(zip(STEPS, (0.10, 0.14, 0.22, 0.32, 0.60, 0.80, 0.90, 1.00, 0.95, 1.00, 0.85)))
MIN_STEP_GAP = 0.022           # OKLab L gap between adjacent steps (JND 0.02 plus margin)
LIGHT_END_CHROMA = 0.15        # chroma weight at the light end, relative to the seed (Tailwind blue ~0.1)
DARK_END_CHROMA = 0.40         # chroma weight at the dark end (Tailwind ~0.35-0.5)
MAX_CHROMA_BOOST = 1.15        # no step more than 15% more chromatic than the seed
REL_END_CHROMA = 0.55          # share of the seed's relative chroma (C / Cmax) kept at either end
GAMUT_CAP = 0.98               # stay just inside the sRGB boundary

SEMANTIC = {
    # name: (base OKLCH hue, allowed hue band for collision avoidance, target chroma at step 500).
    # Order matters: danger is fixed first, success then also has to stay apart from danger under CVD simulation.
    "danger": (27.0, (8.0, 42.0), 0.21),
    "success": (170.0, (135.0, 180.0), 0.17),   # jade-leaning green: stays apart from danger for CVD
    "warning": (70.0, (48.0, 92.0), 0.17),
    "info": (250.0, (225.0, 268.0), 0.17),
}
SEMANTIC_ORDER = ("success", "warning", "danger", "info")   # order in the output
SEMANTIC_MIN_DE = 10.0         # CIEDE2000 floor between a semantic role and brand roles (heuristic, uncalibrated)

TEXT_AA = 4.5
UI_AA = 3.0
GROUND_TINT_MIN_C = 0.005      # a dark ground below this OKLCH chroma is grey: auto neutrals stay untinted


class BuildError(Exception):
    pass


def log(msg):
    print(msg, file=sys.stderr)


# ----------------------------------------------------------------------------- scales

def _hue_drift(h, t, side):
    """Bezold-Bruecke style drift, yellows only: dark yellows turn amber instead of olive, light ones lemon.
    Practice knowledge (Tailwind yellow 950 sits ~45 degrees warmer than its 400), not a measured rule."""
    if 70.0 <= h <= 125.0:
        w = min(1.0, (h - 70.0) / 15.0, (125.0 - h) / 15.0)
    else:
        return 0.0
    return (-38.0 * t if side == "dark" else 8.0 * t) * w


def lightness_ladder(L_seed, base=None):
    """Return (anchor_step, {step: L}) with the anchor at L_seed exactly and both sides rescaled to fit."""
    base = base or LADDER
    k_star = min(STEPS, key=lambda k: abs(base[k] - L_seed))
    i = STEPS.index(k_star)
    n_light, n_dark = i, len(STEPS) - 1 - i
    top = max(base["50"], min(0.995, L_seed + MIN_STEP_GAP * n_light)) if n_light else L_seed
    bottom = min(base["950"], max(0.08, L_seed - MIN_STEP_GAP * n_dark)) if n_dark else L_seed
    out = {k_star: L_seed}
    for j, k in enumerate(STEPS):
        if j < i:
            span = base["50"] - base[k_star]
            t = (base[k] - base[k_star]) / span if span else 1.0
            out[k] = L_seed + t * (top - L_seed)
        elif j > i:
            span = base[k_star] - base["950"]
            t = (base[k_star] - base[k]) / span if span else 1.0
            out[k] = L_seed - t * (L_seed - bottom)
    # Enforce the minimum gap walking outward from the anchor.
    for j in range(i - 1, -1, -1):
        out[STEPS[j]] = max(out[STEPS[j]], out[STEPS[j + 1]] + MIN_STEP_GAP)
    for j in range(i + 1, len(STEPS)):
        out[STEPS[j]] = min(out[STEPS[j]], out[STEPS[j - 1]] - MIN_STEP_GAP)
    return k_star, out


def build_scale(seed_hex, hue_drift=True):
    """11-step scale around `seed_hex`; returns {"anchor": step, "steps": {...}, "oklch": {...}}."""
    seed_hex = cl.normalize_hex(seed_hex)
    L_b, C_b, h_b = cl.hex_to_oklch(seed_hex)
    anchor, ladder = lightness_ladder(L_b)
    cmax_b = cl.max_chroma(L_b, h_b)
    rel_b = min(1.0, C_b / cmax_b) if cmax_b > 0 else 0.0
    top, bottom = ladder["50"], ladder["950"]
    steps, lch = {}, {}
    for k in STEPS:
        L = ladder[k]
        if k == anchor:
            steps[k] = seed_hex
            lch[k] = (L_b, C_b, h_b)
            continue
        if L > L_b:
            t = (L - L_b) / (top - L_b) if top > L_b else 1.0
            w = 1.0 - (1.0 - LIGHT_END_CHROMA) * t ** 1.4
            w_rel = 1.0 - (1.0 - REL_END_CHROMA) * t ** 1.4
            side = "light"
        else:
            t = (L_b - L) / (L_b - bottom) if L_b > bottom else 1.0
            w = 1.0 - (1.0 - DARK_END_CHROMA) * t ** 1.5
            w_rel = 1.0 - (1.0 - REL_END_CHROMA) * t ** 1.5
            side = "dark"
        h = (h_b + (_hue_drift(h_b, t, side) if hue_drift and C_b >= 0.04 else 0.0)) % 360
        cmax = cl.max_chroma(L, h)
        # Absolute taper from the seed's chroma, or the seed's share of the gamut carried along this hue's own
        # max-chroma curve (teal and yellow peak light, blue and violet peak dark), whichever is larger.
        # The boost is bounded so a muted seed keeps a muted family.
        C = min(max(C_b * w, min(rel_b * cmax * w_rel, C_b * MAX_CHROMA_BOOST)), cmax * GAMUT_CAP)
        steps[k] = cl.oklch_to_hex(L, C, h)
        lch[k] = (L, C, h)
    return {"anchor": anchor, "steps": steps, "oklch": lch}


def build_neutral(tint_hue, chroma):
    steps = {}
    for k in STEPS:
        L = NEUTRAL_LADDER[k]
        if chroma <= 0 or tint_hue is None:
            C, h = 0.0, 0.0
        else:
            # yellow-green tints read as "dirty" grey sooner (Radix sand/olive are paler): soften them
            soften = 0.6 if 70.0 <= tint_hue <= 130.0 else 1.0
            C = max(0.003, chroma * soften * NEUTRAL_CHROMA_SHAPE[k])
            h = tint_hue
            C = min(C, cl.max_chroma(L, h) * GAMUT_CAP)
        steps[k] = cl.oklch_to_hex(L, C, h)
    return {"steps": steps}


def semantic_seed(h, target_c):
    L = LADDER["500"]
    return cl.oklch_to_hex(L, min(target_c, cl.max_chroma(L, h) * 0.92), h)


def check_spacing(name, steps):
    problems = []
    for a, b in zip(STEPS, STEPS[1:]):
        d = cl.delta_e_ok(steps[a], steps[b])
        if d < 0.02:
            problems.append(f"scales.{name}: steps {a}->{b} only {d:.3f} deltaE OK apart (< 0.02 JND)")
    return problems


# ----------------------------------------------------------------------------- accent strategies

# Per hue family: (hue, OKLCH L, share of max chroma, why) of a working accent: practice knowledge, not a
# controlled finding.
FAMILY_ACCENTS = {
    "achromatic": (45.0, 0.66, 0.92, "single saturated accent on a monochrome base (orange)"),
    "red": (85.0, 0.84, 0.85, "red family: warm gold accent (analogous-warm)"),
    "brown": (85.0, 0.80, 0.80, "brown/earth family: mustard-gold accent"),
    "orange": (245.0, 0.45, 0.75, "orange family: deep blue accent (orange-cyan/navy pairs rate well, O'Donovan 2011)"),
    "yellow": (262.0, 0.36, 0.70, "yellow family: deep navy accent (strongest lightness contrast)"),
    "green": (80.0, 0.80, 0.85, "green family: amber/yellow accent (green pairs with blue or yellow, not purple)"),
    "teal": (40.0, 0.68, 0.85, "teal/cyan family: coral/orange accent (warm-cool)"),
    "blue": (60.0, 0.72, 0.92, "blue family: amber/orange accent (warm on cool)"),
    "purple": (88.0, 0.86, 0.85, "purple family: gold accent (lightness contrast)"),
    "pink": (170.0, 0.82, 0.60, "pink/magenta family: mint accent, small areas only"),
}


def hue_family(L, C, h):
    if C < 0.03:
        return "achromatic"
    if h < 40 or h >= 350:
        return "red"
    if h < 70:
        return "brown" if C < 0.10 and L < 0.55 else "orange"
    if h < 115:
        return "yellow"
    if h < 165:
        return "green"
    if h < 215:
        return "teal"
    if h < 275:
        return "blue"
    if h < 315:
        return "purple"
    return "pink"


def family_accent(L, C, h):
    """(hue, lightness, relative chroma, why) of a working accent for the primary's hue family."""
    return FAMILY_ACCENTS[hue_family(L, C, h)]


def derive_accent(primary_hex, strategy):
    """(accent_hex, why) for a strategy."""
    L, C, h = cl.hex_to_oklch(primary_hex)
    if strategy == "family":
        ah, aL, rel, why = family_accent(L, C, h)
    elif strategy == "analogous":
        ah, aL, rel, why = (h + 35.0) % 360, min(0.8, max(0.5, L + 0.12)), 0.85, "analogous: +35 degrees"
    elif strategy == "complement":
        ah, aL, rel, why = (h + 180.0) % 360, min(0.75, max(0.5, 1.15 - L)), 0.85, \
            "complement: opposite hue (weak evidence; templates do not predict harmony)"
    elif strategy == "mono":
        ah, aL, rel, why = h, (0.72 if L < 0.6 else 0.42), 0.95, "mono: same hue, opposite lightness, full chroma"
    else:
        raise BuildError(f"unknown --strategy {strategy!r}; use family|analogous|complement|mono")
    return cl.oklch_to_hex(aL, cl.max_chroma(aL, ah) * rel, ah), why


# ----------------------------------------------------------------------------- roles

def _idx(k):
    return STEPS.index(k)


def pick_step(steps, start, ok, order="darker"):
    """First step satisfying ok(hex), searching outward from `start` (distance 0, 1, 2 ...).
    order: 'darker' or 'lighter' breaks ties at equal distance; 'only-darker'/'only-lighter' restrict direction.
    Returns the step key or None."""
    i0 = _idx(start)
    for d in range(len(STEPS)):
        if d == 0:
            cands = [i0]
        else:
            down, up = i0 + d, i0 - d       # higher index = darker
            if order in ("darker", "only-darker"):
                cands = [down] + ([up] if order == "darker" else [])
            else:
                cands = [up] + ([down] if order == "lighter" else [])
        for j in cands:
            if 0 <= j < len(STEPS) and ok(steps[STEPS[j]]):
                return STEPS[j]
    return None


def first_passing(steps, keys, ok):
    """Selection (not a fix): the first of `keys` whose colour satisfies ok; falls back to the best of them."""
    for k in keys:
        if ok(steps[k]):
            return k
    return None


def _min_contrast(c, bgs):
    return min(cl.contrast_ratio(c, bg) for bg in bgs)


# Ordered step preferences for roles that are *selected* from a neutral/semantic scale (lightest/darkest passing).
SELECT = {
    "light": {"text": ("900", "950"), "textMuted": ("500", "600", "700", "800"),
              "semantic": ("600", "700", "800", "900")},
    "dark": {"text": ("100", "50"), "textMuted": ("500", "400", "300", "200"),
             "semantic": ("400", "300", "200", "100")},
}
LINK_VS_TEXT = 3.0             # WCAG technique G183: a link without underline differs from body text by 3:1
LINK_HUE_TOLERANCE = 18.0      # a link tone stays within this many OKLCH degrees of its scale's brand hue
ACCENT_FALLBACK_DISTANCE = 3   # a text role this many steps away from the brand step reads as a different colour
MUDDY_FILL_L = 0.75            # below this OKLCH L a fill with a near-black label looks muddy
GROUND_ALT_DL = 0.03           # surfaceAlt on a tinted ground: this much darker (OKLCH L) than the ground
GROUND_BORDER_DL = 0.10        # divider on a tinted ground: about 1.3:1, like neutral-200 on white
ACHROMATIC_C = 0.03            # primary below this chroma is a grey/black/white brand colour
ACHROMATIC_EXTREME_L = (0.30, 0.90)   # ... and outside this L band it cannot act as an action colour


class RoleBuilder:
    """Derives roles from scales. Moves of a brand-bearing role away from its anchor are recorded as adjustments."""

    def __init__(self, scales, adjustments):
        self.scales = scales
        self.adj = adjustments

    def note(self, mode, role, scale, start, chosen, why):
        if chosen != start:
            self.adj.append({"mode": mode, "role": role, "scale": scale, "from_step": start, "to_step": chosen,
                             "reason": why})

    def select(self, mode, role, scale, keys, bgs, target):
        steps = self.scales[scale]["steps"]
        k = first_passing(steps, keys, lambda c: _min_contrast(c, bgs) >= target)
        if k is None:
            k = max(STEPS, key=lambda s: _min_contrast(steps[s], bgs))
            log(f"warning: {mode}.{role}: no {scale} step reaches {target}:1 on every {mode} surface; "
                f"using the best step {k} ({_min_contrast(steps[k], bgs):.2f}:1)")
        return k, steps[k]

    def fill_pair(self, mode, role, scale, start, text_candidates, order, extra_ok=None, extra_why="",
                  prefer_white_label=False):
        """Fill colour for a button-like role + its on-colour (best of text_candidates, >= 4.5:1)."""
        steps = self.scales[scale]["steps"]

        def ok(c):
            on = cl.best_text_on(c, text_candidates)
            return cl.contrast_ratio(on, c) >= TEXT_AA and (extra_ok is None or extra_ok(c))
        k = pick_step(steps, start, ok, order)
        if k is None:
            k = max(STEPS, key=lambda s: cl.contrast_ratio(cl.best_text_on(steps[s], text_candidates), steps[s]))
            log(f"warning: {mode}.{role}: no {scale} step meets the targets; using {k}")
        why = f"step {start} cannot carry {TEXT_AA}:1 text" if not ok(steps[start]) and extra_ok is None else \
            f"step {start} {extra_why or 'misses its target'}"
        if prefer_white_label and "#ffffff" in text_candidates:
            # A mid-tone fill with a near-black label reads muddy (hazelnut + black); one step darker with a white
            # label is the usual button. Light, clean fills (yellow, sky) keep their dark label.
            fill = steps[k]
            on = cl.best_text_on(fill, text_candidates)
            i = _idx(k)
            if on != "#ffffff" and cl.hex_to_oklch(fill)[0] < MUDDY_FILL_L and i + 1 < len(STEPS):
                darker = steps[STEPS[i + 1]]
                if cl.contrast_ratio("#ffffff", darker) >= TEXT_AA:
                    k = STEPS[i + 1]
                    why = (f"step {start} only carries a dark label, which reads muddy on a mid-tone fill; "
                           f"step {k} carries white")
        self.note(mode, role, scale, start, k, why)
        fill = steps[k]
        return k, fill, cl.best_text_on(fill, text_candidates)

    def link_tone(self, mode, bgs, body_text, scales, ground_target=TEXT_AA):
        """Link colour that holds `ground_target`:1 on every surface in bgs AND LINK_VS_TEXT:1 against body text,
        taken from the brand's own family: first a step of the primary scale, then of the accent scale (the nearest
        passing step to the brand step; roles stay scale steps, so tokens can alias them). `scales` is a list of
        (scale name, brand step) tried in that order. A step must stay within LINK_HUE_TOLERANCE degrees of the
        brand hue. Returns (hex, scale, step) or None when neither scale has such a step (the caller keeps its
        old behaviour and the audit keeps its underline note)."""
        for scale, brand_step in scales:
            steps = self.scales[scale]["steps"]
            brand_h = cl.hex_to_oklch(steps[brand_step])[2]

            def ok(c, brand_h=brand_h):
                dh = abs((cl.hex_to_oklch(c)[2] - brand_h + 180.0) % 360.0 - 180.0)
                return (dh <= LINK_HUE_TOLERANCE and _min_contrast(c, bgs) >= ground_target
                        and cl.contrast_ratio(c, body_text) >= LINK_VS_TEXT)
            k = pick_step(steps, brand_step, ok, "darker" if mode == "light" else "lighter")
            if k is not None:
                return steps[k], scale, k
        return None

    def brand_text(self, mode, role, start, bgs, target, order, accent_role, body_text=None, scale="primary",
                   link_scales=None):
        """Text-like brand role (link, focus) from the primary scale; if that needs a step >= 3 away from the brand
        step, the accent role is used instead when it passes (a far-away step no longer reads as the brand).
        With body_text, a step within 2 of the brand step that ALSO keeps 3:1 against body text is preferred
        (WCAG technique G183: links without underline must differ from surrounding text by 3:1)."""
        steps = self.scales[scale]["steps"]
        if role == "link" and body_text is not None and link_scales:
            found = self.link_tone(mode, bgs, body_text, link_scales, target)
            if found is not None:
                hx, fscale, label = found
                from_step = dict(link_scales)[fscale]
                if label != from_step:
                    self.adj.append({"mode": mode, "role": role, "scale": fscale, "from_step": from_step,
                                     "to_step": label,
                                     "reason": f"a link must keep {target}:1 on the {mode} surfaces and "
                                               f"{LINK_VS_TEXT:g}:1 against body text (WCAG G183); "
                                               f"{fscale} {label} does"})
                return hx
        if body_text is not None:
            ks = pick_step(steps, start, lambda c: _min_contrast(c, bgs) >= target
                           and cl.contrast_ratio(c, body_text) >= UI_AA, order)
            if ks is not None and abs(_idx(ks) - _idx(start)) <= 2:
                self.note(mode, role, scale, start, ks, f"step {start} misses {target}:1 on the {mode} surfaces "
                          f"or 3:1 against body text (G183, links without underline)")
                return steps[ks]
        k = pick_step(steps, start, lambda c: _min_contrast(c, bgs) >= target, order)
        far = scale != "accent" and (k is None or abs(_idx(k) - _idx(start)) >= ACCENT_FALLBACK_DISTANCE)
        if far and _min_contrast(accent_role, bgs) >= target:
            self.adj.append({"mode": mode, "role": role, "scale": scale, "from_step": start, "to_step": "accent",
                             "reason": f"the {scale} scale needs {'no step' if k is None else 'step ' + k} to reach "
                                       f"{target}:1; the accent role passes, so {role} uses it"})
            return accent_role
        if k is None:
            k = max(STEPS, key=lambda s: _min_contrast(steps[s], bgs))
            log(f"warning: {mode}.{role}: no {scale} step reaches {target}:1; using {k}")
        self.note(mode, role, scale, start, k, f"step {start} is below {target}:1 on the {mode} surfaces")
        return steps[k]


def _ground_steps(N, bg, mode):
    """Surface, surfaceAlt and border for a background: the next neutral steps away from it."""
    if mode == "light":
        if bg == "#ffffff":
            return N["50"], N["100"], N["200"]
        # Tinted ground (cream, stone, neutral-50): white cards on it; the alternate surface and the divider are
        # the ground itself made darker (same hue and chroma), so they stay warm on cream and remain visible.
        L, C, h = cl.hex_to_oklch(bg)
        alt = cl.oklch_to_hex(max(0.0, L - GROUND_ALT_DL), C, h)
        border = cl.oklch_to_hex(max(0.0, L - GROUND_BORDER_DL), min(C * 1.2 + 0.004, 0.06), h)
        return "#ffffff", alt, border
    Lb = cl.hex_to_oklch(bg)[0]
    lighter = [N[k] for k in reversed(STEPS) if cl.hex_to_oklch(N[k])[0] > Lb + 0.03]
    lighter += [N["50"]] * 3
    return lighter[0], lighter[1], lighter[2]


def resolve_ground(value, N, mode):
    """--light-bg / --dark-bg value -> hex: 'white', 'neutral-<step>' or any colour."""
    if value is None:
        return "#ffffff" if mode == "light" else N["950"]
    v = value.strip().lower()
    if v.startswith("neutral-"):
        step = v.split("-", 1)[1]
        if step not in N:
            raise BuildError(f"--{mode}-bg {value!r}: neutral step must be one of {', '.join(STEPS)}")
        return N[step]
    try:
        return cl.normalize_hex(value)
    except ValueError as e:
        raise BuildError(f"--{mode}-bg: {e}") from None


def achromatic_extreme(hex_):
    L, C, h = cl.hex_to_oklch(hex_)
    lo, hi = ACHROMATIC_EXTREME_L
    return C < ACHROMATIC_C and (L <= lo or L >= hi)


def brand_roles(scales, primary_anchor, accent_anchor, adjustments, given=None, grounds=None, seed=None):
    """Neutral and brand roles for both modes (semantic roles are added by semantic_roles).
    `given` = {mode: {role: hex}} from a partial palette: those values are kept and the other roles are derived
    against them (e.g. a given background changes which text step passes).
    `grounds` = {"light": hex, "dark": hex} page backgrounds (--light-bg / --dark-bg).
    A near-black/near-white grey primary (C < 0.03 at the lightness extremes) cannot carry links and focus (they
    would read as body text or vanish): those use the accent. The button fill moves to the accent too, except a
    near-black primary in light mode, which keeps its charcoal button with a white label. All moves are logged."""
    rb = RoleBuilder(scales, adjustments)
    N = scales["neutral"]["steps"]
    route_to_accent = seed is not None and achromatic_extreme(seed)
    near_black = route_to_accent and cl.hex_to_oklch(seed)[0] < 0.5
    modes = {}
    for mode in ("light", "dark"):
        # Near-black keeps its own charcoal button on a light ground (white label); only in dark mode, where it
        # would vanish, does the fill move to the accent. Near-white moves everywhere.
        route_fill = route_to_accent and not (near_black and mode == "light")
        g = dict(((given or {}).get(mode) or {}))
        r = {}
        sel = SELECT[mode]

        def put(role, derive):
            r[role] = g[role] if role in g else derive()
            return r[role]
        bg = g.get("background") or (grounds or {}).get(mode) or resolve_ground(None, N, mode)
        sf, sa, bd = _ground_steps(N, bg, mode)
        defaults = {"background": bg, "surface": sf, "surfaceAlt": sa, "border": bd}
        if mode == "light":
            on_cands, order, text_order = ("#ffffff", N["950"]), "darker", "darker"
        else:
            on_cands, order, text_order = (N["950"], "#ffffff"), "only-lighter", "lighter"
        for role, v in defaults.items():
            put(role, lambda v=v: v)
        if mode == "light":
            extra_ok, extra_why = None, ""
        else:
            dark_bgs = (r["background"], r["surface"])

            def extra_ok(c, dark_bgs=dark_bgs):
                return _min_contrast(c, dark_bgs) >= TEXT_AA
            extra_why = f"is below {TEXT_AA}:1 on the dark background (lightened derivative)"
        bgs = (r["background"], r["surface"], r["surfaceAlt"])
        put("text", lambda: rb.select(mode, "text", "neutral", sel["text"], bgs, 7.0)[1])
        put("textMuted", lambda: rb.select(mode, "textMuted", "neutral", sel["textMuted"], bgs, TEXT_AA)[1])
        steps = {}
        for role, scale, anchor in (("accent", "accent", accent_anchor), ("primary", "primary", primary_anchor)):
            on_role = "on" + role[0].upper() + role[1:]
            if role in g:
                fill = g[role]
                r[role] = fill
                steps[role] = next((k for k, v in scales[scale]["steps"].items() if v == fill), anchor)
                put(on_role, lambda fill=fill: cl.best_text_on(fill, on_cands))
            elif role == "primary" and route_fill:
                steps[role] = steps["accent"]
                r[role] = r["accent"]
                put(on_role, lambda: r["onAccent"])
                adjustments.append({"mode": mode, "role": "primary", "scale": "primary", "from_step": anchor,
                                    "to_step": "accent",
                                    "reason": f"the primary {seed} is near-{'black' if near_black else 'white'} grey "
                                              f"(OKLCH C < {ACHROMATIC_C}); it stays the identity colour, but in "
                                              f"{mode} mode buttons use the accent"})
            else:
                k, fill, on = rb.fill_pair(mode, role, scale, anchor, on_cands, order, extra_ok, extra_why,
                                           prefer_white_label=(mode == "light"))
                steps[role] = k
                r[role] = fill
                put(on_role, lambda on=on: on)
        text_scale = "accent" if route_to_accent else "primary"
        tk = steps["accent"] if route_to_accent else steps["primary"]
        # (a near-black fill kept in light mode still sends link/focus to the accent: a near-black link is body text)
        for role, target, body in (("link", TEXT_AA, r["text"]), ("focus", UI_AA, None)):
            if route_to_accent and role not in g:
                adjustments.append({"mode": mode, "role": role, "scale": "primary", "from_step": primary_anchor,
                                    "to_step": "accent", "reason": "achromatic primary: uses the accent scale"})
            lscales = None
            if role == "link":
                lscales = [("accent", steps["accent"])] if route_to_accent else \
                    [("primary", steps["primary"]), ("accent", steps["accent"])]
            put(role, lambda role=role, target=target, body=body, lscales=lscales: rb.brand_text(
                mode, role, tk, (r["background"], r["surface"]), target, text_order, r["accent"], body, text_scale,
                lscales))
        modes[mode] = {k: r[k] for k in ("background", "surface", "surfaceAlt", "border", "text", "textMuted",
                                         "primary", "onPrimary", "accent", "onAccent", "link", "focus")}
    return modes


def _semantic_pick(steps, mode, r):
    bgs = (r["background"], r["surface"])
    keys = SELECT[mode]["semantic"]
    k = first_passing(steps, keys, lambda c: _min_contrast(c, bgs) >= TEXT_AA)
    if k is None:
        k = max(keys, key=lambda s: _min_contrast(steps[s], bgs))
    return steps[k]


def _cvd_separation(a, b):
    return min(cl.delta_e2000(cl.simulate_cvd(a, k), cl.simulate_cvd(b, k)) for k in cl.CVD_KINDS)


def semantic_roles(modes, brand_hexes, hue_drift):
    """Add success/warning/danger/info to both modes and return their scales.
    A semantic hue is rotated inside its family (e.g. danger red -> crimson, success green -> jade) when its role
    colour lands within CIEDE2000 10 of a brand role in the same mode (primary, accent, link, focus; brand hexes in
    light mode). success must also stay >= 10 from danger after protan/deutan/tritan simulation; green-vs-red
    collapses for ~8% of men, a bluer green does not. Icons/labels are still required (WCAG 1.4.1)."""
    scales, notes = {}, []
    refs = {m: [modes[m][k] for k in ("primary", "accent", "link", "focus")] for m in modes}
    refs["light"] = refs["light"] + list(brand_hexes)
    for name, (base_h, (lo, hi), tc) in SEMANTIC.items():
        def trial(h):
            sc = build_scale(semantic_seed(h, tc), hue_drift)
            picks = {m: _semantic_pick(sc["steps"], m, modes[m]) for m in modes}
            brand_de = min(cl.delta_e2000(picks[m], ref) for m in modes for ref in refs[m])
            cvd_de = min(_cvd_separation(picks[m], modes[m]["danger"]) for m in modes) \
                if name == "success" else float("inf")
            return min(brand_de, cvd_de), sc, picks, brand_de, cvd_de
        base = trial(base_h)
        best = (base_h,) + base
        if best[1] < SEMANTIC_MIN_DE:
            cands = sorted({round(lo + i * 2.0, 1) for i in range(int((hi - lo) / 2) + 1)},
                           key=lambda x: abs(x - base_h))
            for h in cands:
                t = trial(h)
                if t[0] >= SEMANTIC_MIN_DE:
                    best = (h,) + t
                    break
                if t[0] > best[1]:
                    best = (h,) + t
            why = []
            if base[3] < SEMANTIC_MIN_DE:
                why.append("role colour too close to a brand role (CIEDE2000 < 10)")
            if base[4] < SEMANTIC_MIN_DE:
                why.append("success and danger collapse under colour-vision-deficiency simulation (< 10)")
            notes.append({"scale": name, "hue_from": base_h, "hue_to": best[0], "min_delta_e2000": round(best[1], 2),
                          "reason": "; ".join(why) + ("" if best[1] >= SEMANTIC_MIN_DE
                                                      else "; no hue in the family fully clears it")})
        h, _, sc, picks = best[:4]
        scales[name] = {"hue": round(h, 1), "steps": sc["steps"]}
        for m in modes:
            modes[m][name] = picks[m]
    return {k: scales[k] for k in SEMANTIC_ORDER}, notes


# ----------------------------------------------------------------------------- extended colours

EXT_UI = 3.0   # an extended colour as a stripe, chart series or tag must reach 3:1 on the page (WCAG 1.4.11)


def build_extended(ext_in, scales, modes, hue_drift=True):
    """Extended colours (product variants, secondary illustration colours): each gets its own 11-step scale
    `scales["ext-N"]` anchored on the given hex, an `on` text colour (best of white / neutral-950, target 4.5:1)
    and a dark-mode derivative `dark.hex` (the given hex if it reaches 3:1 on the dark background and surface,
    else the nearest lighter step of its own scale) with `dark.on`. Core roles are never touched."""
    N = scales["neutral"]["steps"]
    out = []
    for i, (hx, name, usage) in enumerate(ext_in, 1):
        key = f"ext-{i}"
        sc = build_scale(hx, hue_drift)
        scales[key] = {"anchor": sc["anchor"], "steps": sc["steps"]}
        on = cl.best_text_on(hx, ("#ffffff", N["950"]))
        dbgs = (modes["dark"]["background"], modes["dark"]["surface"])
        k = pick_step(sc["steps"], sc["anchor"], lambda c: _min_contrast(c, dbgs) >= EXT_UI, "only-lighter")
        dark_hex = sc["steps"][k] if k else max(sc["steps"].values(), key=lambda c: _min_contrast(c, dbgs))
        entry = {"id": key, "name": name, "hex": hx, "on": on,
                 "dark": {"hex": dark_hex, "on": cl.best_text_on(dark_hex, (N["950"], "#ffffff"))}}
        if usage:
            entry["usage"] = usage
        out.append(entry)
    return out


# ----------------------------------------------------------------------------- assembly

def brand_entry(i, hex_, name, source, locked, usage):
    lab = cl.hex_to_lab(hex_, "D50")
    return {"id": f"brand-{i}", "name": name, "hex": hex_, "source": source, "locked": locked, "usage": usage,
            "print": {"lab_d50": [round(v, 1) for v in lab], "cmyk": None, "pantone_ref": None}}


def _ground_colour(value):
    """A --light-bg / --dark-bg value that names a colour -> hex; None for white, a neutral step or nothing."""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip().lower()
    if v == "white" or v.startswith("neutral-"):
        return None
    try:
        return cl.normalize_hex(v)
    except ValueError:
        return None


def build_palette(seed, accent=None, strategy=None, neutral_tint=None, neutral_chroma=0.022,
                  name=None, direction=None, partial=None, hue_drift=True, light_bg=None, dark_bg=None,
                  extras=None, feels=None):
    """Return a full palette dict. `partial` is a validated partial palette dict or None.
    light_bg / dark_bg: page background ('white', 'neutral-<step>' or a colour); None = white / neutral-950.
    A brand colour equal to a ground colour (flag or the partial's given background) is that ground, not a seed:
    the seed and accent are the next brand colours (a dark-first brand lists its night ground among its colours).
    neutral_tint None = auto: the hue of a dark_bg colour when one is given (a grey ground: untinted), else primary.
    strategy None = auto: 'mono' when a brand colour became the ground and left no accent (the designer chose one
    colour; no new hue is invented), else 'family'.
    A locked brand colour left without a role (beyond seed, accent and grounds) becomes an extended colour.
    extras: [(hex, name, usage)] extended colours (ext-1..ext-5); they never change the core roles.
    feels: 2-4 short adjectives stored as palette["feels"]."""
    partial = copy.deepcopy(partial) if partial else None
    adjustments, notes = [], []
    brand = partial.get("brand", []) if partial else []
    for b in brand:
        if b.get("print") is None:
            b["print"] = {"lab_d50": None, "cmyk": None, "pantone_ref": None}
        if b["print"].get("lab_d50") is None:
            b["print"]["lab_d50"] = [round(v, 1) for v in cl.hex_to_lab(b["hex"], "D50")]
    # Extended colours: partial "extended" list, then brand[] entries with role "extended", then --extra.
    ext_in = []
    for e in (partial or {}).get("extended") or []:
        ext_in.append((e["hex"], e.get("name") or e["id"], e.get("usage") or ""))
    for b in brand:
        if b.get("role") == "extended" and all(b["hex"] != h for h, _, _ in ext_in):
            ext_in.append((b["hex"], b.get("name") or b["id"], b.get("usage") or ""))
    for hx, nm, us in extras or []:
        hx = cl.normalize_hex(hx)
        if all(hx != h for h, _, _ in ext_in):
            ext_in.append((hx, nm, us))
    if len(ext_in) > cl.MAX_EXTENDED:
        raise BuildError(f"at most {cl.MAX_EXTENDED} extended colours (ext-1..ext-{cl.MAX_EXTENDED}), got "
                         f"{len(ext_in)}: " + ", ".join(h for h, _, _ in ext_in))
    if feels is not None:
        feels = [f.strip() for f in feels if f and f.strip()]
        if not 2 <= len(feels) <= cl.MAX_FEELS:
            raise BuildError(f"--feels takes 2-{cl.MAX_FEELS} short adjectives, got {len(feels)}: {feels}")
    elif (partial or {}).get("feels"):
        feels = partial["feels"]
    ext_hexes = {h for h, _, _ in ext_in}
    core = [b for b in brand if b.get("role") != "extended" and b["hex"] not in ext_hexes]
    given_bgs = [((partial or {}).get("modes") or {}).get(m, {}).get("background") for m in ("light", "dark")]
    ground_hexes = {h for h in (_ground_colour(light_bg), _ground_colour(dark_bg), *map(_ground_colour, given_bgs))
                    if h}
    pool = [b for b in core if cl.normalize_hex(b["hex"]) not in ground_hexes] or core
    for b in core:
        if b not in pool:
            adjustments.append({"role": "background", "brand": b["id"],
                                "reason": f"brand colour {b['id']} {b['hex']} is the page ground, not a seed"})
    if seed is None:
        if not pool:
            raise BuildError("no seed colour: pass a hex, or a --from palette with at least one brand[] colour")
        seed = pool[0]["hex"]
    seed = cl.normalize_hex(seed)
    if accent is None:
        rest = [b for b in pool if cl.normalize_hex(b["hex"]) != seed]
        if rest:
            accent = rest[0]["hex"]
    if strategy is None:
        strategy = "mono" if accent is None and len(pool) < len(core) else "family"
    # a locked core colour with no job (not seed, accent or ground) becomes an extended colour: own scale, on
    # colour and dark version; a colour meant as the night ground is passed as dark_bg instead
    taken = {seed, cl.normalize_hex(accent) if accent else None} | ground_hexes
    for b in core:
        hx = cl.normalize_hex(b["hex"])
        if b.get("locked") and hx not in taken and len(ext_in) < cl.MAX_EXTENDED:
            b["role"] = "extended"
            ext_in.append((hx, b.get("name") or b["id"], b.get("usage") or ""))
            ext_hexes.add(hx)
            taken.add(hx)
            adjustments.append({"role": "extended", "brand": b["id"],
                                "reason": f"locked brand colour {b['id']} {hx} is neither seed, accent nor a ground: "
                                          "built as an extended colour (pass it as dark_bg/light_bg if it is a "
                                          "ground, or as the accent)"})
            log("note: " + adjustments[-1]["reason"])
    if not any(b["hex"] == seed for b in brand):
        brand.insert(0, brand_entry(len(brand) + 1, seed, "Primary", "chosen", True, "logo, primary surfaces"))
    accent_why = "given"
    if accent is not None:
        accent = cl.normalize_hex(accent)
        if not any(b["hex"] == accent for b in brand):
            brand.append(brand_entry(len(brand) + 1, accent, "Accent", "chosen", True, "calls to action, highlights"))
    else:
        accent, accent_why = derive_accent(seed, strategy)
        brand.append(brand_entry(len(brand) + 1, accent, "Accent", "derived", False, "calls to action, highlights"))
    given_ext = {cl.normalize_hex(b["hex"]): b for b in brand if b.get("role") == "extended" or b["hex"] in ext_hexes}
    brand = [b for b in brand if b.get("role") != "extended" and b["hex"] not in ext_hexes]
    # brand[] order = seed, accent, other core colours (grounds): readers take brand[0]/brand[1] for primary/accent;
    # ids never change, so logo colours that name a brand id keep their colour
    rank = {seed: 0, accent: 1}
    brand.sort(key=lambda b: rank.get(cl.normalize_hex(b["hex"]), 2))
    for hx, nm, us in ext_in:      # extended colours live in brand[] too, marked role "extended"
        old = given_ext.get(hx)
        if old is not None:        # a brand entry that became extended keeps its id, name and print data
            e = dict(old)
            e["role"] = "extended"
            e.setdefault("usage", us or "extended palette")
        else:
            e = brand_entry(len(brand) + 1, hx, nm, "chosen", True, us or "extended palette")
            e["role"] = "extended"
        brand.append(e)
    ids = set()
    for b in brand:                    # keep ids unique after inserts: a clash takes the lowest free brand-N
        if b["id"] in ids:
            n = 1
            while f"brand-{n}" in ids or any(o["id"] == f"brand-{n}" for o in brand if o is not b):
                n += 1
            b["id"] = f"brand-{n}"
        ids.add(b["id"])

    given_scales = (partial or {}).get("scales") or {}

    def keep_or_build(key, builder):
        sc = given_scales.get(key)
        if sc and all(k in sc.get("steps", {}) for k in STEPS):
            return sc
        return builder()

    p = build_scale(seed, hue_drift)
    a = build_scale(accent, hue_drift)
    primary = keep_or_build("primary", lambda: {"anchor": p["anchor"], "steps": p["steps"]})
    accent_sc = keep_or_build("accent", lambda: {"anchor": a["anchor"], "steps": a["steps"]})
    if primary["steps"].get(primary.get("anchor")) != seed:
        log(f"warning: given scales.primary does not hold the seed {seed} at its anchor")

    if neutral_tint is None:   # auto: a dark ground colour sets the neutral hue (its surfaces are neutral steps)
        dg = _ground_colour(dark_bg)
        if dg is not None:
            neutral_tint = dg if cl.hex_to_oklch(dg)[1] >= GROUND_TINT_MIN_C else "none"
            adjustments.append({"role": "neutral", "reason": f"neutrals follow the dark ground {dg} "
                                f"({'its hue' if neutral_tint == dg else 'grey: untinted'}) so surfaces stay on "
                                "its side of the colour wheel; pass --neutral-tint primary to tint by the primary"})
        else:
            neutral_tint = "primary"
    tint_hue, tint_of = None, neutral_tint
    if neutral_tint == "primary":
        L, C, h = cl.hex_to_oklch(seed)
        tint_hue = h if C >= ACHROMATIC_C else None
        if tint_hue is None:
            # A grey/black/white primary: tinting the greys toward the accent turns them into cream/paper (a
            # yellow accent) or a coloured grey. Pure greys keep the monochrome identity; --neutral-tint accent
            # opts in.
            tint_of = "none"
            adjustments.append({"role": "neutral", "reason": f"primary {seed} is achromatic (OKLCH C < "
                                f"{ACHROMATIC_C}); neutrals stay untinted instead of following the accent. "
                                "Pass --neutral-tint accent to tint them"})
    elif neutral_tint == "accent":
        L, C, h = cl.hex_to_oklch(accent)
        tint_hue = h if C >= 0.02 else None
    elif neutral_tint == "none":
        tint_hue = None
    else:
        L, C, tint_hue = cl.hex_to_oklch(cl.normalize_hex(neutral_tint))
        tint_of = cl.normalize_hex(neutral_tint)
    neutral = keep_or_build("neutral", lambda: build_neutral(tint_hue, neutral_chroma))
    neutral = {"tint_of": tint_of if tint_hue is not None else "none", "steps": neutral["steps"]}

    scales = {"primary": primary, "neutral": neutral, "accent": accent_sc}
    for sc, hx in ((primary, seed), (accent_sc, accent)):
        if sc.get("anchor") not in STEPS:   # a given scale without anchor: the step nearest the seed's lightness
            L0 = cl.hex_to_oklch(hx)[0]
            sc["anchor"] = min(STEPS, key=lambda k: abs(cl.hex_to_oklch(sc["steps"][k])[0] - L0))
    given_modes = {m: {k: v for k, v in ((partial or {}).get("modes") or {}).get(m, {}).items()}
                   for m in ("light", "dark")}
    grounds = {"light": resolve_ground(light_bg, neutral["steps"], "light"),
               "dark": resolve_ground(dark_bg, neutral["steps"], "dark")}
    for m, v in (("light", light_bg), ("dark", dark_bg)):
        if v is not None:
            given_modes[m].pop("background", None)   # an explicit flag beats a background from --from
    modes = brand_roles(scales, primary["anchor"], accent_sc["anchor"], adjustments, given_modes, grounds, seed)
    sem, notes = semantic_roles(modes, [b["hex"] for b in brand if b.get("role") != "extended"], hue_drift)
    for k, v in sem.items():
        scales[k] = keep_or_build(k, lambda v=v: v)
        if scales[k] is not v:   # a given semantic scale wins; pick its role steps the same way
            for m in modes:
                modes[m][k] = _semantic_pick(scales[k]["steps"], m, modes[m])

    for mode, given in given_modes.items():
        for role, v in given.items():
            if role in SEMANTIC:   # semantic roles were derived; a given value wins
                modes[mode][role] = v
            adjustments.append({"mode": mode, "role": role, "kept_given": v})
    modes = {m: {k: modes[m][k] for k in cl.ROLE_KEYS} for m in ("light", "dark")}

    extended = build_extended(ext_in, scales, modes, hue_drift)

    warnings = []
    for k, sc in scales.items():
        warnings += check_spacing(k, sc["steps"])
    used = {v for sc in scales.values() for v in sc["steps"].values()} | \
        {v for m in modes.values() for v in m.values()}
    for b in brand:
        if b.get("locked") and b["hex"] not in used:
            warnings.append(f"locked brand colour {b['id']} {b['hex']} has no role and no scale in this palette; "
                            "pass it as the seed or --accent, or unlock/remove it on purpose")
    for w in warnings:
        log("warning: " + w)

    out = {
        "schema": cl.SCHEMA_ID,
        "name": name or (partial or {}).get("name") or "Untitled",
        "direction": direction if direction is not None else (partial or {}).get("direction", ""),
        "brand": brand,
        "scales": scales,
        "modes": modes,
        "rationale": (partial or {}).get("rationale", []),
        **({"feels": feels} if feels else {}),
        **({"extended": extended} if extended else {}),
        "audit": None,
        "build": {
            "generator": "brand-identity/palette_build.py",
            "seed": seed,
            "accent": {"hex": accent, "how": accent_why, "strategy": strategy if accent_why != "given" else None},
            "neutral": {"tint_hue": None if tint_hue is None else round(tint_hue, 1), "chroma": neutral_chroma},
            "grounds": {"light": light_bg or "white", "dark": dark_bg or "neutral-950"},
            "adjustments": adjustments,
            "warnings": warnings,
            "semantic_shifts": notes,
        },
    }
    cl.validate_palette(out, source="built palette")
    return out


def summary(pal):
    lines = [f"palette '{pal['name']}'"]
    for b in pal["brand"]:
        lines.append(f"  {b['id']:<8} {b['hex']}  {b.get('source', '')}{' locked' if b.get('locked') else ''}"
                     f"  {b.get('name', '')}")
    for k in ("primary", "accent"):
        sc = pal["scales"][k]
        lines.append(f"  {k:<8} anchor {sc.get('anchor')}: " + " ".join(sc["steps"][s] for s in STEPS))
    lines.append("  neutral  " + " ".join(pal["scales"]["neutral"]["steps"][s] for s in STEPS))
    for mode in ("light", "dark"):
        m = pal["modes"][mode]
        bg = m["background"]
        lines.append(f"  {mode:<5} ground {bg}: primary {m['primary']} with label {m['onPrimary']} "
                     f"{cl.contrast_ratio(m['primary'], m['onPrimary']):.2f}:1 (fill vs ground "
                     f"{cl.contrast_ratio(m['primary'], bg):.2f}:1); accent {m['accent']} with label {m['onAccent']} "
                     f"{cl.contrast_ratio(m['accent'], m['onAccent']):.2f}:1 (vs ground "
                     f"{cl.contrast_ratio(m['accent'], bg):.2f}:1); text on ground "
                     f"{cl.contrast_ratio(m['text'], bg):.2f}:1; link {m['link']} on ground "
                     f"{cl.contrast_ratio(m['link'], bg):.2f}:1")
    for e in pal.get("extended") or []:
        lines.append(f"  {e['id']:<8} {e['hex']} {e['name']}: label {e['on']} {cl.contrast_ratio(e['on'], e['hex']):.2f}:1, "
                     f"dark {e['dark']['hex']}")
    if pal.get("feels"):
        lines.append("  feels: " + ", ".join(pal["feels"]))
    for a in pal["build"]["adjustments"]:
        if "to_step" not in a and "kept_given" not in a:
            lines.append(f"  {a.get('role', 'note')}: {a['reason']}")
        elif "to_step" in a:
            target = "the accent role" if a["to_step"] == "accent" else f"{a['scale']} {a['to_step']}"
            lines.append(f"  adjusted {a['mode']}.{a['role']}: {a['scale']} {a['from_step']} -> {target}"
                         f" ({a['reason']})")
        else:
            lines.append(f"  kept given {a['mode']}.{a['role']} = {a['kept_given']}")
    for n in pal["build"]["semantic_shifts"]:
        lines.append(f"  semantic {n['scale']} hue {n['hue_from']} -> {n['hue_to']} ({n['reason']})")
    return "\n".join(lines)


def short_summary(pal, out_path=None):
    """Default stdout: headline, light/dark role contrast, adjustment counts with the first few, path."""
    prim, acc = pal["scales"]["primary"], pal["scales"]["accent"]
    lines = [f"palette '{pal['name']}': primary {pal['brand'][0]['hex']} (step {prim.get('anchor')}), "
             f"accent {acc['steps'].get(acc.get('anchor'), '?')} (step {acc.get('anchor')})"]
    for mode in ("light", "dark"):
        m = pal["modes"][mode]
        bg = m["background"]
        lines.append(f"{mode}: ground {bg}, text {cl.contrast_ratio(m['text'], bg):.1f}:1, primary label "
                     f"{cl.contrast_ratio(m['primary'], m['onPrimary']):.1f}:1, accent label "
                     f"{cl.contrast_ratio(m['accent'], m['onAccent']):.1f}:1")
    moves = [a for a in pal["build"]["adjustments"] if "to_step" in a]
    shifts = pal["build"]["semantic_shifts"]
    lines.append(f"adjustments: {len(moves)} role move(s), {len(shifts)} semantic hue shift(s)")
    for a in moves[:3]:
        lines.append(f"  {a['mode']}.{a['role']}: {a['scale']} {a['from_step']} -> {a['to_step']}")
    if not out_path:
        lines.append("(palette JSON not written: add -o FILE or --json)")
    if out_path:
        lines.append("-> " + out_path)
    return cl.clip_summary(lines)


def parse_extra(spec):
    """'#7b4b2a:Cocoa:product variant' -> (hex, name, usage). The name is required."""
    parts = [p.strip() for p in str(spec).split(":", 2)]
    if len(parts) < 2 or not parts[1]:
        raise BuildError(f"--extra {spec!r}: give '#hex:Name' or '#hex:Name:usage'")
    try:
        hx = cl.normalize_hex(parts[0])
    except ValueError as e:
        raise BuildError(f"--extra {spec!r}: {e}") from None
    return hx, parts[1], parts[2] if len(parts) > 2 else ""


def main(argv=None):
    cl.setup_utf8_console()
    ap = argparse.ArgumentParser(
        description="Build a full brand-identity palette.json (scales + light/dark roles) from seed colours.",
        epilog="examples:" + __doc__.split("Usage:")[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("seeds", nargs="*", metavar="SEED",
                    help="primary colour, optionally followed by the accent colour (#hex, rgb(), hsl(), oklch())")
    ap.add_argument("--accent", help="accent colour (overrides a second SEED)")
    ap.add_argument("--strategy", default=None, choices=("family", "analogous", "complement", "mono"),
                    help="how to derive the accent when none is given (default: family; mono when a brand colour is the ground and leaves no accent)")
    ap.add_argument("--neutral-tint", default=None,
                    help="tint the neutral scale toward: primary | accent | none | a colour (default: the hue of a "
                         "--dark-bg colour when one is given, else primary)")
    ap.add_argument("--neutral-chroma", type=float, default=0.022,
                    help="peak OKLCH chroma of the neutral scale, 0..0.045 (default 0.022; 0 = pure grey)")
    ap.add_argument("--light-bg", metavar="GROUND",
                    help="light page background: white (default), neutral-50 (or any neutral-<step>), or a colour "
                         "such as a cream '#f7f1e8'; surfaces become white cards on a tinted ground")
    ap.add_argument("--dark-bg", metavar="GROUND",
                    help="dark page background: neutral-950 (default), another neutral-<step>, or a colour")
    ap.add_argument("--extra", action="append", default=[], metavar="'#hex:Name[:usage]'",
                    help=f"extended colour (repeatable, at most {cl.MAX_EXTENDED}): product variants, illustration or "
                         "chart colours. Gets its own scale, a readable 'on' colour and a dark-mode version; core "
                         "roles are not affected. Example: --extra '#7b4b2a:Cocoa:product variant'")
    ap.add_argument("--feels", help="2-4 short adjectives the palette should feel like, e.g. 'calm, local, honest'")
    ap.add_argument("--name", help="direction name shown to the user, e.g. Harbor")
    ap.add_argument("--direction", help="one-line direction statement")
    ap.add_argument("--from", dest="from_path", help="partial palette.json to complete (brand[0] = primary seed)")
    ap.add_argument("--no-hue-drift", action="store_true", help="keep yellow scales at a constant hue")
    ap.add_argument("-o", "--out", help="write the palette JSON to this file")
    ap.add_argument("--json", action="store_true", help="print the palette JSON on stdout (instead of the summary)")
    ap.add_argument("--full", action="store_true", help="print the detailed summary (scales, every adjustment)")
    ap.add_argument("-q", "--quiet", action="store_true", help="print nothing but errors (and --json output)")
    args = ap.parse_args(argv)

    if len(args.seeds) > 2:
        ap.error(f"at most two seeds (primary, accent), got {len(args.seeds)}")
    if not 0 <= args.neutral_chroma <= 0.045:
        ap.error(f"--neutral-chroma must be within 0..0.045, got {args.neutral_chroma}")
    try:
        partial = cl.load_palette(args.from_path, partial=True) if args.from_path else None
        seed = args.seeds[0] if args.seeds else None
        accent = args.accent or (args.seeds[1] if len(args.seeds) > 1 else None)
        for label, v in (("seed", seed), ("accent", accent)):
            if v is not None:
                try:
                    cl.parse_color(v)
                except ValueError as e:
                    raise BuildError(f"{label}: {e}") from None
        if args.neutral_tint not in (None, "primary", "accent", "none"):
            try:
                cl.parse_color(args.neutral_tint)
            except ValueError:
                raise BuildError(f"--neutral-tint must be primary|accent|none or a colour, got "
                                 f"{args.neutral_tint!r}") from None
        if seed is None and partial is None:
            raise BuildError("give a seed colour (e.g. palette_build.py '#1f5f7a') or --from partial.json")
        extras = [parse_extra(x) for x in args.extra]
        feels = [f for f in args.feels.split(",")] if args.feels is not None else None
        pal = build_palette(seed, accent, args.strategy, args.neutral_tint, args.neutral_chroma, args.name,
                            args.direction, partial, not args.no_hue_drift, args.light_bg, args.dark_bg,
                            extras, feels)
    except (BuildError, cl.PaletteError) as e:
        log(f"error: {e}")
        return 2
    text = json.dumps(pal, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    if args.json:
        sys.stdout.write(text)
    elif not args.quiet:
        sys.stdout.write(summary(pal) + "\n" + ("-> " + args.out + "\n" if args.out else "")
                         if args.full else short_summary(pal, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
