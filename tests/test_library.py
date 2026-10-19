import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agentlab import agentise, agents, jobs, library, projects, vault

try:
    from tests.test_jobs import R, Script
except ImportError:
    from test_jobs import R, Script


class LibCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.proj = projects.create(self.root, "Lib")["id"]
        self.ws = projects.workspace(self.root, self.proj)

    def run_job(self, script, **agent_kw):
        aid = agents.create(self.root, {"name": "Bot", "mode": "auto", "provider": {"type": "mock"}, **agent_kw})["id"]
        rt = jobs.Runtime(self.root, approval_timeout=0.4)
        with mock.patch("agentlab.jobs.llm.chat", script):
            j = rt.start(self.proj, aid, "go")
            end = time.time() + 20
            while rt.get(j["id"])["status"] not in jobs.FINAL and time.time() < end:
                time.sleep(0.02)
        return rt.get(j["id"], events=60)


class CatalogTests(LibCase):
    def test_roles_are_valid_and_reference_real_skills_and_plugins(self):
        self.assertGreaterEqual((len(library.ROLES), len(library.SKILLS), len(library.PLUGINS)), (14, 14, 10))
        for rid, r in library.ROLES.items():
            agents.validate({**library.apply_role({}, rid), "provider": {"type": "mock"}})
            self.assertTrue(set(r["skills"]) <= set(library.SKILLS) and set(r["plugins"]) <= set(library.PLUGINS), rid)
        self.assertEqual(library.ROLES["reviewer"]["authorizations"], {"write": "deny"})  # reviewers cannot edit
        self.assertEqual(library.ROLES["security"]["authorizations"]["network"], "deny")
        c = library.catalog()
        self.assertEqual({"roles", "skills", "plugins"}, set(c))
        self.assertIn("manager", [r["id"] for r in c["roles"]])
        for p in library.PLUGINS.values():  # every shipped template passes its own safety validation
            library.validate_plugin({**p, "tool": p["tool"] + "_x"})

    def test_role_preset_fills_gaps_but_explicit_choices_win_and_none_is_fine(self):
        merged = library.apply_role({"name": "Ada", "mode": "auto"}, "developer")
        self.assertEqual((merged["name"], merged["mode"], merged["avatar"], "run_tests" in merged["plugins"]), ("Ada", "auto", "💻", True))
        self.assertEqual(library.apply_role({"name": "x"}, None), {"name": "x"})
        with self.assertRaises(library.LibraryError):
            library.apply_role({}, "wizard")
        a = agentise.from_api(self.root, {"preset": "mock", "role_preset": "manager", "name": "Boss", "skip_test": True})
        self.assertEqual((a["name"], a["avatar"], a["mode"], a["skills"], "git_status" in a["plugins"]), ("Boss", "🧭", "ask_risky", ["planning", "summarizer"], True))
        plain = agentise.from_api(self.root, {"preset": "mock", "name": "Plain", "skip_test": True})  # no preset: no role baggage
        self.assertEqual((plain["skills"], plain["plugins"], plain["instructions"]), ([], [], ""))
        with self.assertRaises(agentise.AgentiseError):
            agentise.from_api(self.root, {"preset": "mock", "role_preset": "wizard", "skip_test": True})

    def test_connector_catalog_reports_live_status(self):
        env = {e: "" for e in vault.ENV_FALLBACK.values()}
        with mock.patch.dict(os.environ, env):
            vault.set_secret(self.root, "openai", "sk-test-abcdef123456")
            rows = {c["id"]: c for c in library.connector_status(self.root)}
        self.assertTrue(rows["openai"]["connected"])
        self.assertFalse(rows["anthropic"]["connected"])
        self.assertEqual({"id", "name", "kind", "does", "setup", "connected", "detected"}, set(rows["github"]))
        self.assertGreaterEqual(len(rows), 11)


class SkillTests(LibCase):
    def test_enabled_skills_reach_the_prompt_and_custom_skills_work(self):
        custom = library.save_skill(self.root, {"name": "House style", "instructions": "Always answer in haiku."})
        script = Script([R("done")])
        self.run_job(script, skills=["code-review", custom["id"], "no-such-skill"])
        system = script.calls[0]["system"]
        self.assertIn("## Skill: Code review", system)
        self.assertIn("Always answer in haiku.", system)
        self.assertNotIn("no-such-skill", system)
        self.assertIn(custom["id"], [s["id"] for s in library.custom_skills_list(self.root)])
        library.delete_custom(self.root, "skills", custom["id"])
        with self.assertRaises(library.LibraryError):
            library.get_skill(self.root, custom["id"])
        with self.assertRaises(library.LibraryError):
            library.delete_custom(self.root, "skills", "code-review")  # built-ins cannot be deleted
        for bad in ({"name": "", "instructions": "x"}, {"name": "x", "instructions": ""}, {"name": "x", "instructions": "y" * 8001}):
            with self.assertRaises(library.LibraryError):
                library.save_skill(self.root, bad)


