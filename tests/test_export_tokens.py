"""Unit tests for skills/brand-identity/scripts/export_tokens.py (stdlib unittest)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
EXAMPLE = os.path.join(ROOT, "skills", "brand-identity", "templates", "palette.example.json")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import colorlib as cl  # noqa: E402
import export_tokens as et  # noqa: E402


class TestExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pal = cl.load_palette(EXAMPLE)
        cls.dir = tempfile.mkdtemp()
        cls.written = et.export(cls.pal, cls.dir, et.FORMATS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def read(self, name):
        with open(os.path.join(self.dir, name), encoding="utf-8") as fh:
            return fh.read()

    def test_all_files_written(self):
        self.assertEqual(sorted(os.path.basename(p) for p in self.written), sorted(et.FILENAMES.values()))

    def test_kebab(self):
        self.assertEqual(et.kebab("surfaceAlt"), "surface-alt")
        self.assertEqual(et.kebab("onPrimary"), "on-primary")
        self.assertEqual(et.kebab("textMuted"), "text-muted")
        self.assertEqual(et.kebab("background"), "background")

    def test_css(self):
        css = self.read("tokens.css")
        root = css.split('[data-theme="dark"]')[0]
        dark_attr = css.split('[data-theme="dark"]')[1].split("@media")[0]
        media = css.split("@media (prefers-color-scheme: dark)")[1]
        for role in cl.ROLE_KEYS:
            var = f"--bi-{et.kebab(role)}"
            self.assertIn(f"{var}: {self.pal['modes']['light'][role]};", root, role)
            self.assertIn(f"{var}: {self.pal['modes']['dark'][role]};", dark_attr, role)
            self.assertIn(f"{var}: {self.pal['modes']['dark'][role]};", media, role)
        self.assertIn(':root:not([data-theme="light"])', media)
        for k, v in self.pal["scales"]["primary"]["steps"].items():
            self.assertIn(f"--bi-primary-{k}: {v};", root)
        self.assertIn("--bi-brand-1: #1f5f7a;", root)
        self.assertEqual(css.count("{"), css.count("}"))

    def test_scss(self):
        scss = self.read("tokens.scss")
        self.assertIn("$bi-primary-800: #1f5f7a;", scss)
        self.assertIn("$bi-roles-light: (", scss)
        self.assertIn("$bi-roles-dark: (", scss)
        self.assertIn(f"on-primary: {self.pal['modes']['dark']['onPrimary']},", scss)
        self.assertEqual(scss.count("("), scss.count(")"))

    def test_tailwind_is_valid_js_with_var_refs(self):
        js = self.read("tailwind.palette.js")
        self.assertIn("'var(--bi-primary-600)'", js)
        self.assertIn("'on-primary': 'var(--bi-on-primary)'", js)
        self.assertEqual(len(re.findall(r"^        'primary':", js, re.M)), 1)   # no duplicate keys
        self.assertNotRegex(js, r"#[0-9a-f]{6}")                      # roles are variables, not frozen hex
        node = shutil.which("node")
        if node:
            r = subprocess.run([node, "-e", "const p=require(process.argv[1]);"
                                "const c=p.theme.extend.colors;"
                                "if(c.primary['600']!=='var(--bi-primary-600)')process.exit(3);"
                                "if(c.primary.DEFAULT!=='var(--bi-primary)')process.exit(4);"
                                "if(c['on-primary']!=='var(--bi-on-primary)')process.exit(5)",
                                os.path.join(self.dir, "tailwind.palette.js")],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_dtcg(self):
        doc = json.loads(self.read("tokens.dtcg.json"))

        def resolve(ref):
            node = doc
            for part in ref.strip("{}").split("."):
                node = node[part]
            return node
        n = 0
        for group in doc["color"].values():
            for tok in group.values():
                self.assertEqual(tok["$type"], "color")
                v = tok["$value"]
                self.assertEqual(v["colorSpace"], "srgb")
                self.assertTrue(all(0 <= c <= 1 for c in v["components"]))
                self.assertTrue(cl.is_hex(v["hex"]))
                n += 1
        self.assertEqual(n, len(self.pal["brand"]) + 11 * len(self.pal["scales"]))
        for mode in ("light", "dark"):
            for role in cl.ROLE_KEYS:
                tok = doc["role"][mode][et.kebab(role)]
                v = tok["$value"]
                hexv = resolve(v)["$value"]["hex"] if isinstance(v, str) else v["hex"]
                self.assertEqual(hexv, self.pal["modes"][mode][role], f"{mode}.{role}")
        self.assertTrue(isinstance(doc["role"]["light"]["text"]["$value"], str))     # aliases where possible

    def test_gpl(self):
        lines = self.read("palette.gpl").splitlines()
        self.assertEqual(lines[0], "GIMP Palette")
        self.assertTrue(lines[1].startswith("Name: "))
        rows = [ln for ln in lines if re.match(r"^\s*\d+\s+\d+\s+\d+\t", ln)]
        n_ext = len(self.pal.get("extended", []))
        self.assertEqual(len(rows), len(self.pal["brand"]) + 11 * len(self.pal["scales"]) + 2 * 16 + 2 * 2 * n_ext)
        self.assertIn(" 31  95 122\tbrand-1 Harbor Blue", rows)

    def test_swatches(self):
        page = self.read("swatches.html")
        self.assertTrue(page.startswith("<!doctype html>"))
        for b in self.pal["brand"]:
            self.assertIn(b["hex"], page)
            self.assertIn(b["name"], page)
        for v in self.pal["scales"]["accent"]["steps"].values():
            self.assertIn(v, page)
        self.assertIn("oklch(", page)
        self.assertIn("Lab D50", page)
        self.assertNotRegex(page, r"(src|href)=[\"']?https?:")         # self-contained, prints offline
        self.assertNotIn("APCA", page)

    def test_dtcg_per_mode(self):
        with tempfile.TemporaryDirectory() as d:
            written = et.export(self.pal, d, ["dtcg"], dtcg_per_mode=True)
            names = sorted(os.path.basename(p) for p in written)
            self.assertEqual(names, ["tokens.dark.tokens.json", "tokens.dtcg.json", "tokens.light.tokens.json"])
            for mode in ("light", "dark"):
                with open(os.path.join(d, f"tokens.{mode}.tokens.json"), encoding="utf-8") as fh:
                    doc = json.load(fh)
                self.assertEqual(set(doc["role"]), {et.kebab(r) for r in cl.ROLE_KEYS})
                for role in cl.ROLE_KEYS:
                    v = doc["role"][et.kebab(role)]["$value"]
                    if isinstance(v, str):                      # alias must resolve inside this file
                        node = doc
                        for part in v.strip("{}").split("."):
                            node = node[part]
                        v = node["$value"]
                    self.assertEqual(v["hex"], self.pal["modes"][mode][role], f"{mode}.{role}")

    def test_extended_in_every_format(self):
        ext = self.pal["extended"]
        self.assertEqual(len(ext), 3)
        css = self.read("tokens.css")
        root, dark = css.split('[data-theme="dark"]')[0], css.split('[data-theme="dark"]')[1].split("@media")[0]
        for e in ext:
            self.assertIn(f"--bi-{e['id']}: {e['hex']};", root)
            self.assertIn(f"--bi-on-{e['id']}: {e['on']};", root)
            self.assertIn(f"--bi-{e['id']}: {e['dark']['hex']};", dark)
            self.assertIn(f"--bi-on-{e['id']}: {e['dark']['on']};", dark)
            self.assertIn(f"--bi-{e['id']}-50:", root)                       # its scale
        scss = self.read("tokens.scss")
        self.assertIn(f"$bi-ext-1: {ext[0]['hex']};", scss)
        self.assertIn("$bi-extended-dark: (", scss)
        js = self.read("tailwind.palette.js")
        self.assertIn("DEFAULT: 'var(--bi-ext-1)'", js)
        self.assertIn("'on-ext-1': 'var(--bi-on-ext-1)'", js)
        doc = json.loads(self.read("tokens.dtcg.json"))
        for mode in ("light", "dark"):
            group = doc["extended"][mode]
            self.assertEqual(set(group), {f"{p}{e['id']}" for e in ext for p in ("", "on-")})
            for e in ext:
                v = group[e["id"]]["$value"]
                if isinstance(v, str):
                    node = doc
                    for part in v.strip("{}").split("."):
                        node = node[part]
                    v = node["$value"]
                self.assertEqual(v["hex"], e["hex"] if mode == "light" else e["dark"]["hex"])
        self.assertIn("ext-1", doc["color"])
        page = self.read("swatches.html")
        self.assertIn("Extended colours", page)
        for e in ext:
            self.assertIn(e["name"], page)
        self.assertIn("Feels:", page)


class TestCLI(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "export_tokens.py"), *args],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)

    def test_help(self):
        r = self.run_cli("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--formats", r.stdout)
        self.assertIn("--dtcg-per-mode", r.stdout)

    def test_subset_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_cli(EXAMPLE, "--out", d, "--formats", "css,gpl", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(os.path.basename(p) for p in json.loads(r.stdout)), ["palette.gpl", "tokens.css"])
            self.assertEqual(sorted(os.listdir(d)), ["palette.gpl", "tokens.css"])

    def test_default_stdout_is_a_summary_and_full_lists_paths(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_cli(EXAMPLE, "--out", d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLessEqual(len(r.stdout.encode("utf-8")), 1200)
            self.assertIn("6 file(s)", r.stdout)
            self.assertIn("tokens.css", r.stdout)
            self.assertIn("-> " + os.path.abspath(d), r.stdout)
            full = self.run_cli(EXAMPLE, "--out", d, "--full")
            paths = full.stdout.split()
            self.assertEqual(len(paths), 6)
            self.assertTrue(all(os.path.isabs(x) or x.startswith(d) for x in paths))

    def test_errors(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_cli(EXAMPLE, "--out", d, "--formats", "css,pdf")
            self.assertEqual(r.returncode, 2)
            self.assertIn("pdf", r.stderr)
            bad = os.path.join(d, "bad.json")
            with open(bad, "w") as fh:
                fh.write('{"schema": "brand-identity/palette@1", "name": "x", "brand": []}')
            r = self.run_cli(bad, "--out", d)
            self.assertEqual(r.returncode, 2)
            self.assertIn("brand", r.stderr)


if __name__ == "__main__":
    unittest.main()
