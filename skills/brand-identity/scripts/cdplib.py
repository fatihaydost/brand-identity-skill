"""A small Chrome DevTools protocol driver (standard library only) for the live-site tool (site_palette.py).

It does what the former Node tool got from playwright-core, with the same Chrome settings, so the pages render and
measure the same way:

  launch()            Chrome with Playwright's switches (headless, --hide-scrollbars, hover/pointer blink settings,
                      --force-color-profile=srgb, CDPScreenshotNewSurface, ...) except --no-sandbox; a pipe on
                      Linux and macOS, the websocket port on Windows
  Browser.new_page()  an incognito browser context per page (Target.createBrowserContext), emulated before its
                      first load: viewport and screen, device scale, mobile + touch, user agent and client hints,
                      locale en-US, prefers-color-scheme / prefers-reduced-motion, bypass CSP, ignore HTTPS errors,
                      focus emulation, Playwright's default font families for headless
  Page.goto()         Page.navigate + commit + DOMContentLoaded, main-document response (status, headers)
  Page.wait_for_load_state("networkidle")   Playwright's per-frame model: a frame is idle 500 ms after its last
                      request ends; the page is idle when every frame is; the state is sticky until the next commit
  Page.evaluate()     a function's source called with an argument in the page's main world; values cross as a
                      tree that keeps NaN, Infinity, -0 and undefined (like Playwright's serializer)
  Page.screenshot()   viewport / full page with a clip, element shots, raw clips; animations finished or cancelled
                      and carets hidden in every frame first (Playwright's screenshotter), fonts awaited
  Page.route_stylesheets(fn)   stylesheet responses rewritten before the page sees them (Fetch, response stage)
  Page.stylesheet_texts()      bodies of the stylesheets the page loaded (what page.on('response') collected)

Out-of-process iframes and workers are attached and set up like the page (Target.setAutoAttach), as Playwright does.
docs/architecture.md section 4.6 has the Playwright-to-CDP table.
"""
import base64
import contextlib
import json
import math
import os
import re
import select
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import render_png  # noqa: E402  (browser discovery, temp profiles, process stop)

UTILITY_WORLD = "__bi_utility_world__"
_TRACE = open(os.environ["BI_CDP_TRACE"], "a", encoding="utf-8") if os.environ.get("BI_CDP_TRACE") else None
IDLE_MS = 500

# Playwright 1.63 chromiumSwitches + headless arguments (server/chromium/chromiumSwitches.ts, chromium.ts).
DISABLED_FEATURES = ("AvoidUnnecessaryBeforeUnloadCheckSync,DestroyProfileOnBrowserClose,DialMediaRouteProvider,"
                     "GlobalMediaControls,HttpsUpgrades,LensOverlay,MediaRouter,PaintHolding,"
                     "ThirdPartyStoragePartitioning,BlockOriginHeaderModificationOnRedirect,Translate,AutoDeElevate,"
                     "OptimizationHints,msForceBrowserSignIn,msEdgeUpdateLaunchServicesPreferredVersion")
SWITCHES = [
    "--disable-field-trial-config", "--disable-background-networking", "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows", "--disable-back-forward-cache", "--disable-breakpad",
    "--disable-client-side-phishing-detection", "--disable-component-extensions-with-background-pages",
    "--disable-component-update", "--no-default-browser-check", "--disable-default-apps", "--disable-dev-shm-usage",
    "--disable-edgeupdater", "--disable-extensions", "--disable-features=" + DISABLED_FEATURES,
    "--enable-features=CDPScreenshotNewSurface", "--allow-pre-commit-input", "--disable-hang-monitor",
    "--disable-ipc-flooding-protection", "--disable-popup-blocking", "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding", "--disable-updater-scheduler", "--force-color-profile=srgb",
    "--metrics-recording-only", "--no-first-run", "--password-store=basic", "--use-mock-keychain",
    "--no-service-autorun", "--export-tagged-pdf", "--disable-search-engine-choice-screen",
    "--unsafely-disable-devtools-self-xss-warnings", "--edge-skip-compat-layer-relaunch", "--disable-infobars",
    "--disable-search-engine-choice-screen", "--disable-sync", "--enable-unsafe-swiftshader", "--headless",
    "--hide-scrollbars", "--mute-audio",
    "--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4",
]
# Playwright also passes --no-sandbox. Here the sandbox stays on (these are third-party pages) and --no-sandbox is
# added only when the browser cannot start with it (render_png.start_browser: containers without user namespaces).

# Playwright's default font families for headless Chromium (server/chromium/defaultFontFamilies.ts).
FONT_FAMILIES = {
    "linux": {"fontFamilies": {"standard": "Times New Roman", "fixed": "Monospace", "serif": "Times New Roman",
                               "sansSerif": "Arial", "cursive": "Comic Sans MS", "fantasy": "Impact"}},
    "mac": {"fontFamilies": {"standard": "Times", "fixed": "Courier", "serif": "Times", "sansSerif": "Helvetica",
                             "cursive": "Apple Chancery", "fantasy": "Papyrus"},
            "forScripts": [
                {"script": "jpan", "fontFamilies": {"standard": "Hiragino Kaku Gothic ProN", "fixed": "Osaka-Mono",
                                                    "serif": "Hiragino Mincho ProN",
                                                    "sansSerif": "Hiragino Kaku Gothic ProN"}},
                {"script": "hang", "fontFamilies": {"standard": "Apple SD Gothic Neo", "serif": "AppleMyungjo",
                                                    "sansSerif": "Apple SD Gothic Neo"}},
                {"script": "hans", "fontFamilies": {"standard": ",PingFang SC,STHeiti", "serif": "Songti SC",
                                                    "sansSerif": ",PingFang SC,STHeiti", "cursive": "Kaiti SC"}},
                {"script": "hant", "fontFamilies": {"standard": ",PingFang TC,Heiti TC", "serif": "Songti TC",
                                                    "sansSerif": ",PingFang TC,Heiti TC", "cursive": "Kaiti TC"}}]},
    "win": {"fontFamilies": {"standard": "Times New Roman", "fixed": "Consolas", "serif": "Times New Roman",
                             "sansSerif": "Arial", "cursive": "Comic Sans MS", "fantasy": "Impact"},
            "forScripts": [
                {"script": "cyrl", "fontFamilies": {"standard": "Times New Roman", "fixed": "Courier New",
                                                    "serif": "Times New Roman", "sansSerif": "Arial"}},
                {"script": "arab", "fontFamilies": {"fixed": "Courier New", "sansSerif": "Segoe UI"}},
                {"script": "grek", "fontFamilies": {"standard": "Times New Roman", "fixed": "Courier New",
                                                    "serif": "Times New Roman", "sansSerif": "Arial"}},
                {"script": "jpan", "fontFamilies": {"standard": ",Meiryo,Yu Gothic", "fixed": "MS Gothic",
                                                    "serif": ",Yu Mincho,MS PMincho", "sansSerif": ",Meiryo,Yu Gothic"}},
                {"script": "hang", "fontFamilies": {"standard": "Malgun Gothic", "fixed": "Gulimche",
                                                    "serif": "Batang", "sansSerif": "Malgun Gothic",
                                                    "cursive": "Gungsuh"}},
                {"script": "hans", "fontFamilies": {"standard": "Microsoft YaHei", "fixed": "NSimsun",
                                                    "serif": "Simsun", "sansSerif": "Microsoft YaHei",
                                                    "cursive": "KaiTi"}},
                {"script": "hant", "fontFamilies": {"standard": "Microsoft JhengHei", "fixed": "MingLiU",
                                                    "serif": "PMingLiU", "sansSerif": "Microsoft JhengHei",
                                                    "cursive": "DFKai-SB"}}]},
}


class CDPError(RuntimeError):
    """A protocol call failed; `.message` is the browser's text."""


class BrowserExited(CDPError, render_png.StartupExit):
    """The browser process exited during start-up."""


class NavigationError(RuntimeError):
    """page.goto failed; the message reads like Playwright's (`page.goto: net::ERR_... at URL`). `detail` says, for a
    timeout, which step never finished and what the page had reached by then (one line, for diagnosis)."""

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail


# ----------------------------------------------------------------------------- values across the protocol

class _Undefined:
    """JavaScript undefined coming back from a page: dropped from objects, null in arrays (JSON.stringify)."""
    __slots__ = ()

    def __bool__(self):
        return False

    def __repr__(self):
        return "UNDEFINED"


UNDEFINED = _Undefined()

