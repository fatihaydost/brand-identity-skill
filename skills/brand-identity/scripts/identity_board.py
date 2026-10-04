#!/usr/bin/env python3
"""Comparison board for all sets of a brand work folder. docs/architecture.md sections 4.7, 7.2.

Writes to <work>/:
  board.html, board.png  cards side by side (RECOMMENDED framed), with a site each card's applied first screen
                         and header close-up, then the comparison table. For the user.
  review.png             <= 2000 px long edge, 2x2 grid of cards (4th cell: compact table when there are 3 sets),
                         logo rows legible. For the model's one look.
  table.md               set · mechanism · one column per component in new/refresh · where it differs ·
                         risks/flags. keep/none components get no column.
Cards are (re)rendered through identity_card.render when card.png is missing or older than its inputs.

Usage:
  python3 scripts/identity_board.py brand-identity/ferrow
"""
import argparse
import glob
import json
import os
import shutil
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if __name__ == "__main__":
    import pydeps  # noqa: E402
    pydeps.ensure()  # missing packages: re-run in uv's cached environment (or the fallback venv)

import identity_card  # noqa: E402
import identitylib  # noqa: E402
import presentlib as pl  # noqa: E402
import render_png  # noqa: E402

e = pl.e
COMP_KEY = {"logo": "board.col.logo", "type": "board.col.type", "palette": "board.col.palette"}


def _num_word(t, n):
    return t(f"board.n_word.{n}") if 1 <= n <= 4 else t("board.n_word.other", n=n)


def _t(infos):
    return pl.strings(infos[0]) if infos else pl.strings({"identity": {"brand": {}}, "work": None})


def _frame_font():
    try:
        import palette_board
        return palette_board.font_faces()
    except Exception:  # noqa: BLE001 - the frame falls back to system-ui
        return ""


def _stale(set_dir):
    png = os.path.join(set_dir, "card.png")
    if not os.path.isfile(png):
        return True
    t = os.path.getmtime(png)
    inputs = [os.path.join(set_dir, n) for n in ("identity.json", "palette.json")]
    inputs += glob.glob(os.path.join(set_dir, "logo", "**", "*.svg"), recursive=True)
    return any(os.path.isfile(p) and os.path.getmtime(p) > t for p in inputs)


# ----------------------------------------------------------------------------- table

def table_components(infos):
    comps = []
    for c in identitylib.COMPONENTS:
        if any(pl.mode_of(i["identity"], c) in ("new", "refresh") for i in infos):
            comps.append(c)
    return comps


def cell_component(info, comp, t=None):
    t = t or pl.strings(info)
    ident, pal = info["identity"], info["palette"]
    if comp == "logo":
        lg = ident.get("logo") or {}
        kind = t.word("logo_type", lg.get("type") or "", (lg.get("type") or "").replace("+", " + "))
        return f"{kind}: {lg.get('concept') or ''}".strip(": ")
    if comp == "type":
        ty = ident.get("type") or {}
        d, tx = ty.get("display") or {}, ty.get("text") or {}
        dw = "/".join(str(w) for w in d.get("weights") or [])
        tw = "/".join(str(w) for w in tx.get("weights") or [])
        m = ty.get("mono") or {}
        mono = (f" + {t('board.mono')} {m.get('family', '?')} {'/'.join(str(w) for w in m.get('weights') or [])}"
                if m.get("family") else "")
        pm = ty.get("pairing_mode") or ""
        return (f"{d.get('family', '?')} {dw} + {tx.get('family', '?')} {tw}{mono} "
                f"({t.word('pairing', pm, pm.replace('-', ' '))})")
    if comp == "palette":
        if not pal:
            return t("board.not_built")
        lt = pal["modes"]["light"]
        feels = ", ".join(pal.get("feels") or [])
        hexes = []
        for role in ("primary", "accent"):
            bc = pl.brand_colour(pal, role, lt.get(role))
            if bc:
                hexes.append(f"{bc['name'] + ' ' if bc['name'] else ''}{bc['hex']}")
            elif lt.get(role):
                hexes.append(lt[role])
        return f"{pal.get('name') or ''}: {' + '.join(hexes)}" + (f"; {feels}" if feels else "")
    return ""


