#!/usr/bin/env python3
"""Export the language data the skill uses from gflanguages into skills/brand-identity/assets/languages.json.gz.

  pip install -r requirements-dev.txt      # gflanguages (dev only)
  python3 tools/export_languages.py        # rewrite the asset
  python3 tools/export_languages.py --check   # exit 1 when the asset differs from the installed gflanguages

The skill reads only these fields, so only these are shipped (typelib.languages / typelib.scripts):
  languages: id -> name, population, historical, exemplar_chars.base / .auxiliary / .punctuation
             (gflanguages' own order is kept: typelib.lang_id breaks ties by it)
  scripts:   code -> name
Source: gflanguages (Apache-2.0, Google LLC); its exemplar characters are adapted from Unicode CLDR
(THIRD_PARTY_NOTICES.md). The file is gzip-compressed JSON written deterministically (no timestamp).
"""
import argparse
import gzip
import io
import json
import os
import sys

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET = os.path.join(ROOT, "skills", "brand-identity", "assets", "languages.json.gz")
SCHEMA = "brand-identity/languages@1"
FIELDS = ["name", "population", "historical", "base", "auxiliary", "punctuation"]


def collect():
    try:
        import gflanguages
    except ImportError:
        sys.exit("error: gflanguages is not installed (pip install -r requirements-dev.txt)")
    langs = {}
    for lid, rec in gflanguages.LoadLanguages().items():
        ex = rec.exemplar_chars
        langs[lid] = [rec.name, int(rec.population or 0), 1 if rec.historical else 0, ex.base, ex.auxiliary,
                      ex.punctuation]
    scripts = {code: rec.name for code, rec in gflanguages.LoadScripts().items()}
    version = getattr(gflanguages, "__version__", None)
    return {"schema": SCHEMA, "source": {"package": "gflanguages", "version": version, "license": "Apache-2.0",
                                         "exemplars": "adapted from Unicode CLDR (Unicode License v3)"},
            "fields": FIELDS, "languages": langs, "scripts": scripts}


def encode(doc):
    raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, compresslevel=9, mtime=0) as gz:
        gz.write(raw)
    return raw, buf.getvalue()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="compare with the installed gflanguages, write nothing")
    ap.add_argument("--out", default=ASSET)
    a = ap.parse_args(argv)
    doc = collect()
    raw, packed = encode(doc)
    if a.check:
        try:
            with gzip.open(a.out, "rb") as fh:
                same = json.loads(fh.read().decode("utf-8")) == doc
        except OSError:
            same = False
        print(f"{a.out}: {'matches' if same else 'DIFFERS from'} gflanguages {doc['source']['version']}")
        return 0 if same else 1
    with open(a.out, "wb") as fh:
        fh.write(packed)
    print(f"{a.out}: {len(doc['languages'])} languages, {len(doc['scripts'])} scripts, "
          f"{len(raw) / 1024:.0f} KB JSON -> {len(packed) / 1024:.0f} KB gzip (gflanguages {doc['source']['version']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
