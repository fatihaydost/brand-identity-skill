"""Shared, dependency-free colour maths for the brand-identity skill scripts.

Everything the other scripts measure goes through here: parsing CSS colours, sRGB <-> linear <-> XYZ <-> OKLab/OKLCH,
CIELAB (D65, and D50 via Bradford), WCAG 2 contrast, CIEDE2000, deltaE OK, CSS Color 4 gamut mapping, max chroma per
lightness/hue, colour-vision-deficiency simulation, the Ou-Luo two-colour harmony model (advisory only) and
O'Donovan hue entropy. Python >= 3.9, standard library only, no network.

Sources (see the sources listed below):
  * CSS Color 4 (W3C): sRGB/XYZ matrices, Bradford D65<->D50, gamut mapping "binary search with local MINDE".
  * Ottosson 2020, "A perceptual color space for image processing" (OKLab matrices, 2021-01-25 revision).
  * Sharma, Wu & Dalal 2005, Color Res Appl 30:21-30 (CIEDE2000 implementation notes and test data).
  * WCAG 2.2 relative luminance and contrast ratio (thresholds are never rounded).
  * Machado, Oliveira & Fernandes 2009 (protan/deutan, severity 1.0, linear RGB);
    Brettel, Vienot & Mollon 1997 as precomputed by libDaltonLens (public domain) for tritan.
  * Ou & Luo 2006 / Ou et al. 2010 two-colour harmony; O'Donovan et al. 2011 hue entropy.

No APCA anywhere: its licence restricts redistribution of independent implementations (apca-w3 licence; see references/accessibility.md).

Public API used by the other scripts (names are a contract, see docs/architecture.md section 4.1):
  parse_color, to_hex, hex_to_oklch, oklch_to_hex, contrast_ratio, relative_luminance, delta_e2000, delta_e_ok,
  simulate_cvd, hex_to_lab, max_chroma, best_text_on, load_palette, PaletteError, ROLE_KEYS, setup_utf8_console.
Colour arguments accept a CSS colour string ("#1f5f7a", "rgb(31 95 122)", "oklch(0.45 0.08 225)") or an sRGB tuple of
floats in 0..1.
"""
import json
import math
import re
import sys

__all__ = [
    "PaletteError", "ROLE_KEYS", "STEP_KEYS", "SCHEMA_ID", "MAX_EXTENDED", "MAX_FEELS", "setup_utf8_console",
    "parse_color", "to_hex", "normalize_hex", "is_hex",
    "srgb_to_linear", "linear_to_srgb", "rgb_to_xyz", "xyz_to_rgb", "xyz_d65_to_d50", "xyz_d50_to_d65",
    "xyz_to_lab", "lab_to_lch", "rgb_to_oklab", "oklab_to_rgb", "xyz_to_oklab", "oklab_to_oklch", "oklch_to_oklab",
    "hex_to_oklab", "hex_to_oklch", "oklch_to_rgb", "oklch_to_hex", "in_gamut", "gamut_map_oklch", "max_chroma",
    "relative_luminance", "contrast_ratio", "best_text_on", "hex_to_lab", "lab_of",
    "delta_e2000", "delta_e2000_lab", "delta_e_ok", "delta_e_ok_lab", "simulate_cvd",
    "ou_luo_harmony", "hue_entropy", "load_palette", "validate_palette",
]

SCHEMA_ID = "brand-identity/palette@1"

# Role keys of palette.json `modes.light` / `modes.dark`, in docs/architecture.md section 3 order.
ROLE_KEYS = (
    "background", "surface", "surfaceAlt", "border", "text", "textMuted", "primary", "onPrimary",
    "accent", "onAccent", "link", "focus", "success", "warning", "danger", "info",
)
# Scale step keys, light to dark.
STEP_KEYS = ("50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950")
MAX_EXTENDED = 5   # extended (secondary/variant) colours ext-1..ext-5
MAX_FEELS = 4      # palette.json "feels": 2-4 short adjectives


class PaletteError(ValueError):
    """palette.json does not match the brand-identity/palette@1 contract. The message names the exact field."""