def risks(info, card_findings, t=None):
    t = t or pl.strings(info)
    audit = info["identity"].get("audit") or {}
    fs = pl.shown_findings(info["identity"], card_findings)
    gates = [f for f in fs if f.get("severity") == "gate"]
    warns = [f for f in fs if f.get("severity") == "warn"]
    parts = []
    # finding messages are written for the agent (English); t.finding() localises them for other languages
    if gates:
        parts.append(("gate", f"{t.n('card.gates', len(gates))}: {t.finding(gates[0])}"))
    if warns:
        parts.append(("warn", f"{t.n('card.warnings', len(warns))}: {t.finding(warns[0])}"))
    # AI defaults: only ids the audit matched (audit.defaults.matched). Matched + reason = justified (info);
    # matched without a reason = warn; declared but not matched = not shown.
    matched = ((audit.get("defaults") or {}).get("matched") or []) if isinstance(audit.get("defaults"), dict) else []
    matched = [m if isinstance(m, str) else (m or {}).get("id") for m in matched]
    why = {d.get("id"): d.get("why") for d in info["identity"].get("defaults_used") or [] if d.get("id")}
    for mid in [m for m in matched if m]:
        # a reason may be filed under the flag id or its logo tag (logo-literal-object / literal-object)
        reason = why.get(mid) or why.get(mid.removeprefix("logo-")) or why.get(f"logo-{mid}")
        if reason:
            w = " ".join(str(reason).split())
            w = w if len(w) <= 70 else w[:69].rstrip() + "…"
            parts.append(("info", f"{t('board.ai_justified')}: {mid} — {w}"))
        elif not any(mid in str(f.get("id", "")) or mid in str(f.get("message", "")) for f in warns):
            parts.append(("warn", f"{t('board.ai_no_reason')}: {mid}"))
    if not parts:
        parts.append(("ok", t("board.none_found") if audit else t("board.no_audit")))
    return parts