# JS side of the bridge: SER turns any value into the tree, REV turns a tree back into a value.
BRIDGE_SER = r"""(function __biSer(v) {
  if (v === undefined || typeof v === 'symbol' || typeof v === 'function') return { s: 'undefined' };
  if (v === null || typeof v === 'boolean' || typeof v === 'string') return v;
  if (typeof v === 'number') {
    if (v !== v) return { s: 'NaN' };
    if (v === Infinity) return { s: 'Infinity' };
    if (v === -Infinity) return { s: '-Infinity' };
    if (v === 0 && 1 / v < 0) return { s: '-0' };
    return v;
  }
  if (typeof v === 'bigint') return v.toString();
  if (typeof Node === 'function' && v instanceof Node) return 'ref: <Node>';
  if (Array.isArray(v)) { const a = []; for (let i = 0; i < v.length; i++) a.push(__biSer(v[i])); return { a }; }
  if (typeof v === 'object') {
    const o = [];
    for (const k of Object.keys(v)) { let x; try { x = v[k]; } catch (e) { continue; } o.push([k, __biSer(x)]); }
    return { o };
  }
  return { s: 'undefined' };
})"""
BRIDGE_REV = r"""(function __biRev(t) {
  if (t === null || typeof t !== 'object') return t;
  if ('s' in t) return t.s === 'NaN' ? NaN : t.s === 'Infinity' ? Infinity : t.s === '-Infinity' ? -Infinity : t.s === '-0' ? -0 : undefined;
  if ('a' in t) { const r = []; for (const x of t.a) r.push(__biRev(x)); return r; }
  const r = {};
  for (const [k, x] of t.o) { if (k === '__proto__') continue; r[k] = __biRev(x); }
  return r;
})"""


def to_tree(v):
    """Python value -> bridge tree (float('nan') -> NaN, -0.0 -> -0, UNDEFINED -> undefined)."""
    if v is UNDEFINED:
        return {"s": "undefined"}
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if math.isnan(v):
            return {"s": "NaN"}
        if math.isinf(v):
            return {"s": "Infinity" if v > 0 else "-Infinity"}
        if v == 0 and math.copysign(1.0, v) < 0:
            return {"s": "-0"}
        return v
    if isinstance(v, (list, tuple)):
        return {"a": [to_tree(x) for x in v]}
    if isinstance(v, dict):
        return {"o": [[str(k), to_tree(x)] for k, x in v.items()]}
    raise TypeError(f"cannot send {type(v).__name__} to the page")


_SPECIAL = {"NaN": float("nan"), "Infinity": float("inf"), "-Infinity": float("-inf"), "-0": -0.0}


def from_tree(t):
    """Bridge tree -> Python value (JS object key order kept)."""
    if isinstance(t, dict):
        if "s" in t:
            return _SPECIAL.get(t["s"], UNDEFINED)
        if "a" in t:
            return [from_tree(x) for x in t["a"]]
        return {k: from_tree(x) for k, x in t["o"]}
    return t


def call_expression(fn_source, arg):
    """`(fn)(arg)` for Runtime.evaluate: the function's own source, the argument revived from its tree, the result
    serialized to a tree (awaited when it is a promise). The page's globals are not touched."""
    arg_json = json.dumps(to_tree(arg))
    return (f"(async () => {{ const __biSer = {BRIDGE_SER}; const __biRev = {BRIDGE_REV};\n"
            f"const __biFn = ({fn_source});\nreturn __biSer(await __biFn(__biRev({arg_json})));\n}})()")


# ----------------------------------------------------------------------------- JSON exactly as JSON.stringify writes it

def js_number(x):
    """Number.prototype.toString for a finite double (ECMAScript Number::toString): shortest round-trip digits,
    decimal notation for 1e-7 < |x| < 1e21, else exponent form."""
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, int):
        if abs(x) <= 2 ** 53:
            return str(x)
        x = float(x)
    if x != x:
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    mant, _, exp = repr(abs(x)).partition("e")  # repr = shortest round-trip digits, as ECMAScript requires
    ip, _, fp = mant.partition(".")
    all_digits = ip + fp
    lead = len(all_digits) - len(all_digits.lstrip("0"))
    s = all_digits.lstrip("0").rstrip("0")
    k = len(s)
    n = len(ip) - lead + (int(exp) if exp else 0)  # value = 0.s * 10^n
    if k <= n <= 21:
        return sign + s + "0" * (n - k)
    if 0 < n <= 21:
        return sign + s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + s
    e = n - 1
    return sign + (s if k == 1 else s[0] + "." + s[1:]) + "e" + ("+" if e >= 0 else "-") + str(abs(e))


_ESC = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_NEEDS_ESC = re.compile(r'["\\\x00-\x1f\ud800-\udfff]')


def js_string(s):
    """JSON.stringify of a string: control characters and lone surrogates as \\u00xx / \\udxxx, nothing else."""
    return '"' + _NEEDS_ESC.sub(lambda m: _ESC.get(m.group(0)) or "\\u%04x" % ord(m.group(0)), s) + '"'


def js_json(v, indent=2, _level=0):
    """JSON.stringify(v, null, indent) for values from json.loads / from_tree: same spacing, number and string
    formatting, NaN/Infinity as null, undefined members dropped (null in arrays)."""
    if v is None or v is UNDEFINED:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return "null" if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else js_number(v)
    if isinstance(v, str):
        return js_string(v)
    pad = "\n" + " " * (indent * (_level + 1)) if indent else ""
    end = "\n" + " " * (indent * _level) if indent else ""
    if isinstance(v, (list, tuple)):
        if not v:
            return "[]"
        return "[" + ",".join(pad + js_json(x, indent, _level + 1) for x in v) + end + "]"
    if isinstance(v, dict):
        items = [(k, x) for k, x in v.items() if x is not UNDEFINED and not callable(x)]
        if not items:
            return "{}"
        sep = ": " if indent else ":"
        return "{" + ",".join(pad + js_string(str(k)) + sep + js_json(x, indent, _level + 1) for k, x in items) + end + "}"
    raise TypeError(f"not JSON: {type(v).__name__}")


# ----------------------------------------------------------------------------- websocket

