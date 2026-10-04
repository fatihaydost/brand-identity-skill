"""Tool text in the document language (docs/architecture.md section 3.6).

Every label, heading and caption the scripts put on a user-facing output (card, board, review image, table.md,
kit pages, contact sheet) comes from one catalogue: assets/i18n/en.json (canonical, flat key -> text, `{name}`
placeholders) and its translations. Lookup order for a document language L:

  1. <work>/i18n.json   the agent's translation of en.json for a language the skill does not ship
                        (used when its optional "_lang" is absent or equals L)
  2. assets/i18n/L.json a shipped translation
  3. en.json            per missing key; every fallback is counted and reported as one `i18n.missing` warn

The document language is identity.brand.doc_lang (BCP-47 primary code), else brand.languages[0]. It is separate
from brand.languages, which say what the brand writes in (font coverage, sample sentences, mock copy).
Standard library only.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
I18N_DIR = os.path.join(os.path.dirname(HERE), "assets", "i18n")
WORK_FILE = "i18n.json"
_CACHE = {}
COMPONENT_FAMILIES = ("logo", "palette", "type")  # catch-all finding keys, used only when nothing narrower matches
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

__all__ = ["Strings", "doc_lang", "for_brand", "catalogue", "base_lang", "I18N_DIR", "WORK_FILE"]


def base_lang(code):
    """'pt-BR' / 'tr_Latn' / 'TR' -> 'pt' / 'tr' / 'tr'."""
    return (str(code or "en").strip().replace("_", "-").split("-")[0] or "en").lower()


def doc_lang(brand):
    """The language of the tool text for a brand dict (identity.brand or brief): doc_lang, else languages[0]."""
    brand = brand or {}
    code = brand.get("doc_lang") or ((brand.get("languages") or ["en"])[0])
    return base_lang(code)


def _load(path):
    key = (path, os.path.getmtime(path) if os.path.isfile(path) else None)
    if key in _CACHE:
        return _CACHE[key]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        data = None
    else:
        data = {k: v for k, v in data.items() if isinstance(v, str)}
    _CACHE[key] = data
    return data


def catalogue(lang="en"):
    """A shipped catalogue as a dict (None when the language is not shipped)."""
    return _load(os.path.join(I18N_DIR, f"{base_lang(lang)}.json"))


class Strings:
    """t(key, **values) -> text in the document language. Unknown keys raise KeyError (a typo must not ship)."""

    def __init__(self, lang="en", work=None):
        self.lang = base_lang(lang)
        self.en = catalogue("en") or {}
        self.table, self.source = {}, None
        if self.lang != "en":
            wp = os.path.join(work, WORK_FILE) if work else None
            wt = _load(wp) if wp and os.path.isfile(wp) else None
            if wt and base_lang(wt.get("_lang") or self.lang) == self.lang:
                # a key whose {placeholders} differ from en.json would drop or garble values: treat it as missing
                wt = {k: v for k, v in wt.items()
                      if k not in self.en or sorted(_PLACEHOLDER.findall(v)) == sorted(_PLACEHOLDER.findall(self.en[k]))}
                self.table, self.source = wt, wp
            else:
                shipped = catalogue(self.lang)
                if shipped:
                    self.table, self.source = shipped, os.path.join(I18N_DIR, f"{self.lang}.json")
        self.missing = []

    @property
    def translated(self):
        """True when the text is in the document language (English, or a translation was found)."""
        return self.lang == "en" or bool(self.table)

    @property
    def html_lang(self):
        """lang attribute for the tool text: the document language, or en when everything falls back to English
        (so English labels are never upper-cased with another language's rules)."""
        return self.lang if self.translated else "en"

    def __call__(self, key, **values):
        if key not in self.en:
            raise KeyError(f"i18n key {key!r} is not in assets/i18n/en.json")
        text = self.table.get(key) if self.lang != "en" else None
        if not isinstance(text, str) or not text.strip():
            text = self.en[key]
            if self.lang != "en" and key not in self.missing:
                self.missing.append(key)
        if values:
            text = _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)
        return text

    def n(self, key, count, **values):
        """Plural pair: `<key>.one` for 1, `<key>.other` otherwise; `{n}` is the count."""
        return self(f"{key}.one" if count == 1 else f"{key}.other", n=count, **values)

    def num(self, value, decimals=1):
        """A number with the document language's decimal mark (tr 4,5 · en 4.5)."""
        import presentlib
        return presentlib.format_number(value, self.lang, decimals)

    def ratio(self, value, decimals=1):
        """A contrast ratio: 14.2:1 (en), 14,2:1 (tr)."""
        return f"{self.num(value, decimals)}:1"

    def finding_key(self, fid):
        """The catalogue key for a finding id, or None: the longest `finding.<candidate>` that exists, where the
        candidates are the id's prefixes (split on . and -), also after dropping leading segments (palette.contrast.x
        -> contrast.x) and after dropping the segments behind the first one (type.display+text.license ->
        type.license)."""
        segs = str(fid or "").split(".")
        variants = [(".".join(segs[i:]), i, 1) for i in range(len(segs))]  # (variant, segments dropped, suffix)
        variants += [(segs[0] + "." + ".".join(segs[i:]), i - 1, 0) for i in range(2, len(segs))]
        best, rank = None, None
        for v, dropped, suffix in variants:
            parts, seps = re.split(r"[.-]", v), re.findall(r"[.-]", v)
            for n in range(len(parts), 0, -1):
                c = "".join(p + (seps[i] if i < n - 1 else "") for i, p in enumerate(parts[:n]))
                # a bare component (palette, logo, type) is the last resort; then the fewest dropped segments,
                # a plain suffix before a component + suffix, then the longest match
                r = (c not in COMPONENT_FAMILIES, -dropped, suffix, len(c))
                if f"finding.{c}" in self.en and (rank is None or r > rank):
                    best, rank = c, r
        return f"finding.{best}" if best else None

    def finding(self, f):
        """A finding as user-facing text. English documents show its message (written for the agent); any other
        document language never shows an English message: the catalogue sentence for its id family
        (finding_key) with {roles} (the roles or brand ids involved) and {sets} (set pairs, cohesion.sets-close)
        filled in, else a generic sentence naming the id."""
        f = f or {}
        fid = str(f.get("id") or "")
        if self.lang == "en":
            return str(f.get("message") or fid)
        key = self.finding_key(fid)
        if not key:
            sev = f.get("severity") if f.get("severity") in ("gate", "warn", "info") else "warn"
            return self(f"finding.generic.{sev}", id=fid)
        roles = list(dict.fromkeys(self.word("role", str(r).split(".")[-1]) for r in (f.get("roles") or []) if r))
        meas = f.get("measured")
        sets = ", ".join(str(x) for x in meas) if isinstance(meas, list) and all(isinstance(x, str) for x in meas) else ""
        text = self(key, id=fid, roles=" / ".join(roles), sets=sets)
        text = re.sub(r"\s*\(\s*\)", "", text)  # an empty (…) when the finding carries no roles
        return re.sub(r"\s*:\s*$", "", text).strip()

    def word(self, prefix, value, default=None):
        """An enum word (axis pole, role, pairing mode ...): `<prefix>.<value>` when the key exists, else the value
        itself (or default) unchanged."""
        key = f"{prefix}.{value}"
        if key in self.en:
            return self(key)
        return value if default is None else default

    def findings(self, component):
        """[] or one `i18n.missing` warn: no translation for the document language, or the keys that fell back."""
        import identitylib
        if self.lang == "en" or not self.missing:
            return []
        fix = (f"translate assets/i18n/en.json (keep keys and {{placeholders}}) to {self.lang} and save it as "
               f"<work>/{WORK_FILE} with \"_lang\": \"{self.lang}\", then re-run")
        if not self.table:
            return [identitylib.finding(
                "i18n.missing", component, "warn",
                f"no {self.lang} translation of the tool text; {len(self.missing)} label(s) shown in English",
                measured=len(self.missing), threshold=0, suggested_fix=fix)]
        return [identitylib.finding(
            "i18n.missing", component, "warn",
            f"{len(self.missing)} label(s) missing from {os.path.basename(self.source or '')} ({self.lang}), shown "
            f"in English: {', '.join(self.missing[:8])}{' ...' if len(self.missing) > 8 else ''}",
            measured=list(self.missing), threshold=0, suggested_fix=fix.replace("translate", "add the missing keys of"))]


def for_brand(brand, work=None):
    return Strings(doc_lang(brand), work)