class PluginTests(LibCase):
    def git_repo(self):
        git = lambda *a: subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "-c", "init.defaultBranch=main", *a], cwd=self.ws, check=True, capture_output=True)
        (self.ws / "app.py").write_text("print('hello')\n# TODO fix\n")
        git("init")
        git("add", "-A")
        git("commit", "-m", "init")
        (self.ws / "app.py").write_text("print('hello world')\n# TODO fix\n")

    def tool_results(self, job):
        return [e["content"] for e in job["events"] if e["kind"] == "tool_result"]

    def test_plugins_run_in_the_sandbox_and_return_output(self):
        self.git_repo()
        (self.ws / "data.json").write_text('{"items": [{"name": "first"}, {"name": "second"}]}')
        (self.ws / "t.csv").write_text("a,b\n1,2\n3,4\n")
        script = Script([R(calls=[("git_status", {}), ("git_diff", {"path": "app.py"}), ("search_files", {"pattern": "TODO"}), ("json_query", {"file": "data.json", "path": "items.1.name"}),
                                   ("csv_summary", {"file": "t.csv"}), ("project_tree", {}), ("run_python", {"file": "app.py"})]), R("done")])
        j = self.run_job(script, plugins=["git_status", "git_diff", "grep_files", "json_query", "csv_summary", "tree", "run_python_file"])
        out = self.tool_results(j)
        self.assertEqual(j["status"], "succeeded")
        self.assertIn("## main", out[0])
        self.assertIn("-print('hello')", out[1])
        self.assertIn("app.py:2:# TODO fix", out[2])
        self.assertIn('"second"', out[3])
        self.assertIn("rows: 2", out[4])
        self.assertIn("./app.py", out[5])
        self.assertIn("hello world", out[6])
        names = [t for t in script.calls[0]["tools"]]
        self.assertTrue({"git_status", "search_files", "run_python"} <= set(names))
        self.assertEqual(j["metrics"]["tool_errors"], 0)

    def test_parameters_cannot_inject_commands_or_escape_the_project(self):
        marker = self.ws.parent / "pwned"
        script = Script([R(calls=[("search_files", {"pattern": f"x'; touch {marker}; echo '"}), ("search_files", {"pattern": f"$(touch {marker})"}), ("search_files", {"pattern": "a", "path": "../.."}),
                                   ("search_files", {"pattern": "a", "path": "/etc"}), ("json_query", {"file": "../project.json", "path": ""})]), R("done")])
        j = self.run_job(script, plugins=["grep_files", "json_query"])
        out = self.tool_results(j)
        self.assertFalse(marker.exists())
        self.assertTrue(all("ERROR" in o for o in out[2:]), out)
        self.assertTrue(out[0].startswith("exit") and out[1].startswith("exit"))  # the text was searched for, literally

    def test_plugin_categories_follow_the_control_mode(self):
        script = Script([R(calls=[("run_tests", {})]), R("done")])
        j = self.run_job(script, mode="ask_risky", plugins=["run_tests"])  # 'run' asks in this mode; nobody answers -> denied
        self.assertEqual((j["metrics"]["approvals_asked"], j["metrics"]["approvals_denied"]), (1, 1))
        script = Script([R(calls=[("git_status", {})]), R("done")])
        j = self.run_job(script, mode="ask_risky", plugins=["git_status"])  # 'read' is allowed in this mode
        self.assertEqual(j["metrics"]["approvals_asked"], 0)
        script = Script([R(calls=[("project_tree", {})]), R("done")])
        j = self.run_job(script, mode="auto", authorizations={"read": "deny"}, plugins=["tree"])
        self.assertIn("not permitted", self.tool_results(j)[0])

    def test_tools_are_only_offered_when_enabled(self):
        script = Script([R("hi")])
        self.run_job(script, plugins=["git_status"])
        self.assertIn("git_status", script.calls[0]["tools"])
        self.assertNotIn("search_files", script.calls[0]["tools"])
        script = Script([R(calls=[("git_status", {})]), R("done")])
        j = self.run_job(script, plugins=[])
        self.assertIn("unknown tool", self.tool_results(j)[0])

    def test_custom_plugins_are_validated_then_usable(self):
        rec = library.save_plugin(self.root, {"name": "Word count", "tool": "word_count", "category": "read", "description": "Count words in a file",
                                              "params": {"file": {"type": "string", "kind": "path"}}, "command": "wc -w {file}"})
        (self.ws / "n.txt").write_text("one two three")
        j = self.run_job(Script([R(calls=[("word_count", {"file": "n.txt"})]), R("done")]), plugins=[rec["id"]])
        self.assertIn("3 n.txt", self.tool_results(j)[0])
        for bad in ({"tool": "read_file"}, {"tool": "Bad Name"}, {"tool": "word_count"}, {"category": "root"}, {"command": "wc -w {nothing}"}, {"command": ""}, {"command": "rm -rf ~"},
                    {"params": {"f": {"type": "object"}}}, {"tool": "git_status"}):
            spec = {"tool": "other_tool", "category": "read", "params": {"file": {"type": "string"}}, "command": "wc -w {file}", **bad}
            with self.assertRaises(library.LibraryError, msg=bad):
                library.save_plugin(self.root, spec)
        self.assertIn(rec["id"], [p["id"] for p in library.catalog()["plugins"] if not p["builtin"]] + [p["id"] for p in library.custom_plugins_list(self.root)])
        library.delete_custom(self.root, "plugins", rec["id"])
        self.assertEqual(library.custom_plugins_list(self.root), [])

    def test_offline_projects_refuse_network_plugins(self):
        from agentlab import project_env
        project_env.save(self.root, self.proj, {"network": "off"})
        j = self.run_job(Script([R(calls=[("http_headers", {"url": "http://example.com"})]), R("done")]), mode="auto", plugins=["http_headers"])
        self.assertIn("offline", self.tool_results(j)[0])


if __name__ == "__main__":
    unittest.main()
