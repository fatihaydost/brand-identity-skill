"""pydeps: entry scripts fetch their Python packages through uv (or the fallback venv) instead of a manual pip install."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import pydeps  # noqa: E402

# A stand-in `uv`: logs its arguments, answers the import probe, and runs `python SCRIPT ...` with the test's own
# interpreter (so the hop is real but nothing is downloaded).
FAKE_UV = textwrap.dedent("""\
    #!{python}
    import os, subprocess, sys
    with open(os.environ["FAKE_UV_LOG"], "a", encoding="utf-8") as fh:
        fh.write(" ".join(sys.argv[1:]) + "\\n")
    args = sys.argv[1:]
    cmd = args[args.index("python") + 1:]
    if cmd[:1] == ["-c"]:
        sys.exit(0)
    sys.exit(subprocess.run([sys.executable, *cmd]).returncode)
    """)

PROBE = textwrap.dedent("""\
    import os, sys
    sys.path.insert(0, {scripts!r})
    import pydeps
    pydeps.MODULES = pydeps.MODULES + (("bi_no_such_module", "bi-no-such-module", "test"),)
    pydeps.ensure()
    print("env=" + os.environ.get(pydeps.ENV_FLAG, "-"), " ".join(sys.argv[1:]))
    """)


class Lines(unittest.TestCase):
    def test_install_lines_offer_uv_then_a_venv_never_a_bare_pip(self):
        lines = pydeps.install_lines()
        self.assertIn("astral.sh/uv/install", lines[0])
        self.assertIn("-m venv", lines[1])
        self.assertIn(pydeps.venv_dir(), lines[1])
        self.assertNotIn("--break-system-packages", " ".join(lines))
        # the pip install runs with the venv's own interpreter, not the system one (PEP 668)
        self.assertNotRegex(lines[1], r"(^|\s)(python3?|py -3) -m pip")

    def test_uv_command_is_outside_any_project_and_uses_the_requirements(self):
        cmd = pydeps.uv_command("uv", "python", "x.py", "--flag")
        self.assertEqual(cmd[:2], ["uv", "run"])
        self.assertIn("--no-project", cmd)
        self.assertEqual(cmd[cmd.index("--with-requirements") + 1], pydeps.requirements())
        self.assertEqual(cmd[-3:], ["python", "x.py", "--flag"])
        self.assertEqual(cmd[cmd.index("--python") + 1], sys.executable)

    def test_requirements_carry_every_module(self):
        with open(pydeps.requirements(), encoding="utf-8") as fh:
            req = fh.read()
        for _, line, _ in pydeps.MODULES:
            self.assertIn(line, req)


@unittest.skipIf(os.name == "nt", "the stand-in uv is a POSIX script")
class Hop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "uv.log")
        uv = os.path.join(self.tmp, "uv")
        with open(uv, "w", encoding="utf-8") as fh:
            fh.write(FAKE_UV.format(python=sys.executable))
        os.chmod(uv, 0o755)
        self.script = os.path.join(self.tmp, "probe.py")
        with open(self.script, "w", encoding="utf-8") as fh:
            fh.write(PROBE.format(scripts=SCRIPTS))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_probe(self, path):
        env = dict(os.environ, PATH=path, FAKE_UV_LOG=self.log, HOME=self.tmp, XDG_CACHE_HOME=self.tmp)
        env.pop(pydeps.ENV_FLAG, None)
        return subprocess.run([sys.executable, self.script, "a", "b c"], capture_output=True, text=True, env=env,
                              stdin=subprocess.DEVNULL, timeout=120)

    def test_missing_package_reruns_once_through_uv_with_the_same_arguments(self):
        r = self.run_probe(self.tmp + os.pathsep + os.environ.get("PATH", ""))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "env=uv a b c")
        with open(self.log, encoding="utf-8") as fh:
            calls = fh.read().splitlines()
        self.assertEqual(len(calls), 2, calls)  # the import probe, then the re-run; the re-run does not hop again
        self.assertIn("-c", calls[0])
        self.assertTrue(calls[1].endswith(f"python {self.script} a b c"), calls[1])
        self.assertIn("--no-project", calls[1])

    def test_without_uv_or_venv_the_script_runs_as_is(self):
        r = self.run_probe(os.path.dirname(sys.executable))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "env=- a b c")
        self.assertFalse(os.path.exists(self.log))


if __name__ == "__main__":
    unittest.main()
