#!/usr/bin/env python3
"""Screenshot a local HTML file to PNG with a headless Chromium-based browser (stdlib only).

Used for mock-site previews and for the palette board. Works on Linux, macOS and Windows with any
installed Chrome / Chromium / Edge / Brave, or a Chromium from the Playwright cache. No Node needed: the
page is driven over the Chrome DevTools protocol (local websocket, stdlib only).

Browser search order: $BRAND_IDENTITY_BROWSER or $CHROMIUM_PATH, Google Chrome, Chromium, Microsoft Edge,
Playwright cache Chromium (any revision), Brave, Playwright headless shell.

Usage:
  python3 scripts/render_png.py page.html -o page.png                         # 1440x900 viewport
  python3 scripts/render_png.py page.html -o mobile.png --width 390 --height 844 --scale 2
  python3 scripts/render_png.py board.html -o board.png --width 1880 --full-page
  python3 scripts/render_png.py --which                                       # show the browser in use
  python3 scripts/render_png.py card.html -o card.png --width 1600 --height 1000 --scale 2 --probe
  python3 scripts/render_png.py kit.html --pages kit/pages --pdf kit/guide.pdf --width 1920 --height 1080

render(html, out, width, height, pdf=False, pages=False, probe=True) is the measured path used by the identity
card, board and kit: one DevTools session that also reports FontFace states, each [data-role]'s computed font,
overflowing text, text contrast and [data-logo] heights, screenshots every `.page` element, prints a PDF and lists
its embedded fonts (Type3 = a variable font leaked through). screenshot_html(...) keeps its original behaviour.

Exit codes: 0 ok, 1 render failed, 2 bad arguments, 3 no browser found.
"""
import argparse
import base64
import contextlib
import glob
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

sys.dont_write_bytecode = True

MAX_HEIGHT = 16000


