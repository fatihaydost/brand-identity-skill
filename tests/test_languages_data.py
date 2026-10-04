"""The bundled language data (assets/languages.json.gz) against gflanguages, which is a dev dependency only.

With gflanguages installed (requirements-dev.txt, CI) every language and script must match field for field (the
order of gflanguages' records follows the file system's listing, so it is not compared); without it the comparison is skipped and only the bundled file is checked.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "brand-identity", "scripts")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import typelib  # noqa: E402

try:
    import gflanguages
except ImportError:
    gflanguages = None


class Bundled(unittest.TestCase):
    def test_bundled_data_reads_without_gflanguages(self):
        langs = typelib.languages()
        self.assertGreater(len(langs), 1000)
        tr = langs["tr_Latn"]
        self.assertEqual(tr.name, "Turkish")
        self.assertIn("ğ", tr.exemplar_chars.base.split())
        self.assertFalse(tr.historical)
        self.assertGreater(tr.population, 1_000_000)
        self.assertEqual(typelib.scripts()["Latn"], "Latin")
        self.assertEqual(typelib.lang_id("tr"), "tr_Latn")
        self.assertEqual(typelib.lang_id("sr"), "sr_Cyrl")

    def test_requirements_do_not_carry_gflanguages(self):
        with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as fh:
            self.assertNotIn("gflanguages", fh.read())
        with open(os.path.join(ROOT, "requirements-dev.txt"), encoding="utf-8") as fh:
            self.assertIn("gflanguages", fh.read())


@unittest.skipIf(gflanguages is None, "gflanguages not installed (pip install -r requirements-dev.txt)")
class SameAsGflanguages(unittest.TestCase):
    def test_every_language_matches_field_for_field(self):
        ref = gflanguages.LoadLanguages()
        ours = typelib.languages()
        self.assertEqual(sorted(ours), sorted(ref), "language ids differ")
        bad = []
        for lid, r in ref.items():
            o = ours[lid]
            got = (o.name, o.population, o.historical, o.exemplar_chars.base, o.exemplar_chars.auxiliary,
                   o.exemplar_chars.punctuation)
            want = (r.name, r.population, r.historical, r.exemplar_chars.base, r.exemplar_chars.auxiliary,
                    r.exemplar_chars.punctuation)
            if got != want:
                bad.append(lid)
        self.assertEqual(bad, [], f"{len(bad)} languages differ, e.g. {bad[:5]}")

    def test_every_script_name_matches(self):
        ref = {code: rec.name for code, rec in gflanguages.LoadScripts().items()}
        self.assertEqual(typelib.scripts(), ref)

    def test_lang_id_resolves_like_gflanguages_for_every_language_code(self):
        """lang_id breaks ties alphabetically, so both data sources resolve every base code alike in any record order."""
        codes = sorted({k.split("_")[0] for k in gflanguages.LoadLanguages()})

        def resolve(c):
            try:
                return typelib.lang_id(c)
            except ValueError as e:
                return f"error: {e}"

        ours = {c: resolve(c) for c in codes}
        saved = typelib.languages()
        try:
            ref_langs = gflanguages.LoadLanguages()
            typelib._LANGS = dict(reversed(list(ref_langs.items())))   # another record order must not matter
            ref = {c: resolve(c) for c in codes}
        finally:
            typelib._LANGS = saved
        self.assertEqual(ours, ref)
        self.assertEqual(ours["tr"], "tr_Latn")


if __name__ == "__main__":
    unittest.main()
