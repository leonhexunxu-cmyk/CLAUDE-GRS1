import tempfile
import time
import unittest
from unittest import mock

from agentlab import agents, commands, jobs, project_env, projects, vault

try:
    from tests.test_jobs import R, Script
except ImportError:
    from test_jobs import R, Script


class EnvControlCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.proj = projects.create(self.root, "Env")["id"]

    def run_job(self, calls, **kw):
        aid = agents.create(self.root, {"name": "Ops", "mode": "auto", "environment_control": True, "provider": {"type": "mock"}, **kw})["id"]
        rt = jobs.Runtime(self.root, approval_timeout=0.4)
        with mock.patch("agentlab.jobs.llm.chat", Script([R(calls=calls), R("done")])):
            j = rt.start(self.proj, aid, "set things up")
            end = time.time() + 30
            while rt.get(j["id"])["status"] not in jobs.FINAL and time.time() < end:
                time.sleep(0.02)
        j = rt.get(j["id"], events=60)
        return j, [e["content"] for e in j["events"] if e["kind"] == "tool_result"], [e["content"] for e in j["events"] if e["kind"] == "decision"]


class AgentEnvironmentTests(EnvControlCase):
    def test_tools_are_opt_in(self):
        a = agents.create(self.root, {"name": "Plain", "provider": {"type": "mock"}})
        self.assertFalse(a["environment_control"])
        self.assertNotIn("environment_info", jobs.tool_names(a))
        b = agents.create(self.root, {"name": "Ops", "environment_control": True, "provider": {"type": "mock"}})
        self.assertTrue({"environment_info", "environment_set_var", "environment_install", "environment_network"} <= jobs.tool_names(b))

    def test_variables_can_be_managed_but_secrets_and_reserved_names_cannot(self):
        j, out, _ = self.run_job([("environment_set_var", {"name": "MODE", "value": "dev"}), ("environment_set_var", {"name": "PATH", "value": "/evil"}),
                                  ("environment_set_var", {"name": "API_KEY", "value": "sk-abcdefghijklmnop1234"}), ("environment_info", {}),
                                  ("environment_remove_var", {"name": "MODE"}), ("environment_info", {})])
        self.assertEqual(out[0], "set MODE")
        self.assertTrue(out[1].startswith("ERROR") and "reserved" in out[1])
        self.assertTrue(out[2].startswith("ERROR") and "looks like a secret" in out[2])
        self.assertIn('"MODE": "dev"', out[3])
        self.assertEqual(out[4], "removed MODE")
        self.assertNotIn('"MODE"', out[5])
        self.assertEqual(project_env.get(self.root, self.proj)["vars"], {})

    def test_permissions_follow_the_control_mode_and_environment_category(self):
        j, out, dec = self.run_job([("environment_set_var", {"name": "A", "value": "1"})], mode="ask_risky")  # 'environment' asks in this mode
        self.assertEqual((j["metrics"]["approvals_asked"], j["metrics"]["approvals_denied"]), (1, 1))
        self.assertEqual(project_env.get(self.root, self.proj)["vars"], {})
        j, out, _ = self.run_job([("environment_info", {})], mode="ask_risky")  # reading is fine
        self.assertEqual(j["metrics"]["approvals_asked"], 0)
        j, out, _ = self.run_job([("environment_set_var", {"name": "A", "value": "1"})], mode="auto", authorizations={"environment": "deny"})
        self.assertIn("not permitted", out[0])
        j, out, _ = self.run_job([("environment_set_var", {"name": "B", "value": "2"})], mode="ask_risky", authorizations={"environment": "allow"})
        self.assertEqual(out[0], "set B")

    def test_turning_the_network_on_always_asks_even_in_full_auto(self):
        j, out, dec = self.run_job([("environment_network", {"mode": "off"}), ("environment_network", {"mode": "allowed"})])
        self.assertEqual(out[0], "network is now off")  # tightening is free
        self.assertEqual((j["metrics"]["approvals_asked"], j["metrics"]["approvals_denied"]), (1, 1))  # loosening needed approval; nobody gave it
        self.assertIn("turns network access on", dec[0])
        self.assertEqual(project_env.get(self.root, self.proj)["network"], "off")

    def test_install_needs_environment_and_network_permission_and_valid_specs(self):
        j, out, _ = self.run_job([("environment_install", {"packages": ["requests"]})], mode="auto", authorizations={"network": "deny"})
        self.assertIn("not permitted", out[0])
        j, out, _ = self.run_job([("environment_install", {"packages": ["requests; rm -rf /"]})])
        self.assertTrue(out[0].startswith("ERROR") and "invalid package" in out[0])
        j, out, _ = self.run_job([("environment_install", {"packages": []}), ("environment_network", {"mode": "off"}), ("environment_install", {"packages": ["six"]})])
        self.assertIn("non-empty", out[0])
        self.assertIn("offline", out[2])  # offline projects cannot install until a human lets them online
        self.assertEqual(project_env.get(self.root, self.proj)["python"]["packages"], [])

    def test_a_successful_install_creates_the_venv(self):
        j, out, _ = self.run_job([("environment_install", {"packages": []})] if False else [("environment_install", {"packages": ["pip"]})], mode="auto")
        self.assertTrue(out[0].startswith(("succeeded", "failed")))  # real pip needs the network: either outcome is reported, never a crash
        self.assertEqual(project_env.get(self.root, self.proj)["python"]["packages"], ["pip"])
        self.assertTrue(project_env.get(self.root, self.proj)["python"]["enabled"])

    def test_agents_cannot_share_keys_with_commands(self):
        vault.set_secret(self.root, "openai", "sk-openai-abcdefghij123456")
        j, out, _ = self.run_job([("environment_set_var", {"name": "SECRETS", "value": "openai"}), ("environment_info", {})])
        self.assertEqual(project_env.get(self.root, self.proj)["secrets"], [])  # only a human can opt a key in


