#!/usr/bin/env python3
"""site_palette.py - read a live site's colour roles and preview brand-identity palettes on it.

usage:
  python3 site_palette.py extract <url> --out DIR [--no-dark | --dark-only]
      (reads the site's own dark scheme too when it has one; corrected roles in an existing extract.json are kept)
  python3 site_palette.py apply <url> --palette a.json [--palette b.json ...] [--roles extract.json]
                                      --out DIR [--mode light|dark] [--recolor-logo]
  python3 site_palette.py apply <url> --identity sets/A/identity.json [--identity sets/B/identity.json ...]
                                      --out DIR [--roles extract.json] [--mode light|dark]
      identity sets: palette colours + the set's display/text faces (type/fonts.json, served from bytes,
      size-adjusted to the old x-height) + header logo (logo/build/primary.svg, fitted in the old box +10%);
      apply.json records layoutStress (elements that overflow or gain lines, after vs before)
  python3 site_palette.py competitors <url> [<url> ...] --out DIR [--max 8] [--concurrency 3]
      stdout is a compact table (one line per site + hue-family spread): read THAT, not competitors.json
  python3 site_palette.py check
  --json  print the full JSON summary on stdout instead of the compact text summary

Examples:
  python3 site_palette.py extract https://vitepress.dev --out preview/
  python3 site_palette.py apply https://vitepress.dev --palette harbor.json --palette ember.json --out preview/
  python3 site_palette.py apply https://vitepress.dev --palette harbor.json --mode dark --out preview/
  python3 site_palette.py competitors https://a.com https://b.com --out competitors/

Palettes are brand-identity/palette@1 files (role keys under modes.light / modes.dark).
stdout is a short summary meant to be read; the full data (all colours, tokens, alternatives) stays in the files.
extract.json roles are a PROPOSAL with per-role confidence: confirm them with the user, edit
extract.json if needed, then run apply (it reads the roles from --roles or DIR/extract.json).

Exit codes: 0 ok, 1 failure, 4 no browser, 5 site blocks automated browsers (DIR/blocked.json).

Output layout (DIR): extract.json, logo.png, current/{desktop,mobile,full}.png,
  <palette-slug>/{desktop,mobile,full}.png + apply.json; identity sets: <id-name>/{desktop,mobile,full,header}.png
  + apply.json; current/header.png is the site's header close-up (@2x); dark mode adds a "-dark" suffix.

Needs only Python (standard library) and a Chromium-based browser: BRAND_IDENTITY_BROWSER / CHROMIUM_PATH, the
Playwright cache, or an installed Chrome / Chromium / Edge / Brave. The browser is driven over the DevTools protocol
(cdplib.py); the measuring code runs as JavaScript in the page and in a blank engine page (site/site_engine.js).
Read-only: never clicks, types or submits anything.
"""
import base64
import contextlib
import gzip
import json
import math
import os
import re
import ssl
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cdplib  # noqa: E402
from cdplib import UNDEFINED, js_json, js_number  # noqa: E402

ENGINE_JS = os.path.join(HERE, "site", "site_engine.js")
SELFTEST = os.path.join(HERE, "site", "selftest.html")
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
             "Version/17.0 Mobile/15E148 Safari/604.1")
VIEWPORTS = {
    "desktop": {"viewport": (1440, 900), "device_scale_factor": 1},
    "mobile": {"viewport": (390, 844), "device_scale_factor": 2, "mobile": True, "has_touch": True,
               "user_agent": IPHONE_UA},
}
FULL_PAGE_MAX = 5000
NAV_TIMEOUT = 45
NO_SITE_FALLBACK = "Fallback without a live site: python3 scripts/site_preview.py --mock landing,app --palettes ..."
LOGO_MAX_BYTES = 2 * 1024 * 1024
ROLE_KEYS = ["background", "surface", "surfaceAlt", "border", "text", "textMuted", "primary", "onPrimary", "accent",
             "onAccent", "link", "focus", "success", "warning", "danger", "info"]


class Die(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


class Blocked(Exception):
    def __init__(self, url, reason):
        super().__init__(f"{url}: the site blocks automated browsers ({reason}). Ask the user for screenshots or "
                         f"their colours, or use mocks. {NO_SITE_FALLBACK}")
        self.blocked, self.reason, self.url = True, reason, url


def log(msg):
    sys.stderr.write(_surrogates(str(msg)) + "\n")
    sys.stderr.flush()


def _surrogates(s):
    """Lone surrogates (possible after a UTF-16 slice) print as U+FFFD, as Node's console does."""
    return re.sub("[\ud800-\udfff]", "�", s)


def now_iso():
    """new Date().toISOString()"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_json(path, obj):
    """fs.writeFileSync(path, JSON.stringify(obj, null, 2))"""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(js_json(obj))


# ----------------------------------------------------------------------------- JavaScript value semantics

def truthy(v):
    """JavaScript truthiness ({} and [] are true; 0, '', NaN, null, undefined false)."""
    if v is None or v is UNDEFINED or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v != 0 and v == v
    if isinstance(v, str):
        return v != ""
    return True


def jor(*vals):
    """a || b || c"""
    for v in vals[:-1]:
        if truthy(v):
            return v
    return vals[-1]


def js_str(v):
    """String(v) / template literal conversion."""
    if v is None:
        return "null"
    if v is UNDEFINED:
        return "undefined"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return js_number(v)
    if isinstance(v, list):
        return ",".join("" if x is None or x is UNDEFINED else js_str(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return str(v)


def js_tonum(v):
    """ToNumber"""
    if v is UNDEFINED:
        return float("nan")
    if v is None or v is False:
        return 0
    if v is True:
        return 1
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        t = js_trim(v)
        if t == "":
            return 0
        if re.fullmatch(r"[+-]?Infinity", t):
            return float("-inf") if t.startswith("-") else float("inf")
        if re.fullmatch(r"0[xX][0-9a-fA-F]+", t):
            return int(t, 16)
        if re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", t):
            return float(t)
        return float("nan")
    if isinstance(v, list):
        return 0 if not v else js_tonum(js_str(v)) if len(v) == 1 else float("nan")
    return float("nan")


def js_round(v):
    """Math.round"""
    x = js_tonum(v)
    if isinstance(x, int):
        return x
    if x != x or math.isinf(x):
        return x
    f = math.floor(x)
    return int(f + 1) if x - f >= 0.5 else int(f)


def to_fixed(x, digits):
    """Number.prototype.toFixed (round half up on the exact binary value)."""
    if x != x:
        return "NaN"
    q = Decimal(1).scaleb(-digits)
    return format(Decimal(x).quantize(q, rounding=ROUND_HALF_UP), "f")


def u16len(s):
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def u16slice(s, a, b):
    raw = s.encode("utf-16-le", "surrogatepass")
    return raw[2 * a:2 * b].decode("utf-16-le", "surrogatepass")


def clip(s, n):
    s = js_str(s)
    return u16slice(s, 0, n - 1) + "…" if u16len(s) > n else s


def pad_end(s, n):
    return s + " " * max(0, n - u16len(s))


def js_number_of(v):
    """+v for a CLI value (a string, or true for a bare flag)."""
    return js_tonum(v)


# ----------------------------------------------------------------------------- engine (pure functions in a blank page)

class Engine:
    """site_engine.js loaded in a blank page of the browser; call(name, *args) runs one of its pure functions,
    src[name] is a page function's source for Page.evaluate."""

    def __init__(self, client):
        self.page = client.new_page()
        with open(ENGINE_JS, encoding="utf-8") as fh:
            code = fh.read().replace("BRIDGE_SER", cdplib.BRIDGE_SER).replace("BRIDGE_REV", cdplib.BRIDGE_REV)
        r = self.page.conn.call("Runtime.evaluate", {"expression": code}, self.page.session, 60)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"site_engine.js failed to load: {r['exceptionDetails'].get('text')}")
        self.src = self.page.conn.call("Runtime.evaluate", {"expression": "SiteEngine.sources", "returnByValue": True},
                                       self.page.session, 60)["result"]["value"]

    def call(self, name, *args):
        expr = f"SiteEngine.call({json.dumps(name)}, {json.dumps(cdplib.to_tree(list(args)))})"
        r = self.page.conn.call("Runtime.evaluate", {"expression": expr, "returnByValue": True}, self.page.session, 300)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise RuntimeError(((d.get("exception") or {}).get("description") or d.get("text") or "error").splitlines()[0])
        return cdplib.from_tree((r.get("result") or {}).get("value"))

    def page_call(self, name, arg=None):
        """A page function run in the engine page itself (imagePalette's canvas work)."""
        return self.page.evaluate(self.src[name], arg)


