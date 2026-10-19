import json
import os
import stat
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from agentlab import agentise, agents, hardware, jobs, llm, projects, runners, tokenizers

try:
    from tests.test_agentise import FAKE_LLAMA
except ImportError:
    from test_agentise import FAKE_LLAMA

# A tiny byte-level BPE (in the Hugging Face tokenizer.json layout) with merges we can reason about by hand.
TINY = {"model": {"vocab": {"h": 0, "e": 1, "l": 2, "o": 3, "he": 4, "ll": 5, "hell": 6, "hello": 7, "Ġ": 8, "w": 9, "r": 10, "d": 11, "Ġw": 12, "or": 13, "ld": 14},
                  "merges": ["h e", "l l", "he ll", "hell o", "Ġ w", "o r", "l d"]},
        "pre_tokenizer": {"type": "ByteLevel"}, "added_tokens": [{"id": 99, "content": "<|end|>", "special": True}]}


class EstimateTests(unittest.TestCase):
    def test_presets(self):
        self.assertEqual((tokenizers.estimate("a" * 40, "chars4"), tokenizers.estimate("a" * 40, "chars3")), (10, 14))
        self.assertEqual(tokenizers.estimate("one two three four", "words"), 6)
        self.assertEqual(tokenizers.estimate("你好世界abcd", "cjk"), 5)  # 4 characters + 4 letters/4
        self.assertEqual((tokenizers.estimate("", "chars4"), tokenizers.estimate(None)), (0, 0))

    def test_spec_validation(self):
        self.assertEqual(tokenizers.validate_spec(None), {"kind": "preset", "preset": "chars4"})
        for bad in ("gpt", {"kind": "magic"}, {"kind": "preset", "preset": "nope"}, {"kind": "file"}, {"kind": "file", "project": "p"}):
            with self.assertRaises(ValueError, msg=bad):
                tokenizers.validate_spec(bad)


class BpeTests(unittest.TestCase):
    def test_hand_checked_merges_and_special_tokens(self):
        t = tokenizers.ByteLevelBPE(TINY)
        self.assertEqual(t.encode("hello"), [7])  # h e l l o -> he ll o -> hell o -> hello
        self.assertEqual(t.encode("hello world"), [7, 12, 13, 14])  # 'hello', 'Ġw', 'or', 'ld'
        self.assertEqual(t.encode("hello<|end|>hello"), [7, 99, 7])
        self.assertEqual(t.count(""), 0)
        self.assertEqual(t.count("xyz"), 3)  # unknown bytes still count, one per symbol

    def test_rejects_tokenizers_it_cannot_do(self):
        for bad in ({"model": {"type": "Unigram", "vocab": []}}, {"model": {"vocab": {}, "merges": []}, "pre_tokenizer": {"type": "Metaspace"}}, {"nothing": True}):
            with self.assertRaises(tokenizers.TokenizerError):
                tokenizers.ByteLevelBPE(bad)

    def test_loaded_from_a_project_file_with_safe_fallbacks(self):
        with tempfile.TemporaryDirectory() as root:
            pid = projects.create(root, "Tok")["id"]
            projects.write_file(root, pid, "tokenizer.json", json.dumps(TINY))
            spec = {"kind": "file", "project": pid, "path": "tokenizer.json"}
            self.assertEqual(tokenizers.count("hello world", spec, root), 4)
            d = tokenizers.describe(root, spec)
            self.assertEqual((d["exact"], d["ok"]), (True, True))
            self.assertEqual(tokenizers.count("a" * 40, {"kind": "file", "project": pid, "path": "missing.json"}, root), 10)  # falls back to the estimate
            self.assertFalse(tokenizers.describe(root, {"kind": "file", "project": pid, "path": "../x"})["ok"])
            projects.write_file(root, pid, "bad.json", "{not json")
            self.assertFalse(tokenizers.describe(root, {"kind": "file", "project": pid, "path": "bad.json"})["ok"])

    def test_matches_the_real_gpt2_tokenizer_exactly(self):
        """Ground truth: GPT-2's published token ids. Uses the real tokenizer.json from Hugging Face (skipped offline)."""
        cache = Path(tempfile.gettempdir()) / "agentlab-gpt2-tokenizer.json"
        if not cache.exists():
            try:
                with urllib.request.urlopen("https://huggingface.co/openai-community/gpt2/resolve/main/tokenizer.json", timeout=20) as r:
                    cache.write_bytes(r.read())
            except OSError:
                self.skipTest("Hugging Face is not reachable")
        t = tokenizers.ByteLevelBPE(json.loads(cache.read_text()))
        self.assertEqual(t.encode("Hello world"), [15496, 995])
        self.assertEqual(t.encode("The quick brown fox jumps over the lazy dog."), [464, 2068, 7586, 21831, 18045, 625, 262, 16931, 3290, 13])
        self.assertEqual(t.encode("Hello<|endoftext|>"), [15496, 50256])
        text = "def foo(x):\n    return x * 2  # naïve 😀"
        self.assertLess(abs(t.count(text) - tokenizers.estimate(text)), 12)  # sane next to the estimate