def setup_console():
    """UTF-8 console on every platform (Windows code pages break on non-ASCII paths)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _which_all(names):
    out = []
    for n in names:
        p = shutil.which(n)
        if p:
            out.append(p)
    return out


def _playwright_roots():
    roots = []
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        roots.append(env)
    home = Path.home()
    if sys.platform == "darwin":
        roots.append(str(home / "Library" / "Caches" / "ms-playwright"))
    elif os.name == "nt":
        roots.append(os.path.join(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")), "ms-playwright"))
    else:
        roots.append(os.path.join(os.environ.get("XDG_CACHE_HOME", str(home / ".cache")), "ms-playwright"))
    return roots


def _rev(path):
    m = re.search(r"chromium(?:_headless_shell)?-(\d+)", path)
    return int(m.group(1)) if m else 0


def playwright_browsers(headless_shell=False):
    """Chromium executables in the Playwright cache, newest revision first."""
    if headless_shell:
        pats = ["chromium_headless_shell-*/*/chrome-headless-shell", "chromium_headless_shell-*/*/chrome-headless-shell.exe",
                "chromium_headless_shell-*/*/headless_shell", "chromium_headless_shell-*/*/headless_shell.exe"]
    else:
        pats = ["chromium-*/chrome-linux*/chrome", "chromium-*/chrome-win*/chrome.exe",
                "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
                "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"]
    found = []
    for root in _playwright_roots():
        for pat in pats:
            found.extend(glob.glob(os.path.join(root, pat)))
    return sorted(set(found), key=_rev, reverse=True)


def browser_candidates():
    """All candidate executables in preference order (existing files only)."""
    c = []
    for env in ("BRAND_IDENTITY_BROWSER", "CHROMIUM_PATH"):
        if os.environ.get(env):
            c.append(os.environ[env])
    if sys.platform == "darwin":
        apps = ["Google Chrome", "Chromium", "Microsoft Edge"]
        c += [f"/Applications/{a}.app/Contents/MacOS/{a}" for a in apps]
        c += [str(Path.home() / "Applications" / f"{a}.app" / "Contents" / "MacOS" / a) for a in apps]
    elif os.name == "nt":
        bases = [os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        for b in filter(None, bases):
            c += [os.path.join(b, "Google", "Chrome", "Application", "chrome.exe"),
                  os.path.join(b, "Chromium", "Application", "chrome.exe"),
                  os.path.join(b, "Microsoft", "Edge", "Application", "msedge.exe")]
    else:
        c += _which_all(["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                         "microsoft-edge", "microsoft-edge-stable"])
    c += playwright_browsers()
    if sys.platform == "darwin":
        c.append("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")
    elif os.name == "nt":
        for b in filter(None, [os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")]):
            c.append(os.path.join(b, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"))
    else:
        c += _which_all(["brave", "brave-browser"])
    c += playwright_browsers(headless_shell=True)
    seen, out = set(), []
    for p in c:
        if p and os.path.isfile(p):
            real = os.path.realpath(p)
            if real not in seen:
                seen.add(real)
                out.append(p)
    return out


def find_browser():
    cands = browser_candidates()
    return cands[0] if cands else None


NO_BROWSER = ("no Chromium-based browser found. Install Google Chrome or Chromium, or set "
              "BRAND_IDENTITY_BROWSER=/path/to/chrome (a Playwright Chromium in ~/.cache/ms-playwright also works).")


@contextlib.contextmanager
def _tempdir():
    """mkdtemp whose cleanup never raises (Windows can keep Chrome's profile files locked)."""
    path = tempfile.mkdtemp(prefix="bi-render-")
    try:
        yield path
    finally:
        for _ in range(10):
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                break
            time.sleep(0.3)


def _stop(proc):
    """Stop the browser and its helper processes."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # /T takes the renderer and GPU helpers down with the main process
        kill = ["taskkill", "/F", "/T", "/PID", f"{proc.pid}"]
        subprocess.run(kill, capture_output=True, check=False)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _file_url(path):
    p = os.path.abspath(path).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p  # Windows drive path
    return "file://" + quote(p, safe="/:")


# ----------------------------------------------------------------------------- minimal CDP client
# The CLI --screenshot flag is unreliable on current Chrome builds (new headless can exit without
# writing a file), so the page is driven over the DevTools protocol: one websocket, stdlib only.

class _WebSocket:
    """Just enough RFC 6455 for a local DevTools connection (text frames, client masking)."""

    def __init__(self, url, timeout):
        m = re.match(r"ws://([^/:]+):(\d+)(/.*)", url)
        if not m:
            raise RuntimeError(f"unexpected DevTools URL: {url}")
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("DevTools closed the connection during the handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise RuntimeError("DevTools refused the websocket handshake")
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise RuntimeError("DevTools connection closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode("utf-8")
        n = len(data)
        header = bytes([0x81])
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        parts = []
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if b2 & 0x80 else None
            payload = self._recv_exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise RuntimeError("DevTools closed the connection")
            if opcode == 0x9:  # ping -> ignore (Chrome does not require pong for short sessions)
                continue
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8", errors="replace")

    def close(self):
        with contextlib.suppress(Exception):
            self.sock.close()


class _Page:
    def __init__(self, ws_url, timeout):
        self.ws = _WebSocket(ws_url, timeout)
        self.next_id = 0
        self.events = []

    def call(self, method, params=None, timeout=60):
        self.next_id += 1
        mid = self.next_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg["method"])
        raise RuntimeError(f"{method} timed out")

    def wait_event(self, name, timeout):
        if name in self.events:
            return True
        self.ws.sock.settimeout(timeout)
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                msg = json.loads(self.ws.recv())
                if msg.get("method") == name:
                    return True
        except (socket.timeout, OSError):
            return False
        return False

    def eval(self, expr, timeout=30):
        r = self.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True}, timeout)
        return r.get("result", {}).get("value")


def _is_shell(browser):
    name = os.path.basename(browser).lower()
    return "headless-shell" in name or "headless_shell" in name


# ----------------------------------------------------------------------------- start-up (shared with cdplib.py)

class StartupExit(RuntimeError):
    """The browser process exited before its DevTools endpoint was ready."""


_NO_SANDBOX = set()  # real paths of browsers that started only with --no-sandbox in this process

# Localhost DevTools requests never go through a proxy: urlopen() follows HTTP_PROXY and, on Windows, the system
# proxy in the registry, which does not bypass 127.0.0.1 unless NO_PROXY names it.
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def local_urlopen(url, timeout):
    """GET a http://127.0.0.1 DevTools URL directly, ignoring proxy settings."""
    return _LOCAL.open(url, timeout=timeout)


def _no_sandbox_first(browser):
    """--no-sandbox from the start: as root (Chrome refuses its sandbox there), or when this browser already failed
    to start with the sandbox in this process."""
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
        return True
    return os.path.realpath(browser) in _NO_SANDBOX


def start_browser(browser, start):
    """Call start(no_sandbox) and return (its result, no_sandbox).

    The sandbox stays on by default (the live-site tool opens third-party pages). When the browser exits during
    start-up with it on, as in a container or CI runner without user namespaces ("No usable sandbox!"), start once
    more with --no-sandbox and remember that for this browser. `start` raises StartupExit for an early exit and
    cleans up after itself."""
    no_sandbox = _no_sandbox_first(browser)
    try:
        return start(no_sandbox), no_sandbox
    except StartupExit as first:
        if no_sandbox or os.name == "nt":
            raise
        try:
            result = start(True)
        except StartupExit as second:
            raise StartupExit(f"{first}; with --no-sandbox: {second}") from None
    _NO_SANDBOX.add(os.path.realpath(browser))
    return result, True


def stderr_log(profile):
    """Where the browser's stderr goes: a file beside its profile (read only to explain a failed start)."""
    return profile + ".stderr.log"


def stderr_hint(path):
    """The most telling line of a browser's stderr: one that mentions the sandbox, else the last one."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = [ln.strip() for ln in fh.read().splitlines() if ln.strip()]
    except OSError:
        return ""
    if not lines:
        return ""
    pick = next((ln for ln in lines if "sandbox" in ln.lower()), lines[-1])
    return pick if len(pick) <= 240 else pick[:237] + "..."


def spawn(cmd, profile, **kw):
    """Popen with stdin/stdout closed and stderr to stderr_log(profile)."""
    os.makedirs(os.path.dirname(profile) or ".", exist_ok=True)
    with open(stderr_log(profile), "wb") as err:
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err, stdin=subprocess.DEVNULL, **kw)


def exited(browser, proc, profile):
    """StartupExit for a browser that is gone, with the useful stderr line."""
    hint = stderr_hint(stderr_log(profile))
    return StartupExit(f"{browser} exited during start-up (code {proc.returncode})" + (f": {hint}" if hint else ""))


def _launch_once(browser, profile, timeout, no_sandbox):
    cmd = [browser, "--headless" if _is_shell(browser) else "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--mute-audio",
           "--use-mock-keychain", "--password-store=basic", "--disable-background-networking",
           "--disable-component-update", "--disable-sync", "--force-color-profile=srgb",
           "--allow-file-access-from-files", "--remote-debugging-port=0",
           f"--user-data-dir={profile}", "about:blank"]
    if no_sandbox:
        cmd.append("--no-sandbox")
    proc = spawn(cmd, profile)
    port_file = os.path.join(profile, "DevToolsActivePort")
    deadline = time.time() + min(timeout, 30)
    while time.time() < deadline:
        if proc.poll() is not None:
            raise exited(browser, proc, profile)
        if os.path.exists(port_file):
            try:
                lines = Path(port_file).read_text(encoding="utf-8", errors="replace").split()
            except OSError:  # Windows: Chrome still holds the file while writing it; read again next tick
                lines = []
            if lines and lines[0].isdigit():
                return proc, int(lines[0])
        time.sleep(0.1)
    _stop(proc)
    raise RuntimeError(f"{browser} did not open a DevTools port")


def _launch(browser, profile, timeout):
    """Start `browser` headless on about:blank with a DevTools port: (proc, port, no_sandbox)."""
    (proc, port), no_sandbox = start_browser(
        browser, lambda ns: _launch_once(browser, profile + ("-nosandbox" if ns else ""), timeout, ns))
    return proc, port, no_sandbox


def _page_ws(port, timeout):
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with local_urlopen(f"http://127.0.0.1:{port}/json/list", min(timeout, 10)) as r:
                targets = json.loads(r.read().decode("utf-8"))
            for t in targets:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    raise RuntimeError("no DevTools page target")


def launch_check(browser=None, timeout=20):
    """Really start the browser, open about:blank over DevTools and close it (the `check` command).
    Returns {"browser", "ok", "no_sandbox", "seconds", "error"}."""
    browser = browser or find_browser()
    out = {"browser": browser, "ok": False, "no_sandbox": False, "seconds": 0.0, "error": None}
    if not browser:
        out["error"] = NO_BROWSER
        return out
    t0 = time.monotonic()
    with _tempdir() as tmp:
        proc = page = None
        try:
            proc, port, out["no_sandbox"] = _launch(browser, os.path.join(tmp, "profile"), timeout)
            page = _Page(_page_ws(port, timeout), timeout)
            href = page.eval("location.href", timeout)
            if href != "about:blank":
                raise RuntimeError(f"the start page is {href!r}, not about:blank")
            with contextlib.suppress(Exception):
                page.call("Browser.close", timeout=5)
            out["ok"] = True
        except (RuntimeError, OSError, ValueError, KeyError) as exc:
            out["error"] = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        finally:
            if page:
                page.ws.close()
            if proc:
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                _stop(proc)
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out


SETTLE_JS = """(async () => {
  if (document.readyState !== 'complete') await new Promise(r => addEventListener('load', r, {once: true}));
  if (document.fonts) await document.fonts.ready;
  await Promise.all([...document.images].filter(i => !i.complete).map(i => new Promise(r => { i.onload = i.onerror = r; setTimeout(r, 4000); })));
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const d = document.documentElement, b = document.body;
  return Math.ceil(Math.max(d.scrollHeight, b ? b.scrollHeight : 0));
})()"""


def screenshot_html(html_path, png_path, width=1440, height=900, scale=1.0, full_page=False, browser=None,
                    timeout=60, mobile=False, color_scheme=None):
    """Screenshot `html_path` at a width x height CSS-px viewport (x scale). `full_page` grows the viewport
    to the document height (max MAX_HEIGHT). Returns (width, height_css, browser)."""
    if not os.path.isfile(html_path):
        raise FileNotFoundError(f"HTML file not found: {html_path}")
    browser = browser or find_browser()
    if not browser:
        raise RuntimeError(NO_BROWSER)
    png_path = os.path.abspath(png_path)
    os.makedirs(os.path.dirname(png_path), exist_ok=True)
    last_err = None
    for _attempt in range(2):  # a browser can fail once on a busy machine
        with _tempdir() as tmp:
            proc = None
            page = None
            try:
                proc, port, _ = _launch(browser, os.path.join(tmp, "profile"), timeout)
                page = _Page(_page_ws(port, timeout), timeout)
                metrics = {"width": width, "height": height, "deviceScaleFactor": scale, "mobile": bool(mobile)}
                page.call("Emulation.setDeviceMetricsOverride", metrics)
                if color_scheme:
                    page.call("Emulation.setEmulatedMedia",
                              {"features": [{"name": "prefers-color-scheme", "value": color_scheme}]})
                page.call("Page.enable")
                page.call("Page.navigate", {"url": _file_url(html_path)}, timeout)
                page.wait_event("Page.loadEventFired", timeout)
                doc_h = page.eval(SETTLE_JS, timeout) or height
                if full_page:
                    height = max(16, min(MAX_HEIGHT, int(doc_h)))
                    metrics["height"] = height
                    page.call("Emulation.setDeviceMetricsOverride", metrics)
                    page.eval("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))", timeout)
                shot = page.call("Page.captureScreenshot", {"format": "png", "fromSurface": True}, timeout)
                data = base64.b64decode(shot["data"])
                tmp_png = png_path + ".part"
                with open(tmp_png, "wb") as fh:
                    fh.write(data)
                os.replace(tmp_png, png_path)
                with contextlib.suppress(Exception):
                    page.call("Browser.close", timeout=5)
                return width, height, browser
            except (RuntimeError, OSError, ValueError, KeyError) as exc:
                last_err = exc
            finally:
                if page:
                    page.ws.close()
                if proc:
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
                    _stop(proc)
    raise RuntimeError(f"could not screenshot {html_path} with {browser}: {last_err}")


# ----------------------------------------------------------------------------- render + probe (docs/architecture.md section 4.7)
# One CDP session: load, wait for fonts, measure (fonts, roles, overflow, text contrast, logo px), then
# screenshot the viewport or each `.page` element, then optionally print to PDF.

PROBE_JS = r"""(async () => {
  const out = {fonts: [], roles: [], overflow: [], overflow_mock: [], overflow_clamped: [], logo_ground: [], text_contrast: [], logo_px: {}, logo_px_detail: []};
  if (document.fonts) {
    await Promise.all([...document.fonts].map(f => (f.status === 'unloaded' ? f.load() : f.loaded).catch(() => null)));
    await document.fonts.ready;
    for (const f of document.fonts)
      out.fonts.push({family: f.family.replace(/^["']|["']$/g, ''), weight: String(f.weight), style: f.style, status: f.status});
  }
  const sel = (el) => {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && parts.length < 4; n = n.parentElement) {
      if (n.id) { parts.unshift('#' + n.id); break; }
      let s = n.tagName.toLowerCase();
      const cls = [...n.classList].filter(c => !/^is-/.test(c))[0];
      if (cls) s += '.' + cls;
      const p = n.parentElement;
      if (p) {
        const same = [...p.children].filter(c => c.tagName === n.tagName && (!cls || c.classList.contains(cls)));
        if (same.length > 1) s += ':nth-of-type(' + ([...p.children].filter(c => c.tagName === n.tagName).indexOf(n) + 1) + ')';
      }
      parts.unshift(s);
      if (n.dataset && (n.dataset.block || n.classList.contains('page'))) break;
    }
    return parts.join(' > ');
  };
  const rgba = (s) => {
    const m = /rgba?\(([^)]+)\)/.exec(s || '');
    if (!m) return null;
    const v = m[1].split(/[\s,\/]+/).filter(Boolean).map(parseFloat);
    return [v[0], v[1], v[2], v.length > 3 ? v[3] : 1];
  };
  const over = (top, bot) => { const a = top[3]; return [0, 1, 2].map(i => top[i] * a + bot[i] * (1 - a)).concat([1]); };
  const lum = (c) => { const f = x => { x /= 255; return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const background = (el) => {
    const layers = [];
    let opacity = 1;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const cs = getComputedStyle(n);
      opacity *= parseFloat(cs.opacity || '1');
      if (cs.backgroundImage && cs.backgroundImage !== 'none' && !n.hasAttribute('data-bg-ok')) return {unknown: true};
      const c = rgba(cs.backgroundColor);
      if (c && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
    }
    let bg = [255, 255, 255, 1];
    for (let i = layers.length - 1; i >= 0; i--) bg = over(layers[i], bg);
    return {bg, opacity};
  };
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const ownText = (el) => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
  const frameOf = (el) => el.closest('[data-frame], .page') || document.body;
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('svg, [data-probe-skip], script, style, head') || !ownText(el) || !visible(el)) continue;
    const cs = getComputedStyle(el);
    // overflow = the text's glyph boxes (Range rects) leave their block horizontally, or leave a clipping
    // ancestor / the frame. Vertically each line may exceed its line box by half the content-area surplus
    // (tall ascent/descent fonts at line-height < 1 are not a defect); a clipped line is.
    let over_ = false;
    const frame = frameOf(el);
    let blk = el;
    while (blk !== frame && getComputedStyle(blk).display.startsWith('inline')) blk = blk.parentElement;
    const clips = [];
    for (let n = el; n; n = n.parentElement) {
      const c = getComputedStyle(n);
      if (n === frame || c.overflowX !== 'visible' || c.overflowY !== 'visible')
        clips.push({r: n.getBoundingClientRect(), x: n === frame || c.overflowX !== 'visible', y: n === frame || c.overflowY !== 'visible'});
      if (n === frame) break;
    }
    const br = blk.getBoundingClientRect();
    const lhRaw = parseFloat(cs.lineHeight);
    for (const n of el.childNodes) {
      if (n.nodeType !== 3 || !n.textContent.trim()) continue;
      const rg = document.createRange(); rg.selectNodeContents(n);
      for (const r of rg.getClientRects()) {
        if (!r.width) continue;
        const lh = isNaN(lhRaw) ? r.height : lhRaw;
        const tolV = Math.max(1, (r.height - lh) / 2 + 1);
        if (r.left < br.left - 2 || r.right > br.right + 2) over_ = true;
        for (const c of clips) {
          if (c.x && (r.left < c.r.left - 1 || r.right > c.r.right + 1)) over_ = true;
          if (c.y && (r.top < c.r.top - tolV || r.bottom > c.r.bottom + tolV)) over_ = true;
        }
      }
    }
    if (over_) (el.closest('[data-clamped]') ? out.overflow_clamped : el.closest('[data-mock]') ? out.overflow_mock : out.overflow).push(sel(el));
    // contrast against the composited background
    const b = background(el);
    if (b.unknown) continue;
    let fg = rgba(cs.color);
    if (!fg) continue;
    fg = over([fg[0], fg[1], fg[2], fg[3] * b.opacity], b.bg);
    const size = parseFloat(cs.fontSize), weight = parseInt(cs.fontWeight, 10) || 400;
    let required = (size >= 24 || (size >= 18.66 && weight >= 700)) ? 3 : 4.5;
    if (weight <= 300 || el.closest('[data-hc-serif]')) required = 4.5;  // heuristic, uncalibrated
    const r = Math.round(ratio(fg, b.bg) * 100) / 100;
    out.text_contrast.push({selector: sel(el), text: el.textContent.trim().slice(0, 40), ratio: r, required, ok: r >= required});
  }
  // boxes that must stay whole (buttons, chips): their border box inside every clipping ancestor and the frame
  for (const el of document.querySelectorAll('[data-probe-box]')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect(), frame = frameOf(el);
    let bad = false;
    for (let n = el.parentElement; n; n = n.parentElement) {
      const c = getComputedStyle(n), cr = n.getBoundingClientRect();
      if (n === frame || c.overflowX !== 'visible') if (r.left < cr.left - 1 || r.right > cr.right + 1) bad = true;
      if (n === frame || c.overflowY !== 'visible') if (r.top < cr.top - 1 || r.bottom > cr.bottom + 1) bad = true;
      if (n === frame) break;
    }
    if (bad) (el.closest('[data-mock]') ? out.overflow_mock : out.overflow).push(sel(el));
  }
  // logos on grounds: every visible filled/stroked part of the SVG against the box's composited ground (or the
  // device tile/outline it sits on); a [data-one-colour] box must paint a single colour
  const hex = (c) => '#' + c.slice(0, 3).map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
  for (const box of document.querySelectorAll('[data-logo-ground]')) {
    if (!visible(box) || box.closest('[data-probe-skip]')) continue;
    const g = background(box);
    if (g.unknown) continue;
    const shapes = [...box.querySelectorAll('svg path, svg rect, svg circle, svg ellipse, svg polygon, svg polyline, svg line')];
    const dev = shapes.find(x => /^device/.test(x.getAttribute('data-parts') || ''));
    let base = g.bg;
    if (dev) { const dc = rgba(getComputedStyle(dev).fill); if (dc && dc[3] > 0) base = over(dc, g.bg); }
    const colours = new Set();
    let min = 21, raster = box.querySelector('img') ? 1 : 0, main = null;
    for (const sh of shapes) {
      if (sh === dev) continue;
      const cs = getComputedStyle(sh);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      const rr = sh.getBoundingClientRect(), area = rr.width * rr.height;
      for (const prop of ['fill', 'stroke']) {
        const v = cs[prop];
        if (!v || v === 'none' || (prop === 'stroke' && !(parseFloat(cs.strokeWidth) > 0))) continue;
        const c = rgba(v);
        if (!c || c[3] === 0) continue;
        let op = parseFloat(cs.opacity || '1') * parseFloat(prop === 'fill' ? cs.fillOpacity || '1' : cs.strokeOpacity || '1');
        const fc = over([c[0], c[1], c[2], c[3] * op], base);
        colours.add(hex(fc));
        const rt = ratio(fc, base);
        min = Math.min(min, rt);
        if (prop === 'fill' && (!main || area > main.area)) main = {area, ratio: rt};
      }
    }
    if (!colours.size && !raster) continue;
    out.logo_ground.push({selector: sel(box), ground: hex(base), min_ratio: Math.round(min * 100) / 100,
      main_ratio: Math.round((main ? main.ratio : min) * 100) / 100, device: !!dev,
      colours: [...colours], one_colour: box.hasAttribute('data-one-colour'), raster: !!raster,
      name: box.dataset.logo || ''});
  }
  for (const el of document.querySelectorAll('[data-role]')) {
    if (!visible(el)) continue;
    const cs = getComputedStyle(el);
    const fam = cs.fontFamily.split(',')[0].trim().replace(/^["']|["']$/g, '');
    out.roles.push({selector: sel(el), role: el.dataset.role, family: fam, weight: String(parseInt(cs.fontWeight, 10) || 400), style: cs.fontStyle});
  }
  for (const el of document.querySelectorAll('[data-logo]')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    const key = el.dataset.logo || sel(el);
    const px = Math.round(r.height * 10) / 10;
    out.logo_px_detail.push({selector: sel(el), name: key, px, min: parseFloat(el.dataset.min || '0') || null});
    out.logo_px[key] = key in out.logo_px ? Math.min(out.logo_px[key], px) : px;
  }
  return out;
})()"""

PAGES_JS = r"""(() => [...document.querySelectorAll('.page')].map((p, i) => {
  const r = p.getBoundingClientRect();
  return {x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height, name: p.dataset.name || ('page-' + (i + 1))};
}))()"""


def _norm_font(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def pdf_font_report(data):
    """Fonts in a PDF from its bytes (stdlib): embedded program per FontDescriptor, Type3 count.
    Chromium/Skia writes font dictionaries uncompressed; an /ObjStm PDF is reported as `opaque`."""
    if isinstance(data, str):
        with open(data, "rb") as fh:
            data = fh.read()
    fonts = {}
    for chunk in re.split(rb"endobj", data):
        if b"/FontDescriptor" in chunk:
            m = re.search(rb"/FontName\s*/([^\s/\[\]<>()]+)", chunk)
            if not m:
                continue
            name = re.sub(r"^[A-Z]{6}\+", "", m.group(1).decode("latin-1"))
            kind = next((k for k in ("FontFile2", "FontFile3", "FontFile") if re.search(rb"/" + k.encode() + rb"[\s\d]", chunk)),
                        None)
            if kind or name not in fonts:
                fonts[name] = kind
    base = sorted({re.sub(r"^[A-Z]{6}\+", "", m.decode("latin-1"))
                   for m in re.findall(rb"/BaseFont\s*/([^\s/\[\]<>()]+)", data)})
    return {"fonts": [{"name": n, "embedded": k} for n, k in sorted(fonts.items())], "base_fonts": base,
            "type3": len(re.findall(rb"/Subtype\s*/Type3", data)), "opaque": b"/ObjStm" in data}


def pdf_font_findings(report, families):
    """Gate findings for a PDF: every family is embedded (FontFile2/3) and no Type3 fonts (a variable font that
    leaked through as outlines-per-glyph). `families`: CSS family names that the document must use."""
    from identitylib import finding  # local import keeps screenshot_html stdlib-only
    out = []
    embedded = [_norm_font(f["name"]) for f in report["fonts"] if f["embedded"] in ("FontFile2", "FontFile3")]
    for fam in sorted(set(families)):
        key = _norm_font(fam)
        if not any(e.startswith(key) for e in embedded):
            out.append(finding("pdf-font-not-embedded", "kit", "gate",
                               f"PDF: {fam} is not embedded as /FontFile2 or /FontFile3",
                               measured=[f["name"] for f in report["fonts"]], threshold="embedded",
                               suggested_fix="use a static instance (typelib.resolve_font) in @font-face"))
    if report["type3"]:
        out.append(finding("pdf-type3-font", "kit", "gate",
                           f"PDF has {report['type3']} Type3 font(s): a variable font leaked through",
                           measured=report["type3"], threshold=0,
                           suggested_fix="pin every fvar axis (static instance) before rendering"))
    return out


def render(html, out, width, height, pdf=False, pages=False, probe=True, scale=2.0, browser=None, timeout=90):
    """Render a local HTML file in one CDP session and measure it.

    out:   PNG path (viewport screenshot), or a directory when `pages` is set (one PNG per `.page` element,
           named NN-<data-name>.png).
    pdf:   False, True (PDF next to `out`, same stem) or a PDF path. Uses CSS @page size.
    pages: clip-screenshot each `.page` element (the viewport grows to the document height).
    probe: return FontFace states, each [data-role]'s computed family, overflowing text, text contrast and
           [data-logo] heights.
    Returns {png|pages, pdf, pdf_fonts, fonts, roles, overflow, text_contrast, logo_px, logo_px_detail, browser}.
    """
    if not os.path.isfile(html):
        raise FileNotFoundError(f"HTML file not found: {html}")
    browser = browser or find_browser()
    if not browser:
        raise RuntimeError(NO_BROWSER)
    out = os.path.abspath(out)
    if pdf is True:
        pdf = (out.rstrip("/\\") if pages else os.path.splitext(out)[0]) + ".pdf"
    os.makedirs(out if pages else os.path.dirname(out), exist_ok=True)
    result = {"browser": browser}
    last_err = None
    for _attempt in range(2):
        with _tempdir() as tmp:
            proc = page = None
            try:
                proc, port, _ = _launch(browser, os.path.join(tmp, "profile"), timeout)
                page = _Page(_page_ws(port, timeout), timeout)
                metrics = {"width": width, "height": height, "deviceScaleFactor": scale, "mobile": False}
                page.call("Emulation.setDeviceMetricsOverride", metrics)
                page.call("Page.enable")
                page.call("Page.navigate", {"url": _file_url(html)}, timeout)
                page.wait_event("Page.loadEventFired", timeout)
                doc_h = page.eval(SETTLE_JS, timeout) or height
                if probe:
                    result.update(page.eval(PROBE_JS, timeout) or {})
                if pages:
                    metrics["height"] = max(height, min(MAX_HEIGHT, int(doc_h)))
                    page.call("Emulation.setDeviceMetricsOverride", metrics)
                    page.eval("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))", timeout)
                    rects = page.eval(PAGES_JS, timeout) or []
                    written = []
                    for i, r in enumerate(rects, 1):
                        clip = {"x": r["x"], "y": r["y"], "width": r["w"], "height": r["h"], "scale": 1}
                        shot = page.call("Page.captureScreenshot", {"format": "png", "clip": clip,
                                                                     "captureBeyondViewport": True}, timeout)
                        name = re.sub(r"[^a-z0-9-]+", "-", str(r["name"]).lower()).strip("-") or "page"
                        path = os.path.join(out, f"{i:02d}-{name}.png")
                        with open(path, "wb") as fh:
                            fh.write(base64.b64decode(shot["data"]))
                        written.append(path)
                    result["pages"] = written
                else:
                    shot = page.call("Page.captureScreenshot", {"format": "png", "fromSurface": True}, timeout)
                    with open(out + ".part", "wb") as fh:
                        fh.write(base64.b64decode(shot["data"]))
                    os.replace(out + ".part", out)
                    result["png"] = out
                if pdf:
                    pdf = os.path.abspath(pdf)
                    os.makedirs(os.path.dirname(pdf), exist_ok=True)
                    r = page.call("Page.printToPDF", {"printBackground": True, "preferCSSPageSize": True,
                                                      "marginTop": 0, "marginBottom": 0, "marginLeft": 0,
                                                      "marginRight": 0}, max(timeout, 120))
                    data = base64.b64decode(r["data"])
                    with open(pdf, "wb") as fh:
                        fh.write(data)
                    result["pdf"] = pdf
                    result["pdf_fonts"] = pdf_font_report(data)
                with contextlib.suppress(Exception):
                    page.call("Browser.close", timeout=5)
                return result
            except (RuntimeError, OSError, ValueError, KeyError) as exc:
                last_err = exc
            finally:
                if page:
                    page.ws.close()
                if proc:
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
                    _stop(proc)
    raise RuntimeError(f"could not render {html} with {browser}: {last_err}")


def probe_findings(result, expected_fonts=(), roles=None, min_logo_px=None, component="card"):
    """Turn a render() probe into gate findings (docs/architecture.md section 4.7).

    expected_fonts: [(family, weight)] that must have a FontFace with status 'loaded'.
    roles: {role: family} — every [data-role=role] element's computed first family must be that family and a
           loaded FontFace for (family, computed weight) must exist (a fallback face fails).
    min_logo_px: every [data-logo] element must be at least this tall (elements may carry data-min instead).
    """
    from identitylib import finding
    out = []
    loaded = {(_norm_font(f["family"]), str(f["weight"])) for f in result.get("fonts", []) if f["status"] == "loaded"}

    def has(fam, w):
        key = _norm_font(fam)
        if (key, str(w)) in loaded:
            return True
        for lf, lw in loaded:  # weight ranges such as "100 900"
            parts = lw.split()
            if lf == key and len(parts) == 2 and all(p.isdigit() for p in parts) and int(parts[0]) <= int(w) <= int(parts[1]):
                return True
        return False

    for fam, w in expected_fonts:
        if not has(fam, w):
            st = [f["status"] for f in result.get("fonts", []) if _norm_font(f["family"]) == _norm_font(fam)]
            out.append(finding("font-not-loaded", component, "gate", f"{fam} {w} did not load",
                               measured=st or "no FontFace", threshold="loaded",
                               suggested_fix="check the @font-face file path (typelib cache instance)"))
    bad = {}
    for r in result.get("roles", []):
        want = (roles or {}).get(r["role"])
        if want and (_norm_font(r["family"]) != _norm_font(want) or not has(r["family"], r["weight"])):
            bad.setdefault(r["role"], r)
    for role, r in bad.items():
        out.append(finding("font-fallback", component, "gate",
                           f"{role} text renders in {r['family']} {r['weight']}, not a loaded {roles[role]}",
                           measured=f"{r['family']} {r['weight']}", threshold=roles[role],
                           suggested_fix="declare that weight in @font-face", roles=[role]))
    if result.get("overflow"):
        ov = result["overflow"]
        out.append(finding("text-overflow", component, "gate", f"{len(ov)} text element(s) overflow: {ov[0]}",
                           measured=ov[:8], threshold=0, suggested_fix="shorten the text or let the block wrap"))
    if result.get("overflow_mock"):
        ov = result["overflow_mock"]
        out.append(finding("mock-overflow", component, "warn",
                           f"{len(ov)} element(s) in the mock application overflow: {ov[0]}", measured=ov[:8],
                           threshold=0, suggested_fix="shorter brand copy (identity.brand.copy) or a smaller logo"))
    for lg in result.get("logo_ground", []):
        # docs/architecture.md section 4.4: the largest-area part is the gate, smaller parts warn; a tile/outline device is the ground
        # the parts are measured against when present
        main = lg.get("main_ratio", lg["min_ratio"])
        if lg["colours"] and main < 3:
            out.append(finding("logo-on-ground", component, "gate",
                               f"logo's main part {main}:1 against its ground {lg['ground']} at {lg['selector']}",
                               measured=main, threshold=3.0,
                               suggested_fix="use the logolib version for that ground, or one colour with contrast"))
        elif lg["colours"] and lg["min_ratio"] < 3:
            out.append(finding("logo-part-on-ground", component, "warn",
                               f"a smaller logo part is {lg['min_ratio']}:1 against {lg['ground']} at {lg['selector']}",
                               measured=lg["min_ratio"], threshold=3.0,
                               suggested_fix="declare logo.device (tile/outline) or recolour that part"))
        if lg["one_colour"] and len(lg["colours"]) > 1:
            out.append(finding("logo-not-one-colour", component, "gate",
                               f"'one colour' logo paints {len(lg['colours'])} colours at {lg['selector']}",
                               measured=lg["colours"], threshold=1,
                               suggested_fix="force every fill of the logo to the one colour"))
    if result.get("overflow_clamped"):
        ov = result["overflow_clamped"]
        out.append(finding("content-clamped", component, "warn",
                           f"{len(ov)} long text block(s) shortened with an ellipsis on the card (full text in "
                           f"table.md): {ov[0]}", measured=ov[:8], threshold=0,
                           suggested_fix="a shorter mechanism / differs_by reads better on the card"))
    low = [t for t in result.get("text_contrast", []) if not t["ok"]]
    if low:
        t = min(low, key=lambda x: x["ratio"])
        out.append(finding("text-contrast", component, "gate",
                           f"{len(low)} text node(s) below contrast; worst {t['ratio']}:1 at {t['selector']} "
                           f"('{t['text']}')", measured=t["ratio"], threshold=t["required"],
                           suggested_fix="use the palette's text/textMuted on its surfaces"))
    for d in result.get("logo_px_detail", []):
        need = d.get("min") or min_logo_px
        if need and d["px"] + 0.5 < need:
            out.append(finding("logo-too-small", component, "gate",
                               f"logo '{d['name']}' renders {d['px']} px tall, minimum {need} px",
                               measured=d["px"], threshold=need, suggested_fix="raise the logo size in the template"))
    return out


def png_size(path):
    with open(path, "rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def main(argv=None):
    setup_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("html", nargs="?", help="local HTML file")
    ap.add_argument("-o", "--out", help="output PNG (default: next to the HTML)")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--scale", type=float, default=1.0, help="device scale factor (2 = retina)")
    ap.add_argument("--full-page", action="store_true", help=f"capture the whole page height (max {MAX_HEIGHT}px)")
    ap.add_argument("--mobile", action="store_true", help="emulate a mobile viewport (meta viewport honoured)")
    ap.add_argument("--color-scheme", choices=["light", "dark"], help="emulate prefers-color-scheme")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--which", action="store_true", help="print the browser that would be used and exit")
    ap.add_argument("--probe", action="store_true", help="measure fonts, overflow, contrast, logo px (JSON on stdout)")
    ap.add_argument("--pages", metavar="DIR", help="one PNG per .page element into DIR")
    ap.add_argument("--pdf", metavar="PDF", help="also print a PDF (CSS @page size) and check its fonts")
    a = ap.parse_args(argv)
    if a.which:
        cands = browser_candidates()
        if not cands:
            print(NO_BROWSER, file=sys.stderr)
            return 3
        print(cands[0])
        for c in cands[1:]:
            print(f"  also: {c}", file=sys.stderr)
        return 0
    if not a.html:
        ap.print_usage(sys.stderr)
        print("error: give an HTML file (or --which)", file=sys.stderr)
        return 2
    if a.width < 16 or a.height < 16 or not (0.5 <= a.scale <= 4):
        print("error: width/height must be >= 16 and scale within 0.5-4", file=sys.stderr)
        return 2
    out = a.out or os.path.splitext(a.html)[0] + ".png"
    if a.probe or a.pages or a.pdf:
        try:
            r = render(a.html, a.pages or out, a.width, a.height, pdf=a.pdf or False, pages=bool(a.pages),
                       probe=a.probe, scale=a.scale, timeout=a.timeout)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3 if str(exc) == NO_BROWSER else 1
        summary = {k: r[k] for k in ("png", "pages", "pdf", "pdf_fonts", "fonts", "overflow", "logo_px") if k in r}
        if "text_contrast" in r:
            summary["text_contrast_failures"] = [t for t in r["text_contrast"] if not t["ok"]]
        print(json.dumps(summary, indent=1)[:6000])
        return 0
    try:
        w, h, browser = screenshot_html(a.html, out, a.width, a.height, a.scale, a.full_page, timeout=a.timeout,
                                         mobile=a.mobile, color_scheme=a.color_scheme)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3 if str(exc) == NO_BROWSER else 1
    print(out)
    print(f"wrote {out} ({w}x{h} css px @{a.scale:g}x) via {browser}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