class Session:
    """What one worker needs: a browser client and its engine."""

    def __init__(self, browser, client=None):
        self.browser = browser
        self.client = client or browser.main
        self.eng = Engine(self.client)
        self.src = self.eng.src

    def new_page(self, kind, scheme="light"):
        return self.client.new_page(**VIEWPORTS[kind], reduced_motion="reduce", color_scheme=scheme, bypass_csp=True,
                                    locale="en-US", ignore_https_errors=True)


# ----------------------------------------------------------------------------- capture hygiene

def hide_overlays(S, page):
    """Hide (never click) consent overlays: CSS for known CMPs + a text heuristic for fixed/sticky boxes."""
    try:
        page.add_style_tag(",".join(S.src["CONSENT_SEL"]) + "{display:none!important}")
    except Exception:  # noqa: BLE001
        pass
    try:
        page.evaluate(S.src["pageHideOverlays"])
    except Exception:  # noqa: BLE001
        pass


def settle(S, page):
    """Bounded network idle, frozen motion, lazy content via stepwise scroll, fonts, images, overlays."""
    page.wait_for_load_state("networkidle", 10)
    try:
        page.add_style_tag(S.src["FREEZE_CSS"])
    except Exception:  # noqa: BLE001
        pass
    try:
        page.evaluate(S.src["pageSettleScroll"])
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_load_state("networkidle", 5)
    hide_overlays(S, page)
    page.wait_for_timeout(300)


def open_page(S, page, url, check_block=True):
    try:
        resp = page.goto(url, NAV_TIMEOUT)
    except cdplib.NavigationError as e:
        if e.detail:  # where a timed-out load stopped (stderr only; the error line stays as it was)
            log(f"note: {url}: {e.detail}")
        first = str(e).splitlines()[0].rstrip(".")
        raise RuntimeError(f"could not load {url}: {first}. If the site blocks headless browsers, "
                           f"ask the user for screenshots. {NO_SITE_FALLBACK}") from None
    settle(S, page)
    if check_block:
        try:
            reason = page.evaluate(S.src["pageBlockCheck"], (resp or {}).get("status") or 0 if resp else 0)
        except Exception:  # noqa: BLE001
            reason = None
        if reason:
            raise Blocked(url, reason)
    return resp


def shoot(S, page, file, full=False):
    if not full:
        return page.screenshot(file, animations="disabled")
    h = page.evaluate(S.src["pageScrollHeight"])
    w = page.opts["viewport"][0]
    return page.screenshot(file, full_page=True, clip={"x": 0, "y": 0, "width": w, "height": min(h, FULL_PAGE_MAX)},
                           animations="disabled")


def shoot_header(S, page, file):
    box = page.evaluate(S.src["pageHeaderBox"])
    page.capture_clip(file, {"x": box["x"], "y": box["y"], "width": box["width"], "height": box["height"], "scale": 2})
    return box


def suffix(mode):
    return "-dark" if mode == "dark" else ""


# ----------------------------------------------------------------------------- logo source

def _file_url_path(u):
    """fileURLToPath: percent-decoded local path; encoded '/' is refused (Node throws)."""
    parts = urllib.parse.urlsplit(u)
    if re.search(r"%2f", parts.path, re.I) or (os.name == "nt" and re.search(r"%5c", parts.path, re.I)):
        raise ValueError("File URL path must not include encoded / characters")
    if parts.netloc and parts.netloc != "localhost" and os.name != "nt":
        raise ValueError("File URL host must be \"localhost\" or empty")
    return urllib.request.url2pathname(parts.path)


def logo_source_allowed(src, page_url):
    """What a page may make us save. Remote content only reaches us over http(s) or as a size-limited data: URI; a
    file: path is honoured only when the user pointed us at a local page (file:) and it stays inside that page's
    folder. Anything else (file: from a remote page, blob:, ftp:, javascript:) is skipped, never resolved locally."""
    try:
        u, p = urllib.parse.urlsplit(src), urllib.parse.urlsplit(page_url)
        if not u.scheme or not p.scheme:
            raise ValueError
    except ValueError:
        return {"ok": False, "reason": "skipped: unparsable URL"}
    proto, pproto = u.scheme.lower() + ":", p.scheme.lower() + ":"
    if proto in ("http:", "https:"):
        return {"ok": True}
    if proto == "data:":
        return {"ok": True} if u16len(src) <= LOGO_MAX_BYTES * 1.4 else {"ok": False, "reason": "skipped: data: URI too large"}
    if proto == "file:" and pproto == "file:":
        d = os.path.dirname(_file_url_path(page_url))
        f = os.path.abspath(_file_url_path(src))
        if f.startswith(d + os.sep):
            return {"ok": True}
        return {"ok": False, "reason": "skipped: file: outside the local page folder"}
    return {"ok": False, "reason": f"skipped: unsupported scheme {proto}"}


def _lenient_b64(text):
    """Buffer.from(text, 'base64'): url-safe letters accepted, anything else ignored, padding optional."""
    t = re.sub(r"[^A-Za-z0-9+/]", "", text.replace("-", "+").replace("_", "/"))
    t = t[:len(t) - len(t) % 4] + ("" if len(t) % 4 < 2 else t[len(t) - len(t) % 4:] + "=" * (4 - len(t) % 4))
    return base64.b64decode(t) if t else b""


