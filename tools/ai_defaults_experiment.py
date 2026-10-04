#!/usr/bin/env python3
"""AI default-choice experiment for brand identity (logo, fonts, palette).

Asks plain, isolated `claude -p` sessions to design a brand identity for 30
fictional briefs, then measures how often the same fonts, hues and logo forms
come back. Re-run when models change; results accumulate in ai-defaults-results.json.

Usage
  python3 ai_defaults_experiment.py run --models sonnet opus haiku [--reps 1] [--parallel 3]
  python3 ai_defaults_experiment.py analyze            # prints markdown tables, writes "analysis"
  python3 ai_defaults_experiment.py run --condition cc-default --models sonnet
  python3 ai_defaults_experiment.py --data ai-defaults-results-2027-01.json run --models sonnet opus
      (new round after a model update: a new file, so nothing is skipped)
      (condition "bare" = minimal system prompt; "cc-default" = Claude Code's own
       default agent system prompt, still with no user config / CLAUDE.md / tools)

Isolation: every call runs from an empty scratch directory with --safe-mode
(no CLAUDE.md, skills, plugins, hooks, MCP), --setting-sources "" (no
user/project/local settings), --strict-mcp-config, --tools "" (no tools),
--no-session-persistence and stdin from /dev/null. Verified with a canary
prompt: the model reports no instructions and ~480 input tokens in total.
"""
import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path.cwd() / "ai-defaults-results.json"
REPO = HERE.parent
sys.path.insert(0, str(REPO / "skills" / "brand-identity" / "scripts"))
import colorlib  # noqa: E402

# The Claude Code CLI: found on PATH, or given with --claude.
CLAUDE = shutil.which("claude")
# Empty working directory for the calls (no CLAUDE.md can be discovered there).
# First round (2026-10-02) used a session scratchpad; default is a fresh temp dir.
SCRATCH = os.environ.get("SLOP_SCRATCH")
BARE_SYSTEM = "You are Claude, a helpful AI assistant."
PROMPT = (
    "Design a brand identity for {brief}: propose a logo concept (describe it), primary and "
    "secondary typefaces, and a colour palette with hex codes. Reply as JSON "
    "{{logo:{{type,description}}, fonts:{{heading,body}}, palette:[{{role,hex}}]}}"
)

# 30 fictional briefs: sector, country and tone deliberately varied. No real people.
BRIEFS = [
    ("fintech", "BR", "Kestrel Pay, a mobile-first payments app for freelancers in Brazil"),
    ("coffee", "PT", "Molino Coffee, a neighbourhood specialty coffee shop in Lisbon"),
    ("law", "UK", "Hartwell & Oduya LLP, a mid-sized commercial law firm in London"),
    ("toys", "DE", "Bobbin & Bloom, a maker of wooden educational toys for ages 2-6, sold online in Germany"),
    ("saas", "US", "Ledgerline, a B2B SaaS that automates accounts payable for mid-market companies"),
    ("vet", "AU", "Okapi Veterinary, a small-animal vet clinic in suburban Melbourne"),
    ("hotel", "MX", "Casa Alba, a 12-room boutique hotel in Oaxaca, Mexico"),
    ("ai-startup", "US", "Parallax Labs, an early-stage AI startup building planning agents for supply chains, San Francisco"),
    ("municipality", "TR", "Karadere Municipality, a coastal town council in Turkey launching a citizen services portal"),
    ("fashion", "JP", "Selvedge Row, a sustainable Japanese-made denim label sold across Europe"),
    ("construction", "US", "Barlow Construct, a commercial construction contractor in Texas"),
    ("cosmetics", "KR", "Dewlab, a clean skincare brand for Gen Z in South Korea"),
    ("bakery", "DK", "Brod & Co, an artisan sourdough bakery chain in Copenhagen"),
    ("cybersecurity", "NL", "Bastion Grid, an enterprise cybersecurity provider for hospitals in the Netherlands"),
    ("dental", "PT", "Ferreira Dental, a family dental clinic in Porto"),
    ("brewery", "US", "Hollow Oak Brewing, a small craft brewery in Vermont"),
    ("ngo", "KE", "Clearwater Trust, a non-profit funding community clean-water projects in Kenya"),
    ("edtech", "IN", "Stepwise, an online maths tutoring platform for secondary-school students in India"),
    ("gym", "UK", "Ironclad, a no-frills strength-training gym in Manchester"),
    ("grocery", "CA", "Green Furrow, an organic vegetable-box delivery service in Ontario"),
    ("real-estate", "AE", "Meridian Estates, a luxury real-estate agency in Dubai"),
    ("pet-food", "UK", "Wildbowl, a direct-to-consumer premium raw pet food brand in the UK"),
    ("logistics", "PL", "Northbound Freight, a road freight and logistics company in Poland"),
    ("restaurant", "ZA", "Ember & Salt, a wood-fire grill restaurant in Cape Town"),
    ("telehealth", "CA", "Tendr Health, a telemedicine app for people managing chronic conditions in Canada"),
    ("solar", "ES", "Solstead, a residential solar panel installer in Spain"),
    ("wine", "FR", "Domaine Fauvel, a family-owned natural wine producer in Burgundy"),
    ("crypto", "SG", "Moonvault, a self-custody crypto wallet app based in Singapore"),
    ("insurance", "US", "Steadfast Mutual, a regional car and home insurer in Ohio"),
    ("breakfast", "TR", "Hasat, a traditional Turkish breakfast restaurant in Istanbul"),
]