class CommandTests(EnvControlCase):
    def test_builtins_and_custom_prompt_commands(self):
        names = [c["name"] for c in commands.list_commands(self.root)]
        self.assertTrue({"review", "explain", "summarize", "fix", "test", "plan", "docs", "translate", "commit"} <= set(names))
        self.assertEqual(commands.expand(self.root, "/explain the retry logic")["text"], "Explain the retry logic in simple terms, with one short example.")
        self.assertEqual(commands.expand(self.root, '/translate French "Hello world"')["text"], "Translate the following into French, keeping formatting and placeholders: Hello world")
        with self.assertRaises(commands.CommandError) as cm:
            commands.expand(self.root, "/translate")
        self.assertIn("<lang>", str(cm.exception))
        c = commands.save(self.root, {"name": "standup", "template": "Write a standup for {person}: {args}", "params": ["person"], "description": "Daily standup"})
        self.assertEqual(commands.expand(self.root, "/standup Ada did the thing")["text"], "Write a standup for Ada: did the thing")
        for bad in ({"name": "Bad Name"}, {"name": "review"}, {"name": "standup"}, {"name": "x"}, {"template": ""}, {"kind": "python"}, {"template": "Hi {who}"}, {"params": ["args"]}, {"params": list("abcdefghi")}):
            with self.assertRaises(commands.CommandError, msg=bad):
                commands.save(self.root, {"name": "newone", "template": "x", **bad})
        commands.delete(self.root, "standup")
        with self.assertRaises(commands.CommandError):
            commands.get(self.root, "standup")
        with self.assertRaises(commands.CommandError):
            commands.delete(self.root, "review")
        for bad_line in ("review", "/", "/nope"):
            with self.assertRaises(commands.CommandError):
                commands.expand(self.root, bad_line)

    def test_shell_commands_run_in_the_sandbox_with_quoted_parameters(self):
        commands.save(self.root, {"name": "count", "kind": "shell", "template": "wc -c < {file}", "params": ["file"]})
        projects.write_file(self.root, self.proj, "a.txt", "12345")
        r = commands.run_shell(self.root, self.proj, "/count a.txt")
        self.assertEqual((r["exit_code"], r["output"].strip()), (0, "5"))
        marker = projects.workspace(self.root, self.proj).parent / "pwned"
        r = commands.run_shell(self.root, self.proj, f'/count "a.txt; touch {marker}"')
        self.assertFalse(marker.exists())  # the argument is one quoted word, never shell syntax
        self.assertNotEqual(r["exit_code"], 0)
        with self.assertRaises(commands.CommandError):
            commands.save(self.root, {"name": "wipe", "kind": "shell", "template": "rm -rf ~"})
        commands.save(self.root, {"name": "ping", "kind": "shell", "template": "echo online", "network": True})
        project_env.save(self.root, self.proj, {"network": "off"})
        with self.assertRaises(commands.CommandError) as cm:
            commands.run_shell(self.root, self.proj, "/ping")
        self.assertIn("offline", str(cm.exception))
        with self.assertRaises(commands.CommandError):
            commands.run_shell(self.root, self.proj, "/explain x")  # a prompt command is not runnable as shell


if __name__ == "__main__":
    unittest.main()