def setup_utf8_console():
    """Make stdout/stderr UTF-8 so reports with arrows and symbols print on Windows consoles (cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


SUMMARY_LIMIT = 1200   # default stdout budget of every command, in bytes (docs/architecture.md section 4.0)


def clip_summary(lines, limit=SUMMARY_LIMIT, line_max=220):
    """Join report lines so the text stays within `limit` bytes: long lines are cut, surplus lines are dropped
    (a final line says how many). The last line is kept whole when it is a path line (starts with '->')."""
    cut = [ln if len(ln) <= line_max else ln[:line_max - 3] + "..." for ln in lines]
    tail = [ln for ln in cut[-1:] if ln.startswith("->")]
    body = cut[:-1] if tail else cut
    kept, used = [], sum(len(t.encode("utf-8")) + 1 for t in tail) + 40
    for ln in body:
        n = len(ln.encode("utf-8")) + 1
        if used + n > limit:
            break
        kept.append(ln)
        used += n
    if len(kept) < len(body):
        kept.append(f"... {len(body) - len(kept)} more line(s) (--full)")
    return "\n".join(kept + tail) + "\n"


# ----------------------------------------------------------------------------- parsing

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FUNC_RE = re.compile(r"^(rgba?|hsla?|oklch)\(\s*(.*?)\s*\)$", re.I)
_CANON_HEX_RE = re.compile(r"^#[0-9a-f]{6}$")
_NAMED = {"white": "#ffffff", "black": "#000000"}


def _split_args(body):
    """CSS colour function arguments: commas or spaces, optional '/ alpha' (alpha is dropped)."""
    body = body.split("/")[0]
    parts = [p for p in re.split(r"[\s,]+", body.strip()) if p]
    return parts


def _num(token, what, scale_pct=None):
    token = token.strip()
    try:
        if token.endswith("%"):
            v = float(token[:-1]) / 100.0
            return v * scale_pct if scale_pct is not None else v
        return float(token)
    except ValueError:
        raise ValueError(f"cannot read {what} from {token!r}") from None


def _hue(token):
    token = token.strip().lower()
    for unit, factor in (("deg", 1.0), ("grad", 0.9), ("rad", 180.0 / math.pi), ("turn", 360.0)):
        if token.endswith(unit):
            return float(token[: -len(unit)]) * factor
    return _num(token, "hue")


def _hsl_to_rgb(h, s, l):
    h = (h % 360) / 360.0

    def f(n):
        k = (n + h * 12) % 12
        a = s * min(l, 1 - l)
        return l - a * max(-1, min(k - 3, 9 - k, 1))
    return (f(0), f(8), f(4))


def parse_color(s):
    """Parse a CSS colour into an sRGB tuple of floats 0..1.

    Accepts '#rgb', '#rgba', '#rrggbb', '#rrggbbaa' (alpha ignored), 'rgb()/rgba()' (0-255 or %), 'hsl()/hsla()',
    'oklch(L C h)' (L as 0..1 or %; out-of-gamut values are gamut-mapped per CSS Color 4), 'white', 'black', or an
    already-parsed (r, g, b) sequence. Raises ValueError with the offending text otherwise.
    """
    if isinstance(s, (tuple, list)):
        if len(s) != 3:
            raise ValueError(f"expected an (r, g, b) triple, got {s!r}")
        return tuple(float(c) for c in s)
    if not isinstance(s, str):
        raise ValueError(f"expected a colour string, got {type(s).__name__}: {s!r}")
    text = s.strip()
    low = text.lower()
    if low in _NAMED:
        text = _NAMED[low]
    m = _HEX_RE.match(text)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h[:3])
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    m = _FUNC_RE.match(text)
    if not m:
        raise ValueError(f"not a colour: {s!r} (expected #hex, rgb(), hsl() or oklch())")
    fn, args = m.group(1).lower(), _split_args(m.group(2))
    if len(args) < 3:
        raise ValueError(f"{fn}() needs three components: {s!r}")
    if fn.startswith("rgb"):
        vals = [_num(a, "rgb channel", 255.0) / 255.0 for a in args[:3]]
        return tuple(min(1.0, max(0.0, v)) for v in vals)
    if fn.startswith("hsl"):
        h = _hue(args[0])
        sat = _num(args[1], "saturation") if args[1].endswith("%") else _num(args[1], "saturation") / 100.0
        lig = _num(args[2], "lightness") if args[2].endswith("%") else _num(args[2], "lightness") / 100.0
        return _hsl_to_rgb(h, min(1.0, max(0.0, sat)), min(1.0, max(0.0, lig)))
    # oklch
    L = _num(args[0], "oklch lightness")
    C = _num(args[1], "oklch chroma", 0.4)
    h = 0.0 if args[2].lower() == "none" else _hue(args[2])
    return gamut_map_oklch(L, C, h)


def to_hex(rgb):
    """sRGB tuple (0..1, clipped) or colour string -> '#rrggbb' (lowercase)."""
    if isinstance(rgb, str):
        rgb = parse_color(rgb)
    return "#" + "".join("%02x" % int(round(min(1.0, max(0.0, c)) * 255)) for c in rgb)


def normalize_hex(s):
    """Any parseable colour -> canonical '#rrggbb'."""
    return to_hex(parse_color(s))


def is_hex(s):
    """True for a canonical-or-uppercase 6-digit '#rrggbb' string (the only form palette.json stores)."""
    return isinstance(s, str) and bool(_CANON_HEX_RE.match(s.lower()))


def _rgb(c):
    return parse_color(c) if not isinstance(c, (tuple, list)) else tuple(float(x) for x in c)


# ----------------------------------------------------------------------------- sRGB, XYZ, CIELAB

def srgb_to_linear(c):
    """sRGB transfer function, extended to negative values (CSS Color 4)."""
    a = abs(c)
    return c / 12.92 if a <= 0.04045 else math.copysign(((a + 0.055) / 1.055) ** 2.4, c)


def linear_to_srgb(c):
    a = abs(c)
    return 12.92 * c if a <= 0.0031308 else math.copysign(1.055 * a ** (1 / 2.4) - 0.055, c)


def _mul(m, v):
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


# CSS Color 4, section 19 (rational-derived matrices).
_LIN_SRGB_TO_XYZ = (
    (506752 / 1228815, 87881 / 245763, 12673 / 70218),
    (87098 / 409605, 175762 / 245763, 12673 / 175545),
    (7918 / 409605, 87881 / 737289, 1001167 / 1053270),
)
_XYZ_TO_LIN_SRGB = (
    (12831 / 3959, -329 / 214, -1974 / 3959),
    (-851781 / 878810, 1648619 / 878810, 36519 / 878810),
    (705 / 12673, -2585 / 12673, 705 / 667),
)
# Bradford chromatic adaptation, CSS Color 4.
_D65_TO_D50 = (
    (1.0479297925449969, 0.022946870601609652, -0.05019226628920524),
    (0.02962780877005599, 0.9904344267538799, -0.017073799063418826),
    (-0.009243040646204504, 0.015055191490298152, 0.7518742814281371),
)
_D50_TO_D65 = (
    (0.955473421488075, -0.02309845494876471, 0.06325924320057072),
    (-0.0283697093338637, 1.0099953980813041, 0.021041441191917323),
    (0.012314014864481998, -0.020507649298898964, 1.330365926242124),
)
# Reference whites (CSS Color 4, chromaticity-derived).
WHITES = {
    "D65": (0.3127 / 0.3290, 1.0, (1.0 - 0.3127 - 0.3290) / 0.3290),
    "D50": (0.3457 / 0.3585, 1.0, (1.0 - 0.3457 - 0.3585) / 0.3585),
}


def rgb_to_xyz(rgb):
    """Gamma-encoded sRGB -> CIE XYZ (D65, Y of white = 1)."""
    return _mul(_LIN_SRGB_TO_XYZ, [srgb_to_linear(c) for c in rgb])


def xyz_to_rgb(xyz):
    """CIE XYZ (D65) -> gamma-encoded sRGB (unclipped)."""
    return tuple(linear_to_srgb(c) for c in _mul(_XYZ_TO_LIN_SRGB, xyz))


def xyz_d65_to_d50(xyz):
    return _mul(_D65_TO_D50, xyz)


def xyz_d50_to_d65(xyz):
    return _mul(_D50_TO_D65, xyz)


def xyz_to_lab(xyz, white="D65"):
    """CIE XYZ (already adapted to `white`) -> CIELAB (L*, a*, b*)."""
    if white not in WHITES:
        raise ValueError(f"white must be 'D65' or 'D50', got {white!r}")
    eps, kappa = 216 / 24389, 24389 / 27
    f = []
    for v, w in zip(xyz, WHITES[white]):
        t = v / w
        f.append(t ** (1 / 3) if t > eps else (kappa * t + 16) / 116)
    return (116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2]))


def lab_to_lch(lab):
    L, a, b = lab
    return (L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360)


def hex_to_lab(h, white="D65"):
    """Colour -> CIELAB. white='D65' (screen work, deltaE) or 'D50' (print/ICC convention, via Bradford)."""
    xyz = rgb_to_xyz(_rgb(h))
    if white == "D50":
        xyz = xyz_d65_to_d50(xyz)
    elif white != "D65":
        raise ValueError(f"white must be 'D65' or 'D50', got {white!r}")
    return xyz_to_lab(xyz, white)


lab_of = hex_to_lab


# ----------------------------------------------------------------------------- OKLab / OKLCH

def _oklab_from_linear(r, g, b):
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = (math.copysign(abs(x) ** (1 / 3), x) for x in (l, m, s))
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _linear_from_oklab(L, a, b):
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


# Ottosson's XYZ (D65) -> LMS matrix, used only for the reference test vectors in his post.
_XYZ_TO_LMS = ((0.8189330101, 0.3618667424, -0.1288597137),
               (0.0329845436, 0.9293118715, 0.0361456387),
               (0.0482003018, 0.2643662691, 0.6338517070))
_LMS_TO_OKLAB = ((0.2104542553, 0.7936177850, -0.0040720468),
                 (1.9779984951, -2.4285922050, 0.4505937099),
                 (0.0259040371, 0.7827717662, -0.8086757660))


def xyz_to_oklab(xyz):
    lms = _mul(_XYZ_TO_LMS, xyz)
    return _mul(_LMS_TO_OKLAB, [math.copysign(abs(x) ** (1 / 3), x) for x in lms])


def rgb_to_oklab(rgb):
    return _oklab_from_linear(*(srgb_to_linear(c) for c in rgb))


def oklab_to_rgb(lab):
    """OKLab -> gamma-encoded sRGB, unclipped (may fall outside 0..1)."""
    return tuple(linear_to_srgb(c) for c in _linear_from_oklab(*lab))


def oklab_to_oklch(lab):
    L, a, b = lab
    C = math.hypot(a, b)
    h = math.degrees(math.atan2(b, a)) % 360 if C > 1e-7 else 0.0
    return (L, C, h)


def oklch_to_oklab(L, C, h):
    r = math.radians(h)
    return (L, C * math.cos(r), C * math.sin(r))


def hex_to_oklab(h):
    return rgb_to_oklab(_rgb(h))


def hex_to_oklch(h):
    """Colour -> (L 0..1, C, h degrees 0..360). Achromatic colours get h = 0."""
    return oklab_to_oklch(hex_to_oklab(h))


def oklch_to_rgb(L, C, h):
    """OKLCH -> sRGB, unclipped (no gamut mapping)."""
    return oklab_to_rgb(oklch_to_oklab(L, C, h))


def in_gamut(rgb, eps=1e-6):
    return all(-eps <= c <= 1 + eps for c in rgb)


def _clip(rgb):
    return tuple(min(1.0, max(0.0, c)) for c in rgb)


def delta_e_ok_lab(lab1, lab2):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(lab1, lab2)))


def gamut_map_oklch(L, C, h, jnd=0.02, eps=0.0001):
    """CSS Color 4 section 14.2.1 'binary search with local MINDE' into sRGB. Returns an in-gamut sRGB tuple.

    Keeps L and h, reduces C until the clipped colour is within one JND (deltaE OK 0.02) of the candidate.
    """
    if L >= 1.0:
        return (1.0, 1.0, 1.0)
    if L <= 0.0:
        return (0.0, 0.0, 0.0)
    C = max(0.0, C)
    origin = oklch_to_rgb(L, C, h)
    if in_gamut(origin):
        return _clip(origin)
    clipped = _clip(origin)
    if delta_e_ok_lab(rgb_to_oklab(clipped), oklch_to_oklab(L, C, h)) < jnd:
        return clipped
    lo, hi, lo_in_gamut = 0.0, C, True
    while hi - lo > eps:
        mid = (lo + hi) / 2
        cur_lab = oklch_to_oklab(L, mid, h)
        cur = oklab_to_rgb(cur_lab)
        if lo_in_gamut and in_gamut(cur):
            lo = mid
            continue
        clipped = _clip(cur)
        e = delta_e_ok_lab(rgb_to_oklab(clipped), cur_lab)
        if e < jnd:
            if jnd - e < eps:
                return clipped
            lo_in_gamut = False
            lo = mid
        else:
            hi = mid
    return clipped


def oklch_to_hex(L, C, h):
    """OKLCH -> '#rrggbb', gamut-mapped into sRGB with the CSS Color 4 algorithm."""
    return to_hex(gamut_map_oklch(L, C, h))


def max_chroma(L, h, eps=1e-5):
    """Largest OKLCH chroma at lightness L and hue h that is still inside sRGB (binary search)."""
    if L <= 0.0 or L >= 1.0:
        return 0.0
    lo, hi = 0.0, 0.5
    while hi - lo > eps:
        mid = (lo + hi) / 2
        if in_gamut(oklch_to_rgb(L, mid, h), eps=1e-9):
            lo = mid
        else:
            hi = mid
    return lo


# ----------------------------------------------------------------------------- WCAG 2

def relative_luminance(c):
    """WCAG 2.x relative luminance Y (0..1) of a colour string or sRGB tuple."""
    r, g, b = (srgb_to_linear(x) for x in _clip(_rgb(c)))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a, b):
    """WCAG 2.x contrast ratio, 1..21, NOT rounded (4.499 fails 4.5)."""
    ya, yb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(ya, yb), min(ya, yb)
    return (hi + 0.05) / (lo + 0.05)


def best_text_on(bg, candidates=("#ffffff", "#000000")):
    """Candidate with the highest WCAG contrast on `bg` (first wins ties). Returns the candidate as given."""
    best, best_ratio = None, -1.0
    for c in candidates:
        r = contrast_ratio(c, bg)
        if r > best_ratio + 1e-12:
            best, best_ratio = c, r
    return best


# ----------------------------------------------------------------------------- colour difference

def delta_e2000_lab(lab1, lab2, kL=1.0, kC=1.0, kH=1.0):
    """CIEDE2000 between two CIELAB triples (Sharma, Wu & Dalal 2005, including their edge-case notes)."""
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cbar = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cbar ** 7 / (Cbar ** 7 + 25.0 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if (a1p or b1) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if (a2p or b2) else 0.0
    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        if dhp > 180:
            dhp -= 360
        elif dhp < -180:
            dhp += 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp = (L1 + L2) / 2
    Cbp = (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2
    else:
        hbp = (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dtheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    RC = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25.0 ** 7))
    SL = 1 + 0.015 * (Lbp - 50) ** 2 / math.sqrt(20 + (Lbp - 50) ** 2)
    SC = 1 + 0.045 * Cbp
    SH = 1 + 0.015 * Cbp * T
    RT = -math.sin(math.radians(2 * dtheta)) * RC
    tl, tc, th = dLp / (kL * SL), dCp / (kC * SC), dHp / (kH * SH)
    return math.sqrt(tl * tl + tc * tc + th * th + RT * tc * th)


def delta_e2000(a, b):
    """CIEDE2000 between two colours (strings or sRGB tuples), CIELAB D65. ~1 = just noticeable."""
    return delta_e2000_lab(hex_to_lab(a), hex_to_lab(b))


def delta_e_ok(a, b):
    """Euclidean distance in OKLab between two colours. JND = 0.02 (CSS Color 4)."""
    return delta_e_ok_lab(hex_to_oklab(a), hex_to_oklab(b))


# ----------------------------------------------------------------------------- colour vision deficiency

# Machado, Oliveira & Fernandes 2009, severity 1.0, linear RGB, row-major.
_MACHADO = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
}
# Brettel 1997 tritan, precomputed for linear sRGB by libDaltonLens (public domain).
_BRETTEL_TRITAN = (
    ((1.01277, 0.13548, -0.14826), (-0.01243, 0.86812, 0.14431), (0.07589, 0.80500, 0.11911)),
    ((0.93678, 0.18979, -0.12657), (0.06154, 0.81526, 0.12320), (-0.37562, 1.12767, 0.24796)),
    (0.03901, -0.02788, -0.01113),
)
CVD_KINDS = ("protan", "deutan", "tritan")


def simulate_cvd(c, kind, severity=1.0):
    """Simulate dichromacy on a colour; returns '#rrggbb'. kind: protan | deutan | tritan.

    Matrices are applied in LINEAR RGB (applying them to gamma-encoded values darkens colours; DaltonLens 2021).
    protan/deutan: Machado 2009; tritan: Brettel 1997 (Machado's tritan is only approximate).
    `severity` < 1 blends linearly with the original (anomalous trichromacy approximation).
    """
    if kind not in CVD_KINDS:
        raise ValueError(f"kind must be one of {', '.join(CVD_KINDS)}, got {kind!r}")
    lin = tuple(srgb_to_linear(x) for x in _clip(_rgb(c)))
    if kind == "tritan":
        m1, m2, n = _BRETTEL_TRITAN
        m = m1 if (lin[0] * n[0] + lin[1] * n[1] + lin[2] * n[2]) >= 0 else m2
    else:
        m = _MACHADO[kind]
    sim = _mul(m, lin)
    s = min(1.0, max(0.0, severity))
    out = tuple(s * x + (1 - s) * y for x, y in zip(sim, lin))
    return to_hex(tuple(linear_to_srgb(min(1.0, max(0.0, x))) for x in out))


# ----------------------------------------------------------------------------- harmony signals (advisory)

def ou_luo_harmony(a, b):
    """Ou & Luo two-colour harmony score CH (positive = more harmonious). ADVISORY ONLY.

    Measured on a grey background on a 2006 CRT, no context; it penalises some well-loved complementary pairs
    (orange + navy). Use it to ask "is this deliberate?", never as a verdict (Ou & Luo 2006, Color Res. Appl. 31:191-204).
    """
    L1, C1, h1 = lab_to_lch(hex_to_lab(a))
    L2, C2, h2 = lab_to_lch(hex_to_lab(b))
    dh = abs(h1 - h2)
    dh = min(dh, 360 - dh)
    dH = 2 * math.sqrt(C1 * C2) * math.sin(math.radians(dh / 2))
    dC = math.sqrt(dH ** 2 + ((C1 - C2) / 1.46) ** 2)
    hc = 0.04 + 0.53 * math.tanh(0.8 - 0.045 * dC)
    hl_sum = 0.28 + 0.54 * math.tanh(-3.88 + 0.029 * (L1 + L2))
    hl_d = 0.14 + 0.15 * math.tanh(-2 + 0.2 * abs(L1 - L2))

    def hsy(L, C, h):
        ec = 0.5 + 0.5 * math.tanh(-2 + 0.5 * C)
        hs = -0.08 - 0.14 * math.sin(math.radians(h + 50)) - 0.07 * math.sin(math.radians(2 * h + 90))
        x = (90 - h) / 10
        ey = ((0.22 * L - 12.8) / 10) * math.exp(x - math.exp(x)) if x < 50 else 0.0
        return ec * (hs + ey)
    return hc + hl_sum + hl_d + hsy(L1, C1, h1) + hsy(L2, C2, h2)


def hue_entropy(colors, min_chroma=0.03, kappa=2 * math.pi, bins=360):
    """O'Donovan et al. 2011 hue entropy of the chromatic colours (OKLCH C >= min_chroma).

    p(theta) proportional to sum_i exp(kappa*cos(theta - theta_i)), natural-log entropy over `bins` bins.
    One hue ~4.6; five evenly spaced hues ~5.87; well-rated themes ~4.7-5.4. Returns None with no chromatic colour.
    Accepts colours or bare hue angles (int/float).
    """
    hues = []
    for c in colors:
        if isinstance(c, (int, float)):
            hues.append(float(c))
            continue
        L, C, h = hex_to_oklch(c)
        if C >= min_chroma:
            hues.append(h)
    if not hues:
        return None
    p = []
    for i in range(bins):
        t = math.radians(i * 360.0 / bins)
        p.append(sum(math.exp(kappa * math.cos(t - math.radians(h))) for h in hues))
    total = sum(p)
    return -sum((x / total) * math.log(x / total) for x in p if x > 0)


# ----------------------------------------------------------------------------- palette.json contract

def _err(path, msg):
    raise PaletteError(f"{path}: {msg}")


def _check_hex(path, v):
    if not is_hex(v):
        _err(path, f"{v!r} is not a 6-digit '#rrggbb' hex")
    return v.lower()


def validate_palette(data, partial=False, source="palette.json"):
    """Validate (and lowercase the hex values of) a palette dict in place; return it.

    partial=True allows missing `scales`/`modes` and missing role keys (an input to palette_build.py or an audit of
    a measured site); full palettes need every role in both modes and 11-step scales.
    Raises PaletteError naming the exact field, e.g. "palette.json: modes.dark.onPrimary: '#12345' is not ...".
    """
    def fail(path, msg):
        raise PaletteError(f"{source}: {path}: {msg}")

    if not isinstance(data, dict):
        fail("<root>", f"expected a JSON object, got {type(data).__name__}")
    if data.get("schema") != SCHEMA_ID:
        fail("schema", f"expected {SCHEMA_ID!r}, got {data.get('schema')!r}")
    if not partial or "name" in data:
        if not isinstance(data.get("name"), str) or not data.get("name", "").strip():
            fail("name", "missing or empty (direction name shown to the user)")
    brand = data.get("brand", [] if partial else None)
    if not isinstance(brand, list) or (not partial and not brand):
        fail("brand", "expected a non-empty list of identity colours")
    seen = set()
    for i, b in enumerate(brand):
        p = f"brand[{i}]"
        if not isinstance(b, dict):
            fail(p, "expected an object")
        if not isinstance(b.get("id"), str) or not b["id"]:
            fail(p + ".id", "missing")
        if b["id"] in seen:
            fail(p + ".id", f"duplicate id {b['id']!r}")
        seen.add(b["id"])
        try:
            b["hex"] = _check_hex(p + ".hex", b.get("hex"))
        except PaletteError as e:
            raise PaletteError(f"{source}: {e}") from None
        if "locked" in b and not isinstance(b["locked"], bool):
            fail(p + ".locked", "must be true or false")
        if "source" in b and b["source"] not in ("chosen", "measured", "derived"):
            fail(p + ".source", f"must be chosen|measured|derived, got {b['source']!r}")
    scales = data.get("scales")
    if scales is None and not partial:
        fail("scales", "missing")
    if scales is not None:
        if not isinstance(scales, dict):
            fail("scales", "expected an object of named scales")
        for name, sc in scales.items():
            p = f"scales.{name}"
            if not isinstance(sc, dict) or not isinstance(sc.get("steps"), dict):
                fail(p, "expected {\"steps\": {\"50\": \"#..\", ..., \"950\": \"#..\"}}")
            steps = sc["steps"]
            if not (partial and not steps):
                missing = [k for k in STEP_KEYS if k not in steps]
                extra = [k for k in steps if k not in STEP_KEYS]
                if missing:
                    fail(p + ".steps", f"missing step(s) {', '.join(missing)}")
                if extra:
                    fail(p + ".steps", f"unknown step(s) {', '.join(extra)}; keys are 50..950")
            for k in list(steps):
                try:
                    steps[k] = _check_hex(f"{p}.steps.{k}", steps[k])
                except PaletteError as e:
                    raise PaletteError(f"{source}: {e}") from None
            anchor = sc.get("anchor")
            if anchor is not None and str(anchor) not in STEP_KEYS:
                fail(p + ".anchor", f"{anchor!r} is not a step key (50..950)")
    for k in ("primary", "neutral"):
        if not partial and k not in (scales or {}):
            fail("scales", f"missing required scale {k!r}")
    modes = data.get("modes")
    if modes is None and not partial:
        fail("modes", "missing")
    if modes is not None:
        if not isinstance(modes, dict):
            fail("modes", "expected {\"light\": {...}, \"dark\": {...}}")
        for mode in ("light", "dark"):
            m = modes.get(mode)
            if m is None:
                if partial:
                    continue
                fail(f"modes.{mode}", "missing")
            if not isinstance(m, dict):
                fail(f"modes.{mode}", "expected an object of role -> hex")
            for role in ROLE_KEYS:
                if role not in m:
                    if partial:
                        continue
                    fail(f"modes.{mode}.{role}", "missing role")
                try:
                    m[role] = _check_hex(f"modes.{mode}.{role}", m[role])
                except PaletteError as e:
                    raise PaletteError(f"{source}: {e}") from None
            unknown = [r for r in m if r not in ROLE_KEYS]
            if unknown:
                fail(f"modes.{mode}", f"unknown role(s) {', '.join(unknown)}; role keys are fixed: "
                                      f"{', '.join(ROLE_KEYS)}")
    feels = data.get("feels")
    if feels is not None:
        if not isinstance(feels, list) or not all(isinstance(f, str) and f.strip() for f in feels):
            fail("feels", "expected a list of short adjectives, e.g. [\"calm\", \"local\", \"honest\"]")
        if not 2 <= len(feels) <= MAX_FEELS:
            fail("feels", f"give 2-{MAX_FEELS} words, got {len(feels)}")
    ext = data.get("extended")
    if ext is not None:
        if not isinstance(ext, list):
            fail("extended", "expected a list of {id, name, hex, on, dark: {hex, on}, usage}")
        if len(ext) > MAX_EXTENDED:
            fail("extended", f"at most {MAX_EXTENDED} extended colours, got {len(ext)}")
        for i, e in enumerate(ext):
            pth = f"extended[{i}]"
            if not isinstance(e, dict):
                fail(pth, "expected an object")
            if e.get("id") != f"ext-{i + 1}":
                fail(pth + ".id", f"must be 'ext-{i + 1}' (the order is the CSS index), got {e.get('id')!r}")
            try:
                e["hex"] = _check_hex(pth + ".hex", e.get("hex"))
                if not partial or "on" in e:
                    e["on"] = _check_hex(pth + ".on", e.get("on"))
                d = e.get("dark")
                if d is not None or not partial:
                    if not isinstance(d, dict):
                        raise PaletteError(f"{pth}.dark: expected {{hex, on}}")
                    d["hex"] = _check_hex(pth + ".dark.hex", d.get("hex"))
                    d["on"] = _check_hex(pth + ".dark.on", d.get("on"))
            except PaletteError as err:
                raise PaletteError(f"{source}: {err}") from None
            if not partial and f"ext-{i + 1}" not in (scales or {}):
                fail("scales", f"missing scale 'ext-{i + 1}' for extended[{i}]")
    rat = data.get("rationale", [])
    if rat is not None and not isinstance(rat, list):
        fail("rationale", "expected a list of {claim, evidence, source}")
    for i, r in enumerate(rat or []):
        if not isinstance(r, dict) or "claim" not in r:
            fail(f"rationale[{i}]", "expected {claim, evidence, source}")
        ev = r.get("evidence")
        if ev is not None and ev not in ("strong", "limited", "practice", "popular-unsupported"):
            fail(f"rationale[{i}].evidence", f"must be strong|limited|practice|popular-unsupported, got {ev!r}")
    if data.get("audit") is not None and not isinstance(data["audit"], dict):
        fail("audit", "expected null or an object written by palette_audit.py")
    return data


def load_palette(path, partial=False):
    """Read and validate a palette.json; returns the dict (hex values lowercased). Raises PaletteError."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise PaletteError(f"{path}: file not found") from None
    except json.JSONDecodeError as e:
        raise PaletteError(f"{path}: invalid JSON at line {e.lineno} column {e.colno}: {e.msg}") from None
    return validate_palette(data, partial=partial, source=str(path))


# ----------------------------------------------------------------------------- CLI (inspection helper)

def _main(argv=None):
    """Describe colours: hex, OKLCH, Lab D65/D50, WCAG contrast on white/black and between the first two."""
    import argparse
    setup_utf8_console()
    ap = argparse.ArgumentParser(
        description="colorlib: shared colour maths of the brand-identity skill. As a script it describes colours.",
        epilog="examples:\n  python3 scripts/colorlib.py \"#1f5f7a\"\n"
               "  python3 scripts/colorlib.py \"#777\" \"#fff\"            # contrast 4.48:1 (fails 4.5, no rounding)\n"
               "  python3 scripts/colorlib.py \"oklch(0.7 0.25 150)\" --json  # gamut-mapped into sRGB",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("colors", nargs="+", metavar="COLOR", help="#hex, rgb(), hsl() or oklch()")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args(argv)
    rows = []
    for c in args.colors:
        try:
            hx = normalize_hex(c)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        L, C, h = hex_to_oklch(hx)
        row = {"input": c, "hex": hx, "oklch": [round(L, 4), round(C, 4), round(h, 2)],
               "lab_d65": [round(v, 2) for v in hex_to_lab(hx)], "lab_d50": [round(v, 2) for v in hex_to_lab(hx, "D50")],
               "on_white": round(contrast_ratio(hx, "#ffffff"), 3), "on_black": round(contrast_ratio(hx, "#000000"), 3),
               "max_chroma_here": round(max_chroma(L, h), 4)}
        rows.append(row)
    out = {"colors": rows}
    if len(rows) >= 2:
        a, b = rows[0]["hex"], rows[1]["hex"]
        out["pair"] = {"contrast": round(contrast_ratio(a, b), 3), "delta_e2000": round(delta_e2000(a, b), 2),
                       "delta_e_ok": round(delta_e_ok(a, b), 4)}
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    for r in rows:
        print(f"{r['hex']}  oklch({r['oklch'][0]:.4f} {r['oklch'][1]:.4f} {r['oklch'][2]:.2f})  "
              f"Lab D50 {r['lab_d50'][0]:.1f}/{r['lab_d50'][1]:.1f}/{r['lab_d50'][2]:.1f}  "
              f"white {r['on_white']:.2f}:1  black {r['on_black']:.2f}:1")
    if "pair" in out:
        p = out["pair"]
        print(f"pair: contrast {p['contrast']:.3f}:1, CIEDE2000 {p['delta_e2000']:.2f}, deltaE OK {p['delta_e_ok']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