def _download(url, user_agent):
    """context.request.get(url, {timeout: 20 s, maxRedirects: 5}) with ignoreHTTPSErrors: (status, final url,
    headers, body)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    class Redirects(urllib.request.HTTPRedirectHandler):
        max_redirections = 5

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not re.match(r"^https?://", newurl, re.I):
                return None
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), Redirects())
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "*/*",
                                               "Accept-Encoding": "gzip, deflate"})
    try:
        resp = opener.open(req, timeout=20)
    except urllib.error.HTTPError as e:
        return e.code, e.geturl(), {k.lower(): v for k, v in e.headers.items()}, b""
    with resp:
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.headers.items()}
        enc = headers.get("content-encoding", "").lower()
        if enc in ("gzip", "x-gzip"):
            body = gzip.decompress(body)
        elif enc == "deflate":
            try:
                body = zlib.decompress(body)
            except zlib.error:
                body = zlib.decompress(body, -zlib.MAX_WBITS)
        return resp.status, resp.geturl(), headers, body


def save_logo_source(S, page, raw, out):
    """Save the site's own logo as a file the pipeline can keep: an inline SVG as a safe copy (plus logo-dark.svg when
    the page paints it differently in dark mode), an <img> / CSS background image as its downloaded file."""
    lg = raw.get("logo")
    if not truthy(lg):
        return
    try:
        if lg.get("kind") == "svg":
            light = page.evaluate(S.src["pageLogoCopy"])
            if not truthy(light):
                return
            _write_text(os.path.join(out, "logo.svg"), light)
            lg["source"] = "logo.svg"
            page.emulate_media("dark")
            page.wait_for_timeout(100)
            try:
                dark = page.evaluate(S.src["pageLogoCopy"])
            except Exception:  # noqa: BLE001
                dark = None
            page.emulate_media("light")
            if truthy(dark) and dark != light:
                _write_text(os.path.join(out, "logo-dark.svg"), dark)
                lg["sourceDark"] = "logo-dark.svg"
            return
        src = lg.get("src") if lg.get("kind") == "img" else None
        if lg.get("kind") == "bg":
            src = page.evaluate(S.src["pageLogoBgSrc"])
        if not truthy(src):
            return
        ok = logo_source_allowed(src, page.url())
        if not ok["ok"]:
            lg["sourceSkipped"] = ok["reason"]
            log(f"logo source skipped: {ok['reason']}")
            return
        typ = ""
        if src.startswith("data:"):
            m = re.match(r"^data:([^;,]+)?(;base64)?,(.*)$", src, re.S)
            if not m:
                return
            typ = m.group(1) or ""
            if m.group(2):
                buf = _lenient_b64(m.group(3))
            else:
                if re.search(r"%(?![0-9a-fA-F]{2})", m.group(3)):
                    raise ValueError("URI malformed")
                buf = urllib.parse.unquote(m.group(3), errors="strict").encode("utf-8")
        elif src.startswith("file:"):
            with open(_file_url_path(src), "rb") as fh:  # allowed above: local page, its own folder
                buf = fh.read()
        else:
            status, final, headers, buf = _download(src, S.browser.user_agent)
            if not (200 <= status <= 299) or not re.match(r"^https?:", final, re.I):
                return
            typ = headers.get("content-type", "")
        if len(buf) > LOGO_MAX_BYTES:
            lg["sourceSkipped"] = f"too large ({len(buf)} bytes)"
            return
        head = buf[:200].decode("latin-1")
        if re.search("svg", typ) or re.search(r"<svg|<\?xml", head, re.I) or re.search(r"\.svg(\?|$)", src, re.I):
            ext = "svg"
        elif head.startswith("\x89PNG"):
            ext = "png"
        elif head.startswith("\xff\xd8"):
            ext = "jpg"
        elif head.startswith("RIFF"):
            ext = "webp"
        else:
            m = re.search(r"\.(png|jpe?g|webp|gif|avif)(\?|$)", src, re.I)
            ext = (m.group(1) if m else "png").lower()
        name = f"logo.{'jpg' if ext == 'jpeg' else ext}"
        if ext == "svg":
            _write_text(os.path.join(out, name), S.eng.call("stripScripts", buf.decode("utf-8", errors="replace")))
        else:
            with open(os.path.join(out, name), "wb") as fh:
                fh.write(buf)
        lg["source"] = name
    except Exception as e:  # noqa: BLE001
        log(f"logo source not saved: {str(e).splitlines()[0] if str(e) else type(e).__name__}")


def _write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(_surrogates(text))


# ----------------------------------------------------------------------------- extract

def read_scheme(S, url, out, scheme, shots=True, logo=False):
    """Load + read one scheme of a page: returns roles analysis + shots in DIR/current."""
    cur = os.path.join(out, "current")
    os.makedirs(cur, exist_ok=True)
    sx = suffix(scheme)
    page = S.new_page("desktop", scheme)
    page.collect_stylesheets()
    try:
        open_page(S, page, url)
        if shots:
            shoot(S, page, os.path.join(cur, f"desktop{sx}.png"))
            shoot(S, page, os.path.join(cur, f"full{sx}.png"), True)
        raw = page.evaluate(S.src["pageExtract"], {"LIB": S.src["COLOR_LIB_SRC"], "LOGO": S.src["LOGO_SRC"]})
        css_texts = page.stylesheet_texts()
        raw["signals"]["darkModeSupport"] = jor(raw["signals"].get("darkModeSupport"),
                                                S.eng.call("darkCss", "\n".join(css_texts)))
        if logo and truthy(raw.get("logo")):
            try:
                page.screenshot_element("[data-bi-site-logo]", os.path.join(out, "logo.png"))
                raw["logo"]["file"] = "logo.png"
            except Exception:  # noqa: BLE001
                pass
        if logo and truthy(raw.get("logo")):
            save_logo_source(S, page, raw, out)
        try:
            raw["textChars"] = page.evaluate(S.src["pageTextChars"])
        except Exception:  # noqa: BLE001
            raw["textChars"] = 0
        if logo and shots:
            try:
                shoot_header(S, page, os.path.join(cur, f"header{sx}.png"))
            except Exception as e:  # noqa: BLE001
                log(f"header shot failed: {e}")
        return raw, S.eng.call("buildRoles", raw, css_texts)
    finally:
        page.close()


def shoot_mobile(S, url, file, scheme):
    page = S.new_page("mobile", scheme)
    try:
        open_page(S, page, url)
        shoot(S, page, file)
    finally:
        page.close()


def read_dark(S, url, out, result):
    raw, built = read_scheme(S, url, out, "dark")
    if S.eng.call("isDarkHex", built["roles"]["background"]):  # only trust it if the page really went dark
        shoot_mobile(S, url, os.path.join(out, "current", "mobile-dark.png"), "dark")
        result["rolesDark"] = built["roles"]
        result["roleDetailsDark"] = built["roleDetails"]
        result["shots"].update({"desktop-dark": "current/desktop-dark.png", "mobile-dark": "current/mobile-dark.png",
                                "full-dark": "current/full-dark.png"})
        result["notes"] = [n for n in (result.get("notes") or []) if "dark" not in n]
    else:
        result["notes"].append("site declares dark-mode CSS but did not render dark under prefers-color-scheme: dark; "
                               "dark previews recolour the light page")
        for f in ("desktop-dark.png", "full-dark.png"):
            try:
                os.remove(os.path.join(out, "current", f))
            except OSError:
                pass


def _order(o):
    return {k: o[k] for k in ROLE_KEYS if truthy(o.get(k))} if o is not None else o


def cmd_extract(S, url, out, mode="light", quiet=False, dark="auto"):
    """dark: 'auto' (read the site's dark scheme when it has one), 'never', or 'only' (add the dark reading to an
    existing extract.json without touching its light roles)."""
    os.makedirs(out, exist_ok=True)
    t0 = time.monotonic()
    file = os.path.join(out, "extract.json")
    try:
        with open(file, encoding="utf-8") as fh:
            prev = json.load(fh)
    except (OSError, ValueError):
        prev = None
    if dark == "only":
        if not truthy(prev):
            raise Die(f"--dark-only needs an existing {file}; run extract first")
        result = prev
        if not truthy(result.get("signals")) or not truthy(result["signals"].get("darkModeSupport")):
            result["notes"] = [n for n in (result.get("notes") or []) if "dark" not in n] + [
                "site has no dark scheme; dark previews recolour the light page with the palette's dark roles"]
        else:
            read_dark(S, url, out, result)
    else:
        raw, built = read_scheme(S, url, out, "light", logo=True)
        shoot_mobile(S, url, os.path.join(out, "current", "mobile.png"), "light")
        result = {"schema": "brand-identity/extract@1", "url": raw["url"], "requestedUrl": url, "extractedAt": now_iso(),
                  "rolesAreProposal": True,
                  "note": "Roles are a proposal measured from the rendered page. Confirm them with the user (fix by "
                          "editing \"roles\"), then set \"rolesConfirmed\": true."}
        result.update(built)
        result.update({"rolesDark": None, "roleDetailsDark": None, "fonts": raw.get("fonts", UNDEFINED),
                       "logo": raw.get("logo", UNDEFINED), "favicon": raw.get("favicon", UNDEFINED),
                       "signals": raw.get("signals", UNDEFINED),
                       "shots": {"desktop": "current/desktop.png", "mobile": "current/mobile.png",
                                 "full": "current/full.png", "header": "current/header.png"}})
        with open(os.path.join(out, "current", "desktop.png"), "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        result["imagePalette"] = S.eng.page_call("pageImagePalette", {"b64": b64, "k": 6, "LIB": S.src["COLOR_LIB_SRC"]})
        empty = S.eng.call("emptyPageReason", raw.get("textChars", UNDEFINED), result["imagePalette"])
        result["status"] = "empty" if truthy(empty) else "ok"
        if truthy(empty):
            result["notes"].insert(0, f"page looks empty ({empty}); the site may live under a sub-path, pass the full "
                                      "URL (e.g. https://host/app/). Roles below are not reliable.")
        if dark != "never" and truthy(raw["signals"].get("darkModeSupport")):
            read_dark(S, url, out, result)
        elif not truthy(raw["signals"].get("darkModeSupport")):
            result["notes"].append("site has no dark scheme; dark previews recolour the light page with the palette's "
                                   "dark roles")
    # keep what was corrected before (a re-extract must not undo the user's role fixes)
    if truthy(prev) and dark != "only":
        kept = {"light": S.eng.call("correctedRoles", prev, "roles", "roleDetails"),
                "dark": S.eng.call("correctedRoles", prev, "rolesDark", "roleDetailsDark")}
        result["roles"].update(kept["light"])
        if truthy(result.get("rolesDark")):
            result["rolesDark"].update(kept["dark"])
        elif kept["dark"]:
            merged = dict(prev.get("rolesDark") or {})
            merged.update(kept["dark"])
            result["rolesDark"] = merged
        result["roles"] = _order(result["roles"])
        if truthy(result.get("rolesDark")):
            result["rolesDark"] = _order(result["rolesDark"])
        if truthy(prev.get("rolesConfirmed")):
            result["rolesConfirmed"] = True
            if truthy(prev.get("rolesConfirmedBy")):
                result["rolesConfirmedBy"] = prev["rolesConfirmedBy"]
        result["preservedRoles"] = {"light": list(kept["light"]), "dark": list(kept["dark"])}
        if not quiet and (kept["light"] or kept["dark"]):
            log(f"extract: kept corrected roles from the previous extract.json: {js_json(kept, None)}")
    if mode == "dark" and not truthy(result.get("rolesDark")) and not quiet:
        log("note: no dark reading of this site; dark previews recolour the light page")
    write_json(file, result)
    if not quiet:
        log(f"extract: done in {to_fixed(time.monotonic() - t0, 1)}s")
    return result


# ----------------------------------------------------------------------------- apply

def prepare_page(S, kind, url, old_roles, new_roles, scheme, keep_logo, baseline, scales, font_plan=None):
    """newRoles None = keep the site's colours. font_plan (identity apply): stylesheet font-family declarations get
    the set's family names prepended; they resolve only once the faces are added (pageFontSwap)."""
    page = S.new_page(kind, scheme)
    if new_roles is not None or font_plan:
        eng = S.eng

        def rewrite(css, _url):
            return eng.call("rewriteStylesheet", css, old_roles, new_roles, scales, font_plan)
        page.route_stylesheets(rewrite)
    open_page(S, page, url)
    try:
        page.evaluate(S.src["pageMarkLogo"])
    except Exception:  # noqa: BLE001
        pass
    guard = None
    if new_roles is not None:
        page.evaluate(S.src["pageRecolor"], {"LIB": S.src["COLOR_LIB_SRC"], "oldRoles": old_roles, "newRoles": new_roles,
                                              "keepLogo": keep_logo, "scales": scales})
        page.wait_for_timeout(250)
        guard = page.evaluate(S.src["pageContrastGuard"], {"LIB": S.src["COLOR_LIB_SRC"], "GUARD": S.src["GUARD_SRC"],
                                                            "newRoles": new_roles, "baseline": baseline})
    if font_plan:
        page.evaluate(S.src["pageRewriteFonts"], {"FONT": S.src["FONT_LIB_SRC"], "plan": font_plan})
    return page, guard