_lock = threading.Lock()


# ---------------------------------------------------------------- storage
def load():
    if DATA.exists():
        return json.loads(DATA.read_text())
    return {"meta": {}, "briefs": [], "runs": []}


def save(db):
    tmp = DATA.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=1))
    tmp.replace(DATA)


# ---------------------------------------------------------------- calling
def cmd_for(model, condition, prompt):
    cmd = [CLAUDE, "-p", prompt, "--model", model, "--output-format", "json",
           "--safe-mode", "--setting-sources", "", "--strict-mcp-config",
           "--tools", "", "--no-session-persistence"]
    if condition == "bare":
        cmd += ["--system-prompt", BARE_SYSTEM]
    return cmd


def extract_json(text):
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    cand = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(cand)
    except Exception:
        pass
    try:  # trailing commas
        return json.loads(re.sub(r",\s*([}\]])", r"\1", cand))
    except Exception:
        pass
    # lenient fallback: pull the fields with regexes (model emitted broken JSON)
    def field(name):
        m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % name, text)
        return m.group(1) if m else ""
    roles = re.findall(r'"role"\s*:\s*"([^"]*)"[^}]*?(#[0-9A-Fa-f]{6})', text)
    if not roles:
        return None
    return {"logo": {"type": field("type"), "description": field("description")},
            "fonts": {"heading": field("heading"), "body": field("body")},
            "palette": [{"role": r, "hex": h} for r, h in dict.fromkeys(roles)], "_lenient_parse": True}


