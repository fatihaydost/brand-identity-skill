#!/usr/bin/env python3
"""Measure one Claude Code run from its transcript: turns, tokens, billed-input-equivalent, peak context, reads.

  python3 tools/measure_run.py ~/.claude/projects/<project>/<session>.jsonl
  python3 tools/measure_run.py <session>.jsonl --json
  python3 tools/measure_run.py <folder-with-transcripts> --no-subagents

Method:
  turn      one unique assistant `message.id` (a message is written once per content block; per id the largest
            value of each counter is kept, because streamed snapshots grow).
  tokens    input, cache write (cache_creation_input_tokens), cache read (cache_read_input_tokens), output.
  BIE       billed-input-equivalent = input + 1.25 * cache write + 0.1 * cache read + 5 * output.
  context   per turn input + cache write + cache read; "peak" is the largest, "base" the first turn's.
  reads     Read tool calls: distinct files, image reads (png/jpg/jpeg/gif/webp), and the bytes the results put
            into the context (text of the tool result; an image counts 0 bytes here).
A session file also pulls in `<session>/subagents/*.jsonl` unless --no-subagents is given; a folder argument
reads every .jsonl below it. Budgets compared at the end are docs/architecture.md section 9 (to the checkpoint, 3 sets).
Exit status: 0 = measured, 2 = nothing readable.
"""
import argparse
import glob
import json
import os
import sys

sys.dont_write_bytecode = True

W_INPUT, W_CACHE_WRITE, W_CACHE_READ, W_OUTPUT = 1.0, 1.25, 0.1, 5.0
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
# docs/architecture.md section 9: (target = measured mean, release ceiling)
BUDGET = {"turns": (40, 45), "peak_context": (160_000, 175_000), "bie": (860_000, 1_000_000), "image_reads": (6, 8)}
USAGE_KEYS = (("input", "input_tokens"), ("cache_write", "cache_creation_input_tokens"),
              ("cache_read", "cache_read_input_tokens"), ("output", "output_tokens"))


def transcript_files(path, with_subagents=True):
    """The main transcript first, then its subagent transcripts (or every .jsonl below a folder)."""
    if os.path.isdir(path):
        return sorted(glob.glob(os.path.join(path, "**", "*.jsonl"), recursive=True))
    files = [path]
    if with_subagents:
        stem = os.path.splitext(path)[0]
        files += sorted(glob.glob(os.path.join(stem, "subagents", "*.jsonl")))
    return files


def iter_records(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                yield rec


def result_bytes(content):
    """Bytes of the text a tool result put into the context."""
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    total = 0
    for part in content or []:
        if isinstance(part, dict) and part.get("type") == "text":
            total += len(str(part.get("text", "")).encode("utf-8"))
    return total


def read_transcript(path, turns, reads):
    """Fold one transcript into `turns` ({message id: counters}) and `reads` ({tool_use id: {...}})."""
    for rec in iter_records(path):
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if rec.get("type") == "assistant" and msg.get("id"):
            slot = turns.setdefault(msg["id"], {"file": path, "input": 0, "cache_write": 0, "cache_read": 0,
                                                "output": 0, "order": len(turns)})
            usage = msg.get("usage") or {}
            for key, field in USAGE_KEYS:
                slot[key] = max(slot[key], int(usage.get(field) or 0))
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Read":
                    target = str((block.get("input") or {}).get("file_path", ""))
                    reads[block.get("id")] = {"path": target, "bytes": 0,
                                              "image": target.lower().endswith(IMAGE_EXT)}
        elif rec.get("type") == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") in reads:
                    reads[block["tool_use_id"]]["bytes"] = result_bytes(block.get("content"))


def measure(path, with_subagents=True):
    files = transcript_files(path, with_subagents)
    turns, reads = {}, {}
    for f in files:
        read_transcript(f, turns, reads)
    order = sorted(turns.values(), key=lambda t: t["order"])
    total = {k: sum(t[k] for t in order) for k, _ in USAGE_KEYS}
    contexts = [t["input"] + t["cache_write"] + t["cache_read"] for t in order]
    bie = (W_INPUT * total["input"] + W_CACHE_WRITE * total["cache_write"] + W_CACHE_READ * total["cache_read"]
           + W_OUTPUT * total["output"])
    by_file = {}
    for r in reads.values():
        if r["path"]:
            by_file[r["path"]] = by_file.get(r["path"], 0) + r["bytes"]
    return {
        "files": [os.path.basename(f) for f in files],
        "turns": len(order),
        "tokens": total,
        "processed": total["input"] + total["cache_write"] + total["cache_read"],
        "bie": round(bie),
        "peak_context": max(contexts, default=0),
        "base_context": contexts[0] if contexts else 0,
        "read_calls": len(reads),
        "image_reads": sum(1 for r in reads.values() if r["image"]),
        "files_read": len(by_file),
        "read_bytes": sum(by_file.values()),
        "read_files": [{"path": p, "bytes": b} for p, b in sorted(by_file.items(), key=lambda kv: -kv[1])],
    }


def verdict(name, value):
    target, ceiling = BUDGET[name]
    return "ok" if value <= target else "over target" if value <= ceiling else "OVER CEILING"


def fmt_tokens(n):
    return f"{n / 1e6:.3f} M" if n >= 1e6 else f"{n / 1e3:.1f} K" if n >= 1e3 else str(n)


def table(m, top=8):
    t = m["tokens"]
    rows = [
        ("turns", str(m["turns"]), verdict("turns", m["turns"])),
        ("input / cache write / cache read / output",
         f"{fmt_tokens(t['input'])} / {fmt_tokens(t['cache_write'])} / {fmt_tokens(t['cache_read'])} / "
         f"{fmt_tokens(t['output'])}", ""),
        ("BIE (1 / 1.25 / 0.1 / 5)", fmt_tokens(m["bie"]), verdict("bie", m["bie"])),
        ("peak context (base)", f"{fmt_tokens(m['peak_context'])} ({fmt_tokens(m['base_context'])})",
         verdict("peak_context", m["peak_context"])),
        ("image reads", str(m["image_reads"]), verdict("image_reads", m["image_reads"])),
        ("files read / bytes", f"{m['files_read']} / {m['read_bytes']:,}", ""),
    ]
    width = max(len(r[0]) for r in rows)
    lines = [f"{len(m['files'])} transcript(s), {m['read_calls']} Read call(s)"]
    lines += [f"{a:<{width}}  {b}" + (f"  [{c}]" if c else "") for a, b, c in rows]
    if m["read_files"]:
        lines.append("largest reads:")
        lines += [f"  {f['bytes']:>9,}  {f['path']}" for f in m["read_files"][:top]]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcript", help="a Claude Code transcript .jsonl, or a folder of them")
    ap.add_argument("--no-subagents", action="store_true", help="ignore <session>/subagents/*.jsonl")
    ap.add_argument("--json", action="store_true", help="print the measurements as JSON")
    ap.add_argument("--top", type=int, default=8, help="how many of the largest reads to list (default 8)")
    args = ap.parse_args()
    if not os.path.exists(args.transcript):
        print(f"error: {args.transcript} not found", file=sys.stderr)
        return 2
    m = measure(args.transcript, not args.no_subagents)
    if m["turns"] == 0:
        print("error: no assistant turns found in the transcript(s)", file=sys.stderr)
        return 2
    print(json.dumps(m, indent=2) if args.json else table(m, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
