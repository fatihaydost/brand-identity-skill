"""Tool text in the document language (docs/architecture.md section 3.6) and the brand-specific misuse page (7.3).

The Turkish fixture is the demo brand with every model-written and brand field in Turkish, brand.doc_lang "tr".
The outputs (card, board, review, table.md, kit pages, fonts.md, contact sheet) must then show none of the English
tool text from assets/i18n/en.json: reverting any label to a hard-coded English string turns these tests red.
"""
import copy
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "brand-identity")
SCRIPTS = os.path.join(SKILL, "scripts")
DEMO = os.path.join(SKILL, "templates", "demo")
I18N = os.path.join(SKILL, "assets", "i18n")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import i18nlib  # noqa: E402
import identity_board as ib  # noqa: E402
import identity_card as ic  # noqa: E402
import identitylib  # noqa: E402
import kit_build as kb  # noqa: E402
import presentlib as pl  # noqa: E402
import render_png as rp  # noqa: E402
from test_card_board_kit import BROWSER, HAS_FT, _offline, _restore  # noqa: E402


def _cat(lang):
    with open(os.path.join(I18N, f"{lang}.json"), encoding="utf-8") as fh:
        return json.load(fh)


EN, TR = _cat("en"), _cat("tr")
PH = re.compile(r"\{[a-z_]+\}")

TR_SETS = {
    "A": {"name": "Tek Jant", "mechanism": "Tek bir jant teli bütün işi yapar: işarette tekerleği keser, geometrik "
          "yazıyı belirler ve paletteki tek turuncudur.", "expression_move": "Turuncu jant teli her yerde tek doygun "
          "renk.", "differs_by": "Mahalle tamircileri el yazısı ve kırmızı kullanıyor; bu, atölye yeşilinde sakin "
          "bir geometrik yazı.", "concept": "Tek telli bir tekerlek: dükkân bozuk olan tek şeyi onarır.",
          "pal": ("Tek Jant", "Atölye yeşili ve tek sinyal turuncusu.", ["dingin", "çevik", "yerel"],
                  ["Atölye Yeşili", "Sinyal Turuncusu"])},
    "B": {"name": "Atölye Defteri", "mechanism": "Tezgâhtaki tamir defteri: kalın tırnaklı kayıtlar, her işin "
          "altında bir çizgi ve kraft kâğıda pas mürekkebi.", "expression_move": "Adın altındaki çizgi, tek ayraç "
          "olarak tekrar eder.", "differs_by": "Kategorinin sportif logolarından daha sıcak ve el emeği.",
          "concept": "Deftere yazılmış ve bir kez altı çizilmiş ad.",
          "pal": ("Atölye Defteri", "Kraft kâğıtta pas mürekkebi, işaretler için aşı boyası.",
                  ["sıcak", "dürüst", "el yapımı"], ["Defter Pası", "Aşı Çizgisi"])},
    "C": {"name": "Gece Sürüşü", "mechanism": "Karanlıkta yansıtıcı donanım: geniş ince büyük harfler, bir far "
          "işareti ve gece lacivertinde tek yansıtıcı sarı.", "expression_move": "Yansıtıcı bant gibi okunan aralıklı "
          "büyük harfler.", "differs_by": "Akşam işe gidenler için kurulmuş tek yön; serin ve teknik.",
          "concept": "Yol çizgisinin üstünde bir far: yarısı aydınlık, yarısı karanlık.",
          "pal": ("Gece Sürüşü", "Tek yansıtıcı sarıyla gece lacivert.", ["serin", "uyanık", "kesin"],
                  ["Gece Lacivert", "Yansıtıcı Sarı"])},
}