class WebSocket:
    """RFC 6455 client for a local DevTools socket: text frames, client masking, non-blocking frame parsing (a
    timeout never leaves a half-read frame behind)."""

    def __init__(self, url, timeout=30):
        m = re.match(r"ws://([^/:]+):(\d+)(/.*)", url)
        if not m:
            raise CDPError(f"unexpected DevTools URL: {url}")
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("DevTools closed the connection during the handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise CDPError("DevTools refused the websocket handshake")
        self.buf = bytearray(head.split(b"\r\n\r\n", 1)[1])
        self.parts = []
        self.sock.setblocking(False)

    def send(self, text):
        data = text.encode("utf-8")
        n = len(data)
        if n < 126:
            header = struct.pack(">BB", 0x81, 0x80 | n)
        elif n < 65536:
            header = struct.pack(">BBH", 0x81, 0x80 | 126, n)
        else:
            header = struct.pack(">BBQ", 0x81, 0x80 | 127, n)
        mask = os.urandom(4)
        masked = (int.from_bytes(data, "big") ^ int.from_bytes((mask * (n // 4 + 1))[:n], "big")).to_bytes(n, "big") \
            if n else b""
        view = memoryview(header + mask + masked)
        while view:
            try:
                sent = self.sock.send(view)
            except (BlockingIOError, InterruptedError):
                select.select([], [self.sock], [], 5)
                continue
            view = view[sent:]

    def poll(self, timeout):
        """Messages that arrived within `timeout` seconds (possibly none)."""
        out = self._parse()
        if out:
            return out
        r, _, _ = select.select([self.sock], [], [], max(0.0, timeout))
        if not r:
            return []
        try:
            chunk = self.sock.recv(1 << 20)
        except (BlockingIOError, InterruptedError):
            return []
        if not chunk:
            raise CDPError("DevTools connection closed")
        self.buf += chunk
        while True:  # drain what is already there without waiting
            try:
                chunk = self.sock.recv(1 << 20)
            except (BlockingIOError, InterruptedError):
                break
            if not chunk:
                break
            self.buf += chunk
        return self._parse()

    def _parse(self):
        out = []
        while True:
            b = self.buf
            if len(b) < 2:
                return out
            fin, opcode, n, masked, pos = b[0] & 0x80, b[0] & 0x0F, b[1] & 0x7F, b[1] & 0x80, 2
            if n == 126:
                if len(b) < 4:
                    return out
                n, pos = struct.unpack(">H", bytes(b[2:4]))[0], 4
            elif n == 127:
                if len(b) < 10:
                    return out
                n, pos = struct.unpack(">Q", bytes(b[2:10]))[0], 10
            mask = None
            if masked:
                if len(b) < pos + 4:
                    return out
                mask, pos = bytes(b[pos:pos + 4]), pos + 4
            if len(b) < pos + n:
                return out
            payload = bytes(b[pos:pos + n])
            del self.buf[:pos + n]
            if mask:
                payload = (int.from_bytes(payload, "big") ^ int.from_bytes((mask * (n // 4 + 1))[:n], "big")).to_bytes(n, "big")
            if opcode == 0x8:
                raise CDPError("DevTools closed the connection")
            if opcode in (0x9, 0xA):
                continue
            self.parts.append(payload)
            if fin:
                out.append(b"".join(self.parts).decode("utf-8", errors="replace"))
                self.parts = []

    def close(self):
        with contextlib.suppress(Exception):
            self.sock.close()


class PipeTransport:
    """--remote-debugging-pipe (POSIX): JSON messages separated by NUL on the child's fds 3 (in) and 4 (out), the
    transport Playwright uses. Chrome renders measurably differently over the websocket port (the enumeration order
    of custom properties and image rasterization differ), so the pipe is used wherever it is available."""

    def __init__(self, read_fd, write_fd):
        self.r, self.w, self.buf = read_fd, write_fd, b""
        os.set_blocking(self.r, False)
        os.set_blocking(self.w, False)

    def send(self, text):
        view = memoryview(text.encode("utf-8") + b"\0")
        while view:
            try:
                n = os.write(self.w, view)
            except (BlockingIOError, InterruptedError):
                select.select([], [self.w], [], 5)
                continue
            except OSError as exc:
                raise CDPError(f"DevTools pipe closed ({exc})") from None
            view = view[n:]

    def poll(self, timeout):
        out = self._parse()
        if out:
            return out
        r, _, _ = select.select([self.r], [], [], max(0.0, timeout))
        if not r:
            return []
        got = False
        while True:
            try:
                chunk = os.read(self.r, 1 << 20)
            except (BlockingIOError, InterruptedError):
                break
            if not chunk:
                if not got and not self.buf:
                    raise CDPError("DevTools pipe closed")
                break
            got = True
            self.buf += chunk
        return self._parse()

    def _parse(self):
        out = []
        while b"\0" in self.buf:
            msg, self.buf = self.buf.split(b"\0", 1)
            out.append(msg.decode("utf-8", errors="replace"))
        return out

    def close(self):
        if self.r is None:
            return
        for fd in (self.r, self.w):
            with contextlib.suppress(OSError):
                os.close(fd)
        self.r = self.w = None


# ----------------------------------------------------------------------------- connection and event loop

class Connection:
    """One browser websocket, flat sessions. Single-threaded: waiting for a reply pumps events, fires timers and runs
    deferred tasks (stylesheet rewrites) one at a time."""

    def __init__(self, transport):
        self.ws = transport if not isinstance(transport, str) else WebSocket(transport)
        self.next_id = 0
        self.replies = {}
        self.listeners = {}      # (session id, or None for the browser, method) -> [callbacks]
        self.timers = []         # [deadline, seq, callback, alive]
        self.tasks = []
        self.in_task = False
        self._seq = 0

    # -- protocol
    def send(self, method, params=None, session=None):
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        if _TRACE:
            _TRACE.write(f"{time.monotonic():.3f} SEND {json.dumps(msg)[:400]}\n")
        self.ws.send(json.dumps(msg))
        return self.next_id

    def call(self, method, params=None, session=None, timeout=60):
        mid = self.send(method, params, session)
        deadline = time.monotonic() + timeout
        while mid not in self.replies:
            left = deadline - time.monotonic()
            if left <= 0:
                raise CDPError(f"{method} timed out after {timeout} s")
            self.pump(left)
        msg = self.replies.pop(mid)
        if "error" in msg:
            raise CDPError(f"{method}: {msg['error'].get('message')}")
        return msg.get("result", {})

    def try_call(self, method, params=None, session=None, timeout=60):
        try:
            return self.call(method, params, session, timeout)
        except CDPError:
            return None

    def on(self, method, callback, session=None):
        self.listeners.setdefault((session, method), []).append(callback)

    def off_session(self, session):
        for key in [k for k in self.listeners if k[0] == session]:
            del self.listeners[key]

    # -- timers and deferred work
    def add_timer(self, delay, callback):
        self._seq += 1
        t = [time.monotonic() + delay, self._seq, callback, True]
        self.timers.append(t)
        return t

    @staticmethod
    def cancel_timer(t):
        if t:
            t[3] = False

    def defer(self, fn):
        self.tasks.append(fn)

    def _fire_timers(self):
        now = time.monotonic()
        due = sorted((t for t in self.timers if t[3] and t[0] <= now), key=lambda t: (t[0], t[1]))
        self.timers = [t for t in self.timers if t[3] and t[0] > now]
        for t in due:
            t[3] = False
            t[2]()

    def _run_tasks(self):
        if self.in_task:
            return
        while self.tasks:
            fn = self.tasks.pop(0)
            self.in_task = True
            try:
                fn()
            finally:
                self.in_task = False

    def pump(self, timeout):
        """Handle what arrives within `timeout` seconds (shortened to the next timer)."""
        self._run_tasks()
        live = [t[0] for t in self.timers if t[3]]
        wait = min([timeout] + [max(0.0, d - time.monotonic()) for d in live])
        for raw in self.ws.poll(wait):
            msg = json.loads(raw)
            if _TRACE:
                _TRACE.write(f"{time.monotonic():.3f} RECV {raw[:300]}\n")
            if "id" in msg:
                self.replies[msg["id"]] = msg
                continue
            method, session = msg.get("method"), msg.get("sessionId")
            for cb in list(self.listeners.get((session, method), ())):
                cb(msg.get("params") or {}, session)
        self._fire_timers()
        self._run_tasks()

    def wait(self, predicate, timeout):
        """Pump until predicate() is true; False on timeout."""
        deadline = time.monotonic() + timeout
        while not predicate():
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            self.pump(min(left, 0.25))
        return True

    def sleep(self, seconds):
        deadline = time.monotonic() + seconds
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            self.pump(left)

    def close(self):
        self.ws.close()


# ----------------------------------------------------------------------------- browser

def browser_candidates():
    """Browsers in the order the Node tool tried them: BRAND_IDENTITY_BROWSER / CHROMIUM_PATH, any Chromium in the
    Playwright cache (newest revision, full before headless shell), then installed Chrome / Edge / Brave / Chromium."""
    out = []
    env = os.environ.get("BRAND_IDENTITY_BROWSER") or os.environ.get("CHROMIUM_PATH")
    if env:
        out.append(env)
    shells = render_png.playwright_browsers(headless_shell=True)
    fulls = render_png.playwright_browsers()
    revs = sorted({render_png._rev(p) for p in fulls + shells}, reverse=True)
    for r in revs:
        out += [p for p in fulls if render_png._rev(p) == r] + [p for p in shells if render_png._rev(p) == r]
    if sys.platform == "darwin":
        out += [f"/Applications/{a}.app/Contents/MacOS/{a}" for a in ("Google Chrome", "Chromium", "Microsoft Edge",
                                                                       "Brave Browser")]
    elif os.name == "nt":
        for b in filter(None, (os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"))):
            out += [os.path.join(b, "Google", "Chrome", "Application", "chrome.exe"),
                    os.path.join(b, "Microsoft", "Edge", "Application", "msedge.exe"),
                    os.path.join(b, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                    os.path.join(b, "Chromium", "Application", "chrome.exe")]
    else:
        for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
                  "microsoft-edge-stable", "brave", "brave-browser"):
            for d in os.environ.get("PATH", "").split(os.pathsep):
                out.append(os.path.join(d, n))
    seen, found = set(), []
    for p in out:
        try:
            if not os.path.isfile(p):
                continue
            real = os.path.realpath(p)
        except OSError:
            continue
        if real not in seen:
            seen.add(real)
            found.append(p)
    return found


def _platform(user_agent):
    if "Windows" in user_agent:
        return "win"
    if "Macintosh" in user_agent:
        return "mac"
    return "linux"


def ua_metadata(ua, mobile):
    """Client hints for a user-agent override (Playwright's calculateUserAgentMetadata)."""
    md = {"mobile": bool(mobile), "model": "", "architecture": "x86", "platform": "Windows", "platformVersion": ""}
    for pat, plat, arm in ((r"Android (\d+(\.\d+)?(\.\d+)?)", "Android", True), (r"iPhone OS (\d+(_\d+)?)", "iOS", True),
                           (r"iPad; CPU OS (\d+(_\d+)?)", "iOS", True)):
        m = re.search(pat, ua)
        if m:
            md.update(platform=plat, platformVersion=m.group(1))
            if arm:
                md["architecture"] = "arm"
            break
    else:
        m = re.search(r"Mac OS X (\d+(_\d+)?(_\d+)?)", ua)
        w = re.search(r"Windows\D+(\d+(\.\d+)?(\.\d+)?)", ua)
        if m:
            md.update(platform="macOS", platformVersion=m.group(1))
            if "Intel" not in ua:
                md["architecture"] = "arm"
        elif w:
            md.update(platform="Windows", platformVersion=w.group(1))
        elif "linux" in ua.lower():
            md["platform"] = "Linux"
    if "ARM" in ua:
        md["architecture"] = "arm"
    return md


class Browser:
    """A launched Chromium-based browser. On Linux and macOS it is driven over --remote-debugging-pipe with
    Playwright's command line (one client); on Windows over the websocket port, where any number of clients
    (one websocket each) can attach. Threads need a client each (Connection is single-threaded).
    The sandbox is on unless the browser only starts without it (`no_sandbox`, see render_png.start_browser)."""

    def __init__(self, executable, timeout=60, pipe=None):
        self.executable = executable
        self._tmp = tempfile.mkdtemp(prefix="bi-site-")
        if pipe is None:  # BRAND_IDENTITY_CDP=websocket runs the Windows transport anywhere (tests, diagnosis)
            pipe = os.name == "posix" and os.environ.get("BRAND_IDENTITY_CDP", "").strip().lower() != "websocket"
        self.pipe = pipe
        self.clients = []
        self.created = {}    # target id -> the client that made it
        self.lock = threading.RLock()
        self._transport = None
        self.proc = self.main = None
        self._attempt = 0
        try:
            _, self.no_sandbox = render_png.start_browser(executable, lambda ns: self._start(ns, timeout))
        except BaseException:
            self.close()
            raise

    def _start(self, no_sandbox, timeout):
        """One start: process, first client, Browser.getVersion. BrowserExited when the process dies on the way."""
        self._attempt += 1
        profile = os.path.join(self._tmp, f"profile-{self._attempt}")
        extra = ["--no-sandbox"] if no_sandbox else []
        try:
            if self.pipe:
                self._start_pipe(profile, extra)
                self.ws_url = None
            else:
                # the browser socket path is line 2 of DevToolsActivePort: no HTTP request (and no proxy) involved
                self.proc = render_png.spawn([self.executable, *SWITCHES, *extra, f"--user-data-dir={profile}",
                                              "--remote-debugging-port=0", "about:blank"], profile)
                port, path = self._port(profile, timeout)
                self.ws_url = f"ws://127.0.0.1:{port}{path}"
            self.main = self.client()
            info = self.main.conn.call("Browser.getVersion", timeout=timeout)
        except Exception as exc:
            gone = False
            if self.proc is not None:
                try:
                    self.proc.wait(timeout=2)
                    gone = True
                except subprocess.TimeoutExpired:
                    pass
            err = render_png.exited(self.executable, self.proc, profile) if gone else None
            self._shutdown()
            if err:
                raise BrowserExited(str(err)) from exc
            raise
        self.version = info.get("product", "")
        self.user_agent = info.get("userAgent", "")
        self.platform = _platform(self.user_agent)

    def _start_pipe(self, profile, extra=()):
        to_chrome_r, to_chrome_w = os.pipe()
        from_chrome_r, from_chrome_w = os.pipe()

        def fds():  # in the child: our pipe ends become fds 3 (commands in) and 4 (replies out)
            a, b = os.dup(to_chrome_r), os.dup(from_chrome_w)
            os.dup2(a, 3)
            os.dup2(b, 4)
            os.set_inheritable(3, True)
            os.set_inheritable(4, True)

        cmd = [self.executable, *SWITCHES, *extra, f"--user-data-dir={profile}", "--remote-debugging-pipe",
               "--no-startup-window"]
        try:
            self.proc = render_png.spawn(cmd, profile, preexec_fn=fds, close_fds=False)
        except BaseException:
            for fd in (to_chrome_w, from_chrome_r):
                os.close(fd)
            raise
        finally:
            os.close(to_chrome_r)
            os.close(from_chrome_w)
        self._transport = PipeTransport(from_chrome_r, to_chrome_w)

    def _port(self, profile, timeout):
        """(port, browser websocket path) from DevToolsActivePort."""
        port_file = os.path.join(profile, "DevToolsActivePort")
        deadline = time.monotonic() + min(timeout, 30)
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise render_png.exited(self.executable, self.proc, profile)
            try:
                with open(port_file, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().split()
            except OSError:  # Windows: Chrome still holds the file while writing it
                lines = []
            if len(lines) >= 2 and lines[0].isdigit() and lines[1].startswith("/devtools/browser/"):
                return int(lines[0]), lines[1]
            time.sleep(0.05)
        raise CDPError(f"{self.executable} did not open a DevTools port")

    def foreign(self, client):
        """Target ids made by other clients."""
        with self.lock:
            return {t for t, c in self.created.items() if c is not client}

    def client(self):
        """A new client. Over the pipe there is exactly one; use another Browser for parallel work."""
        if self.pipe and self.clients:
            raise CDPError("a pipe-driven browser has a single client")
        c = Client(self)
        self.clients.append(c)
        return c

    def new_page(self, **opts):
        return self.main.new_page(**opts)

    def _shutdown(self):
        """Close every client and stop the process (the temp folder stays for another start)."""
        main, self.main = self.main, None
        if main and self.proc is not None:
            with contextlib.suppress(Exception):
                main.conn.send("Browser.close")
                deadline = time.monotonic() + 5
                while self.proc.poll() is None and time.monotonic() < deadline:
                    main.conn.pump(0.05)
        for c in self.clients:
            with contextlib.suppress(Exception):
                c.conn.close()
        self.clients = []
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            render_png._stop(proc)

    def close(self):
        self._shutdown()
        import shutil
        for _ in range(10):
            shutil.rmtree(self._tmp, ignore_errors=True)
            if not os.path.exists(self._tmp):
                break
            time.sleep(0.3)


class Client:
    """One websocket to the browser; makes pages (each in its own incognito context unless context=False).

    Like Playwright, new pages are auto-attached paused (Target.setAutoAttach, waitForDebuggerOnStart) so every
    emulation is in place before their first document; pages made by another client are let go at once.

    Every client is auto-attached to every new page, and Chrome holds a new page's navigations until each of them
    has let it go. So a client must keep reading its socket while other clients work (`idle`), or be closed
    (`close`): a client nobody pumps stalls every later page of the other clients (page.goto times out)."""

    def __init__(self, browser):
        self.browser = browser
        self.conn = Connection(browser._transport if browser.pipe else browser.ws_url)
        self.attached = {}   # target id -> session id
        self.mine = set()    # target ids this client created
        self.conn.on("Target.attachedToTarget", self._on_attached)
        self.conn.call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True})

    def _on_attached(self, p, _session):
        info, sid = p.get("targetInfo") or {}, p.get("sessionId")
        tid = info.get("targetId")
        if info.get("type") == "page" and (tid in self.mine or not info.get("browserContextId")
                                            or tid not in self.browser.foreign(self)):
            self.attached[tid] = sid
            return
        self.conn.send("Runtime.runIfWaitingForDebugger", session=sid)
        self.conn.send("Target.detachFromTarget", {"sessionId": sid})

    def idle(self, until, step=0.1):
        """Keep answering the browser (letting other clients' new pages go) until until() is true."""
        while not until():
            self.conn.pump(step)

    def close(self):
        """Disconnect a websocket client (not the browser's main client); Chrome drops its auto-attachments."""
        if self.browser.pipe or self is self.browser.main:
            raise CDPError("only an extra websocket client can be closed")
        with self.browser.lock:
            for tid in [t for t, c in self.browser.created.items() if c is self]:
                del self.browser.created[tid]
        if self in self.browser.clients:
            self.browser.clients.remove(self)
        self.conn.close()

    def new_page(self, viewport=(1280, 720), device_scale_factor=1, mobile=False, has_touch=False, user_agent=None,
                 color_scheme="light", reduced_motion="reduce", locale="en-US", bypass_csp=True,
                 ignore_https_errors=True, context=True):
        ctx = None
        if context:
            ctx = self.conn.call("Target.createBrowserContext", {"disposeOnDetach": True})["browserContextId"]
            self.conn.try_call("Browser.setDownloadBehavior", {"behavior": "deny", "browserContextId": ctx,
                                                               "eventsEnabled": True})
        page = Page(self, ctx, dict(viewport=viewport, device_scale_factor=device_scale_factor, mobile=mobile,
                                    has_touch=has_touch, user_agent=user_agent, color_scheme=color_scheme,
                                    reduced_motion=reduced_motion, locale=locale, bypass_csp=bypass_csp,
                                    ignore_https_errors=ignore_https_errors))
        params = {"url": "about:blank"}
        if ctx:
            params["browserContextId"] = ctx
        with self.browser.lock:
            tid = self.conn.call("Target.createTarget", params)["targetId"]
            self.mine.add(tid)
            self.browser.created[tid] = self
        page.target_id = tid
        if not self.conn.wait(lambda: tid in self.attached, 30):
            raise CDPError("the new page did not attach")
        page._attach(self.attached.pop(tid))
        page._initialize()
        return page


def launch(executable=None):
    """Launch the first browser that starts: `executable`, else browser_candidates(). Returns (Browser or None,
    [error per browser tried])."""
    errors = []
    for exe in ([executable] if executable else browser_candidates()):
        try:
            return Browser(exe), errors
        except (CDPError, render_png.StartupExit, OSError, ValueError, KeyError) as exc:
            errors.append(f"{exe}: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
    return None, errors


# ----------------------------------------------------------------------------- frames and network idle

class _Frame:
    def __init__(self, page, fid, parent):
        self.page, self.id, self.parent = page, fid, parent
        self.children = set()
        self.url = ""
        self.document = None
        self.fired = {"commit"}
        self.early = {}      # loader id -> lifecycle events that arrived before that document's frameNavigated
        self.inflight = set()
        self.timer = None
        self.idle_self = False
        self.detached = False
        if parent:
            parent.children.add(self)
        self.start_idle_timer()

    def start_idle_timer(self):
        if self.timer and self.timer[3]:
            return
        if "networkidle" in self.fired or self.detached:
            return
        self.timer = self.page.conn.add_timer(IDLE_MS / 1000, self._idle)

    def _idle(self):
        self.timer = None
        self.idle_self = True
        self.page.main_frame_recalc()

    def stop_idle_timer(self):
        Connection.cancel_timer(self.timer)
        self.timer = None
        self.idle_self = False

    def recalc(self, allows_removing=None):
        idle = self.idle_self
        for c in list(self.children):
            c.recalc(allows_removing)
            if "networkidle" not in c.fired:
                idle = False
        if idle and "networkidle" not in self.fired:
            self.fired.add("networkidle")
        if allows_removing is not self and "networkidle" in self.fired and not idle:
            self.fired.discard("networkidle")

    def lifecycle(self, event):
        if event in self.fired:
            return
        self.fired.add(event)
        self.page.main_frame_recalc()

    def clear_lifecycle(self):
        self.fired.clear()
        keep = {r for r in self.inflight if self.page.requests.get(r, {}).get("document") == self.document}
        self.inflight = keep
        self.stop_idle_timer()
        if not self.inflight:
            self.start_idle_timer()
        self.page.main_frame_recalc(self)
        self.lifecycle("commit")
        for event in self.early.pop(self.document, ()):  # the new document's events that came first
            self.lifecycle(event)
        self.early.clear()


# ----------------------------------------------------------------------------- page

class Page:
    def __init__(self, client, context, opts):
        self.client, self.conn, self.context, self.opts = client, client.conn, context, opts
        self.browser = client.browser
        self.session = None
        self.target_id = None
        self.frames = {}
        self.main_frame = None
        self.contexts = {}           # (session, context id) -> (frame id, world)
        self.requests = {}           # request id -> {frame, type, document, url}
        self.responses = {}          # request id -> {status, headers, url, type}
        self.finished = set()
        self.failures = {}           # request id -> errorText
        self.sessions = {}           # session -> "page" | "iframe" | "worker"
        self.worker_frame = {}       # worker session -> frame id its requests count for
        self.styles = {}             # request id -> session, OK stylesheet responses not read yet
        self.style_texts = None      # list once collect_stylesheets() is on: bodies in the order they were read
        self.route = None            # callable(css text, url) -> css text, for stylesheets
        self.media = {"color_scheme": opts["color_scheme"], "reduced_motion": opts["reduced_motion"]}
        self.lifecycle_log = []      # last main-frame lifecycle events as (loader id prefix, name), for diagnosis
        self.closed = False

    # -- set-up (Playwright FrameSession._initialize)
    def _attach(self, session):
        self.session = session
        self.sessions[session] = "page"
        self._listen(session)

    def _listen(self, s):
        on = self.conn.on
        on("Page.frameAttached", lambda p, _s: self._frame_attached(p.get("frameId"), p.get("parentFrameId")), s)
        on("Page.frameNavigated", lambda p, _s: self._frame_navigated(p.get("frame") or {}), s)
        on("Page.frameDetached", lambda p, _s: self._frame_detached(p.get("frameId"), p.get("reason")), s)
        on("Page.lifecycleEvent", lambda p, _s: self._lifecycle(p), s)
        on("Runtime.executionContextCreated", lambda p, ss: self._context_created(ss, p.get("context") or {}), s)
        on("Runtime.executionContextDestroyed", lambda p, ss: self.contexts.pop((ss, p.get("executionContextId")), None), s)
        on("Runtime.executionContextsCleared", lambda p, ss: self._contexts_cleared(ss), s)
        on("Network.requestWillBeSent", lambda p, ss: self._request(ss, p), s)
        on("Network.responseReceived", lambda p, ss: self._response(ss, p), s)
        on("Network.loadingFinished", lambda p, ss: self._finished(ss, p, True), s)
        on("Network.loadingFailed", lambda p, ss: self._finished(ss, p, False), s)
        on("Fetch.requestPaused", lambda p, ss: self.conn.defer(lambda: self._paused(ss, p)), s)
        on("Target.attachedToTarget", lambda p, ss: self._child_attached(ss, p), s)
        on("Target.detachedFromTarget", lambda p, ss: self._child_detached(p.get("sessionId")), s)

    def _setup_frame_session(self, s, main, window_id=None):
        """The calls of Playwright's FrameSession._initialize, in its order, sent together; the target runs once they
        are queued (it was created paused)."""
        o, c = self.opts, self.conn
        calls = [("Page.enable", {}), ("Page.getFrameTree", {}), ("Log.enable", {}),
                 ("Page.setLifecycleEventsEnabled", {"enabled": True}), ("Runtime.enable", {}),
                 ("Page.addScriptToEvaluateOnNewDocument", {"source": "", "worldName": UTILITY_WORLD}),
                 ("Network.enable", {})]
        if self.route:
            calls += [("Network.setCacheDisabled", {"cacheDisabled": True}),
                      ("Fetch.enable", {"patterns": [{"resourceType": "Stylesheet", "requestStage": "Response"}]})]
        calls.append(("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}))
        if main:
            calls.append(("Emulation.setFocusEmulationEnabled", {"enabled": True}))
        if o["bypass_csp"]:
            calls.append(("Page.setBypassCSP", {"enabled": True}))
        if o["ignore_https_errors"]:
            calls.append(("Security.setIgnoreCertificateErrors", {"ignore": True}))
        if main:
            w, h = o["viewport"]
            mobile = bool(o["mobile"])
            if window_id is not None:  # the headless window takes the viewport size first
                calls.append(("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"width": w, "height": h}}))
            calls.append(("Emulation.setDeviceMetricsOverride", {
                "mobile": mobile, "width": w, "height": h, "screenWidth": w, "screenHeight": h,
                "deviceScaleFactor": o["device_scale_factor"] or 1,
                "screenOrientation": ({"angle": 90, "type": "landscapePrimary"} if w > h else
                                      {"angle": 0, "type": "portraitPrimary"}) if mobile else
                {"angle": 0, "type": "landscapePrimary"}}))
        if o["has_touch"]:
            calls.append(("Emulation.setTouchEmulationEnabled", {"enabled": True}))
        if o["user_agent"] or o["locale"]:
            ua = {"userAgent": o["user_agent"] or "", "acceptLanguage": o["locale"]}
            if o["user_agent"]:
                ua["userAgentMetadata"] = ua_metadata(o["user_agent"], o["mobile"])
            calls.append(("Emulation.setUserAgentOverride", ua))
        if o["locale"]:
            calls.append(("Emulation.setLocaleOverride", {"locale": o["locale"]}))
        calls.append(("Page.setFontFamilies", FONT_FAMILIES[self.browser.platform]))
        calls.append(("Emulation.setEmulatedMedia", self._media_params()))
        calls.append(("Runtime.runIfWaitingForDebugger", {}))
        ids = [(m, c.send(m, p, s)) for m, p in calls]
        for m, i in ids:
            c.wait(lambda: i in c.replies, 30)
            msg = c.replies.pop(i, {})
            if m == "Page.getFrameTree" and "result" in msg:
                self._frame_tree(msg["result"]["frameTree"], s)
                for fid in self._tree_ids(msg["result"]["frameTree"]):
                    c.send("Page.createIsolatedWorld", {"frameId": fid, "grantUniveralAccess": True,
                                                        "worldName": UTILITY_WORLD}, s)

    @classmethod
    def _tree_ids(cls, tree):
        yield (tree.get("frame") or {}).get("id")
        for ch in tree.get("childFrames") or []:
            yield from cls._tree_ids(ch)

    def _media_params(self):
        return {"media": "", "features": [
            {"name": "prefers-color-scheme", "value": self.media["color_scheme"] or ""},
            {"name": "prefers-reduced-motion", "value": self.media["reduced_motion"] or ""},
            {"name": "forced-colors", "value": "none"}, {"name": "prefers-contrast", "value": "no-preference"}]}

    def _initialize(self):
        win = self.conn.try_call("Browser.getWindowForTarget", session=self.session) or {}
        self._setup_frame_session(self.session, True, win.get("windowId"))

    def _frame_tree(self, tree, session):
        fr = tree.get("frame") or {}
        self._frame_attached(fr.get("id"), fr.get("parentId"))
        f = self.frames.get(fr.get("id"))
        if f and session == self.session and not fr.get("parentId"):
            self.main_frame = f
        if f:
            f.url = (fr.get("url") or "") + (fr.get("urlFragment") or "")
            f.document = fr.get("loaderId")
        for child in tree.get("childFrames") or []:
            self._frame_tree(child, session)

    def _child_attached(self, parent_session, p):
        info, sid = p.get("targetInfo") or {}, p.get("sessionId")
        kind = info.get("type")
        if kind == "iframe":
            fid = info.get("targetId")
            if fid not in self.frames and info.get("parentFrameId") in self.frames:
                self._frame_attached(fid, info["parentFrameId"])
            f = self.frames.get(fid)
            if f:
                for ch in list(f.children):
                    self._remove_frame(ch)
            self.sessions[sid] = "iframe"
            self._listen(sid)
            self.conn.defer(lambda: self._setup_frame_session(sid, False))
            return
        if kind == "worker":
            self.sessions[sid] = "worker"
            self.worker_frame[sid] = info.get("parentFrameId") or (self.main_frame.id if self.main_frame else None)
            self._listen(sid)
            self.conn.send("Runtime.enable", session=sid)
            self.conn.send("Network.enable", session=sid)
            if self.route:
                self.conn.send("Network.setCacheDisabled", {"cacheDisabled": True}, sid)
            self.conn.send("Runtime.runIfWaitingForDebugger", session=sid)
            self.conn.send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}, sid)
            return
        self.conn.send("Target.detachFromTarget", {"sessionId": sid}, parent_session)

    def _child_detached(self, sid):
        self.sessions.pop(sid, None)
        self.conn.off_session(sid)

    # -- frames
    def _frame_attached(self, fid, parent_id):
        if not fid or fid in self.frames:
            return
        parent = self.frames.get(parent_id) if parent_id else None
        self.frames[fid] = _Frame(self, fid, parent)
        if parent is None and self.main_frame is None:
            self.main_frame = self.frames[fid]

    def _frame_navigated(self, fr):
        fid = fr.get("id")
        if fid not in self.frames:
            self._frame_attached(fid, fr.get("parentId"))
        f = self.frames[fid]
        for ch in list(f.children):
            self._remove_frame(ch)
        f.url = (fr.get("url") or "") + (fr.get("urlFragment") or "")
        f.document = fr.get("loaderId")
        f.clear_lifecycle()

    def _frame_detached(self, fid, reason):
        if reason == "swap":  # the frame moves to its own process (an iframe session); it is still there
            return
        f = self.frames.get(fid)
        if f:
            self._remove_frame(f)
            self.main_frame_recalc()

    def _remove_frame(self, f):
        for ch in list(f.children):
            self._remove_frame(ch)
        f.detached = True
        f.stop_idle_timer()
        if f.parent:
            f.parent.children.discard(f)
        self.frames.pop(f.id, None)

    def _lifecycle(self, p):
        f = self.frames.get(p.get("frameId"))
        name, loader = p.get("name"), p.get("loaderId")
        if f is not None and f is self.main_frame and loader:
            self.lifecycle_log.append((loader[:8], name))
            del self.lifecycle_log[:-12]
        if f and name in ("load", "DOMContentLoaded"):
            event = "load" if name == "load" else "domcontentloaded"
            f.lifecycle(event)
            if loader and f.document and loader != f.document:
                # for a document not committed yet (its frameNavigated comes later and clears the events): kept
                # and replayed at its commit, so the order of the two messages cannot stall goto()
                f.early.setdefault(loader, []).append(event)

    def main_frame_recalc(self, allows_removing=None):
        if self.main_frame:
            self.main_frame.recalc(allows_removing)

    # -- execution contexts
    def _context_created(self, session, ctx):
        aux = ctx.get("auxData") or {}
        world = "main" if aux.get("isDefault") else ("utility" if ctx.get("name") == UTILITY_WORLD else None)
        if world:
            self.contexts[(session, ctx.get("id"))] = (aux.get("frameId"), world)

    def _contexts_cleared(self, session):
        for key in [k for k in self.contexts if k[0] == session]:
            del self.contexts[key]

    def _context_id(self, frame_id, world):
        for (s, cid), (fid, w) in self.contexts.items():
            if fid == frame_id and w == world:
                return s, cid
        return None, None

    # -- network
    def _request(self, session, p):
        rid = p.get("requestId")
        if session in self.worker_frame:
            fid = self.worker_frame[session]
        else:
            fid = p.get("frameId")
        url = (p.get("request") or {}).get("url", "")
        if rid in self.requests and p.get("redirectResponse"):
            self._done(rid)  # a redirect ends one request and starts the next (Playwright counts both)
        nav = rid == p.get("loaderId") and p.get("type") == "Document"
        self.requests[rid] = {"frame": fid, "type": p.get("type"), "document": p.get("loaderId") if nav else None,
                              "url": url, "session": session}
        self.finished.discard(rid)
        if url.endswith("/favicon.ico") or p.get("type") == "EventSource":
            return
        f = self.frames.get(fid)
        if f:
            f.inflight.add(rid)
            if len(f.inflight) == 1:
                f.stop_idle_timer()

    def _response(self, session, p):
        r = p.get("response") or {}
        rid = p.get("requestId")
        self.responses[rid] = {"status": r.get("status"), "headers": r.get("headers") or {}, "url": r.get("url"),
                               "type": p.get("type"), "session": session}
        status = r.get("status") or 0
        if self.style_texts is not None and p.get("type") == "Stylesheet" and (status == 0 or 200 <= status <= 299):
            self.styles[rid] = session

    def _finished(self, session, p, ok):
        rid = p.get("requestId")
        if ok:
            self.finished.add(rid)
        else:
            self.failures[rid] = p.get("errorText") or "net::ERR_FAILED"
        self._done(rid)
        if rid in self.styles:  # page.on('response') + response.text(): the body is read as soon as it is complete
            sess = self.styles.pop(rid)
            if ok:
                self.conn.defer(lambda: self._read_style(sess, rid))

    def _read_style(self, session, rid):
        r = self.conn.try_call("Network.getResponseBody", {"requestId": rid}, session, 30)
        if r is not None and self.style_texts is not None:
            self.style_texts.append(base64.b64decode(r["body"]).decode("utf-8", errors="replace")
                                    if r.get("base64Encoded") else r["body"])

    def _done(self, rid):
        req = self.requests.get(rid)
        if not req:
            return
        f = self.frames.get(req["frame"])
        if f and rid in f.inflight:
            f.inflight.discard(rid)
            if not f.inflight:
                f.start_idle_timer()

    def _paused(self, session, p):
        """Fetch.requestPaused at the response stage for a stylesheet: rewrite its text and fulfil, or let it go."""
        rid = p["requestId"]
        status = p.get("responseStatusCode") or 0
        headers = p.get("responseHeaders") or []
        if not self.route or p.get("responseErrorReason") or 300 <= status < 400:
            self.conn.try_call("Fetch.continueRequest", {"requestId": rid}, session)
            return
        try:
            body = self.conn.call("Fetch.getResponseBody", {"requestId": rid}, session, 60)
            raw = base64.b64decode(body["body"]) if body.get("base64Encoded") else body["body"].encode("utf-8")
            text = self.route(raw.decode("utf-8", errors="replace"), (p.get("request") or {}).get("url", ""))
        except Exception:  # noqa: BLE001 - as route.fetch() failing: the original response goes through
            self.conn.try_call("Fetch.continueRequest", {"requestId": rid}, session)
            return
        out = [h for h in headers if h.get("name", "").lower() not in ("content-type", "content-length",
                                                                         "content-encoding")]
        out.append({"name": "content-type", "value": "text/css; charset=utf-8"})
        self.conn.try_call("Fetch.fulfillRequest", {"requestId": rid, "responseCode": status or 200,
                                                    "responseHeaders": out,
                                                    "body": base64.b64encode(text.encode("utf-8")).decode("ascii")},
                           session)

    def route_stylesheets(self, rewrite):
        """Rewrite every stylesheet response with rewrite(css_text, url) -> css_text before the page reads it
        (call before goto). The HTTP cache is off while routing, as with Playwright's page.route."""
        self.route = rewrite
        for s, kind in list(self.sessions.items()):
            if kind == "worker":
                continue
            self.conn.call("Network.setCacheDisabled", {"cacheDisabled": True}, s)
            self.conn.call("Fetch.enable", {"patterns": [{"resourceType": "Stylesheet", "requestStage": "Response"}]}, s)

    def collect_stylesheets(self):
        """Start reading the body of every OK stylesheet response as it completes (call before goto)."""
        self.style_texts = []

    def stylesheet_texts(self):
        """Bodies read so far (collect_stylesheets), in the order they were read; reads still queued run first."""
        self.conn.pump(0)
        return list(self.style_texts or [])

    # -- navigation
    def url(self):
        return self.main_frame.url if self.main_frame else ""

    def goto(self, url, timeout=45):
        """Navigate and wait for DOMContentLoaded of the new document; returns its final response
        {status, headers, url} or None. Errors read like Playwright's."""
        deadline = time.monotonic() + timeout
        main = self.main_frame
        before = main.document  # taken before the call: a commit that arrives ahead of the reply still counts
        timed_out = f"page.goto: Timeout {int(timeout * 1000)}ms exceeded."
        try:
            r = self.conn.call("Page.navigate", {"url": url, "frameId": main.id,
                                                 "referrerPolicy": "unsafeUrl"}, self.session, timeout)
        except CDPError as exc:
            if "timed out" in str(exc):
                raise NavigationError(timed_out, self._stall("no reply to Page.navigate", None)) from None
            raise NavigationError(f"page.goto: {exc}") from None
        if r.get("errorText"):
            raise NavigationError(f"page.goto: {r['errorText']} at {url}")
        loader = r.get("loaderId")
        if loader:  # the first new document decides (Playwright: else "interrupted by another navigation")
            ok = self.conn.wait(lambda: main.document != before or loader in self.failures,
                                max(0.0, deadline - time.monotonic()))
            if not ok:
                raise NavigationError(timed_out, self._stall("the new document never committed", loader))
            if main.document == before and loader in self.failures:
                raise NavigationError(f"page.goto: {self.failures[loader]}")
            if main.document != loader:
                raise NavigationError(f'page.goto: Navigation to "{url}" is interrupted by another navigation to '
                                      f'"{main.url}"')
        if not self.conn.wait(lambda: "domcontentloaded" in self.main_frame.fired,
                              max(0.0, deadline - time.monotonic())):
            raise NavigationError(timed_out, self._stall("committed, no DOMContentLoaded", loader))
        resp = self.responses.get(loader) if loader else None
        return dict(resp) if resp else None

    def _stall(self, step, loader):
        """One line on where a navigation stopped, for the timeout's `detail`."""
        main = self.main_frame
        req = self.requests.get(loader) if loader else None
        resp = self.responses.get(loader) if loader else None
        doc = ("no document request" if not loader else
               f"document {loader[:8]} " + ("failed " + self.failures[loader] if loader in self.failures else
                                            f"response {resp.get('status')}" if resp else
                                            "requested, no response" if req else "not requested"))
        events = " ".join(f"{lid}:{name}" for lid, name in self.lifecycle_log) or "none"
        return (f"{step}; {doc}; main frame {main.url[:80] if main else '?'} (document "
                f"{(main.document or '')[:8] if main else '?'}, fired {sorted(main.fired) if main else []}); "
                f"lifecycle {events}; transport {'pipe' if self.browser.pipe else 'websocket'}")

    def wait_for_load_state(self, state, timeout):
        key = {"networkidle": "networkidle", "load": "load", "domcontentloaded": "domcontentloaded"}[state]
        return self.conn.wait(lambda: self.main_frame is not None and key in self.main_frame.fired, timeout)

    def wait_for_timeout(self, ms):
        self.conn.sleep(ms / 1000)

    # -- evaluation
    def evaluate(self, fn_source, arg=None, timeout=180, world="main", frame_id=None, session=None):
        """Call a function (its source) with `arg` in the main world of the main frame (or `frame_id`/`world`).
        Raises RuntimeError('page.evaluate: ...') when it throws."""
        params = {"expression": call_expression(fn_source, arg), "awaitPromise": True, "returnByValue": True}
        s = session or self.session
        if world != "main" or frame_id:
            s2, cid = self._context_id(frame_id or self.main_frame.id, world)
            if cid is None:
                raise RuntimeError("page.evaluate: Frame does not yet have the execution context")
            s, params["contextId"] = s2, cid
        r = self.conn.call("Runtime.evaluate", params, s, timeout)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            text = ((d.get("exception") or {}).get("description") or d.get("text") or "error").splitlines()[0]
            raise RuntimeError(f"page.evaluate: {text}")
        return from_tree((r.get("result") or {}).get("value"))

    def evaluate_in_all_frames(self, fn_source, arg=None, world="utility"):
        """Like Playwright's safeNonStallingEvaluateInAllFrames: every frame's context of `world`, errors ignored."""
        for (s, cid), (fid, w) in list(self.contexts.items()):
            if w != world or fid not in self.frames:
                continue
            with contextlib.suppress(CDPError):
                self.conn.call("Runtime.evaluate", {"expression": call_expression(fn_source, arg),
                                                    "awaitPromise": True, "returnByValue": True, "contextId": cid}, s, 30)

    def add_style_tag(self, content):
        """page.addStyleTag({content}): a <style> appended to <head>, resolved when the sheet loads.

        Playwright hands the <style> back as an element handle and describes it with its injected script, created in
        the page's main world on first use. That script's constructor registers capture listeners for pointer and
        touch events on the window (passive: false). Non-passive touch listeners change how Chrome rasterizes the page
        afterwards (measured: anti-aliased edges of images differ), so the same listeners are registered here at the
        same moment: the first style tag of each document."""
        self.evaluate(_ADD_STYLE, content)
        self._injected_listeners("main")

    def _injected_listeners(self, world):
        doc = self.main_frame.document if self.main_frame else None
        done = self.__dict__.setdefault("_listeners_done", set())
        if doc and (doc, world) not in done:
            done.add((doc, world))
            with contextlib.suppress(RuntimeError, CDPError):
                self.evaluate(_HIT_TARGET_LISTENERS, None, world=world)

    def emulate_media(self, color_scheme):
        self.media["color_scheme"] = color_scheme
        for s, kind in list(self.sessions.items()):
            if kind != "worker":
                self.conn.try_call("Emulation.setEmulatedMedia", self._media_params(), s)

    # -- screenshots (Playwright screenshotter + crPage.takeScreenshot)
    def _prepare(self, disable_animations):
        self.evaluate_in_all_frames(_PREPARE, [None, True, bool(disable_animations), False])
        with contextlib.suppress(RuntimeError, CDPError):
            self.evaluate("() => document.fonts.ready.then(() => undefined)", None, world="utility")

    def _restore(self):
        self.evaluate_in_all_frames("() => window.__pwCleanupScreenshot && window.__pwCleanupScreenshot()")

    def _capture(self, clip, beyond):
        r = self.conn.call("Page.captureScreenshot", {"format": "png", "clip": clip, "captureBeyondViewport": beyond},
                           self.session, 120)
        return base64.b64decode(r["data"])

    def screenshot(self, path, full_page=False, clip=None, animations="disabled"):
        """page.screenshot({path, fullPage, clip, animations, caret: 'hide'})."""
        vw, vh = self.opts["viewport"]
        self._prepare(animations == "disabled")
        try:
            if full_page:
                size = self.evaluate(_FULL_PAGE_SIZE, None, world="utility")
                doc = {"x": 0, "y": 0, "width": size["width"], "height": size["height"]}
                fits = size["width"] <= vw and size["height"] <= vh
                if clip:
                    doc = _trim_clip(clip, doc)
                data = self._capture(dict(doc, scale=1), not fits)
            else:
                rect = _trim_clip(clip, {"width": vw, "height": vh}) if clip else {"x": 0, "y": 0, "width": vw, "height": vh}
                m = self.conn.call("Page.getLayoutMetrics", session=self.session)
                vv = m["visualViewport"]
                doc = {"x": vv["pageX"] + rect["x"], "y": vv["pageY"] + rect["y"],
                       "width": math.floor(rect["width"] / vv["scale"] + 1e-3),
                       "height": math.floor(rect["height"] / vv["scale"] + 1e-3)}
                data = self._capture(dict(doc, scale=vv["scale"]), False)
        finally:
            self._restore()
        _write(path, data)
        return data

    def screenshot_element(self, selector, path, animations="allow", timeout=30):
        """locator(selector).first().screenshot({path}): visible and stable, scrolled into view, its border box."""
        vw, vh = self.opts["viewport"]
        self._prepare(animations == "disabled")
        self._injected_listeners("utility")  # the locator's injected script, created in the utility world
        try:
            deadline = time.monotonic() + timeout
            while True:
                state = self.evaluate(_ELEMENT_STATE, selector)
                if state == "ok":
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError(f"locator.screenshot: Timeout {timeout * 1000}ms exceeded.")
                self.conn.sleep(0.1)
            obj = self.conn.call("Runtime.evaluate", {"expression": f"document.querySelector({json.dumps(selector)})"},
                                 self.session)["result"].get("objectId")
            self.conn.try_call("DOM.scrollIntoViewIfNeeded", {"objectId": obj}, self.session)
            quad = self.conn.call("DOM.getBoxModel", {"objectId": obj}, self.session)["model"]["border"]
            x, y = min(quad[0::2]), min(quad[1::2])
            w, h = max(quad[0::2]) - x, max(quad[1::2]) - y
            if not (w and h):
                raise RuntimeError("locator.screenshot: Node has 0 width or height.")
            scroll = self.evaluate("() => ({x: window.scrollX, y: window.scrollY})", None, world="utility")
            ex, ey = math.floor(x + scroll["x"] + 1e-3), math.floor(y + scroll["y"] + 1e-3)
            ex2, ey2 = math.ceil(x + scroll["x"] + w - 1e-3), math.ceil(y + scroll["y"] + h - 1e-3)
            data = self._capture({"x": ex, "y": ey, "width": ex2 - ex, "height": ey2 - ey, "scale": 1},
                                 not (w <= vw and h <= vh))
        finally:
            self._restore()
        _write(path, data)
        return data

    def capture_clip(self, path, clip):
        """A raw Page.captureScreenshot of a document rect (scale in clip), beyond the viewport."""
        data = self._capture(clip, True)
        _write(path, data)
        return data

    def close(self):
        if self.closed:
            return
        self.closed = True
        for s in list(self.sessions):
            self.conn.off_session(s)
        if self.context:
            self.conn.try_call("Target.disposeBrowserContext", {"browserContextId": self.context}, timeout=30)
        elif self.target_id:
            self.conn.try_call("Target.closeTarget", {"targetId": self.target_id}, timeout=30)
        for f in list(self.frames.values()):
            f.stop_idle_timer()


def _trim_clip(clip, size):
    x1 = max(0, min(clip["x"], size["width"]))
    y1 = max(0, min(clip["y"], size["height"]))
    x2 = max(0, min(clip["x"] + clip["width"], size["width"]))
    y2 = max(0, min(clip["y"] + clip["height"], size["height"]))
    if x2 - x1 <= 0 or y2 - y1 <= 0:
        raise RuntimeError("Clipped area is either empty or outside the resulting image")
    return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}


def _write(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


_ADD_STYLE = r"""async (content) => {
  const style = document.createElement('style');
  style.type = 'text/css';
  style.appendChild(document.createTextNode(content));
  const promise = new Promise((res, rej) => { style.onload = res; style.onerror = rej; });
  document.head.appendChild(style);
  await promise;
}"""

# The window listeners Playwright's injected script registers when it is created (InjectedScript
# _setupHitTargetInterceptors and _setupGlobalListenersRemovalDetection): no-op capture listeners for the hit-target
# events, re-registered if the document element is replaced. Compiling that script takes Playwright a few frames, so
# its listeners arrive after the style change has been drawn; waiting two animation frames reproduces that order (with
# the listeners registered at once, 3 of 10 runs rasterized images differently from the Node tool; after the frames,
# none did).
_HIT_TARGET_LISTENERS = r"""async () => {
  await Promise.race([new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))),
                      new Promise(r => setTimeout(r, 100))]);
  const events = ['mousemove', 'pointerdown', 'pointerup', 'touchstart', 'touchend', 'touchcancel', 'mousedown',
    'mouseup', 'click', 'auxclick', 'dblclick', 'contextmenu'];
  const listener = () => undefined;
  const add = () => { for (const e of events) window.addEventListener(e, listener, { capture: true, passive: false }); };
  const check = '__playwright_global_listeners_check__';
  let seen = false;
  const mark = () => { seen = true; };
  window.addEventListener(check, mark);
  new MutationObserver((entries) => {
    if (!entries.some(e => Array.from(e.addedNodes).includes(document.documentElement))) return;
    seen = false;
    window.dispatchEvent(new CustomEvent(check));
    if (seen) return;
    window.addEventListener(check, mark);
    add();
  }).observe(document, { childList: true });
  add();
}"""

_FULL_PAGE_SIZE = r"""() => {
  if (!document.body || !document.documentElement) return null;
  return {
    width: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth, document.body.offsetWidth,
      document.documentElement.offsetWidth, document.body.clientWidth, document.documentElement.clientWidth),
    height: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, document.body.offsetHeight,
      document.documentElement.offsetHeight, document.body.clientHeight, document.documentElement.clientHeight),
  };
}"""

# Before a screenshot, in every frame (Playwright's screenshotter): finite animations jump to their end, infinite
# ones are cancelled (and resumed afterwards), carets in inputs are hidden; window.__pwCleanupScreenshot undoes it.
# Adapted from Playwright's inPagePrepareForScreenshots, Apache-2.0; see THIRD_PARTY_NOTICES.md.
_PREPARE = r"""([screenshotStyle, hideCaret, disableAnimations, syncAnimations]) => {
  if (syncAnimations) {
    const style = document.createElement('style');
    style.textContent = 'body {}';
    document.head.appendChild(style);
    document.documentElement.getBoundingClientRect();
    style.remove();
  }
  if (!screenshotStyle && !hideCaret && !disableAnimations) return;
  const collectRoots = (root, roots = []) => {
    roots.push(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    do {
      const node = walker.currentNode;
      const shadowRoot = node instanceof Element ? node.shadowRoot : null;
      if (shadowRoot) collectRoots(shadowRoot, roots);
    } while (walker.nextNode());
    return roots;
  };
  const roots = collectRoots(document);
  const cleanups = [];
  if (screenshotStyle) {
    for (const root of roots) {
      const tag = document.createElement('style');
      tag.textContent = screenshotStyle;
      if (root === document) document.documentElement.append(tag); else root.append(tag);
      cleanups.push(() => tag.remove());
    }
  }
  if (hideCaret) {
    const saved = new Map();
    for (const root of roots) {
      root.querySelectorAll('input,textarea,[contenteditable]').forEach(el => {
        saved.set(el, { value: el.style.getPropertyValue('caret-color'), priority: el.style.getPropertyPriority('caret-color') });
        el.style.setProperty('caret-color', 'transparent', 'important');
      });
    }
    cleanups.push(() => { for (const [el, v] of saved) el.style.setProperty('caret-color', v.value, v.priority); });
  }
  if (disableAnimations) {
    const resume = new Set();
    const handle = (root) => {
      for (const a of root.getAnimations()) {
        if (!a.effect || a.playbackRate === 0 || resume.has(a)) continue;
        const end = a.effect.getComputedTiming().endTime;
        if (Number.isFinite(end)) { try { a.finish(); } catch (e) {} }
        else { try { a.cancel(); resume.add(a); } catch (e) {} }
      }
    };
    for (const root of roots) {
      const h = handle.bind(null, root);
      h();
      root.addEventListener('transitionrun', h);
      root.addEventListener('animationstart', h);
      cleanups.push(() => { root.removeEventListener('transitionrun', h); root.removeEventListener('animationstart', h); });
    }
    cleanups.push(() => { for (const a of resume) { try { a.play(); } catch (e) {} } });
  }
  window.__pwCleanupScreenshot = () => { for (const c of cleanups) c(); delete window.__pwCleanupScreenshot; };
}"""

# Playwright's element states for a screenshot: visible (visibility: visible and a non-empty box, display:contents
# through its children) and stable (the same box in two animation frames).
_ELEMENT_STATE = r"""async (selector) => {
  const el = document.querySelector(selector);
  if (!el) return 'missing';
  const visible = (e) => {
    const s = getComputedStyle(e);
    if (s.display === 'contents') {
      for (let c = e.firstChild; c; c = c.nextSibling) {
        if (c.nodeType === 1 && visible(c)) return true;
        if (c.nodeType === 3) { const r = document.createRange(); r.selectNode(c); const b = r.getBoundingClientRect(); if (b.width > 0 && b.height > 0) return true; }
      }
      return false;
    }
    if (typeof e.checkVisibility === 'function' && !e.checkVisibility()) return false;
    if (s.visibility !== 'visible') return false;
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  if (!visible(el)) return 'hidden';
  const box = () => { const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height].join(); };
  const a = await new Promise(r => requestAnimationFrame(() => r(box())));
  const b = await new Promise(r => requestAnimationFrame(() => r(box())));
  return a === b ? 'ok' : 'moving';
}"""