def call(model, condition, bid, rep, brief, timeout):
    prompt = PROMPT.format(brief=brief)
    t0 = time.time()
    rec = {"model_alias": model, "condition": condition, "brief_id": bid, "rep": rep,
           "ts": dt.datetime.now().isoformat(timespec="seconds")}
    try:
        p = subprocess.run(cmd_for(model, condition, prompt), cwd=SCRATCH, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
        out = json.loads(p.stdout)
        rec["raw"] = out.get("result", "")
        rec["model_id"] = next(iter(out.get("modelUsage", {}) or {}), None)
        rec["input_tokens"] = (out.get("usage") or {}).get("input_tokens")
        rec["cost_usd"] = out.get("total_cost_usd")
        rec["parsed"] = extract_json(rec["raw"])
        rec["ok"] = rec["parsed"] is not None and not out.get("is_error")
        if out.get("is_error"):
            rec["error"] = rec["raw"][:300]
    except subprocess.TimeoutExpired:
        rec.update(ok=False, error="timeout")
    except Exception as e:  # noqa: BLE001
        rec.update(ok=False, error=f"{type(e).__name__}: {e}"[:300])
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def reparse(db):
    """Re-run the parser on failed records that do have text (keeps the original sample)."""
    for r in db["runs"]:
        if not r.get("ok") and r.get("raw") and not r.get("error"):
            r["parsed"] = extract_json(r["raw"])
            r["ok"] = r["parsed"] is not None


def run(args):
    global SCRATCH
    if not CLAUDE:
        sys.exit("claude CLI not found on PATH; pass --claude /path/to/claude")
    if not SCRATCH:
        SCRATCH = tempfile.mkdtemp(prefix="ai-defaults-")
    Path(SCRATCH).mkdir(parents=True, exist_ok=True)
    if any(Path(SCRATCH).iterdir()):
        print(f"warning: scratch dir {SCRATCH} is not empty", file=sys.stderr)
    db = load()
    db["briefs"] = [{"id": i, "sector": s, "country": c, "brief": b} for i, (s, c, b) in enumerate(BRIEFS)]
    ver = subprocess.run([CLAUDE, "--version"], capture_output=True, text=True).stdout.strip()
    db["meta"].update(prompt_template=PROMPT, bare_system_prompt=BARE_SYSTEM,
                      flags="--safe-mode --setting-sources '' --strict-mcp-config --tools '' "
                            "--no-session-persistence --output-format json (+ --system-prompt for bare)",
                      cli_version=ver, last_run=dt.date.today().isoformat())
    reparse(db)
    done = {(r["model_alias"], r["condition"], r["brief_id"], r["rep"]) for r in db["runs"] if r.get("ok")}
    db["runs"] = [r for r in db["runs"] if r.get("ok")]  # retry failures
    ids = args.briefs if args.briefs else range(len(BRIEFS))
    jobs = [(m, args.condition, i, rep) for m in args.models for rep in range(args.reps) for i in ids
            if (m, args.condition, i, rep) not in done]
    print(f"{len(jobs)} calls to make", flush=True)
    with cf.ThreadPoolExecutor(max_workers=min(args.parallel, 3)) as ex:
        futs = {ex.submit(call, m, c, i, rep, BRIEFS[i][2], args.timeout): (m, i) for m, c, i, rep in jobs}
        for f in cf.as_completed(futs):
            rec = f.result()
            with _lock:
                db["runs"].append(rec)
                save(db)
            print(f"{rec['model_alias']:7} #{rec['brief_id']:02d} ok={rec.get('ok')} {rec['seconds']}s "
                  f"{rec.get('error', '')}", flush=True)


# ---------------------------------------------------------------- normalisation
FONT_GENRE = {}  # filled below: lower name -> genre


def _g(genre, *names):
    for n in names:
        FONT_GENRE[n.lower()] = genre


_g("sans", "Poppins", "Montserrat", "Futura", "Futura PT", "Gilroy", "Outfit", "Sora", "Urbanist",
   "Lexend", "Nunito", "Nunito Sans", "Quicksand", "Comfortaa", "Raleway", "Josefin Sans", "Jost",
   "Space Grotesk", "Manrope", "Plus Jakarta Sans", "Red Hat Display", "Red Hat Text", "Questrial",
   "Century Gothic", "Avenir", "Avenir Next", "Circular", "Circular Std", "Gotham", "Proxima Nova",
   "Brandon Grotesque", "Fredoka", "Fredoka One", "Baloo 2", "Varela Round", "Mulish", "Lato", "Kanit",
   "Sofia Pro", "Cabinet Grotesk", "Satoshi", "General Sans", "Clash Display", "Syne", "Unbounded",
   "Visby CF", "Museo Sans", "Figtree", "DM Sans", "Albert Sans", "Be Vietnam Pro", "Rubik", "Archivo",
   "Archivo Black", "Archivo Narrow", "Barlow", "Barlow Condensed", "Barlow Semi Condensed", "Oswald",
   "Bebas Neue", "Anton", "League Spartan", "Spartan", "Work Sans", "Inter", "Inter Tight", "Inter Display",
   "Roboto", "Open Sans", "Source Sans Pro", "Source Sans 3", "IBM Plex Sans", "Helvetica", "Helvetica Neue",
   "Neue Haas Grotesk", "Neue Haas Grotesk Display", "Aktiv Grotesk", "Akzidenz-Grotesk", "Graphik",
   "Söhne", "Suisse Int'l", "Geist", "Neue Montreal", "PP Neue Montreal", "Hanken Grotesk", "Public Sans",
   "Noto Sans", "Pretendard", "Noto Sans KR", "SUIT", "Spoqa Han Sans", "Roboto Condensed", "Titillium Web",
   "Exo 2", "Chakra Petch", "Rajdhani", "Saira", "Eurostile", "Din", "DIN", "DIN Next", "DIN 2014",
   "Barlow Semi Condensed", "Overpass", "Karla", "Libre Franklin", "Franklin Gothic", "Gill Sans", "Frutiger",
   "Myriad Pro", "Source Sans", "Atkinson Hyperlegible", "Assistant", "Heebo", "Cabin", "Catamaran",
   "Hind", "Mukta", "Aeonik", "Neue Machina", "Monument Extended",
   "Druk", "Druk Wide", "Industry", "Big Shoulders Display", "Teko", "Alumni Sans", "Fira Sans", "Ubuntu",
   "Encode Sans", "Encode Sans Expanded", "Encode Sans Condensed", "Space Grotesk", "Instrument Sans",
   "Onest", "Golos", "Golos Text", "Wix Madefor Display", "Wix Madefor Text", "Tenor Sans", "Didact Gothic",
   "Montserrat Alternates", "Gothic A1", "Josefin Sans", "Lexend Deca", "Epilogue", "Bricolage Grotesque",
   "Schibsted Grotesk", "Anek Latin", "Mona Sans", "Hubot Sans", "Nunito Sans", "Raleway", "Antonio",
   "Saira Condensed", "Archivo Expanded", "Zen Kaku Gothic New", "Noto Sans JP", "M PLUS 1p", "Inter Variable",
   "Sarabun", "Prompt", "Sen", "Commissioner", "Chivo", "Familjen Grotesk", "Darker Grotesque", "Space Mono")
_g("serif", "Playfair Display", "Cormorant Garamond", "Cormorant", "EB Garamond", "Garamond", "Libre Baskerville",
   "Baskerville", "Lora", "Merriweather", "Crimson Pro", "Crimson Text", "DM Serif Display", "DM Serif Text",
   "Fraunces", "Instrument Serif", "Source Serif Pro", "Source Serif 4", "Spectral", "Cardo", "Bodoni Moda",
   "Bodoni", "Didot", "Canela", "Tiempos Headline", "Tiempos Text", "GT Sectra", "Freight Display",
   "Freight Text", "Recoleta", "Cooper", "Cooper Black", "Libre Caslon Text", "Libre Caslon Display",
   "Caslon", "Adobe Caslon Pro", "Big Caslon", "Noto Serif", "PT Serif", "Gelasio", "Newsreader",
   "Literata", "Young Serif", "Abril Fatface", "Prata", "Marcellus", "Cinzel", "Trajan", "Trajan Pro",
   "Ivar", "Domaine Display", "Editorial New", "PP Editorial New", "Gloock", "Bitter", "Zilla Slab",
   "Roboto Slab", "Arvo", "Rockwell", "Josefin Slab", "Alegreya", "Vollkorn", "Sentinel", "Chronicle",
   "Mrs Eaves", "Minion Pro", "Georgia", "Times New Roman", "Halant", "Martel", "Noto Serif KR",
   "Noto Serif JP", "Shippori Mincho", "Zen Old Mincho", "Fanwood Text", "Gilda Display", "Italiana",
   "Bellefair", "Petrona", "Brygada 1918", "Castoro", "Lustria", "Rufina", "Rozha One", "Yeseva One",
   "Ogg", "Sangbleu", "Saol Display", "Le Jour Serif", "Clearface", "Recoleta Alt", "Caudex",
   "Cormorant Infant", "Bree Serif", "Crete Round", "Domine", "Lusitana", "Libre Bodoni", "Ibarra Real Nova",
   "Aleo", "Merriweather Sans", "Hepta Slab", "Rokkitt", "Slabo 27px", "Alike", "Amiri", "Noto Naskh Arabic",
   "Zen Antique", "Gambetta", "Erode", "Sentient", "Boska", "Zodiak", "Tanker", "Chillax", "Sporting Grotesque")
_g("serif", "GT Alpina", "GT Super", "Tiempos", "Reckless", "Reckless Neue", "Lyon Display", "Lyon Text", "Merriweather")
_g("sans", "Atkinson Hyperlegible Next", "Maison Neue", "Druk Wide", "Söhne Breit", "ABC Diatype", "GT Walsheim", "GT America", "Gmarket Sans", "Neue Haas Unica", "Akkurat", "Apercu", "Founders Grotesk", "Nunito Sans", "Plus Jakarta Sans")
_g("mono", "JetBrains Mono", "IBM Plex Mono", "Space Mono", "Roboto Mono", "Fira Code", "Fira Mono",
   "Source Code Pro", "Geist Mono", "DM Mono", "Courier Prime", "Inconsolata", "Ubuntu Mono", "Martian Mono")
_g("script-hand", "Pacifico", "Dancing Script", "Caveat", "Lobster", "Great Vibes", "Sacramento",
   "Amatic SC", "Kalam", "Patrick Hand", "Shadows Into Light", "Satisfy", "Allura", "Parisienne",
   "Permanent Marker", "Gochi Hand", "Indie Flower", "Yellowtail", "Kaushan Script", "Homemade Apple")
# Families wrongly classed above as sans when they are serif / slab
for n in ("Sporting Grotesque", "Tanker", "Chillax"):
    FONT_GENRE[n.lower()] = "sans"
for n in ("Space Mono",):
    FONT_GENRE[n.lower()] = "mono"


def norm_font(s):
    """'Inter (SemiBold 600) / Google Fonts' -> 'Inter'. Returns (name, genre)."""
    if not s:
        return None, None
    if isinstance(s, dict):
        s = s.get("name") or s.get("family") or s.get("font") or json.dumps(s)
    s = str(s)
    s = re.split(r"\s+[–—-]\s+|[(,;/:]|\.\s|\bwith\b|\bor\b|\bfor\b", s)[0]
    s = re.sub(r"\b(Variable|Bold|SemiBold|Semi Bold|Medium|Regular|Light|ExtraBold|Black|Book|Italic|"
               r"Display Bold|\d{3})\b", "", s, flags=re.I).strip(" '\"–—-")
    s = re.sub(r"\s+", " ", s)
    key = s.lower()
    genre = FONT_GENRE.get(key)
    if genre is None:
        # strip trailing width/optical words and retry
        base = re.sub(r"\s+(display|text|condensed|extended|expanded|wide|pro|std|sc)$", "", key)
        genre = FONT_GENRE.get(base, "unknown")

    return s, genre




def hue_band(hexv):
    L, C, h = colorlib.hex_to_oklch(hexv)
    if C < 0.03:
        if L > 0.93:
            band = "neutral-light"
        elif L < 0.25:
            band = "neutral-dark"
        else:
            band = "neutral-mid"
        warm = C >= 0.008 and 40 <= h <= 110
        return band + ("-warm" if warm else ""), L, C, h
    bands = [(5, 40, "red"), (40, 65, "orange"), (65, 110, "yellow-gold"), (110, 170, "green"),
             (170, 225, "teal-cyan"), (225, 268, "blue"), (268, 285, "indigo"), (285, 330, "violet-purple")]
    band = "pink-magenta"
    for lo, hi, name in bands:
        if lo <= h < hi:
            band = name
    return band, L, C, h


TAILWIND = {  # selected Tailwind v3 default hexes
    "#6366f1": "indigo-500", "#4f46e5": "indigo-600", "#4338ca": "indigo-700", "#818cf8": "indigo-400",
    "#312e81": "indigo-900", "#eef2ff": "indigo-50", "#e0e7ff": "indigo-100",
    "#8b5cf6": "violet-500", "#7c3aed": "violet-600", "#a855f7": "purple-500", "#9333ea": "purple-600",
    "#3b82f6": "blue-500", "#2563eb": "blue-600", "#1d4ed8": "blue-700",
    "#10b981": "emerald-500", "#059669": "emerald-600", "#14b8a6": "teal-500", "#0d9488": "teal-600",
    "#06b6d4": "cyan-500", "#0ea5e9": "sky-500", "#22c55e": "green-500", "#84cc16": "lime-500",
    "#f59e0b": "amber-500", "#fbbf24": "amber-400", "#f97316": "orange-500", "#ef4444": "red-500",
    "#f43f5e": "rose-500", "#ec4899": "pink-500", "#d946ef": "fuchsia-500", "#facc15": "yellow-400",
    "#0f172a": "slate-900", "#1e293b": "slate-800", "#334155": "slate-700", "#475569": "slate-600",
    "#64748b": "slate-500", "#94a3b8": "slate-400", "#cbd5e1": "slate-300", "#e2e8f0": "slate-200",
    "#f1f5f9": "slate-100", "#f8fafc": "slate-50", "#020617": "slate-950",
    "#111827": "gray-900", "#1f2937": "gray-800", "#374151": "gray-700", "#4b5563": "gray-600",
    "#6b7280": "gray-500", "#9ca3af": "gray-400", "#d1d5db": "gray-300", "#e5e7eb": "gray-200",
    "#f3f4f6": "gray-100", "#f9fafb": "gray-50", "#18181b": "zinc-900", "#09090b": "zinc-950",
    "#fafafa": "zinc-50", "#f4f4f5": "zinc-100", "#e4e4e7": "zinc-200",
}

LOGO_TYPES = [  # order matters: first hit wins on the model's own "type" field
    ("combination", r"combination|combo|lockup|symbol \+|icon \+|mark \+|\+ ?word"),
    ("emblem-badge", r"emblem|badge|seal|crest|stamp"),
    ("mascot", r"mascot|character"),
    ("monogram-lettermark", r"monogram|lettermark|letter ?mark|initial|letterform|letter-based|logotype mark"),
    ("wordmark", r"wordmark|word mark|logotype|typographic|text-based"),
    ("abstract-mark", r"abstract"),
    ("pictorial-mark", r"pictorial|symbol|icon|brand ?mark|pictogram|illustrat"),
    ("abstract-mark", r"geometric|mark"),
]
MOTIFS = {
    "leaf-sprout-plant": r"\bleaf|leaves|sprout|seedling|botanical|plant|wheat|grain|stalk|branch|vine|olive|grape",
    "spark-star-burst": r"spark|star\b|stars|starburst|burst|twinkle|glint|asterisk",
    "sun-sunrise": r"\bsun\b|sunrise|sunset|sunburst|solar disc|\brays\b",
    "wave-water-drop": r"\bwave|water|droplet|\bdrop\b|ripple|river|ocean|sea\b",
    "circle-ring-orbit": r"\bcircle|\bcircular|\bring\b|\brings\b|\borbit|\bdisc\b|\bdisk\b|\bsemicircle|\bhalf-circle",
    "hexagon-polygon": r"hexagon|hex\b|octagon|polygon|pentagon",
    "shield": r"shield",
    "arrow-chevron": r"arrow|chevron|forward-pointing|upward",
    "house-roof-building": r"\bhouse|\broof|\bbuilding\b|skyline|\btower|\barch\b|\barches\b|archway|doorway|\bwindow",
    "mountain-peak": r"mountain|peak|summit|hill",
    "flame-fire": r"flame|fire\b|ember",
    "heart": r"\bheart",
    "animal": r"\bpaw|\bbird|kestrel|okapi|\banimal|\bdog\b|\bcat\b|\bwolf|\bbear\b|feather|\bwing",
    "node-network-neural": r"\bnode|\bnetwork|neural|connected dots|circuit|lattice",
    "crown-key-lock": r"crown|\bkey\b|keyhole|padlock|\block\b|vault",
    "moon-crescent": r"moon|crescent",
    "interlock-overlap": r"interlock|overlapping|intertwin|interwoven|woven|braid",
}
STYLE_TAGS = {
    "negative-space": r"negative space|negative-space|cut-?out|counter space",
    "minimal": r"minimal|clean|simple|simplif",
    "geometric": r"geometric",
    "stylized-letter": r"stylized ['\"]?[a-z]['\"]?\b|stylised ['\"]?[a-z]['\"]?\b|letter ['\"]?[a-z]['\"]?\b|"
                       r"initials?\b|monogram",
    "hand-drawn": r"hand-?drawn|hand-?lettered|brush|illustrat|sketch",
}


_NAMES = sorted(FONT_GENRE, key=len, reverse=True)


def font_mentions(text):
    """All known families named anywhere in the fonts field (primary + alternatives)."""
    t = text.lower()
    found = []
    for n in _NAMES:
        rx = r"(?<![a-z])" + re.escape(n) + r"(?![a-z])"
        if re.search(rx, t):
            found.append(n)
            t = re.sub(rx, " ", t)
    return sorted(found)


def families(hexv):
    """Named colour families the anti-slop literature talks about (OKLCH boxes)."""
    L, C, h = colorlib.hex_to_oklch(hexv)
    f = []
    if 225 <= h < 285 and C >= 0.03 and L < 0.40:
        f.append("navy")
    if L > 0.88 and 0.008 <= C < 0.05 and 40 <= h <= 110:
        f.append("cream")
    if 25 <= h < 60 and 0.45 <= L <= 0.72 and 0.08 <= C <= 0.18:
        f.append("terracotta-clay")
    if 60 <= h < 100 and 0.55 <= L <= 0.85 and 0.07 <= C <= 0.17:
        f.append("gold-brass-ochre")
    if 130 <= h < 175 and L < 0.50 and C >= 0.03:
        f.append("forest-green")
    if 268 <= h < 330 and C >= 0.10 and L >= 0.40:
        f.append("vivid-indigo-violet")
    if 170 <= h < 225 and C >= 0.08 and L >= 0.50:
        f.append("bright-teal")
    if L < 0.25 and 30 <= h < 110:
        f.append("warm-near-black")
    return f


def gradient_suggested(raw):
    """True when a gradient is proposed; 'no gradients' style negations do not count."""
    for m in re.finditer(r"gradient", raw, re.I):
        before = raw[max(0, m.start() - 30):m.start()]
        if not re.search(r"\b(no|without|not|never|avoid\w*|free of)\b[^.]*$", before, re.I):
            return True
    return False


def logo_type(t, desc):
    t = (t or "").lower()
    for name, rx in LOGO_TYPES:
        if re.search(rx, t):
            return name
    d = (desc or "").lower()
    for name, rx in LOGO_TYPES:
        if re.search(rx, d):
            return name
    return "other"


def entropy(counter):
    n = sum(counter.values())
    return -sum(c / n * math.log2(c / n) for c in counter.values()) if n else 0.0


def get_palette(p):
    pal = p.get("palette") or []
    out = []
    if isinstance(pal, dict):
        pal = [{"role": k, "hex": v} for k, v in pal.items()]
    for i, e in enumerate(pal):
        if isinstance(e, str):
            e = {"role": f"#{i}", "hex": e}
        hx = e.get("hex") or e.get("color") or ""
        m = re.search(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", str(hx))
        if not m:
            continue
        out.append((str(e.get("role", "")).lower(), colorlib.normalize_hex(m.group(0)).lower()))
    return out


def primary_of(pal):
    for role, hx in pal:
        if "primary" in role:
            return hx
    return pal[0][1] if pal else None


def normalize(rec):
    p = rec["parsed"]
    logo = p.get("logo") or {}
    if isinstance(logo, str):
        logo = {"type": "", "description": logo}
    fonts = p.get("fonts") or {}
    head, head_g = norm_font(fonts.get("heading") or fonts.get("primary"))
    body, body_g = norm_font(fonts.get("body") or fonts.get("secondary"))
    pal = get_palette(p)
    desc = str(logo.get("description", ""))
    prim = primary_of(pal)
    return {
        "logo_type": logo_type(str(logo.get("type", "")), desc),
        "logo_type_raw": str(logo.get("type", "")),
        "motifs": [k for k, rx in MOTIFS.items() if re.search(rx, desc, re.I)],
        "styles": [k for k, rx in STYLE_TAGS.items() if re.search(rx, desc, re.I)],
        "heading": head, "heading_genre": head_g, "body": body, "body_genre": body_g,
        "palette": [{"role": r, "hex": h, "band": hue_band(h)[0],
                     "oklch": [round(x, 3) for x in hue_band(h)[1:]]} for r, h in pal],
        "primary": prim, "primary_band": hue_band(prim)[0] if prim else None,
        "font_mentions": font_mentions(json.dumps(fonts, ensure_ascii=False)),
        "has_wordmark": bool(re.search(r"wordmark|word mark|logotype|combination", json.dumps(logo).lower())),
        "gradient": gradient_suggested(rec["raw"]),
        "gradient_negated": bool(re.search(r"\b(no|without|not|never|avoid\w*)\b[^.]{0,25}gradient", rec["raw"], re.I)),
        "n_colors": len(pal),
        "families": sorted({f for _, h in pal for f in families(h)}),
        "primary_families": families(prim) if prim else [],
        "heading_serif": head_g == "serif",
    }


# ---------------------------------------------------------------- analysis
def pct(n, d):
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "0"


def table(title, rows, cols):
    lines = [f"\n**{title}**\n" if title else "\n", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(lines)


def analyze(args):
    db = load()
    reparse(db)
    every = [r for r in db["runs"] if r.get("ok")]
    for r in every:
        r["norm"] = normalize(r)
    runs = [r for r in every if (args.condition is None or r["condition"] == args.condition)
            and (args.all_reps or r["rep"] == 0)]
    groups = collections.OrderedDict()
    for r in sorted(runs, key=lambda r: (r["condition"], r["model_alias"])):
        groups.setdefault(f"{r['model_alias']}/{r['condition']}", []).append(r)
    groups["ALL"] = runs
    out = []
    summary = {}
    for g, rs in groups.items():
        n = len(rs)
        nm = [r["norm"] for r in rs]
        heads = collections.Counter(x["heading"] for x in nm)
        bodies = collections.Counter(x["body"] for x in nm)
        anyfont = collections.Counter()
        for x in nm:
            anyfont.update(set(x["font_mentions"]))
        hg = collections.Counter(x["heading_genre"] for x in nm)
        bg = collections.Counter(x["body_genre"] for x in nm)
        pb = collections.Counter(x["primary_band"] for x in nm)
        allc = [c for x in nm for c in x["palette"]]
        cb = collections.Counter(c["band"] for c in allc)
        lt = collections.Counter(x["logo_type"] for x in nm)
        mo = collections.Counter(m for x in nm for m in x["motifs"])
        st = collections.Counter(s for x in nm for s in x["styles"])
        hexes = collections.Counter(c["hex"] for c in allc)
        tw = collections.Counter(TAILWIND[c["hex"]] for c in allc if c["hex"] in TAILWIND)
        grad = sum(x["gradient"] for x in nm)
        runs_with_band = lambda b: sum(any(c["band"] == b for c in x["palette"]) for x in nm)  # noqa: E731
        s = {
            "n": n,
            "distinct_heading_fonts": len(heads), "distinct_body_fonts": len(bodies),
            "top_heading": heads.most_common(10), "top_body": bodies.most_common(10),
            "top_any_font": anyfont.most_common(12),
            "heading_genre": hg.most_common(), "body_genre": bg.most_common(),
            "primary_band": pb.most_common(), "primary_band_entropy_bits": round(entropy(pb), 2),
            "all_color_band": cb.most_common(),
            "runs_with_band": {b: runs_with_band(b) for b in
                               ("indigo", "violet-purple", "teal-cyan", "blue", "neutral-light-warm")},
            "logo_type": lt.most_common(), "motifs": mo.most_common(), "styles": st.most_common(),
            "top_hex": hexes.most_common(12), "tailwind_hits": sum(tw.values()), "tailwind": tw.most_common(10),
            "n_colors_total": len(allc), "gradient_runs": grad,
            "mean_colors": round(len(allc) / n, 1) if n else 0,
            "families": collections.Counter(f for x in nm for f in x["families"]).most_common(),
            "primary_families": collections.Counter(f for x in nm for f in x["primary_families"]).most_common(),
            "combo_cream_terracotta_serif": sum({"cream", "terracotta-clay"} <= set(x["families"]) and x["heading_serif"]
                                                for x in nm),
            "combo_navy_gold": sum({"navy", "gold-brass-ochre"} <= set(x["families"]) for x in nm),
            "heading_serif": sum(x["heading_serif"] for x in nm),
            "has_wordmark": sum(x["has_wordmark"] for x in nm),
        }
        summary[g] = s
        if g != "ALL" and not args.all_tables:
            continue
    # markdown output (ALL + per model side by side for key metrics)
    keys = [k for k in summary if k != "ALL"]
    out.append(table("Coverage", [[k, summary[k]["n"], summary[k]["distinct_heading_fonts"],
                                 summary[k]["distinct_body_fonts"], summary[k]["primary_band_entropy_bits"],
                                 pct(summary[k]["gradient_runs"], summary[k]["n"]),
                                 summary[k]["mean_colors"]] for k in summary],
                     ["group", "n", "distinct heading fonts", "distinct body fonts", "primary hue entropy (bits, max 3.6)",
                      "answers proposing a gradient", "mean colour count"]))
    for k in summary:
        s = summary[k]
        out.append(f"\n### {k}")
        out.append(table("Heading font (top 10)", [[f, pct(c, s['n'])] for f, c in s["top_heading"]], ["font", "n"]))
        out.append(table("Body font (top 10)", [[f, pct(c, s['n'])] for f, c in s["top_body"]], ["font", "n"]))
        out.append(table("Named in the fonts field (alternatives included, top 12)",
                         [[f, pct(c, s['n'])] for f, c in s["top_any_font"]], ["font", "n"]))
        out.append(table("Font genre (heading / body)", [[g_, c] for g_, c in s["heading_genre"]], ["heading genre", "n"])
                   + table("", [[g_, c] for g_, c in s["body_genre"]], ["body genre", "n"]))
        out.append(table("Primary colour hue band", [[b, pct(c, s['n'])] for b, c in s["primary_band"]],
                         ["band", "n"]))
        out.append(table("Answers with the band anywhere in the palette", [[b, pct(c, s['n'])] for b, c in s["runs_with_band"].items()],
                         ["band", "n"]))
        out.append(table("Colour family (anywhere in the palette / as primary)",
                         [[f, pct(c, s['n']), dict(s["primary_families"]).get(f, 0)] for f, c in s["families"]],
                         ["family", "palette", "primary"]))
        out.append(f"\nCombination: cream + terracotta + serif heading {pct(s['combo_cream_terracotta_serif'], s['n'])}; "
                   f"navy + gold/brass {pct(s['combo_navy_gold'], s['n'])}; serif heading {pct(s['heading_serif'], s['n'])}")
        out.append(table("Logo type", [[t, pct(c, s['n'])] for t, c in s["logo_type"]], ["type", "n"]))
        out.append(table("Logo motif (named in the description)", [[t, pct(c, s['n'])] for t, c in s["motifs"]], ["motif", "n"]))
        out.append(table("Logo style tag", [[t, pct(c, s['n'])] for t, c in s["styles"]], ["tag", "n"]))
        out.append(table("Most frequent hex", [[h, c, TAILWIND.get(h, "")] for h, c in s["top_hex"]], ["hex", "n", "Tailwind"]))
        out.append(f"\nTailwind default hex hits: {s['tailwind_hits']}/{s['n_colors_total']} colours; "
                   f"{s['tailwind']}")
    # agreement on the same brief for every pair of (model, condition, rep) cells
    cells = collections.defaultdict(dict)
    for r in every:
        cells[f"{r['model_alias']}/{r['condition']}/r{r['rep']}"][r["brief_id"]] = r["norm"]
    names = sorted(cells)
    rows = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = cells[names[i]], cells[names[j]]
            common = sorted(set(a) & set(b))
            if not common:
                continue
            hf = sum(a[k]["heading"] == b[k]["heading"] for k in common)
            bf = sum(a[k]["body"] == b[k]["body"] for k in common)
            pb_ = sum(a[k]["primary_band"] == b[k]["primary_band"] for k in common)
            hx = sum(bool({c["hex"] for c in a[k]["palette"]} & {c["hex"] for c in b[k]["palette"]}) for k in common)
            rows.append([names[i], names[j], len(common), pct(hf, len(common)), pct(bf, len(common)),
                         pct(pb_, len(common)), pct(hx, len(common))])
    rows_pairs = rows
    out.append(table("Same brief, two cells: agreement (r0/r1 = repeat)", rows,
                     ["A", "B", "briefs", "same heading font", "same body font", "same primary band",
                      "at least one identical hex"]))
    # cross-model agreement per brief (same heading font / same primary band)
    by_brief = collections.defaultdict(dict)
    for r in runs:
        if r["rep"] == 0:
            by_brief[r["brief_id"]][f"{r['model_alias']}/{r['condition']}"] = r["norm"]
    agree_font = agree_band = pairs = 0
    for b, d in by_brief.items():
        ks = sorted(d)
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                pairs += 1
                agree_font += d[ks[i]]["heading"] == d[ks[j]]["heading"]
                agree_band += d[ks[i]]["primary_band"] == d[ks[j]]["primary_band"]
    out.append(f"\nCross-model agreement on the same brief: heading font {pct(agree_font, pairs)}, "
               f"primary band {pct(agree_band, pairs)} ({pairs} pairs)")
    # per-brief listing
    rows = []
    for b in sorted(by_brief):
        d = by_brief[b]
        cells = []
        for k in keys:
            x = d.get(k)
            cells.append(f"{x['heading']} / {x['body']} · {x['primary_band']} {x['primary']} · {x['logo_type']}"
                         if x else "")
        rows.append([db["briefs"][b]["sector"]] + cells)
    out.append(table("Choices per brief (heading / body · primary · logo type)", rows, ["sector"] + keys))
    unknown = collections.Counter(x for r in runs for x in (
        [r["norm"]["heading"]] if r["norm"]["heading_genre"] == "unknown" else []) + (
        [r["norm"]["body"]] if r["norm"]["body_genre"] == "unknown" else []))
    out.append(f"\nUnclassified fonts: {dict(unknown)}")
    db["analysis"] = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "groups": summary,
                      "pairwise": rows_pairs,
                      "cross_model_pairs": pairs, "agree_heading": agree_font, "agree_primary_band": agree_band}
    for r in db["runs"]:
        if r.get("ok"):
            r["norm"] = normalize(r)
    save(db)
    print("\n".join(out))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="results JSON (default: ai-defaults-results.json in the current folder). Use a new "
                    "file for a fresh round when model versions change; finished cells are skipped per file.")
    ap.add_argument("--claude", help="path to the claude CLI (default: `claude` on PATH)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--models", nargs="+", default=["sonnet", "opus"])
    r.add_argument("--condition", default="bare", choices=["bare", "cc-default"])
    r.add_argument("--reps", type=int, default=1)
    r.add_argument("--parallel", type=int, default=3)
    r.add_argument("--timeout", type=int, default=300)
    r.add_argument("--briefs", type=int, nargs="*")
    a = sub.add_parser("analyze")
    a.add_argument("--condition", default=None)
    a.add_argument("--all-tables", action="store_true")
    a.add_argument("--all-reps", action="store_true", help="include repeat runs in the frequency tables")
    args = ap.parse_args()
    global CLAUDE, DATA
    if args.claude:
        CLAUDE = args.claude
    if args.data:
        DATA = Path(args.data).resolve()
    (run if args.cmd == "run" else analyze)(args)


if __name__ == "__main__":
    main()