def _md(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def _head(t, comps):
    return ([t("board.col.set"), t("board.col.mechanism")] + [t(COMP_KEY[c]) for c in comps]
            + [t("board.col.differs"), t("board.col.risks")])


def table_md(infos, results, t=None):
    t = t or _t(infos)
    comps = table_components(infos)
    head = _head(t, comps)
    rows = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for info in infos:
        st = info["identity"]["set"]
        name = f"**{st['id']} · {st.get('name') or ''}**" + (f" ({t('card.recommended')})" if st.get("recommended")
                                                              else "")
        cells = [name, st.get("mechanism") or ""] + [cell_component(info, c, t) for c in comps]
        cells += [st.get("differs_by") or "", "; ".join(x for _k, x in risks(info, results[info["dir"]], t))]
        rows.append("| " + " | ".join(_md(c) for c in cells) + " |")
    for comp, fs in pl.kept_notes([i["identity"] for i in infos]).items():
        rows.append("")
        rows.append(t("board.kept_notes_md", comp=t(f"comp.{comp}"), n=len(fs)) + " — " + "; ".join(
            dict.fromkeys(_md(t.finding(f)) for f in fs[:3])))
    return "\n".join(rows) + "\n"


def table_html(infos, results, t=None):
    t = t or _t(infos)
    comps = table_components(infos)
    head = _head(t, comps)
    out = ["<table><thead><tr>" + "".join(f"<th>{e(h)}</th>" for h in head) + "</tr></thead><tbody>"]
    for info in infos:
        st = info["identity"]["set"]
        tds = [f"<td>{e(st['id'])} · {e(st.get('name') or '')}</td>",
               f'<td style="width:24%">{e(st.get("mechanism") or "")}</td>']
        for c in comps:
            txt = e(cell_component(info, c, t))
            if c == "palette" and info["palette"]:
                lt = info["palette"]["modes"]["light"]
                sw = "".join(f'<i class="sw" style="background:{lt[k]}"></i>' for k in ("primary", "accent", "text")
                             if lt.get(k))
                txt = sw + " " + txt
            tds.append(f"<td>{txt}</td>")
        tds.append(f"<td>{e(st.get('differs_by') or '')}</td>")
        rk = "<br>".join(f'<span class="{"g" if k == "gate" else ""}">{e(x)}</span>'
                         for k, x in risks(info, results[info["dir"]], t))
        tds.append(f'<td class="risk">{rk}</td>')
        out.append("<tr>" + "".join(tds) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


# ----------------------------------------------------------------------------- board

def _site_shots(set_dir):
    """Applied-site screenshots from site_preview (WP4), if any: (first screen, header close-up)."""
    def first(pats):
        for p in pats:
            hits = sorted(glob.glob(os.path.join(set_dir, "site", p)))
            if hits:
                return hits[0]
        return None
    return (first(["*first*screen*.png", "*desktop*.png", "*applied*.png"]),
            first(["*header*.png", "*close*.png"]))


def cards_html(infos, results, work, t=None):
    t = t or _t(infos)
    cols = []
    for info in infos:
        st = info["identity"]["set"]
        res = results[info["dir"]]
        failed = identity_card.first_gate(info["identity"], res)
        badge = (f'<span class="rec">{e(t("board.recommended"))}</span>' if st.get("recommended") and not failed else
                 f'<span class="fail">{e(t("card.failed"))}</span>' if failed else "")
        rel = os.path.relpath(os.path.join(info["dir"], "card.png"), work).replace(os.sep, "/")
        site = ""
        first, header = _site_shots(info["dir"])
        if first or header:
            imgs = ""
            if first:
                imgs += (f'<div><img src="{e(os.path.relpath(first, work))}" alt="">'
                         f'<div class="cap">{e(t("board.site_first"))}</div></div>')
            if header:
                imgs += (f'<div><img src="{e(os.path.relpath(header, work))}" alt="">'
                         f'<div class="cap">{e(t("board.site_header"))}</div></div>')
            site = f'<div class="site">{imgs}</div>'
        cls = "col is-rec" if st.get("recommended") and not failed else "col"
        cols.append(f'<div class="{cls}"><div class="hd">{e(st["id"])}<b>{e(st.get("name") or "")}</b>{badge}</div>'
                    f'<img class="shot" src="{e(rel)}" alt="{e(t("board.card_alt", id=st["id"]))}">{site}</div>')
    return "".join(cols)


def lede(infos, t=None):
    t = t or _t(infos)
    rec = next((i for i in infos if i["identity"]["set"].get("recommended")), None)
    if not rec:
        return e(t("board.no_rec"))
    st = rec["identity"]["set"]
    return (f"<b>{e(t('board.rec_line', id=st['id'], name=st.get('name') or ''))}</b> "
            f"{e(st.get('mechanism') or '')}")


def board_warnings(infos, t=None):
    """Board-level warnings already present in the sets' audits (cohesion findings are written by pipeline)."""
    t = t or _t(infos)
    seen, out = set(), []
    for i in infos:
        for f in (i["identity"].get("audit") or {}).get("findings") or []:
            txt = t.finding(f) if f.get("component") == "cohesion" else None
            if txt and txt not in seen:  # dedupe what the reader sees (the sentence carries the sets involved)
                seen.add(txt)
                out.append(txt)
    lines = [f"{e(t('board.cohesion'))}: {e(m)}" for m in out[:4]]
    for comp, fs in pl.kept_notes([i["identity"] for i in infos]).items():
        gates = sum(1 for f in fs if f.get("severity") == "gate")
        first = " ".join(str(t.finding(fs[0])).split())[:110]
        lines.append(e(t.n("board.kept_notes", len(fs), comp=t(f"comp.{comp}")))
                     + (f" ({e(t.n('card.gates', gates))})" if gates else "") + f" — {e(first)}")
    if not lines:
        return ""
    return '<div class="warns">' + "<br>".join(lines) + "</div>"


def review_html(infos, results, work, css, font, t=None):
    t = t or _t(infos)
    cells = []
    for info in infos[:4]:
        st = info["identity"]["set"]
        rel = os.path.relpath(os.path.join(info["dir"], "card.png"), work).replace(os.sep, "/")
        failed = identity_card.first_gate(info["identity"], results[info["dir"]])
        rec = " is-rec" if st.get("recommended") and not failed else ""
        cells.append(f'<div class="cell{rec}"><img src="{e(rel)}" alt=""></div>')  # the card names itself
    if len(cells) < 4:
        rows = []
        for info in infos:
            st = info["identity"]["set"]
            rk = "; ".join(x for k, x in risks(info, results[info["dir"]], t) if k != "info")  # compact for review
            g = any(k == "gate" for k, _x in risks(info, results[info["dir"]], t))
            rows.append(f'<div class="r"><b>{e(st["id"])}</b><div>{e(st.get("name") or "")} — '
                        f'{e(st.get("differs_by") or st.get("mechanism") or "")}<br>'
                        f'<span class="{"g" if g else ""}">{e(rk)}</span></div></div>')
        cells.append(f'<div class="tcell"><h2>{e(t("board.comparison"))}</h2>{"".join(rows)}</div>')
    while len(cells) < 4:
        cells.append('<div class="cell"></div>')
    return pl.fill(pl.read_template("board", "review.html"),
                   {"BRAND": e(infos[0]["identity"]["brand"]["name"]), "FRAME_FONT": font, "CSS": css,
                    "CELLS": "".join(cells), "DOC_LANG": e(t.html_lang), "T_REVIEW": e(t("board.review"))})


def _render_page(html_path, png_path, width, height, scale):
    tmp = png_path + ".pages"
    r = render_png.render(html_path, tmp, width, height, pages=True, probe=True, scale=scale)
    if not r.get("pages"):
        raise RuntimeError(f"no page rendered from {html_path}")
    shutil.move(r["pages"][0], png_path)
    shutil.rmtree(tmp, ignore_errors=True)
    r["png"] = png_path
    return r


def board_page(infos, results, work, date_str=None, t=None):
    """The board's HTML (labels in the document language) -> (html, width)."""
    n = len(infos)
    cols = n if n <= 3 else 2
    width = 3344 if cols == 3 else (2300 if cols == 2 else 1400)
    css = pl.read_template("board", "board.css")
    font = _frame_font()
    brand = infos[0]["identity"]["brand"]
    t = t or _t(infos)
    d = date_str or pl.today(t=t)
    nw = _num_word(t, n)
    values = {
        "LANG": e((brand.get("languages") or ["en"])[0]), "BRAND": e(brand["name"]), "FRAME_FONT": font,
        "WIDTH": width, "COLS": cols, "CSS": css, "ARROW": pl.ARROW, "DOC_LANG": e(t.html_lang),
        "T_DOC": e(t("card.doc")), "T_SUB": e(t("board.sub", n_word=nw)), "T_PICK": e(t("board.pick")),
        "T_COMPARISON": e(t("board.comparison")), "T_DRAFT": e(t("card.draft")),
        "VERSION": identity_card.VERSION, "DATE": e(d), "N": n,
        "TITLE": e(t("board.title", n_word=nw, brand=brand["name"])), "LEDE": lede(infos, t),
        "CARDS": cards_html(infos, results, work, t), "TABLE": table_html(infos, results, t),
        "WARNINGS": board_warnings(infos, t),
        "FOOT_MID": e(" · ".join(x for x in (brand["name"], brand.get("sector")) if x)), "YEAR": d[-4:],
    }
    return pl.fill(pl.read_template("board", "board.html"), values), width


def render(work_dir, scale=1.5, rerender=False, date_str=None):
    """Cards (when stale) -> board.html/png, review.png, table.md. Returns paths and per-set card findings."""
    work = os.path.abspath(work_dir)
    dirs = identitylib.set_dirs(work)
    if not dirs:
        raise FileNotFoundError(f"no sets under {work}/sets (expected sets/A/identity.json, ...)")
    infos = [pl.load_set(d) for d in dirs]
    results, cards = {}, {}
    for info in infos:
        if rerender or _stale(info["dir"]):
            r = identity_card.render(info["dir"], date_str=date_str)
            cards[info["dir"]] = r
            results[info["dir"]] = r["findings"]
        else:
            cached = os.path.join(work, ".cache", "cards", f"{info['identity']['set']['id']}.json")
            try:
                with open(cached, encoding="utf-8") as fh:
                    results[info["dir"]] = json.load(fh)
            except (OSError, ValueError):
                r = identity_card.render(info["dir"], date_str=date_str)
                cards[info["dir"]] = r
                results[info["dir"]] = r["findings"]
    t = _t(infos)
    page, width = board_page(infos, results, work, date_str, t)
    css, font = pl.read_template("board", "board.css"), _frame_font()
    board_html = os.path.join(work, "board.html")
    with open(board_html, "w", encoding="utf-8") as fh:
        fh.write(page)
    board = _render_page(board_html, os.path.join(work, "board.png"), width, 1200, scale)
    review_path = os.path.join(work, ".cache", "review.html")
    os.makedirs(os.path.dirname(review_path), exist_ok=True)
    with open(review_path, "w", encoding="utf-8") as fh:
        # the review page lives in .cache/, so image paths are relative to the work folder via <base>
        fh.write(review_html(infos, results, work, css, font, t).replace(
            "<head>", f'<head><base href="{pl._url(work, None).rstrip("/")}/">', 1))
    review = _render_page(review_path, os.path.join(work, "review.png"), 2000, 1250, 1.0)
    table = os.path.join(work, "table.md")
    with open(table, "w", encoding="utf-8") as fh:
        fh.write(table_md(infos, results, t))
    findings = render_png.probe_findings(board, component="card") + t.findings("card")
    return {"board": board["png"], "board_html": board_html, "review": review["png"], "table": table,
            "cards": {os.path.basename(k): v["png"] for k, v in cards.items()},
            "card_findings": {os.path.basename(k): v for k, v in results.items()}, "board_findings": findings,
            "review_size": render_png.png_size(review["png"])}


def main(argv=None):
    identity_card.pl_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work_dir")
    ap.add_argument("--rerender", action="store_true", help="re-render every card even when up to date")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        r = render(a.work_dir, rerender=a.rerender)
    except (identitylib.IdentityError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(r, indent=1, default=str)[:20000])
        return 0
    for k in ("review", "board", "table"):  # the model reads review.png; board.png is for the user
        print(r[k])
    for sid, fs in r["card_findings"].items():
        g = [f for f in fs if f["severity"] == "gate"]
        print(f"{sid}: {'✔' if not g else '✘ ' + g[0]['message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