def turkish_work(tmp, doc_lang="tr"):
    """The demo brand with Turkish brand copy and model text; brand.doc_lang as given."""
    work = os.path.join(tmp, "ferrow")
    shutil.copytree(DEMO, work)
    for sid, tx in TR_SETS.items():
        sd = os.path.join(work, "sets", sid)
        with open(os.path.join(sd, "identity.json"), encoding="utf-8") as fh:
            d = json.load(fh)
        d["brand"].update({"tagline": "Bugün tamir, yarın yolda.", "languages": ["tr"], "doc_lang": doc_lang,
                           "sector": "kentsel bisiklet tamiri ve kiralama kooperatifi",
                           "copy": {"sentence": {"tr": "Her mesaja bir iş günü içinde yanıt veriyoruz."},
                                    "nav": ["Tamir", "Kiralama", "Hakkımızda"], "cta": "Randevu al"}})
        for k in ("name", "mechanism", "expression_move", "differs_by"):
            d["set"][k] = tx[k]
        if isinstance(d.get("logo"), dict):
            d["logo"]["concept"] = tx["concept"]
        with open(os.path.join(sd, "identity.json"), "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
        pp = os.path.join(sd, "palette.json")
        with open(pp, encoding="utf-8") as fh:
            p = json.load(fh)
        p["name"], p["direction"], p["feels"], names = tx["pal"]
        for b, n in zip(p.get("brand") or [], names):
            b["name"] = n
            b["usage"] = "başlıklar ve geniş yüzeyler"
        with open(pp, "w", encoding="utf-8") as fh:
            json.dump(p, fh, ensure_ascii=False, indent=1)
    return work


def visible_text(page):
    """Text a reader sees: no head, scripts, styles or SVG; tags dropped, entities decoded."""
    page = re.sub(r"(?is)<head>.*?</head>", " ", page)
    page = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", page)
    page = re.sub(r"(?s)<[^>]+>", " ", page)
    page = re.sub(r"https?://\S+", " ", htmllib.unescape(page))  # links (fonts.googleapis.com) are not prose
    return " ".join(page.split())


def brand_text_removed(text):
    """The Turkish fixture's own brand and model text taken out (it is Turkish on purpose)."""
    for tx in TR_SETS.values():
        for v in [tx[k] for k in ("name", "mechanism", "expression_move", "differs_by", "concept")] + \
                 [tx["pal"][0], tx["pal"][1]] + tx["pal"][2] + tx["pal"][3]:
            text = text.replace(v, " ")
    for v in ("başlıklar ve geniş yüzeyler", "Bugün tamir, yarın yolda.", "kentsel bisiklet tamiri ve kiralama kooperatifi",
              "Her mesaja bir iş günü içinde yanıt veriyoruz.", "Tamir", "Kiralama", "Hakkımızda", "Randevu al"):
        text = text.replace(v, " ")
    return text


def leaked(text, src=EN, other=TR):
    """Values of `src` (whose `other` translation differs) found in text: whole words, case-sensitive; for values
    with {placeholders}, every literal piece of 4+ characters."""
    text = re.sub(r"https?://\S+|\S+\.json\b", " ", text)  # links and file names (fonts.google.com, fonts.googleapis.com) are not prose
    hits = []
    for key, val in src.items():
        if key.startswith("_") or other.get(key) == val:
            continue
        for part in PH.split(val):
            part = part.strip(" ·:,.;()")
            if len(part) < 4 or not re.search(r"[A-Za-zÀ-ÿĞğİıŞş]", part):
                continue
            if re.search(r"(?<![\w])" + re.escape(part) + r"(?![\w])", text):
                hits.append(f"{key}: {part!r}")
    return hits


ENGLISH_WORDS = re.compile(r"(?<![\w])(the|and|for|with|your|never|only|every|from|when)(?![\w])")


class TestCatalogue(unittest.TestCase):
    def test_tr_matches_en_keys_and_placeholders(self):
        self.assertEqual(set(EN), set(TR))
        for k, v in EN.items():
            if k.startswith("_"):
                continue
            self.assertEqual(sorted(PH.findall(v)), sorted(PH.findall(TR[k])), k)
            self.assertTrue(TR[k].strip(), k)

    def test_literal_keys_in_code_exist(self):
        keys = set()
        for fn in os.listdir(SCRIPTS):
            if fn.endswith(".py"):
                with open(os.path.join(SCRIPTS, fn), encoding="utf-8") as fh:
                    src = fh.read()
                keys |= set(re.findall(r"""\b(?:tr|t|k\.tr)(?:\.n)?\(["']([a-z_]+\.[a-z0-9_.-]+)["']""", src))
        plural = {k.rsplit(".", 1)[0] for k in EN if k.endswith((".one", ".other"))}
        missing = [k for k in keys if k not in EN and k not in plural]
        self.assertEqual(missing, [])
        self.assertGreater(len(keys), 100)

    def test_every_finding_family_has_a_sentence(self):
        t = i18nlib.Strings("tr")
        fams = finding_families()
        self.assertGreater(len(fams), 80)
        catch_all = {f"finding.{c}" for c in i18nlib.COMPONENT_FAMILIES}
        missing = sorted({f"{s} ({fn})" for s, fn in fams if t.finding_key(s) in (None, *catch_all)})
        self.assertEqual(missing, [])
        # parameters: the sets and roles involved stay in the localised sentence
        self.assertEqual(t.finding({"id": "cohesion.sets-close", "measured": ["A–C"], "severity": "warn"}),
                         "Bu yönler eksenlerde birbirine çok yakın: A–C")
        self.assertEqual(t.finding({"id": "palette.distinct.dark.primary-accent", "roles": ["primary", "accent"]}),
                         "İki renk birbirine çok benziyor (Ana renk / Vurgu)")
        self.assertEqual(t.finding({"id": "palette.distinct.x"}), "İki renk birbirine çok benziyor")
        self.assertEqual(t.finding({"id": "type.display+text.license.embedding"}), "Yazı karakteri lisansına bakılmalı")
        self.assertEqual(t.finding({"id": "zzz", "severity": "gate"}), "Çözülmemiş engelleyici sorun: zzz")
        self.assertEqual(t.finding({"id": "palette.cvd.dark.deutan.primary-accent", "roles": ["dark.primary"]}),
                         "İki renk renk körlüğünde zor ayırt ediliyor (Ana renk)")
        self.assertEqual(t.finding({"id": "palette.contract.missing-role.dark.text"}), "Palet dosyası eksik")
        en = i18nlib.Strings("en")
        self.assertEqual(en.finding({"id": "logo.kept", "message": "kept logo shown as is"}), "kept logo shown as is")
        self.assertEqual(en.n("swatch.warns", 1), "1 warning")
        self.assertEqual(en.n("swatch.notes", 3), "3 notes")

    def test_lookup_order_and_findings(self):
        tmp = tempfile.mkdtemp(prefix="bi-i18n-")
        try:
            t = i18nlib.Strings("tr", tmp)
            self.assertEqual(t("kit.sec.misuse"), "Yanlış kullanım")
            self.assertEqual(t.html_lang, "tr")
            self.assertEqual(t.findings("kit"), [])
            self.assertEqual(t.n("card.warnings", 2), "2 uyarı")
            with self.assertRaises(KeyError):
                t("no.such.key")
            de = i18nlib.Strings("de", tmp)  # nothing for German: English text, lang en, one warn
            self.assertEqual(de("kit.sec.misuse"), "Misuse")
            self.assertEqual(de.html_lang, "en")
            f = de.findings("kit")
            self.assertEqual([x["id"] for x in f], ["i18n.missing"])
            self.assertEqual(f[0]["severity"], "warn")
            self.assertIn("i18n.json", f[0]["suggested_fix"])
            with open(os.path.join(tmp, "i18n.json"), "w", encoding="utf-8") as fh:
                json.dump({"_lang": "de", "kit.sec.misuse": "Falsche Verwendung"}, fh)
            de = i18nlib.Strings("de", tmp)
            self.assertEqual(de("kit.sec.misuse"), "Falsche Verwendung")
            self.assertEqual(de.html_lang, "de")
            de("kit.sec.cover")  # not in the work file: English, and named in the finding
            f = de.findings("card")
            self.assertEqual(f[0]["measured"], ["kit.sec.cover"])
            self.assertIn("kit.sec.cover", f[0]["message"])
            self.assertEqual(i18nlib.Strings("tr", tmp)("kit.sec.misuse"), "Yanlış kullanım")  # _lang de: ignored
            self.assertEqual(i18nlib.doc_lang({"languages": ["pt-BR", "en"]}), "pt")
            self.assertEqual(i18nlib.doc_lang({"languages": ["tr"], "doc_lang": "en"}), "en")
            self.assertEqual(i18nlib.doc_lang({}), "en")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_doc_lang_validation_and_init(self):
        ident = identitylib.load_identity(os.path.join(DEMO, "sets", "A", "identity.json"))
        for good in ("tr", "pt-BR", "zh_Hant"):
            d = copy.deepcopy(ident)
            d["brand"]["doc_lang"] = good
            identitylib.validate_identity(d)
        for bad in ("", "türkçe", 5, "x"):
            d = copy.deepcopy(ident)
            d["brand"]["doc_lang"] = bad
            with self.assertRaises(identitylib.IdentityError):
                identitylib.validate_identity(d)
        tmp = tempfile.mkdtemp(prefix="bi-i18n-init-")
        try:
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "brand.py"), "init", "Hasat", "--sets", "1",
                                "--langs", "en,tr", "--doc-lang", "tr", "--root", tmp], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(tmp, "hasat", "brief.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["doc_lang"], "tr")
            sk = identitylib.load_identity(os.path.join(tmp, "hasat", "sets", "A", "identity.json"), partial=True)
            self.assertEqual(sk["brand"]["doc_lang"], "tr")
            self.assertEqual(sk["brand"]["languages"], ["en", "tr"])  # the brand's languages are not touched
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "brand.py"), "init", "Bad", "--doc-lang",
                                "türkçe", "--root", tmp], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertNotEqual(r.returncode, 0)
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "brand.py"), "init", "Moon", "--langs", "de",
                                "--root", tmp], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            with open(os.path.join(tmp, "moon", "brief.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["doc_lang"], "de")  # default: the first brand language
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def finding_families():
    """Finding id families the code can produce: (sample id, file). palette_audit ids reach identity.audit as
    palette.<id>, font_audit ids as type.<roles>.<id>; both forms are sampled."""
    pat = re.compile(r"""(?:\bfinding|\b_f|\b_finding|self\.add)\(\s*f?(["'])([a-z][a-z0-9_.{}+-]*?)\1""")
    out = []
    for fn in sorted(os.listdir(SCRIPTS)):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(SCRIPTS, fn), encoding="utf-8") as fh:
            src = fh.read()
        for m in pat.finditer(src):
            raw = m.group(2)
            if "." not in raw and "-" not in raw and "{" not in raw:
                continue
            sample = re.sub(r"\{[^}]*\}", "x", raw)  # f"type.{role}.resolve" -> type.x.resolve
            out.append((sample, fn))
            if fn == "palette_audit.py":
                out.append(("palette." + sample, fn))
            if fn == "font_audit.py" and sample.startswith("type."):
                out.append(("type.display+text." + sample[5:], fn))
    return out


class TestTurkishHtml(unittest.TestCase):
    """No browser: the HTML and Markdown the renderers write, checked for English tool text."""

    @classmethod
    def setUpClass(cls):
        pl._typelib = lambda: None
        cls.tmp = tempfile.mkdtemp(prefix="bi-i18n-tr-")
        cls.work = turkish_work(cls.tmp)
        cls.infos = [pl.load_set(d) for d in identitylib.set_dirs(cls.work)]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def outputs(self, infos):
        out = {}
        for info in infos:
            faces = pl.resolve_faces(info["identity"], info["dir"])
            out[f"card {info['identity']['set']['id']}"] = visible_text(ic.build_html(info, faces, info["dir"], []))
        res = {i["dir"]: [] for i in infos}
        out["table.md"] = ib.table_md(infos, res)
        board = " ".join([ib.cards_html(infos, res, self.work), ib.table_html(infos, res), ib.lede(infos),
                          ib.review_html(infos, res, self.work, "", "")])
        out["board"] = visible_text(board)
        for info in infos:
            k = kb.Kit(info["dir"])
            html, _n = kb.build_html(k, full=True, html_dir=info["dir"])
            out[f"kit {info['identity']['set']['id']}"] = visible_text(html) + " " + kb.fonts_md(k)
        return out

    def test_no_english_tool_text(self):
        for name, text in self.outputs(self.infos).items():
            self.assertEqual(leaked(text), [], name)
            self.assertEqual(ENGLISH_WORDS.findall(text), [], f"{name}: {text[:300]}")

    def test_root_lang_and_brand_lang(self):
        info = self.infos[0]
        page = ic.build_html(info, pl.resolve_faces(info["identity"], info["dir"]), info["dir"], [])
        self.assertIn('<html lang="tr">', page)
        html, _n = kb.build_html(kb.Kit(info["dir"]), html_dir=info["dir"])
        self.assertIn('<html lang="tr">', html)
        self.assertIn("Marka kılavuzu", html)
        self.assertIn("Yanlış kullanım", html)

    def test_english_doc_lang_shows_no_turkish(self):
        tmp = tempfile.mkdtemp(prefix="bi-i18n-en-")
        try:
            shutil.copytree(DEMO, os.path.join(tmp, "ferrow"))  # English brand text, doc_lang absent -> en
            infos = [pl.load_set(d) for d in identitylib.set_dirs(os.path.join(tmp, "ferrow"))]
            for name, text in self.outputs(infos).items():
                self.assertEqual(leaked(text, TR, EN), [], name)
                self.assertFalse(re.search(r"[ğışİĞŞ]", text), name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_english_labels_on_turkish_brand(self):
        """doc_lang en on a Turkish brand: English labels under lang=en, brand lines keep lang=tr (no İ casing)."""
        info = copy.deepcopy(self.infos[0])
        info["identity"]["brand"]["doc_lang"] = "en"
        page = ic.build_html(info, pl.resolve_faces(info["identity"], info["dir"]), info["dir"], [])
        self.assertIn('<html lang="en">', page)
        self.assertIn("Identity directions", page)
        self.assertIn('lang="tr"', page)
        self.assertEqual(leaked(brand_text_removed(visible_text(page)), TR, EN), [])

    def test_untranslated_language_warns_and_falls_back(self):
        info = copy.deepcopy(self.infos[0])
        info["identity"]["brand"]["doc_lang"] = "fi"
        tr = pl.strings(info)
        page = ic.build_html(info, [], info["dir"], [], tr=tr)
        self.assertIn('<html lang="en">', page)  # English text keeps English casing rules
        self.assertIn("Identity directions", page)
        self.assertEqual([f["id"] for f in tr.findings("card")], ["i18n.missing"])


def _misuse_kit(work, items, svgs=None, mode=None):
    sd = os.path.join(work, "sets", "A")
    with open(os.path.join(sd, "identity.json"), encoding="utf-8") as fh:
        d = json.load(fh)
    if items is None:
        d["logo"].pop("misuse", None)
    else:
        d["logo"]["misuse"] = items
    if mode:
        d["components"]["logo"]["mode"] = mode
        d["components"]["logo"]["status"] = "fixed" if mode == "keep" else "proposed"
        d["components"]["logo"]["source"] = "sets/A/logo/symbol.svg"
    with open(os.path.join(sd, "identity.json"), "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    for name, svg in (svgs or {}).items():
        with open(os.path.join(sd, "logo", name), "w", encoding="utf-8") as fh:
            fh.write(svg)
    return kb.Kit(sd)


GAP_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="46" '
           'data-color="primary"/><rect x="20" y="46" width="60" height="8" fill="#ffffff"/></svg>')


class TestMisuse(unittest.TestCase):
    def setUp(self):
        pl._typelib = lambda: None
        self.tmp = tempfile.mkdtemp(prefix="bi-misuse-")
        self.work = turkish_work(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def page(self, k):
        p = kb.page_on_colour(k)
        mis = p["content"][p["content"].index('class="mis"'):]
        return mis, [f["id"] for f in k.findings]

    def test_absent_field_draws_six_generic_and_warns(self):
        k = _misuse_kit(self.work, None)
        mis, ids = self.page(k)
        self.assertIn('data-n="6"', mis)
        self.assertEqual(mis.count('class="t"'), 6)
        self.assertIn("Orantısını bozmayın", mis)
        self.assertEqual(ids, ["kit.misuse.generic"])

    def test_brand_specific_item_no_warn(self):
        items = [{"do": "stretch"}, {"do": "recolour"}, {"do": "crowd"},
                 {"svg": "logo/misuse-1.svg", "label": "Teldeki boşluğu doldurmayın"}, {"do": "rearrange"}]
        k = _misuse_kit(self.work, items, {"misuse-1.svg": GAP_SVG})
        identitylib.validate_identity(k.ident)
        mis, ids = self.page(k)
        self.assertIn('data-n="5"', mis)
        self.assertEqual(mis.count('class="t"'), 5)
        self.assertIn("Teldeki boşluğu doldurmayın", mis)
        self.assertIn("Sembolle adın yerini değiştirmeyin", mis)
        self.assertIn('fill="#ffffff"', mis)  # the model's drawing, painted with the palette
        self.assertNotIn("data-logo-ground", mis)  # deliberately wrong: never measured
        self.assertEqual(ids, [])

    def test_generic_only_warns_unless_kept(self):
        four = [{"do": d} for d in ("stretch", "rotate", "effects", "outline")]
        k = _misuse_kit(self.work, four)
        mis, ids = self.page(k)
        self.assertIn('data-n="4"', mis)
        self.assertEqual(ids, ["kit.misuse.generic"])
        k = _misuse_kit(self.work, four, mode="keep")
        _mis, ids = self.page(k)
        self.assertEqual(ids, [])  # a kept logo may stay generic

    def test_unsafe_svg_is_a_gate_and_not_drawn(self):
        for bad in ('<svg viewBox="0 0 100 100"><text x="1" y="9">x</text></svg>',
                    '<svg viewBox="0 0 100 100"><script>alert(1)</script></svg>',
                    '<svg viewBox="0 0 100 100"><image href="https://e.com/a.png"/></svg>',
                    '<svg viewBox="0 0 100 100"><rect width="9" height="9" onclick="x()"/></svg>',
                    '<svg viewBox="0 0 100 100"><rect width="9" height="9" fill="url(#g)"/></svg>',
                    '<svg viewBox="0 0 100 100"><rect width="9" height="9" data-op="subtract"/></svg>',
                    '<svg><rect width="9" height="9"/></svg>'):
            items = [{"do": "stretch"}, {"do": "rotate"}, {"do": "recolour"},
                     {"svg": "logo/misuse-1.svg", "label": "Yanlış"}]
            k = _misuse_kit(self.work, items, {"misuse-1.svg": bad})
            mis, ids = self.page(k)
            self.assertIn("kit.misuse.svg", ids, bad)
            self.assertIn("kit.misuse.generic", ids, bad)
            self.assertEqual(mis.count('class="t"'), 3, bad)
            self.assertNotIn("alert", mis)

    def test_symlink_outside_and_oversized_svg_are_gates(self):
        items = [{"do": "stretch"}, {"do": "rotate"}, {"do": "recolour"}, {"svg": "logo/misuse-1.svg", "label": "Yanlış"}]
        outside = os.path.join(self.tmp, "outside.svg")
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write(GAP_SVG)
        k = _misuse_kit(self.work, items)
        link = os.path.join(k.info["dir"], "logo", "misuse-1.svg")
        if os.path.exists(link):
            os.remove(link)
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks here")
        _mis, ids = self.page(kb.Kit(k.info["dir"]))
        self.assertIn("kit.misuse.svg", ids)
        os.remove(link)
        big = GAP_SVG.replace("</svg>", '<rect width="1" height="1"/>' * 25000 + "</svg>")
        with open(link, "w", encoding="utf-8") as fh:
            fh.write(big)
        k = kb.Kit(k.info["dir"])
        _mis, ids = self.page(k)
        self.assertIn("kit.misuse.svg", ids)
        self.assertIn("512 KB", " ".join(f["message"] for f in k.findings))

    def test_validation(self):
        ident = identitylib.load_identity(os.path.join(DEMO, "sets", "A", "identity.json"))
        ok = [{"do": "stretch"}, {"do": "rotate"}, {"do": "crowd"}, {"svg": "logo/misuse-1.svg", "label": "Yapma"}]
        d = copy.deepcopy(ident)
        d["logo"]["misuse"] = ok
        identitylib.validate_identity(d)
        bad = [ok[:3], ok + ok[:3], ok[:3] + [{"do": "melt"}], ok[:3] + [{"do": "stretch"}],
               ok[:3] + [{"svg": "/etc/x.svg", "label": "x"}], ok[:3] + [{"svg": "../x.svg", "label": "x"}],
               ok[:3] + [{"svg": "logo/m.png", "label": "x"}], ok[:3] + [{"svg": "logo/m.svg"}],
               ok[:3] + [{"svg": "logo/m.svg", "label": "x", "do": "rotate"}], "stretch"]
        for b in bad:
            d = copy.deepcopy(ident)
            d["logo"]["misuse"] = b
            with self.assertRaises(identitylib.IdentityError, msg=str(b)):
                identitylib.validate_identity(d)
        d = copy.deepcopy(ident)
        d["logo"]["type"] = "wordmark"
        d["logo"]["symbol"] = None
        d["logo"]["misuse"] = ok[:3] + [{"do": "rearrange"}]
        with self.assertRaises(identitylib.IdentityError):
            identitylib.validate_identity(d)


@unittest.skipUnless(BROWSER and HAS_FT, "needs a Chromium-based browser and fontTools")
class TestRenderedTurkish(unittest.TestCase):
    """Real renders: card + board + review + table + kit with doc_lang tr; misuse page with 5 items fits."""

    @classmethod
    def setUpClass(cls):
        _offline()
        cls.tmp = tempfile.mkdtemp(prefix="bi-i18n-render-")
        cls.work = turkish_work(cls.tmp)
        _misuse_kit(cls.work, [{"do": "stretch"}, {"do": "recolour"}, {"do": "low-contrast"},
                               {"svg": "logo/misuse-1.svg", "label": "Teldeki boşluğu doldurmayın"},
                               {"do": "rearrange"}], {"misuse-1.svg": GAP_SVG})
        cls.board = ib.render(cls.work, rerender=True)
        cls.kit = kb.build(os.path.join(cls.work, "sets", "A"))

    @classmethod
    def tearDownClass(cls):
        _restore()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_files_show_no_english_tool_text(self):
        files = [self.board["board_html"], os.path.join(self.work, ".cache", "review.html"), self.kit["html"],
                 os.path.join(self.kit["kit"], "tokens", "swatches.html")]
        files += [os.path.join(d, "card.html") for d in identitylib.set_dirs(self.work)]
        for p in files:
            with open(p, encoding="utf-8") as fh:
                page = fh.read()
            self.assertRegex(page, r'<html lang="tr"', p)
            self.assertEqual(leaked(visible_text(page)), [], p)
            self.assertEqual(ENGLISH_WORDS.findall(brand_text_removed(visible_text(page))), [], p)
        for p in (self.board["table"], os.path.join(self.kit["kit"], "fonts.md")):
            with open(p, encoding="utf-8") as fh:
                self.assertEqual(leaked(fh.read()), [], p)

    def test_kit_passes_and_misuse_fits(self):
        gates = [f for f in self.kit["findings"] if f["severity"] == "gate"]
        self.assertEqual(gates, [])
        ids = [f["id"] for f in self.kit["findings"]]
        self.assertNotIn("kit.misuse.generic", ids)
        self.assertNotIn("i18n.missing", ids)
        self.assertTrue(any(p.endswith("05-logo-on-colour.png") for p in self.kit["pages"]))
        self.assertEqual(self.board["board_findings"], [])


# ----------------------------------------------------------------------------- pseudo-locale

PSEUDO_OPEN, PSEUDO_CLOSE = "⟦", "⟧"
EXEMPT_WORDS = {  # codes, units, font style names, axis tags: not prose, never translated
    "HEX", "RGB", "OKLCH", "Lab", "D", "UI", "px", "mm", "em", "Aa", "oklch", "rgb", "WCAG", "CMYK", "Pantone",
    "Thin", "ExtraLight", "Light", "Regular", "Medium", "SemiBold", "Bold", "ExtraBold", "Black",
    "wght", "opsz", "wdth", "GRAD", "SOFT", "WONK", "slnt", "ital", "H", "v", "x",
}


def _strings_of(obj, out, keys_of=("location",)):
    """Every string value in a JSON tree, plus the keys of the dicts named in keys_of (axis tags)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys_of and isinstance(v, dict):
                out.update(str(x) for x in v)
            _strings_of(v, out, keys_of)
    elif isinstance(obj, list):
        for v in obj:
            _strings_of(v, out, keys_of)
    elif isinstance(obj, str) and obj.strip():
        out.add(obj.strip())


def pseudo_work(tmp):
    """Demo copy with doc_lang "xx" and a pseudo catalogue (<work>/i18n.json: every value wrapped in ⟦⟧); set B has a
    logo gate (FAILED card), set A a cohesion warning, a card warning and a brand-specific misuse, set C a kept
    palette with a kept note. Returns (work, brand data strings)."""
    work = os.path.join(tmp, "ferrow")
    shutil.copytree(DEMO, work)
    cat = {k: (v if k.startswith("_") else f"{PSEUDO_OPEN}{v}{PSEUDO_CLOSE}") for k, v in EN.items()}
    cat["_lang"] = "xx"
    with open(os.path.join(work, "i18n.json"), "w", encoding="utf-8") as fh:
        json.dump(cat, fh, ensure_ascii=False)

    def audit(*fs):
        return {"schema": identitylib.AUDIT_SCHEMA, "passed": not any(f["severity"] == "gate" for f in fs),
                "counts": {s: sum(f["severity"] == s for f in fs) for s in ("gate", "warn", "info")},
                "findings": list(fs)}
    extra = {
        "A": audit(identitylib.finding("cohesion.sets-close", "cohesion", "warn", "A and B sit 1 apart on the axes",
                                       measured=["A–B"]),
                   identitylib.finding("logo.thin-feature", "logo", "warn", "the spoke vanishes at 16 px"),
                   identitylib.finding("default.outfit", "type", "warn", "Outfit is a model default")),
        "B": audit(identitylib.finding("logo.contrast", "logo", "gate", "wordmark on ground is 2.1:1, below 3:1"),
                   identitylib.finding("zzz.unmapped", "type", "warn", "something unmapped happened")),
        "C": audit(identitylib.finding("palette.kept-note", "palette", "warn", "kept palette accent is near text")),
    }
    data = set()
    for sid in "ABC":
        sd = os.path.join(work, "sets", sid)
        with open(os.path.join(sd, "identity.json"), encoding="utf-8") as fh:
            d = json.load(fh)
        d["brand"]["doc_lang"] = "xx"
        d["brand"]["copy"] = {"sentence": {"en": "Bikes fixed while you wait."}, "nav": ["Repairs", "Rentals", "Visit"],
                              "cta": "Book a repair"}
        d["audit"] = extra[sid]
        if sid == "A":
            d["logo"]["misuse"] = [{"do": "stretch"}, {"do": "crowd"}, {"do": "rearrange"},
                                   {"svg": "logo/misuse-1.svg", "label": "Never fill the spoke gap"}]
            with open(os.path.join(sd, "logo", "misuse-1.svg"), "w", encoding="utf-8") as fh:
                fh.write(GAP_SVG)
        if sid == "C":
            d["components"]["palette"] = {"mode": "keep", "status": "fixed", "source": "sets/C/palette.json",
                                          "sha256": None}
        with open(os.path.join(sd, "identity.json"), "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
        _strings_of({k: v for k, v in d.items() if k != "audit"}, data)
        with open(os.path.join(sd, "palette.json"), encoding="utf-8") as fh:
            _strings_of(json.load(fh), data)
    data |= {v for v in pl.SAMPLES.values()}  # specimen sentences: brand-language text, not labels
    data |= {v.upper() for v in data}  # set names are also shown upper-cased
    return work, data


def unmarked_words(text, data):
    """Latin words left after removing ⟦…⟧ labels, brand data, links, e-mail addresses and exempt classes."""
    text = re.sub(r"https?://\S+|\S+@\S+", " ", text)
    for v in sorted(data, key=len, reverse=True):
        if len(v) > 1:
            text = text.replace(v, " ")
    while True:
        new = re.sub(re.escape(PSEUDO_OPEN) + r"[^" + PSEUDO_OPEN + PSEUDO_CLOSE + r"]*" + re.escape(PSEUDO_CLOSE),
                     " ", text)
        if new == text:
            break
        text = new
    words = re.findall(r"[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ'’]*", text)
    alphabet = {c + c.lower() for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}  # the type specimen's Aa Bb Cc ...
    return sorted({w for w in words if w not in EXEMPT_WORDS and w not in alphabet and len(w) > 1})


class TestPseudoLocale(unittest.TestCase):
    """Every label on every user-facing output comes from the catalogue: with a pseudo catalogue, no visible Latin
    word is left outside ⟦…⟧ except brand data and codes. A label hard-coded in the code turns this red."""

    @classmethod
    def setUpClass(cls):
        pl._typelib = lambda: None
        cls.tmp = tempfile.mkdtemp(prefix="bi-pseudo-")
        cls.work, cls.data = pseudo_work(cls.tmp)
        cls.infos = [pl.load_set(d) for d in identitylib.set_dirs(cls.work)]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def pages(self):
        import export_tokens
        import logo_audit
        infos, work = self.infos, self.work
        res = {i["dir"]: [] for i in infos}
        out = {}
        for info in infos:
            faces = pl.resolve_faces(info["identity"], info["dir"])
            out[f"card {info['identity']['set']['id']}"] = ic.build_html(info, faces, info["dir"], [])
        out["board"] = ib.board_page(infos, res, work)[0]
        out["review"] = ib.review_html(infos, res, work, "", "")
        out["table.md"] = ib.table_md(infos, res)
        for sid in ("A", "C"):
            k = kb.Kit(os.path.join(work, "sets", sid))
            out[f"kit {sid}"] = kb.build_html(k, full=True, html_dir=k.info["dir"])[0]
            out[f"fonts.md {sid}"] = kb.fonts_md(k)
            pal = os.path.join(k.info["dir"], "palette.json")
            out[f"swatches {sid}"] = export_tokens.to_swatches(identitylib_palette(pal), k.tr)
            self.assertEqual(k.tr.missing, [], sid)
        recs = []
        for info in infos:
            ident = info["identity"]
            recs.append({"id": ident["set"]["id"], "identity": ident, "set_dir": info["dir"], "manifest": None,
                         "findings": (ident.get("audit") or {}).get("findings") or [], "palette": info["palette"]})
        sym = os.path.join(work, "sets", "A", "logo", "symbol.svg")
        recs.append({"id": "D", "identity": infos[0]["identity"], "set_dir": infos[0]["dir"], "findings": [],
                     "palette": infos[0]["palette"], "manifest": {"kept": True, "source": sym, "dark": sym,
                                                                  "dark_from": "x"}})
        out["contact sheet"] = logo_audit.sheet_html(recs, os.path.join(work, ".cache"))[0]
        return out

    def test_every_visible_word_is_a_label_or_brand_data(self):
        pages = self.pages()
        for name, page in pages.items():
            if not name.startswith(("table.md", "fonts.md")):
                self.assertRegex(page, r'<html lang="xx"', name)
                page = re.sub(r"(?is)<pre\b.*?</pre>", " ", page)  # the embed code block is CSS
                text = visible_text(page)
            else:
                text = page
            self.assertEqual(unmarked_words(text, self.data), [], f"{name}: unmarked words")
        # the finding paths were exercised: gate, warnings, cohesion, kept note, all through the catalogue
        joined = " ".join(pages.values())
        for key in ("finding.logo.contrast", "finding.cohesion.sets-close", "finding.generic.warn", "card.failed"):
            self.assertIn(f"{PSEUDO_OPEN}{EN[key].split('{')[0]}", joined, key)
        for msg in ("wordmark on ground", "sit 1 apart", "accent is near text", "something unmapped", "spoke vanishes"):
            self.assertNotIn(msg, joined)  # English finding messages never reach a non-English document


def identitylib_palette(path):
    import colorlib
    return colorlib.load_palette(path)
