"""Unit tests for tools/measure_run.py and the size checks in tools/skillmeta.py."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)
import measure_run as mr  # noqa: E402
import skillmeta as sm  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def assistant(mid, usage, tools=()):
    content = [{"type": "tool_use", "id": tid, "name": "Read", "input": {"file_path": p}} for tid, p in tools]
    return {"type": "assistant", "message": {"id": mid, "usage": usage, "content": content or [{"type": "text", "text": "x"}]}}


def result(tid, text):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}


class TestMeasureRun(unittest.TestCase):
    def build(self, d):
        main = os.path.join(d, "s1.jsonl")
        sub = os.path.join(d, "s1", "subagents", "agent-1.jsonl")
        u1 = {"input_tokens": 10, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 0, "output_tokens": 50}
        u1b = dict(u1, output_tokens=200)           # a later snapshot of the same message
        u2 = {"input_tokens": 5, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000, "output_tokens": 20}
        rows = [assistant("m1", u1, [("t1", "/x/a.md"), ("t2", "/x/shot.png")]), assistant("m1", u1b),
                result("t1", "abc" * 100), result("t2", ""), assistant("m2", u2)]
        write(main, "\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
        write(sub, json.dumps(assistant("m3", u2, [("t3", "/x/a.md")])) + "\n" + json.dumps(result("t3", "z" * 10)) + "\n")
        return main

    def test_counts_turns_tokens_bie_and_reads(self):
        with tempfile.TemporaryDirectory() as d:
            m = mr.measure(self.build(d))
        self.assertEqual(m["turns"], 3)
        self.assertEqual(m["tokens"], {"input": 20, "cache_write": 1200, "cache_read": 2000, "output": 240})
        self.assertEqual(m["bie"], round(20 + 1.25 * 1200 + 0.1 * 2000 + 5 * 240))
        self.assertEqual(m["peak_context"], 1105)
        self.assertEqual(m["image_reads"], 1)
        self.assertEqual(m["files_read"], 2)
        self.assertEqual(m["read_bytes"], 310)
        self.assertEqual(len(m["files"]), 2)

    def test_no_subagents_and_cli(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.build(d)
            self.assertEqual(mr.measure(path, with_subagents=False)["turns"], 2)
            r = subprocess.run([sys.executable, os.path.join(TOOLS, "measure_run.py"), path, "--json"],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["turns"], 3)
            r = subprocess.run([sys.executable, os.path.join(TOOLS, "measure_run.py"), path],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
            self.assertIn("BIE", r.stdout)
            self.assertIn("[ok]", r.stdout)
            empty = os.path.join(d, "empty.jsonl")
            write(empty, "")
            r = subprocess.run([sys.executable, os.path.join(TOOLS, "measure_run.py"), empty],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
            self.assertEqual(r.returncode, 2)


class TestSkillBudgets(unittest.TestCase):
    def skill(self, d, body="body\n", desc="A short description."):
        write(os.path.join(d, "SKILL.md"), f"---\nname: brand-identity\ndescription: {desc}\n---\n{body}")
        return d

    def test_skill_md_and_description_caps(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(sm.check_budgets(self.skill(d)), [])
            problems = sm.check_budgets(self.skill(d, body="x" * 9000, desc="d" * 601))
            self.assertEqual(len(problems), 2)
            self.assertTrue(any("bytes" in p for p in problems) and any("description" in p for p in problems))

    def test_reference_caps_only_for_existing_files(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(sm.check_reference_caps(os.path.join(d, "references")), [])
            write(os.path.join(d, "references", "cohesion.md"), "x" * (5 * 1024))
            write(os.path.join(d, "references", "type.md"), "x" * (6 * 1024 + 1))
            write(os.path.join(d, "references", "mystery.md"), "x")
            problems = sm.check_reference_caps(os.path.join(d, "references"))
            self.assertEqual(len(problems), 2)
            self.assertTrue(any(p.startswith("type.md") for p in problems))
            self.assertTrue(any(p.startswith("mystery.md") for p in problems))

if __name__ == "__main__":
    unittest.main()
