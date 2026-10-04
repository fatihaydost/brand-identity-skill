"""Browser start-up shared by render_png.py and cdplib.py: the --no-sandbox fallback, proxy-free localhost DevTools
calls, no --remote-allow-origins, the real start in `brand.py check`, and no orphaned browser when site_palette.py is
interrupted. Everything here needs a Chromium-based browser and is skipped without one.
"""
import base64
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import cdplib  # noqa: E402
import render_png as rp  # noqa: E402

BROWSER = rp.find_browser()
needs_browser = unittest.skipUnless(BROWSER, "no Chromium-based browser found")
posix_only = unittest.skipIf(os.name == "nt", "the fake browser is a shell script")

PAGE = "<!doctype html><html><body style='margin:0;background:#c2410c'></body></html>"
SANDBOX_MSG = "[1:1:ERROR:zygote_host_impl_linux.cc(129)] No usable sandbox! you can try using --no-sandbox."


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bi-launch-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.html = os.path.join(self.tmp, "page.html")
        Path(self.html).write_text(PAGE, encoding="utf-8")

    def fake(self, name, body):
        """A shell script standing in for the browser; it logs every command line to <name>.args."""
        path = os.path.join(self.tmp, name)
        Path(path).write_text("#!/bin/sh\n" f'printf "%s\\n" "$*" >> "{path}.args"\n' + body, encoding="utf-8")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def no_sandbox_fake(self):
        """Exits 1 like Chromium in a container without user namespaces unless --no-sandbox is given; then runs the
        real browser (exec keeps the DevTools pipe fds 3 and 4)."""
        return self.fake("nosandbox-chrome", 'for a in "$@"; do\n'
                         f'  if [ "$a" = "--no-sandbox" ]; then exec "{BROWSER}" "$@"; fi\n'
                         f'done\necho "{SANDBOX_MSG}" >&2\nexit 1\n')

    def calls(self, fake):
        return Path(fake + ".args").read_text(encoding="utf-8").splitlines()


@needs_browser
@posix_only
class SandboxFallback(_Tmp):
    """A browser that cannot start with its sandbox is started again with --no-sandbox, once."""

    def test_render_png_retries_without_sandbox(self):
        fake = self.no_sandbox_fake()
        out = os.path.join(self.tmp, "page.png")
        rp.screenshot_html(self.html, out, 64, 48, browser=fake, timeout=30)
        self.assertEqual(rp.png_size(out), (64, 48))
        calls = self.calls(fake)
        self.assertNotIn("--no-sandbox", calls[0].split(), "the first start keeps the sandbox")
        self.assertIn("--no-sandbox", calls[1].split())

    def test_render_probe_retries_without_sandbox(self):
        fake = self.no_sandbox_fake()
        r = rp.render(self.html, os.path.join(self.tmp, "probe.png"), 64, 48, probe=False, scale=1, browser=fake,
                      timeout=30)
        self.assertTrue(os.path.isfile(r["png"]))

    def test_cdplib_pipe_retries_without_sandbox(self):
        fake = self.no_sandbox_fake()
        b = cdplib.Browser(fake, timeout=30, pipe=True)
        try:
            self.assertTrue(b.no_sandbox)
            self.assertIn("Chrome", b.version)
        finally:
            b.close()
        self.assertNotIn("--no-sandbox", self.calls(fake)[0].split())

    def test_cdplib_websocket_retries_without_sandbox(self):
        fake = self.no_sandbox_fake()
        b = cdplib.Browser(fake, timeout=30, pipe=False)
        try:
            self.assertTrue(b.no_sandbox)
        finally:
            b.close()

    def test_check_reports_no_sandbox(self):
        r = rp.launch_check(self.no_sandbox_fake(), timeout=30)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["no_sandbox"])

    def test_browser_that_never_starts(self):
        fake = self.fake("dead-chrome", f'echo "{SANDBOX_MSG}" >&2\nexit 1\n')
        r = rp.launch_check(fake, timeout=10)
        self.assertFalse(r["ok"])
        self.assertIn("No usable sandbox", r["error"])
        self.assertIn("with --no-sandbox", r["error"])
        self.assertEqual(len(self.calls(fake)), 2, "one retry, not more")
        b, errors = cdplib.launch(fake)
        self.assertIsNone(b)
        self.assertIn("exited during start-up", errors[0])

    def test_sandbox_kept_when_it_works(self):
        fake = self.fake("ok-chrome", f'exec "{BROWSER}" "$@"\n')
        r = rp.launch_check(fake, timeout=30)
        self.assertTrue(r["ok"], r)
        if os.geteuid() != 0 and not rp.launch_check(BROWSER, timeout=30)["no_sandbox"]:
            self.assertFalse(r["no_sandbox"])
            self.assertTrue(all("--no-sandbox" not in c.split() for c in self.calls(fake)))


