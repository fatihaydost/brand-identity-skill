"""Unit tests for skills/brand-identity/scripts/identitylib.py and `brand.py init` (stdlib unittest)."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
EXAMPLE = os.path.join(ROOT, "skills", "brand-identity", "templates", "identity.example.json")
sys.path.insert(0, SCRIPTS)

import identitylib as I  # noqa: E402


class IdentityContract(unittest.TestCase):
    def setUp(self):
        with open(EXAMPLE, encoding="utf-8") as fh:
            self.ok = json.load(fh)

    def broken(self, mutate):
        d = copy.deepcopy(self.ok)
        mutate(d)
        return d

    def assertRejects(self, mutate, field):
        with self.assertRaises(I.IdentityError) as cm:
            I.validate_identity(self.broken(mutate))
        self.assertIn(field, str(cm.exception))

    def test_example_is_valid(self):
        I.load_identity(EXAMPLE)

    def test_axis_out_of_range(self):
        self.assertRejects(lambda d: d["axes"].update(warm_cool=3), "axes.warm_cool")

    def test_unknown_axis(self):
        self.assertRejects(lambda d: d["axes"].update(spicy_mild=1), "axes.spicy_mild")

    def test_raw_hex_in_logo_colours(self):
        self.assertRejects(lambda d: d["logo"]["colors"].update(symbol="#112233"), "logo.colors.symbol")

    def test_keep_needs_fixed_and_source(self):
        self.assertRejects(lambda d: d["components"]["logo"].update(mode="keep"), "components.logo.status")
        self.assertRejects(lambda d: d["components"]["logo"].update(mode="keep", status="fixed"),
                           "components.logo.source")

    def test_symbol_logo_needs_svg(self):
        self.assertRejects(lambda d: d["logo"].update(symbol=None), "logo.symbol")

    def test_defaults_used_needs_reason(self):
        self.assertRejects(lambda d: d["defaults_used"].append({"id": "inter-body", "why": ""}),
                           "defaults_used[0].why")

    def test_face_license_required(self):
        self.assertRejects(lambda d: d["type"]["display"].pop("license"), "type.display.license")

    def test_save_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "identity.json")
            I.save_identity(p, self.ok)
            self.assertEqual(I.load_identity(p), self.ok)


class InitCommand(unittest.TestCase):
    def test_init_makes_partial_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "brand.py"), "init", "Moon Vault",
                                "--sets", "2", "--langs", "en,tr", "--state", "logo=keep"],
                               cwd=tmp, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            work = os.path.join(tmp, "brand-identity", "moon-vault")
            dirs = I.set_dirs(work)
            self.assertEqual([os.path.basename(d) for d in dirs], ["A", "B"])
            d = I.load_identity(os.path.join(dirs[0], "identity.json"), partial=True)
            self.assertEqual(d["components"]["logo"], {"mode": "keep", "status": "fixed", "source": None,
                                                       "sha256": None})
            self.assertEqual(d["brand"]["languages"], ["en", "tr"])

    def test_too_many_sets(self):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "brand.py"), "init", "X", "--sets", "5"],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--sets", r.stderr)


if __name__ == "__main__":
    unittest.main()


class CheckCommand(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt", "needs symlinks")
    def test_requirements_found_through_a_symlinked_skill(self):
        """~/.claude/skills/brand-identity -> repo/skills/brand-identity must still point at the repo's requirements."""
        tmp = tempfile.mkdtemp()
        try:
            link = os.path.join(tmp, "skills", "brand-identity")
            os.makedirs(os.path.dirname(link))
            os.symlink(os.path.join(ROOT, "skills", "brand-identity"), link)
            code = ("import sys; sys.argv=['brand.py']; import runpy; "
                    f"g = runpy.run_path({os.path.join(link, 'scripts', 'brand.py')!r}, run_name='probe'); "
                    "print(g['_requirements']())")
            r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(os.path.realpath(r.stdout.strip()), os.path.realpath(os.path.join(ROOT, "requirements.txt")))
        finally:
            import shutil
            shutil.rmtree(tmp)