class ProviderTokenizerTests(unittest.TestCase):
    def test_usage_numbers_follow_the_providers_tokenizer(self):
        with tempfile.TemporaryDirectory() as root:
            pid = projects.create(root, "Tok")["id"]
            projects.write_file(root, pid, "tok.json", json.dumps(TINY))
            plain = llm.validate_provider({"type": "mock"})
            exact = llm.validate_provider({"type": "mock", "tokenizer": {"kind": "file", "project": pid, "path": "tok.json"}})
            msgs = [{"role": "user", "content": "hello world hello world"}]
            a = llm.chat(plain, "", msgs, project=root)["usage"]["input"]
            b = llm.chat(exact, "", msgs, project=root)["usage"]["input"]
            self.assertEqual(a, 6 + 20)  # 23 characters / 4 + the mock's fixed overhead
            self.assertEqual(b, 9 + 20)  # hello | Ġw or ld | Ġ hello | Ġw or ld = 1 + 3 + 2 + 3 BPE tokens
            for bad in ({"tokenizer": "gpt"}, {"compute": {"device": "tpu"}}, {"compute": {"threads": 0}}):
                with self.assertRaises(ValueError, msg=bad):
                    llm.validate_provider({"type": "mock", **bad})

    def test_context_window_trims_old_tool_output(self):
        with tempfile.TemporaryDirectory() as root:
            rt = jobs.Runtime(root)
            big = [{"role": "user", "content": "go"}] + [{"role": "tool", "tool_call_id": str(i), "name": "t", "content": "x" * 4000} for i in range(12)]
            rt._compact(big, None, 0)  # no window set: below the size threshold, untouched
            self.assertTrue(all(len(m["content"]) == 4000 for m in big[1:]))
            rt._compact(big, None, 5000)  # ~12,000 tokens against a 5,000-token window
            self.assertTrue(all(m["content"].startswith("[earlier") for m in big[1:5]))
            self.assertTrue(all(len(m["content"]) == 4000 for m in big[-8:]))  # the recent turns stay intact
        self.assertEqual(agents.validate({"name": "a"})["limits"]["context_tokens"], 0)
        with self.assertRaises(agents.AgentError):
            agents.validate({"name": "a", "limits": {"context_tokens": -1}})


class ComputeTests(unittest.TestCase):
    def test_hardware_info_and_fit(self):
        hw = hardware.info()
        self.assertGreaterEqual(hw["cpu_count"], 1)
        self.assertGreaterEqual(hw["recommended_threads"], 1)
        small = {"cpu_count": 4, "recommended_threads": 3, "ram_bytes": 16e9, "ram_available_bytes": 10e9, "gpus": []}
        self.assertEqual(hardware.check_fit(2e9, 4096, "auto", small)["verdict"], "fits")
        self.assertEqual(hardware.check_fit(8e9, 4096, "auto", small)["verdict"], "tight")
        big = hardware.check_fit(30e9, 4096, "cpu", small)
        self.assertEqual((big["verdict"], big["where"]), ("too_big", "RAM"))
        self.assertIn("smaller", big["advice"])
        gpu = {**small, "gpus": [{"name": "X", "memory_bytes": 8e9}]}
        self.assertEqual(hardware.check_fit(5e9, 4096, "auto", gpu)["where"], "GPU memory")
        self.assertEqual(hardware.check_fit(5e9, 4096, "cpu", gpu)["where"], "RAM")
        self.assertEqual(hardware.check_fit(1e9, 2048, "auto", {**small, "ram_available_bytes": None, "ram_bytes": None})["verdict"], "unknown")

    def test_cpu_and_threads_reach_the_runner_and_ollama(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)  # registered first, so it runs LAST (after the runner is stopped)
        if True:
            root = tmp.name
            pid = projects.create(root, "W", quota_mb=20)["id"]
            projects.workspace(root, pid).joinpath("m.gguf").write_bytes(b"GGUF" + b"\0" * 64)
            args_file = Path(root) / "args.txt"
            fake = Path(root) / "llama-server"
            fake.write_text(FAKE_LLAMA.replace("a = sys.argv[1:]", f"a = sys.argv[1:]; open({str(args_file)!r}, 'w').write(' '.join(a))"))
            fake.chmod(0o755)
            res = agentise.from_weights(root, {"project_id": pid, "path": "m.gguf", "device": "cpu", "threads": 6, "tokenizer": {"kind": "preset", "preset": "chars3"}}, binary=str(fake))
            self.addCleanup(lambda rid=res["runner"]["id"], r=root: runners.stop(r, rid) if runners._load(r) else None)
            end = time.time() + 10
            while not args_file.exists() and time.time() < end:
                time.sleep(0.05)
            args = args_file.read_text().split()
            self.assertEqual((args[args.index("-ngl") + 1], args[args.index("-t") + 1]), ("0", "6"))
            self.assertEqual((res["runner"]["device"], res["runner"]["threads"]), ("cpu", 6))
            self.assertEqual(res["agent"]["provider"]["compute"], {"device": "cpu", "threads": 6})
            self.assertEqual(res["agent"]["provider"]["tokenizer"], {"kind": "preset", "preset": "chars3"})
            for bad in ({"device": "tpu"}, {"threads": 0}, {"threads": 999}):
                with self.assertRaises(agentise.AgentiseError, msg=bad):
                    agentise.from_weights(root, {"project_id": pid, "path": "m.gguf", **bad}, binary=str(fake))
            ollama = Path(root) / "ollama"
            ollama.write_text(f'#!/bin/sh\ncat "$4" > "{root}/modelfile.txt"\n')
            ollama.chmod(0o755)
            agentise.from_weights(root, {"project_id": pid, "path": "m.gguf", "runtime": "ollama", "device": "cpu", "threads": 4, "name": "cpu-one"}, binary=str(ollama))
            self.assertEqual((Path(root) / "modelfile.txt").read_text().split("\n")[1:3], ["PARAMETER num_gpu 0", "PARAMETER num_thread 4"])


if __name__ == "__main__":
    unittest.main()