def hex_ok(v):
    return isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", js_trim(v)) is not None


_JS_WS = "\t\n\v\f\r                  　﻿"


def js_trim(s):
    return s.strip(_JS_WS)


def _basename(file, ext):
    b = os.path.basename(file)
    return b[:-len(ext)] if b.endswith(ext) and b != ext else b


def parse_palette(file, mode):
    """brand-identity/palette@1 -> {file, name, direction, roles, scales} for one mode; raises ValueError."""
    try:
        with open(file, encoding="utf-8") as fh:
            j = json.load(fh)
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read palette {file}: {e}") from None
    if not isinstance(j, dict):
        j = {}
    if j.get("schema") != "brand-identity/palette@1":
        log(f"warning: {file}: schema is {js_json(j.get('schema', UNDEFINED), None) if 'schema' in j else 'undefined'}, "
            "expected \"brand-identity/palette@1\"")
    roles = (j.get("modes") or {}).get(mode) if truthy(j.get("modes")) and isinstance(j.get("modes"), dict) else None
    if not truthy(roles) or not isinstance(roles, (dict, list)):
        raise ValueError(f"{file}: modes.{mode} is missing (palette.json §3 needs modes.light and modes.dark)")
    if isinstance(roles, list):
        roles = {}
    show = lambda v: "undefined" if v is UNDEFINED else js_json(v, None)  # noqa: E731
    for k in ("background", "text", "primary"):
        if not hex_ok(roles.get(k)):
            raise ValueError(f"{file}: modes.{mode}.{k} must be a #rrggbb hex, got {show(roles.get(k, UNDEFINED))}")
    clean = {}
    for k in ROLE_KEYS:
        if roles.get(k) is not None:
            if not hex_ok(roles[k]):
                raise ValueError(f"{file}: modes.{mode}.{k} must be a #rrggbb hex, got {show(roles[k])}")
            clean[k] = roles[k].lower()
    scales = {}
    sc = j.get("scales") if isinstance(j.get("scales"), dict) else {}
    for k in ("primary", "accent"):
        if truthy(sc.get(k)) and isinstance(sc[k], dict) and truthy(sc[k].get("steps")):
            scales[k] = sc[k]["steps"]
    return {"file": os.path.abspath(file), "name": jor(j.get("name"), _basename(file, ".json")),
            "direction": jor(j.get("direction"), ""), "roles": clean, "scales": scales}


def load_palette(file, mode):
    try:
        return parse_palette(file, mode)
    except ValueError as e:
        raise Die(str(e)) from None


def load_old_roles(S, file, mode):
    """old roles: extract.json ({roles, rolesDark}), {roles:{}} or a flat role map."""
    try:
        with open(file, encoding="utf-8") as fh:
            j = json.load(fh)
    except (OSError, ValueError) as e:
        raise Die(f"cannot read roles {file}: {e}") from None
    roles, scheme = jor(j.get("roles") if isinstance(j, dict) else None, j), "light"
    if mode == "dark" and isinstance(j, dict) and truthy(j.get("rolesDark")):
        roles, scheme = j["rolesDark"], "dark"
    for k in ("background", "text"):
        v = roles.get(k) if isinstance(roles, dict) else None
        if not truthy(v) or not S.eng.call("colorOk", v):
            raise Die(f"{file}: roles.{k} is missing or not a colour; fix the role proposal first")
    return roles, scheme, j if isinstance(j, dict) and j.get("schema") == "brand-identity/extract@1" else None


def cmd_apply(S, url, out, palettes, roles_file, mode, keep_logo):
    os.makedirs(out, exist_ok=True)
    roles_path = roles_file or os.path.join(out, "extract.json")
    if not os.path.exists(roles_path):
        if roles_file:
            raise Die(f"roles file not found: {roles_file}")
        log("no extract.json yet: extracting first (roles are unconfirmed)")
        cmd_extract(S, url, out, mode)
        roles_path = os.path.join(out, "extract.json")
    if mode == "dark" and os.path.abspath(roles_path) == os.path.abspath(os.path.join(out, "extract.json")):
        with open(roles_path, encoding="utf-8") as fh:
            ex = json.load(fh)
        if not truthy(ex.get("rolesDark")) and truthy(ex.get("signals")) and truthy(ex["signals"].get("darkModeSupport")) \
                and not any("did not render dark" in n for n in (ex.get("notes") or [])):
            log("extract.json has no dark reading yet: reading the site's dark scheme first (light roles untouched)")
            cmd_extract(S, url, out, "dark", dark="only")
    old_roles, scheme, _ = load_old_roles(S, roles_path, mode)
    sx = suffix(mode)
    pals = [load_palette(p, mode) for p in palettes]  # validate every palette before touching the network
    baselines = {}
    for kind in ("desktop", "mobile"):
        page = S.new_page(kind, scheme)
        try:
            open_page(S, page, url)
            baselines[kind] = page.evaluate(S.src["pageGuardBaseline"], {"LIB": S.src["COLOR_LIB_SRC"],
                                                                          "GUARD": S.src["GUARD_SRC"]})
        finally:
            page.close()
    used, results = set(), []
    for pal in pals:
        slug = S.eng.call("slugify", pal["name"])
        if slug == "current":
            slug = "current-palette"
        i = 2
        while slug in used:
            slug = f"{S.eng.call('slugify', pal['name'])}-{i}"
            i += 1
        used.add(slug)
        d = os.path.join(out, slug)
        os.makedirs(d, exist_ok=True)
        guards, t0 = {}, time.monotonic()
        for kind in ("desktop", "mobile"):
            page, guard = prepare_page(S, kind, url, old_roles, pal["roles"], scheme, keep_logo, baselines[kind],
                                       pal["scales"])
            try:
                guards[kind] = guard
                shoot(S, page, os.path.join(d, f"{kind}{sx}.png"))
                if kind == "desktop":
                    shoot(S, page, os.path.join(d, f"full{sx}.png"), True)
            finally:
                page.close()
        meta = {"schema": "brand-identity/apply@1", "url": url, "name": pal["name"], "slug": slug,
                "direction": pal["direction"], "palette": pal["file"], "mode": mode, "pageScheme": scheme,
                "oldRoles": old_roles, "roles": pal["roles"],
                "shots": {"desktop": f"{slug}/desktop{sx}.png", "mobile": f"{slug}/mobile{sx}.png",
                          "full": f"{slug}/full{sx}.png"},
                "contrastGuard": guards, "appliedAt": now_iso()}
        write_json(os.path.join(d, f"apply{sx}.json"), meta)
        log(f"apply: {js_str(pal['name'])} ({mode}) -> {slug}/ in {to_fixed(time.monotonic() - t0, 1)}s; contrast guard "
            f"fixed {js_str(guards['desktop']['fixed'])}/{js_str(guards['desktop']['checked'])} desktop, "
            f"{js_str(guards['mobile']['fixed'])}/{js_str(guards['mobile']['checked'])} mobile")
        results.append({"name": pal["name"], "slug": slug, "dir": d, "shots": meta["shots"],
                        "contrastGuard": {"desktop": guards["desktop"]["fixed"], "mobile": guards["mobile"]["fixed"]}})
    return results


# ----------------------------------------------------------------------------- identity apply

