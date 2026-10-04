#!/usr/bin/env python3
"""Search the brand palette library: real brands' website palettes, measured and labelled by industry.

Use it to read a category's colour codes before choosing a direction: which hue families the sector's brands
already use, how they structure lightness (light or dark ground, dark or mid primary), and which families are
empty or rare (candidate open space). Then check the user's live competitors; the library is a prior, not a
substitute for the actual market.

Measured hex values approximate what the public sites rendered on the measurement day. They are not official
brand specifications, and every name and colour mark belongs to its owner (TRADEMARKS.md). Study, never copy.

Examples:
  python3 scripts/search_library.py --industry fintech --summary
  python3 scripts/search_library.py --industry banking --country TR --summary
  python3 scripts/search_library.py --industry food --hue red,orange            # near-miss industry names work
  python3 scripts/search_library.py --hue 180-230 --exemplary                   # hue range in OKLCH degrees
  python3 scripts/search_library.py --industry saas --strategy differentiate     # brands outside the category's families
  python3 scripts/search_library.py --price luxury --format json
  python3 scripts/search_library.py --query "coral" --format paths               # gallery anchors to open
  python3 scripts/search_library.py --list-values

Filters combine with AND; comma lists inside one filter mean OR. --hue accepts families
(red|orange|yellow|green|teal|blue|purple|pink|brown|neutral), a degree range "200-260", or one angle "25" (±20°);
it matches the primary colour, or any brand colour with --any-colour.
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.dont_write_bytecode = True  # keep the skill folder clean (no __pycache__)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_catalog as bc  # noqa: E402
import colorlib  # noqa: E402


def load_catalog(library=None):
    path = os.path.join(library or bc.LIBRARY_DIR, "catalog.json")
    if not os.path.exists(path):
        raise SystemExit(f"error: {path} not found - run scripts/build_catalog.py first")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def resolve(value, known, flag):
    """Exact value, or the single known value containing it ('food' -> 'food-beverage'); exit otherwise."""
    if value in known:
        return value
    close = [k for k in known if value.lower() in k.lower() or k.lower() in value.lower()]
    if len(close) == 1:
        print(f"(--{flag} '{value}' -> using '{close[0]}')", file=sys.stderr)
        return close[0]
    hint = ", ".join(close or known)
    raise SystemExit(f"error: unknown --{flag} '{value}'. Did you mean: {hint}")


def hue_matcher(spec):
    """Predicate on a hex colour from a --hue spec (families, 'a-b' degree range or single angle)."""
    tests = []
    for part in [p.strip().lower() for p in spec.split(",") if p.strip()]:
        if part in bc.FAMILIES:
            tests.append(lambda c, f=part: bc.hue_family(c) == f)
            continue
        try:
            if "-" in part:
                a, b = (float(x) % 360 for x in part.split("-", 1))
            else:
                x = float(part)
                a, b = (x - 20) % 360, (x + 20) % 360
        except ValueError:
            raise SystemExit(f"error: --hue '{part}': use a family ({', '.join(bc.FAMILIES)}), "
                             f"a range like 200-260, or an angle like 25")

        def in_range(c, a=a, b=b):
            L, C, h = colorlib.hex_to_oklch(c)
            if C < bc.NEUTRAL_C:
                return False
            return a <= h <= b if a <= b else (h >= a or h <= b)
        tests.append(in_range)
    return lambda c: any(t(c) for t in tests)


def matches(r, a, hue_ok):
    if a.industry and r["industry"] not in a.industry:
        return False
    if a.country and r["country"] not in a.country:
        return False
    if a.price and r["positioning"]["price"] not in a.price:
        return False
    if a.exemplary and not r.get("exemplary", False):
        return False
    if a.strategy and r.get("category_fit") != a.strategy:
        return False
    if a.dark_ground and not r.get("dark_ground"):
        return False
    if hue_ok:
        cols = r["brand_colors"] if a.any_colour else [r["roles"].get("primary")]
        if not any(c and hue_ok(c) for c in cols):
            return False
    if a.query:
        hay = " ".join([r["brand"], r["url"], r["industry"], r["strategy_note"], r.get("role_note") or "",
                        " ".join(r["positioning"]["tone"]), r["primary_family"], " ".join(r.get("families", []))]
                       ).lower()
        for word in a.query.lower().split():
            if word not in hay:
                return False
    return True


def bar(share, width=20):
    n = int(round(share * width))
    return "#" * n + "." * (width - n)


def print_summary(rows, total, out=None):
    s = bc.summarize(rows)
    w = (out or sys.stdout).write
    w(f"{s['n']} brands match (of {total}).\n")
    if not s["n"]:
        return s
    n = s["n"]
    small = " (small sample: read as anecdotes, not a norm)" if n < 6 else ""
    w(f"\nPrimary colour family (what the main action/brand colour is){small}\n")
    for f, share in s["primary_family"].items():
        names = [r["brand"] for r in rows if r["primary_family"] == f][:6]
        w(f"  {f:8s} {bar(share)} {share * 100:5.1f}%  {', '.join(names)}\n")
    w("\nFamily presence (share of brands using the family anywhere in their brand colours)\n")
    for f, share in s["family_presence"].items():
        w(f"  {f:8s} {bar(share)} {share * 100:5.1f}%\n")
    pl = s["primary_lightness"]
    w("\nLightness structure\n")
    for k, c in s["lightness_structures"].items():
        w(f"  {k:32s} {c:3d}  {c / n * 100:5.1f}%\n")
    pc = s["primary_contrast"]
    w(f"  dark ground: {s['dark_ground_share'] * 100:.0f}% · primary OKLCH L median {pl['median']} "
      f"(p25 {pl['p25']}, p75 {pl['p75']})\n")
    if pc["median"] is not None:
        w(f"  primary vs ground contrast: median {pc['median']}:1 (p25 {pc['p25']}, p75 {pc['p75']}); "
          f"{pc['below_3'] * 100:.0f}% below 3:1 (primary used as fills with text on it, not as text)\n")
    w(f"  hues per site: mean {s['mean_n_hues']} (chromatic brand colours merged within {bc.HUE_MERGE} deg)\n")
    w("\nOpen space (in this sample)\n")
    w(f"  unused families: {', '.join(s['unused_families']) or 'none'}\n")
    w(f"  rare families (in < {bc.RARE_SHARE * 100:.0f}% of brands): {', '.join(s['rare_families']) or 'none'}\n")
    for g in s["hue_gaps"]:
        w(f"  empty hue arc {g['from']}-{g['to']} deg ({g['width']} deg wide: {', '.join(g['families'])})\n")
    w(f"\nCountries: {', '.join(f'{k} {v}' for k, v in s['countries'].items())}\n")
    ex = s["exemplary"][:10]
    if ex:
        w(f"Exemplary: {', '.join(ex)}\n")
    diff = [r["brand"] for r in rows if r.get("category_fit") == "differentiate"][:8]
    if diff:
        w(f"Differentiators (primary family used by < 20% of industry peers): {', '.join(diff)}\n")
    w("\nReading: the top primary families are the category's colour code; using them signals belonging, leaving\n"
      "them buys distinction at some risk (references/color-strategy.md). Open space is open only in this sample:\n"
      "confirm it against the user's live competitors (site_palette.py competitors) before recommending it.\n")
    return s


def main(argv=None):
    colorlib.setup_utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--industry", help="industry from the vocabulary (comma = OR); near-misses resolve")
    ap.add_argument("--hue", help="family, degree range 'a-b' or angle (comma = OR); matches the primary colour")
    ap.add_argument("--any-colour", "--any-color", action="store_true", help="--hue matches any brand colour")
    ap.add_argument("--country", help="ISO 3166-1 alpha-2, e.g. TR (comma = OR)")
    ap.add_argument("--price", help="budget|mid|premium|luxury (comma = OR)")
    ap.add_argument("--strategy", choices=("conform", "differentiate"),
                    help="primary family shared by >= 20%% of industry peers (conform) or not (differentiate)")
    ap.add_argument("--dark-ground", action="store_true", help="only sites on a dark page background")
    ap.add_argument("--exemplary", action="store_true", help="only teaching examples")
    ap.add_argument("--query", help="words matched against brand, url, notes, tone, families")
    ap.add_argument("--summary", action="store_true", help="aggregate view of the matching set (category codes)")
    ap.add_argument("--format", choices=("table", "json", "paths"), default="table",
                    help="table (default), json records, or paths = gallery.html anchors")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--library", help=argparse.SUPPRESS)
    ap.add_argument("--list-values", action="store_true", help="print allowed filter values with counts")
    a = ap.parse_args(argv)

    cat = load_catalog(a.library)
    if a.list_values:
        for key, vals in (("industry", Counter(r["industry"] for r in cat)),
                          ("country", Counter(r["country"] for r in cat)),
                          ("price", Counter(r["positioning"]["price"] for r in cat)),
                          ("primary family (--hue)", Counter(r["primary_family"] for r in cat))):
            print(f"{key}: " + ", ".join(f"{k}({v})" for k, v in vals.most_common()))
        return 0
    known_ind = sorted({r["industry"] for r in cat} | set(bc.INDUSTRIES))
    a.industry = [resolve(x.strip(), known_ind, "industry") for x in a.industry.split(",")] if a.industry else None
    a.country = [x.strip().upper() for x in a.country.split(",")] if a.country else None
    if a.price:
        a.price = [resolve(x.strip(), list(bc.PRICES), "price") for x in a.price.split(",")]
    hue_ok = hue_matcher(a.hue) if a.hue else None

    rows = [r for r in cat if matches(r, a, hue_ok)]
    rows.sort(key=lambda r: (not r.get("exemplary"), r["industry"], r["brand"].lower()))
    if a.summary:
        if a.format == "json":
            print(json.dumps(bc.summarize(rows), ensure_ascii=False, indent=1))
        else:
            print_summary(rows, len(cat))
        return 0
    shown = rows[: a.limit]
    if a.format == "json":
        drop = ("measured_roles", "measured_confidence", "top_colors")
        print(json.dumps([{k: v for k, v in r.items() if k not in drop} for r in shown], ensure_ascii=False, indent=1))
    elif a.format == "paths":
        gallery = os.path.join(a.library or bc.LIBRARY_DIR, "gallery.html")
        for r in shown:
            print(f"{gallery}#{r['slug']}")
    else:
        print(f"{len(rows)} match, showing {len(shown)}  (* = exemplary; ? = low-confidence primary; "
              f"hex = measured website colours, approximate)")
        for r in shown:
            star = "*" if r.get("exemplary") else " "
            p = r["roles"].get("primary", "-")
            q = "?" if r["role_confidence"].get("primary", 1) < 0.5 and "primary" not in r["roles_overridden"] else " "
            acc = r["roles"].get("accent", "-")
            print(f"{star} {r['brand'][:24]:24s} {r['country']} {r['industry'][:20]:20s} primary {p}{q} "
                  f"({r['primary_family']:7s}) accent {acc:7s} bg {r['roles'].get('background', '-')} "
                  f"{r['positioning']['price']:7s} {r['category_fit']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