@needs_browser
class NoProxyForLocalhost(_Tmp):
    """A proxy in the environment (or the Windows registry) must not catch the 127.0.0.1 DevTools requests."""

    def setUp(self):
        super().setUp()
        env = {k: v for k, v in os.environ.items() if k.lower() not in ("no_proxy", "http_proxy", "all_proxy")}
        env.update(HTTP_PROXY="http://127.0.0.1:9", http_proxy="http://127.0.0.1:9")
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_render_png_behind_proxy(self):
        out = os.path.join(self.tmp, "page.png")
        rp.screenshot_html(self.html, out, 64, 48, browser=BROWSER, timeout=20)
        self.assertEqual(rp.png_size(out), (64, 48))

    def test_cdplib_websocket_behind_proxy(self):
        b = cdplib.Browser(BROWSER, timeout=20, pipe=False)
        try:
            self.assertTrue(b.version)
        finally:
            b.close()


@needs_browser
class NoRemoteAllowOrigins(_Tmp):
    """Without --remote-allow-origins a web page origin cannot open the DevTools socket; our client sends no Origin."""

    @staticmethod
    def handshake(ws_url, origin):
        host, rest = ws_url[len("ws://"):].split("/", 1)
        h, port = host.rsplit(":", 1)
        with socket.create_connection((h, int(port)), timeout=10) as s:
            key = base64.b64encode(os.urandom(16)).decode()
            s.sendall((f"GET /{rest} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: {origin}\r\n\r\n").encode())
            return s.recv(256).split(b"\r\n", 1)[0].decode("latin-1")

    def test_cdplib_websocket_refuses_web_origins(self):
        b = cdplib.Browser(BROWSER, timeout=30, pipe=False)
        try:
            page = b.new_page(viewport=(64, 48))  # our own client works without the flag
            self.assertEqual(page.evaluate("() => 1 + 1"), 2)
            status = self.handshake(b.ws_url, "https://evil.example")
            self.assertNotIn(" 101 ", status, "a web origin must not get a DevTools socket")
        finally:
            b.close()

    def test_render_png_refuses_web_origins(self):
        with rp._tempdir() as tmp:
            proc, port, _ = rp._launch(BROWSER, os.path.join(tmp, "profile"), 30)
            try:
                ws = rp._page_ws(port, 10)
                self.assertNotIn(" 101 ", self.handshake(ws, "https://evil.example"))
                page = rp._Page(ws, 10)
                self.assertEqual(page.eval("1 + 1"), 2)
                page.ws.close()
            finally:
                rp._stop(proc)


@needs_browser
class SitePaletteCleanup(_Tmp):
    """An interrupt (Ctrl+C) in site_palette.py still closes the browser it started."""

    def test_keyboard_interrupt_closes_browser(self):
        import site_palette as M
        started = []
        real_launch = cdplib.launch

        def launch(*a, **kw):
            b, errors = real_launch(*a, **kw)
            started.append(b)
            return b, errors

        def interrupted(*_a, **_kw):
            raise KeyboardInterrupt

        with mock.patch.object(cdplib, "launch", launch), mock.patch.object(M, "cmd_extract", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                M.main(["extract", Path(self.html).as_uri(), "--out", os.path.join(self.tmp, "out")])
        self.assertTrue(started and started[0])
        b = started[0]
        self.assertIsNone(b.proc, "the browser was not closed")
        self.assertFalse(os.path.exists(b._tmp))


@needs_browser
class CheckStartsTheBrowser(unittest.TestCase):
    def test_check_line(self):
        env = dict(os.environ, BRAND_IDENTITY_BROWSER=BROWSER)
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "brand.py"), "check"], capture_output=True,
                           text=True, encoding="utf-8", env=env, stdin=subprocess.DEVNULL, timeout=120)
        line = next((ln for ln in r.stdout.splitlines() if "browser" in ln), "")
        self.assertRegex(line, r"^\[ok\] browser: .* \(started in [0-9.]+ s", r.stdout + r.stderr)

    def test_check_line_when_the_browser_cannot_start(self):
        if os.name == "nt":
            self.skipTest("the fake browser is a shell script")
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "chrome")
            Path(fake).write_text("#!/bin/sh\necho broken >&2\nexit 3\n", encoding="utf-8")
            os.chmod(fake, 0o755)
            env = dict(os.environ, BRAND_IDENTITY_BROWSER=fake)
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "brand.py"), "check"], capture_output=True,
                               text=True, encoding="utf-8", env=env, stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"[--] browser: {fake} does not start", r.stdout)
        self.assertIn("BRAND_IDENTITY_BROWSER", r.stdout)


if __name__ == "__main__":
    unittest.main()