def font_axes(buf):
    """sfnt axes from the 'fvar' table (TTF/OTF only; woff/woff2 return None = unknown, treated as static)."""
    import struct
    if len(buf) < 12:
        return None
    if buf[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        return None
    n = struct.unpack(">H", buf[4:6])[0]
    for i in range(n):
        r = 12 + i * 16
        if buf[r:r + 4] != b"fvar":
            continue
        off = struct.unpack(">I", buf[r + 8:r + 12])[0]
        axes_off, count, size = (struct.unpack(">H", buf[off + 4:off + 6])[0], struct.unpack(">H", buf[off + 8:off + 10])[0],
                                 struct.unpack(">H", buf[off + 10:off + 12])[0])
        axes = {}
        for j in range(count):
            a = off + axes_off + j * size
            mn, df, mx = struct.unpack(">iii", buf[a + 4:a + 16])
            axes[buf[a:a + 4].decode("latin-1")] = {"min": mn / 65536, "def": df / 65536, "max": mx / 65536}
        return axes
    return {}


def _get(o, k):
    return o.get(k, UNDEFINED) if isinstance(o, dict) else UNDEFINED


def load_identity_set(S, file, mode):
    """identity.json -> what apply needs: palette roles for the mode, font faces from type/fonts.json ({role: {family,
    file | files, location}}; file relative to the set or work folder), and the logo variants in logo/build/*.svg
    (full-color.svg / primary.svg first). Kept / absent components are left as the site has them."""
    try:
        with open(file, encoding="utf-8") as fh:
            j = json.load(fh)
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read identity {file}: {e}") from None
    if not isinstance(j, dict) or j.get("schema") != "brand-identity/identity@1":
        raise ValueError(f'{file}: schema must be "brand-identity/identity@1"')
    d = os.path.dirname(os.path.abspath(file))
    work = os.path.abspath(os.path.join(d, "..", ".."))
    comp = jor(j.get("components"), {})
    mo = lambda k: jor(truthy(_get(comp, k)) and _get(_get(comp, k), "mode"), "new")  # noqa: E731
    st = j.get("set")
    sid = jor(truthy(st) and _get(st, "id"), os.path.basename(d))
    if not isinstance(sid, str):
        raise ValueError("id.toLowerCase is not a function")
    name = jor(truthy(st) and _get(st, "name"), f"Set {sid}")
    notes = []
    palette = None
    if mo("palette") != "none" and truthy(j.get("palette")):
        pf = os.path.abspath(os.path.join(d, js_str(j["palette"])))
        if os.path.exists(pf):
            palette = parse_palette(pf, mode)
        else:
            notes.append(f"palette: {js_str(j['palette'])} not found; site colours kept")
    else:
        notes.append("palette: not part of this set; site colours kept")
    faces, role_style = [], {}
    ty = j.get("type")
    if mo("type") in ("new", "refresh") and truthy(ty):
        try:
            with open(os.path.join(d, "type", "fonts.json"), encoding="utf-8") as fh:
                fj = json.load(fh)
        except (OSError, ValueError):
            fj = {}
        for role in ("display", "text", "mono"):
            if role == "mono" and not truthy(_get(ty, "mono")) and not truthy(_get(fj, "mono")):
                continue
            face, e = jor(_get(ty, role), {}), jor(_get(fj, role), {})
            loc = jor(_get(e, "location"), _get(face, "location"), {})
            if isinstance(_get(e, "files"), list):
                files = [dict(x) for x in e["files"] if truthy(x) and isinstance(x, dict) and isinstance(x.get("file"), str)]
            elif truthy(_get(e, "file")):
                files = [{"file": e["file"], "location": loc}]
            elif truthy(_get(face, "file")):
                files = [{"file": face["file"], "location": loc}]
            else:
                files = []
            for x in files:
                cands = [x["file"], os.path.abspath(os.path.join(d, x["file"])), os.path.abspath(os.path.join(work, x["file"]))]
                x["path"] = next((p for p in cands if os.path.isabs(p) and os.path.exists(p)), UNDEFINED)
            missing = [x for x in files if not truthy(x["path"])]
            if not files or missing:
                what = f"file not found ({', '.join(js_str(x['file']) for x in missing)})" if files else "no file in type/fonts.json"
                notes.append(f"{role} font: {what}; not applied")
                continue
            nm = f"__bi_{role}_{sid.lower()}"
            axes_all = None
            for x in files:
                with open(x["path"], "rb") as fh:
                    buf = fh.read()
                axes = font_axes(buf)
                axes_all = axes_all if truthy(axes_all) else axes
                xl = jor(x.get("location", UNDEFINED), loc)
                if truthy(axes) and truthy(axes.get("wght")):
                    weight = f"{js_number(axes['wght']['min'])} {js_number(axes['wght']['max'])}"
                elif len(files) > 1:
                    weight = js_str(js_round(jor(_get(xl, "wght"), 400)))
                else:
                    weight = "1 1000"
                dflt = (jor(_get(face, "weights"), [600]) or [UNDEFINED])
                dflt = dflt[0] if isinstance(dflt, list) and dflt else UNDEFINED
                mw = jor(_get(xl, "wght"), dflt if role == "display" else 400)
                faces.append({"role": role, "name": nm, "family": jor(_get(e, "family"), _get(face, "family")),
                              "b64": base64.b64encode(buf).decode("ascii"), "weight": weight, "file": x["path"],
                              "measureWeight": js_round(mw) if isinstance(mw, (int, float)) else float("nan")})
            fallback = jor(_get(face, "fallback"), "serif" if role == "display" else "sans-serif")
            feats = _get(face, "features")
            role_style[role] = {
                "name": nm, "family": jor(_get(e, "family"), _get(face, "family")),
                "fallback": jor(_get(face, "fallback"), "monospace") if role == "mono" else fallback,
                "fvs": S.eng.call("fvsOf", loc, axes_all, role != "display"), "location": loc,
                "tracking": face["tracking"] if role == "display" and isinstance(_get(face, "tracking"), (int, float))
                and not isinstance(face["tracking"], bool) else None,
                "case": _get(face, "case") if role == "display" else None,
                "features": ", ".join(f'"{js_str(f)}"' for f in feats) if role == "display" and isinstance(feats, list)
                and feats else None,
                "static": not (truthy(axes_all) and len(axes_all) > 0), "files": [x["path"] for x in files]}
    else:
        notes.append(f"type: {js_str(mo('type'))}; site fonts kept")
    variants = []
    if mo("logo") in ("new", "refresh"):
        bdir = os.path.join(d, "logo", "build")
        try:
            names = os.listdir(bdir)
        except OSError:
            names = []
        try:
            with open(os.path.join(bdir, "manifest.json"), encoding="utf-8") as fh:
                manifest = json.load(fh)
        except (OSError, ValueError):
            manifest = {}
        vers = jor(_get(manifest, "versions"), {})
        for v in S.eng.call("pickVariantNames", names):
            with open(os.path.join(bdir, v["name"]), "rb") as fh:
                svg = S.eng.call("cleanSvg", fh.read().decode("utf-8", errors="replace"))
            sz = S.eng.call("svgSize", svg)
            if not truthy(sz) or not (sz["w"] > 0 and sz["h"] > 0):
                continue
            mv = _get(vers, v["key"])
            reversed_ = (_get(mv, "mode") == "dark") if truthy(mv) else S.eng.call("reversedKey", v["key"])
            variants.append({"name": v["name"], "w": sz["w"], "h": sz["h"], "svg": svg, "primary": v["primary"],
                             "reversed": reversed_})
        if not variants:
            notes.append("logo: no painted SVG in logo/build/ (full-color.svg); site logo kept")
    else:
        notes.append(f"logo: {js_str(mo('logo'))}; site logo kept")
    return {"file": os.path.abspath(file), "dir": d, "id": sid, "name": name, "palette": palette, "faces": faces,
            "roleStyle": role_style, "variants": variants, "notes": notes}


def apply_identity_page(S, kind, url, old_roles, st, scheme, baseline, old_fonts):
    """One identity set on one viewport: colours (prepare_page), then before-layout, fonts, logo, after-layout."""
    plan = {}
    for role in ("display", "text", "mono"):
        if truthy(st["roleStyle"].get(role)):
            plan[role] = {"name": st["roleStyle"][role]["name"],
                          "old": S.eng.call("firstFamily", old_fonts[role]) if truthy(old_fonts.get(role)) else None}
    has_fonts = len(plan) > 0
    pal = st["palette"]
    page, guard = prepare_page(S, kind, url, old_roles, pal["roles"] if pal else None, scheme, True, baseline,
                               pal["scales"] if pal else None, plan if has_fonts else None)
    before = page.evaluate(S.src["pageLayoutSnapshot"])
    fonts, logo = None, {"replaced": False, "reason": "logo not part of this set"}
    if has_fonts:
        one = bool(truthy(plan.get("display")) and truthy(plan.get("text")) and truthy(plan["display"]["old"])
                   and plan["display"]["old"] == plan["text"]["old"])
        role_style = dict(st["roleStyle"], oneFamily=one)
        if truthy(role_style.get("display")):
            role_style["display"] = dict(role_style["display"], fallback=jor(old_fonts.get("display"),
                                                                             role_style["display"]["fallback"]))
        fonts = page.evaluate(S.src["pageFontSwap"], {
            "faces": st["faces"], "oldStacks": {"display": old_fonts["display"], "text": old_fonts["text"],
                                                "mono": old_fonts["mono"]},
            "roleStyle": role_style, "ICON": S.src["ICON_FONT"]})
    if st["variants"]:
        logo = page.evaluate(S.src["pageLogoSwap"], {"LIB": S.src["COLOR_LIB_SRC"], "GUARD": S.src["GUARD_SRC"],
                                                      "LOGO": S.src["LOGO_SRC"], "variants": st["variants"]})
    try:
        page.evaluate(S.src["pageSettleSwap"])
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(200)
    after = page.evaluate(S.src["pageLayoutSnapshot"])
    return page, guard, fonts, logo, S.eng.call("layoutStress", before, after)


def cmd_apply_identity(S, url, out, identities, roles_file, mode):
    os.makedirs(out, exist_ok=True)
    roles_path = roles_file or os.path.join(out, "extract.json")
    if not os.path.exists(roles_path):
        if roles_file:
            raise Die(f"roles file not found: {roles_file}")
        log("no extract.json yet: extracting first (roles are unconfirmed)")
        cmd_extract(S, url, out, mode)
        roles_path = os.path.join(out, "extract.json")
    old_roles, scheme, extract = load_old_roles(S, roles_path, mode)
    ef = jor(truthy(extract) and extract.get("fonts"), {})
    old_fonts = {"display": jor(_get(ef, "heading"), None), "text": jor(_get(ef, "paragraph"), _get(ef, "body"), None),
                 "mono": jor(_get(ef, "code"), None)}
    sets = []
    for f in identities:  # validate all before the network
        try:
            sets.append(load_identity_set(S, f, mode))
        except ValueError as e:
            raise Die(str(e)) from None
    baselines = {}
    if any(s["palette"] for s in sets):
        for kind in ("desktop", "mobile"):
            page = S.new_page(kind, scheme)
            try:
                open_page(S, page, url)
                baselines[kind] = page.evaluate(S.src["pageGuardBaseline"], {"LIB": S.src["COLOR_LIB_SRC"],
                                                                              "GUARD": S.src["GUARD_SRC"]})
            finally:
                page.close()
    sx, used, results = suffix(mode), set(), []
    for st in sets:
        base = S.eng.call("slugify", f"{st['id']}-{js_str(st['name'])}")
        slug, i = base, 2
        while slug in used:
            slug = f"{base}-{i}"
            i += 1
        used.add(slug)
        d = os.path.join(out, slug)
        os.makedirs(d, exist_ok=True)
        t0, per = time.monotonic(), {}
        for kind in ("desktop", "mobile"):
            page, guard, fonts, logo, stress = apply_identity_page(S, kind, url, old_roles, st, scheme,
                                                                   baselines.get(kind, UNDEFINED), old_fonts)
            try:
                shoot(S, page, os.path.join(d, f"{kind}{sx}.png"))
                if kind == "desktop":
                    shoot(S, page, os.path.join(d, f"full{sx}.png"), True)
                    try:
                        shoot_header(S, page, os.path.join(d, f"header{sx}.png"))
                    except Exception as e:  # noqa: BLE001
                        log(f"header shot failed: {e}")
                per[kind] = {"contrastGuard": guard, "fonts": fonts, "logo": logo, "layoutStress": stress}
            finally:
                page.close()
        fonts_ok = not st["faces"] or all(truthy(per[k]["fonts"]) and all(truthy(x.get("ok"))
                                                                           for x in per[k]["fonts"]["roles"].values())
                                          for k in ("desktop", "mobile"))
        meta = {"schema": "brand-identity/site-apply@1", "url": url, "set": st["id"], "name": st["name"], "slug": slug,
                "identity": st["file"], "mode": mode, "pageScheme": scheme, "oldRoles": old_roles,
                "roles": st["palette"]["roles"] if st["palette"] else None, "oldFonts": old_fonts,
                "fonts": {k: {"family": v["family"], "location": v["location"], "variationSettings": jor(v["fvs"], None),
                              "static": v["static"], "files": v["files"]} for k, v in st["roleStyle"].items()},
                "fontsLoaded": fonts_ok, "logoVariants": [v["name"] for v in st["variants"]], "notes": st["notes"],
                "layoutStress": {"desktop": per["desktop"]["layoutStress"]["count"],
                                 "mobile": per["mobile"]["layoutStress"]["count"]},
                "viewports": per,
                "shots": {"desktop": f"{slug}/desktop{sx}.png", "mobile": f"{slug}/mobile{sx}.png",
                          "full": f"{slug}/full{sx}.png", "header": f"{slug}/header{sx}.png"},
                "appliedAt": now_iso()}
        write_json(os.path.join(d, f"apply{sx}.json"), meta)
        dl = per["desktop"]["logo"]
        log(f"apply: set {js_str(st['id'])} {js_str(st['name'])} ({mode}) -> {slug}/ in {to_fixed(time.monotonic() - t0, 1)}s; "
            f"layoutStress {js_str(meta['layoutStress']['desktop'])} desktop, {js_str(meta['layoutStress']['mobile'])} mobile; "
            f"fonts {('loaded' if fonts_ok else 'NOT LOADED') if st['faces'] else 'kept'}; "
            f"logo {js_str(dl['variant']) if truthy(dl.get('replaced')) else 'kept'}")
        ml = per["mobile"]["logo"]
        results.append({"set": st["id"], "name": st["name"], "slug": slug, "dir": d, "shots": meta["shots"],
                        "layoutStress": meta["layoutStress"], "fontsLoaded": fonts_ok,
                        "logo": {"desktop": dl["variant"] if truthy(dl.get("replaced")) else None,
                                 "mobile": ml["variant"] if truthy(ml.get("replaced")) else None},
                        "contrastGuard": {"desktop": per["desktop"]["contrastGuard"]["fixed"]
                                          if truthy(per["desktop"]["contrastGuard"]) else None,
                                          "mobile": per["mobile"]["contrastGuard"]["fixed"]
                                          if truthy(per["mobile"]["contrastGuard"]) else None}})
    return results


# ----------------------------------------------------------------------------- competitors

def _cause(e):
    msg = str(e).split("\n")[0]
    msg = re.sub(r"\. If the site blocks[\s\S]*$", "", msg).replace(NO_SITE_FALLBACK, "")
    return re.sub(r"^could not load \S+: (page\.goto: )?", "", msg).strip()


def _one_competitor(S, url, out):
    host = S.eng.call("hostOf", url)
    if host is None:
        return {"url": url, "status": "error", "error": "invalid URL"}
    page = S.new_page("desktop", "light")
    page.collect_stylesheets()
    try:
        try:
            try:
                resp = open_page(S, page, url)
            except Exception as e:  # noqa: BLE001
                # parked / for-sale domains often have no TLS: one plain-http retry on a connection error
                if getattr(e, "blocked", False) or not re.match(r"^https:", url, re.I) or \
                        not re.search(r"ERR_(CONNECTION|SSL|CERT|TIMED_OUT|NAME)|net::", str(e), re.I):
                    raise
                resp = open_page(S, page, re.sub(r"^https:", "http:", url, flags=re.I))
            hb = S.eng.call("httpBlockReason", resp["status"], resp["headers"]) if resp else None
            if truthy(hb):
                raise Blocked(url, hb)
            info = page.evaluate(S.src["pageInfo"])
            parked = S.eng.call("parkedReason", dict({"requested": url}, **info))
            if truthy(parked):
                return {"url": url, "host": host, "status": "parked", "finalUrl": info["final"], "reason": parked,
                        "error": f"parked: {parked}"}
            if resp and resp["status"] >= 400:
                raise RuntimeError(f"HTTP {js_str(resp['status'])}")
            shot = f"competitors/{S.eng.call('slugify', host)}.png"
            shoot(S, page, os.path.join(out, shot))
            raw = page.evaluate(S.src["pageExtract"], {"LIB": S.src["COLOR_LIB_SRC"], "LOGO": S.src["LOGO_SRC"]})
            b = S.eng.call("buildRoles", raw, page.stylesheet_texts())
            stats = S.eng.call("competitorStats", b)
            sig, f = raw["signals"], raw["fonts"]
            return {
                "url": raw["url"], "host": host, "status": "ok", "title": sig.get("title", UNDEFINED),
                "siteName": sig.get("siteName", UNDEFINED), "description": sig.get("description", UNDEFINED),
                "lang": sig.get("lang", UNDEFINED), "themeColor": sig.get("themeColor", UNDEFINED),
                "darkModeSupport": sig.get("darkModeSupport", UNDEFINED),
                "roles": b["roles"], "confidence": {k: v["confidence"] for k, v in b["roleDetails"].items()},
                "notes": b["notes"],
                "brandColors": list(dict.fromkeys(x for x in (b["roles"].get("primary"), b["roles"].get("accent"))
                                                  if truthy(x))),
                "topColors": [{"hex": p["hex"], "oklch": p["oklch"], "score": p["score"], "areaShare": p["areaShare"],
                               "roles": p["roles"]} for p in b["palette"][:8]],
                "nHues": stats["nHues"], "hues": stats["hues"], "lightnessRange": stats["lightnessRange"],
                "strategy": b["strategy"],
                "fonts": {"heading": f.get("heading", UNDEFINED), "body": f.get("body", UNDEFINED),
                          "paragraph": f.get("paragraph", UNDEFINED), "loaded": jor(f.get("loaded"), [])},
                "screenshot": shot}
        except Exception as e:  # noqa: BLE001
            # the cause only: a competitor that cannot be read is not a reason to switch the user's preview to mocks
            blocked = getattr(e, "blocked", False)
            r = {"url": url, "host": host, "status": "blocked" if blocked else "error",
                 "error": f"blocked: {e.reason}" if blocked else _cause(e)}
            if blocked:
                r.update(blocked=True, reason=e.reason)
            return r
    finally:
        page.close()


def cmd_competitors(S, urls, out, concurrency=3):
    os.makedirs(os.path.join(out, "competitors"), exist_ok=True)
    results = [None] * len(urls)
    workers = max(1, min(int(concurrency), len(urls)))
    if workers == 1:
        for i, u in enumerate(urls):
            results[i] = _one_competitor(S, u, out)
    else:
        lock, nxt, errors = threading.Lock(), [0], []

        def run(sess):
            while True:
                with lock:
                    i = nxt[0]
                    nxt[0] += 1
                if i >= len(urls):
                    return
                results[i] = _one_competitor(sess, urls[i], out)

        # one browser per extra worker over the pipe (one client each), else one more client per worker
        extra = [cdplib.Browser(S.browser.executable) for _ in range(workers - 1)] if S.browser.pipe else []
        shared = not extra   # websocket: all workers are clients of one browser
        sessions, running = [S], [workers]

        def work(sess):
            try:
                _guard(errors, run, sess)
            finally:
                with lock:
                    running[0] -= 1
                if shared:
                    # Chrome holds every new page's navigation until each client lets it go: a finished worker keeps
                    # answering until the last one is done (else the last pages time out in page.goto)
                    with contextlib.suppress(Exception):
                        sess.client.idle(lambda: running[0] == 0)

        try:
            sessions += ([Session(b) for b in extra] if extra else
                         [Session(S.browser, S.browser.client()) for _ in range(workers - 1)])
            threads = [threading.Thread(target=work, args=(s,), daemon=True) for s in sessions]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            for b in extra:
                b.close()
            if shared:
                for sess in sessions[1:]:
                    with contextlib.suppress(Exception):
                        sess.client.close()
        if errors:
            raise errors[0]
    doc = {"schema": "brand-identity/competitors@1", "measuredAt": now_iso(),
           "note": "Measured hex values approximate public websites; roles are unconfirmed proposals.",
           "sites": [r for r in results if r["status"] == "ok"],
           # parked / blocked / failed sites are listed, never measured: they stay out of the competitor map
           "errors": [dict({"url": r["url"], "host": r.get("host", UNDEFINED), "status": r["status"],
                            "error": r.get("error", UNDEFINED), "blocked": truthy(r.get("blocked")),
                            "reason": r.get("reason", UNDEFINED)}, **({"finalUrl": r["finalUrl"]}
                                                                       if truthy(r.get("finalUrl")) else {}))
                      for r in results if r["status"] != "ok"]}
    write_json(os.path.join(out, "competitors.json"), doc)
    return doc


def _guard(errors, fn, *args):
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001
        errors.append(e)


# ----------------------------------------------------------------------------- check

def cmd_check():
    info = {"python": sys.version.split()[0], "pythonOk": sys.version_info >= (3, 10),
            "engine": ENGINE_JS, "engineOk": os.path.isfile(ENGINE_JS),
            "browsers": cdplib.browser_candidates()}
    browser, errors = cdplib.launch()
    if browser:
        info["browser"] = browser.executable
        info["browserVersion"] = browser.version
        # Self-test: really run extract on a bundled offline page, so a tool that loads but does nothing is caught.
        out = tempfile.mkdtemp(prefix="bi-selftest-")
        t0 = time.monotonic()
        try:
            S = Session(browser)
            url = S.eng.call("checkUrl", Path(SELFTEST).resolve().as_uri())  # file:///C:/... on Windows
            cmd_extract(S, url, out, "light", quiet=True)
            with open(os.path.join(out, "extract.json"), encoding="utf-8") as fh:
                ex = json.load(fh)
            want = {"background": "#fbfaf7", "text": "#23201c", "primary": "#1f5f7a"}
            wrong = [f"{k} {ex['roles'].get(k)} != {v}" for k, v in want.items() if ex["roles"].get(k) != v]
            info["selfTest"] = {"ok": not wrong, "seconds": round(time.monotonic() - t0, 1), "roles": ex["roles"]}
            if wrong:
                info["selfTest"]["wrong"] = wrong
        except Exception as e:  # noqa: BLE001
            info["selfTest"] = {"ok": False, "error": str(e).splitlines()[0] if str(e) else type(e).__name__}
        finally:
            browser.close()
            import shutil
            shutil.rmtree(out, ignore_errors=True)
    else:
        info["browserError"] = "; ".join(errors) or render_png_no_browser()
    info["ok"] = bool(info["pythonOk"] and info["engineOk"] and info.get("browser") and (info.get("selfTest") or {}).get("ok"))
    return info


def render_png_no_browser():
    import render_png
    return render_png.NO_BROWSER


# ----------------------------------------------------------------------------- compact summaries (what the agent reads)

def _pjoin(*parts):
    return os.path.normpath(os.path.join(*parts))


def extract_summary(S, r, out):
    lines = []
    kept = set(((r.get("preservedRoles") or {}).get("light")) or [])
    dark = "read" if truthy(r.get("rolesDark")) else ("declared, not rendered" if truthy(r.get("signals")) and
                                                       truthy(r["signals"].get("darkModeSupport")) else "none")
    lines.append(f"extract {clip(r['url'], 60)} · {js_str(r['strategy'])} ({js_str(r['tokens']['count'])} colour tokens, "
                 f"{js_str(r['tokens']['used'])} used) · site dark theme: {dark}")
    lines.append(f"→ {_pjoin(out, 'extract.json')} (full data: alternatives, palette, tokens; read only if needed)")
    if r.get("status") == "empty":
        lines.append(f"WARNING: {js_str(r['notes'][0])}")
    lg = r.get("logo")
    if truthy(lg) and truthy(lg.get("source")):
        dark_copy = f" (+ {js_str(lg['sourceDark'])})" if truthy(lg.get("sourceDark")) else ""
        lines.append(f"logo: {js_str(lg['kind'])} → {_pjoin(out, lg['source'])}{dark_copy} (usable as the kept logo source)")
    for k, hx in r["roles"].items():
        dd = (r.get("roleDetails") or {}).get(k)
        how = "kept from your earlier edit" if k in kept else (clip(dd["method"], 46) if truthy(dd) else "set by hand")
        conf = to_fixed(dd["confidence"], 2) if truthy(dd) and k not in kept else "  - "
        lines.append(f"  {pad_end(k, 10)} {js_str(hx)}  {conf}  {how}")
    if truthy(r.get("rolesDark")):
        rd = r["rolesDark"]
        lines.append("  dark: " + " · ".join(f"{k} {js_str(rd[k])}" for k in ("background", "surface", "text", "primary",
                                                                              "accent", "link") if truthy(rd.get(k))))
    confirm = [c for c in (r.get("confirm") or []) if c.split(" ")[0] not in kept]
    if confirm:
        lines.append("confirm with the user: " + "; ".join(clip(c, 70) for c in confirm))
    for n in (r.get("notes") or [])[1 if r.get("status") == "empty" else 0:]:
        lines.append(f"note: {clip(n, 170)}")
    return "\n".join(lines)


def apply_summary(items, mode):
    return "\n".join(f"apply {mode}: {js_str(i['name'])} → {i['dir']}/ (desktop, mobile, full) · contrast guard fixed "
                     f"{js_str(i['contrastGuard']['desktop'])} desktop, {js_str(i['contrastGuard']['mobile'])} mobile"
                     for i in items)


def identity_summary(items, mode):
    return "\n".join(f"apply {mode}: set {js_str(i['set'])} {js_str(i['name'])} → {i['dir']}/ (desktop, mobile, full, header) · "
                     f"layoutStress {js_str(i['layoutStress']['desktop'])} desktop, {js_str(i['layoutStress']['mobile'])} "
                     f"mobile · fonts {'loaded' if i['fontsLoaded'] else 'NOT LOADED'} · logo {jor(i['logo']['desktop'], 'kept')}"
                     for i in items)


FAMILIES = ["red", "orange", "yellow", "green", "teal", "blue", "violet", "magenta", "pink"]


def competitors_summary(S, d, out, skipped):
    """One line per competitor: domain · status · dominant colour · heading / body font. Parked, blocked and failed
    sites say so and are skipped from the hue map."""
    st_of = lambda e: jor(e.get("status"), "blocked" if truthy(e.get("blocked")) else "error")  # noqa: E731
    n = lambda st: len(d["sites"]) if st == "ok" else sum(1 for e in d["errors"] if st_of(e) == st)  # noqa: E731
    lines = [f"competitors: {n('ok')} ok, {n('parked')} parked, {n('blocked')} blocked, {n('error')} failed"
             f"{f', {skipped} skipped (--max)' if skipped else ''} → {_pjoin(out, 'competitors.json')}"]
    if d["errors"]:
        lines.append(f"{n('ok')} of {len(d['sites']) + len(d['errors'])} competitors measured; positioning falls back to "
                     "the brief for the rest")

    def fam(s):
        return clip(re.sub(r"^[\"']|[\"']\Z", "", js_trim(js_str(s).split(",")[0])), 18) if truthy(s) else "-"

    hues = {}
    for s in d["sites"]:
        roles = s["roles"]
        dom = jor(roles.get("primary"), next((c["hex"] for c in (s.get("topColors") or [])
                                             if S.eng.call("chromaOf", c["hex"]) >= 0.06), None), roles.get("background"))
        ground = "dark" if truthy(roles.get("background")) and S.eng.call("isDarkHex", roles["background"]) else "light"
        acc = f" + {roles['accent']} {S.eng.call('hueFamily', roles['accent'])}" if truthy(roles.get("accent")) else ""
        f = jor(s.get("fonts"), {})
        dom_s = f"{dom} {S.eng.call('hueFamily', dom)}" if truthy(dom) else "-"
        lines.append(f"{pad_end(clip(s['host'], 25), 26)}ok       {dom_s}{acc} · {ground} · {fam(f.get('heading'))} / "
                     f"{fam(jor(f.get('paragraph'), f.get('body')))}")
        for h in (roles.get("primary"), roles.get("accent")):
            f2 = S.eng.call("hueFamily", h)
            if truthy(f2) and f2 != "neutral":
                hues[f2] = hues.get(f2, 0) + 1
    for e in d["errors"]:
        st = st_of(e)
        lines.append(f"{pad_end(clip(jor(e.get('host'), e.get('url')), 25), 26)}{pad_end(st, 8)} "
                     f"{'failed' if st == 'error' else st}, skipped: {clip(jor(e.get('reason'), e.get('error')), 60)}")
    spread = " · ".join(f"{f} {k}" for f, k in sorted(hues.items(), key=lambda x: -x[1]))
    empty = [f for f in FAMILIES if f not in hues]
    lines.append(f"hue families (primary + accent, ok sites only): {spread or '-'}")
    lines.append(f"empty families: {', '.join(empty) or '-'}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- CLI

REPEAT = {"palette", "palettes"}


def parse_args(argv):
    o = {"_": [], "palette": [], "identity": []}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-h":
            o["help"] = True
        elif not a.startswith("--"):
            o["_"].append(a)
        else:
            k, eq, v = a[2:].partition("=")
            if not eq:
                if i + 1 < len(argv) and argv[i + 1] and not argv[i + 1].startswith("--"):
                    i += 1
                    v = argv[i]
                else:
                    v = True
            if k in REPEAT or k in ("identity", "identities"):
                dest = o["palette"] if k in REPEAT else o["identity"]
                dest.extend(x for x in js_str(v).split(",") if x)
                while i + 1 < len(argv) and argv[i + 1] and not argv[i + 1].startswith("--") and argv[i + 1].endswith(".json"):
                    i += 1
                    dest.append(argv[i])
            else:
                o[k] = v
        i += 1
    return o


def _valid_url_shape(u):
    try:
        x = urllib.parse.urlsplit(u)
    except ValueError:
        return False
    return x.scheme.lower() in ("http", "https", "file") and (x.scheme.lower() == "file" or bool(x.netloc))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    a = parse_args(sys.argv[1:] if argv is None else argv)
    cmd, rest = (a["_"][0] if a["_"] else None), a["_"][1:]
    if not cmd or a.get("help") or cmd == "help":
        print(__doc__.strip())
        return 0
    browser = None
    try:
        mode = a.get("mode") or "light"
        if mode not in ("light", "dark"):
            raise Die("--mode must be light or dark")
        if cmd == "check":
            info = cmd_check()
            print(js_json(info))
            return 0 if info["ok"] else 3
        if cmd not in ("extract", "apply", "competitors"):
            raise Die(f"unknown command {cmd} (extract | apply | competitors | check)")
        if not rest:
            raise Die(f"{cmd} needs a URL")
        if not a.get("out") or a["out"] is True:
            raise Die("--out DIR is required")
        for u in rest:
            if not _valid_url_shape(u):
                raise Die(f"not a valid http(s) or file URL: {u}")
        if cmd == "apply" and not a["palette"] and not a["identity"]:
            raise Die("apply needs --identity sets/X/identity.json or --palette palette.json")
        if cmd == "apply" and a["palette"] and a["identity"]:
            raise Die("apply takes --identity or --palette, not both")
        for p in a["palette"]:
            if not os.path.exists(p):
                raise Die(f"palette not found: {p}")
        for p in a["identity"]:
            if not os.path.exists(p):
                raise Die(f"identity not found: {p}")
        browser, errors = cdplib.launch()
        if not browser:
            raise Die("no usable Chromium-based browser.\nTried:\n  " + ("\n  ".join(errors) or "(none found)") +
                      "\nInstall Chrome or Chromium, or set BRAND_IDENTITY_BROWSER=/path/to/chrome.\n" + NO_SITE_FALLBACK, 4)
        S = Session(browser)
        if a.get("verbose"):
            log(f"browser: {browser.executable} ({browser.version}), DevTools protocol driver")
        urls = []
        for u in rest:
            href = S.eng.call("checkUrl", u)
            if href is None:
                raise Die(f"not a valid http(s) or file URL: {u}")
            urls.append(href)
        out = a["out"]
        if cmd == "extract":
            r = cmd_extract(S, urls[0], out, mode, dark="only" if a.get("dark-only") else "never" if a.get("no-dark") else "auto")
            summary = {"command": "extract", "out": os.path.abspath(out), "roles": r["roles"], "confirm": r.get("confirm", UNDEFINED),
                       "notes": r.get("notes", UNDEFINED), "strategy": r.get("strategy", UNDEFINED)}
            text = extract_summary(S, r, out)
        elif cmd == "apply" and a["identity"]:
            items = cmd_apply_identity(S, urls[0], out, a["identity"], a.get("roles") if a.get("roles") is not True else None, mode)
            summary = {"command": "apply", "out": os.path.abspath(out), "mode": mode, "sets": items}
            text = identity_summary(items, mode)
        elif cmd == "apply":
            items = cmd_apply(S, urls[0], out, a["palette"], a.get("roles") if a.get("roles") is not True else None, mode,
                              not truthy(a.get("recolor-logo")))
            summary = {"command": "apply", "out": os.path.abspath(out), "mode": mode, "palettes": items}
            text = apply_summary(items, mode)
        else:
            mx = 8 if "max" not in a else js_number_of(a["max"])
            if not (mx >= 1):
                raise Die("--max must be a positive number")
            take = urls[:int(mx) if mx != float("inf") else len(urls)]
            conc = js_number_of(a.get("concurrency"))
            d = cmd_competitors(S, take, out, int(conc) if truthy(conc) and conc != float("inf") else 3)
            summary = {"command": "competitors", "out": os.path.abspath(out), "ok": len(d["sites"]),
                       "failed": [s["url"] for s in d["errors"]],
                       "blocked": [s["url"] for s in d["errors"] if s["status"] == "blocked"],
                       "parked": [s["url"] for s in d["errors"] if s["status"] == "parked"],
                       "skipped": urls[len(take):]}
            text = competitors_summary(S, d, out, len(urls) - len(take))
    except Blocked as e:
        try:
            os.makedirs(a["out"], exist_ok=True)
            write_json(os.path.join(a["out"], "blocked.json"), {"schema": "brand-identity/blocked@1", "url": e.url,
                                                                "blocked": True, "reason": e.reason, "at": now_iso()})
        except (OSError, TypeError):
            pass
        log(f"error: {e}")
        return 5
    except Die as e:
        log(f"error: {e}")
        return e.code
    except Exception as e:  # noqa: BLE001
        log(f"error: {e}")
        return 1
    finally:
        # also on Ctrl+C / SystemExit: a browser on the websocket port (Windows) would outlive us otherwise
        if browser:
            browser.close()
    print(_surrogates(js_json(summary) if truthy(a.get("json")) else text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
