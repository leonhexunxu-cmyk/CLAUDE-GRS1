import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentlab import envs, projects
from agentlab.projects import ProjectError

ISOLATION = envs.capabilities()["network_isolation"]


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.p = projects.create(self.root, "My Site!", "demo", quota_mb=1, kind="website")["id"]


class FileApiTests(ProjectCase):
    def test_create_list_update_delete(self):
        self.assertRegex(self.p, r"^my-site-[a-f0-9]{4}$")
        self.assertEqual(projects.get(self.root, self.p)["quota_mb"], 1)
        self.assertEqual(projects.list_projects(self.root)[0]["free_bytes"], 1_000_000)
        projects.update(self.root, self.p, name="Renamed", quota_mb=2, agent="a1")
        self.assertEqual((projects.get(self.root, self.p)["name"], projects.get(self.root, self.p)["agent"]), ("Renamed", "a1"))
        projects.delete(self.root, self.p)
        with self.assertRaises(ProjectError):
            projects.get(self.root, self.p)
        for bad in ({"name": ""}, {"name": "x", "kind": "nope"}, {"name": "x", "quota_mb": 0}, {"name": "x", "quota_mb": "lots"}, {"name": "x", "quota_mb": 10**9}):
            with self.assertRaises(ProjectError, msg=bad):
                projects.create(self.root, **bad)
        with self.assertRaises(ProjectError):
            projects.get(self.root, "../../etc")

    def test_read_write_tree_append_delete(self):
        projects.write_file(self.root, self.p, "src/app.js", "console.log(1)\n")
        projects.write_file(self.root, self.p, "src/app.js", "more\n", append=True)
        self.assertEqual(projects.read_file(self.root, self.p, "src/app.js")["content"], "console.log(1)\nmore\n")
        self.assertEqual(projects.tree(self.root, self.p), ["src/app.js"])
        self.assertEqual(projects.list_dir(self.root, self.p, "src"), [{"name": "app.js", "dir": False, "size": 20}])
        self.assertEqual(projects.usage(self.root, self.p)["files"], 1)
        (Path(projects.workspace(self.root, self.p)) / "bin.dat").write_bytes(b"\x00\x01\x02")
        self.assertTrue(projects.read_file(self.root, self.p, "bin.dat")["binary"])
        projects.delete_path(self.root, self.p, "src")
        self.assertEqual(projects.tree(self.root, self.p), ["bin.dat"])
        with self.assertRaises(ProjectError):
            projects.delete_path(self.root, self.p, "")

    def test_paths_cannot_escape(self):
        ws = projects.workspace(self.root, self.p)
        secret = Path(self.root) / "outside.txt"
        secret.write_text("TOP SECRET")
        os.symlink(secret, ws / "link.txt")
        os.symlink(self.root, ws / "dirlink")
        for bad in ("../outside.txt", "a/../../outside.txt", "/etc/passwd", "~/x", "link.txt", "dirlink/outside.txt", "x\x00y"):
            for fn in (lambda r: projects.read_file(self.root, self.p, r), lambda r: projects.write_file(self.root, self.p, r, "pwn"),
                       lambda r: projects.delete_path(self.root, self.p, r)):
                with self.assertRaises(ProjectError, msg=bad):
                    fn(bad)
        self.assertEqual(secret.read_text(), "TOP SECRET")
        self.assertEqual(projects.list_dir(self.root, self.p), [])  # symlinks are invisible
        self.assertEqual(projects.usage(self.root, self.p)["files"], 0)

    def test_quota_is_enforced_on_write_and_overwrite_frees_space(self):
        projects.write_file(self.root, self.p, "big.txt", "x" * 900_000)
        with self.assertRaises(ProjectError) as cm:
            projects.write_file(self.root, self.p, "other.txt", "y" * 200_000)
        self.assertEqual(cm.exception.code, 413)
        projects.write_file(self.root, self.p, "big.txt", "x" * 100)  # shrinking frees quota
        projects.write_file(self.root, self.p, "other.txt", "y" * 200_000)
        with self.assertRaises(ProjectError):
            projects.write_file(self.root, self.p, "huge.txt", "z" * 6_000_000)


class RunCommandTests(ProjectCase):
    def run_cmd(self, command, **kw):
        ws = projects.workspace(self.root, self.p)
        return envs.run_command(ws, command, home=projects.home(self.root, self.p), **{"network": True, "timeout": 20, **kw})

    def test_runs_in_workspace_with_scrubbed_env(self):
        with mock.patch.dict(os.environ, {"MY_SECRET_KEY": "s3"}):
            r = self.run_cmd('pwd; echo "[$MY_SECRET_KEY]"; echo hi > out.txt; echo $HOME')
        lines = r["output"].split()
        self.assertEqual(r["exit_code"], 0)
        self.assertTrue(lines[0].endswith("/workspace"))
        self.assertIn("[]", lines)
        self.assertEqual(projects.read_file(self.root, self.p, "out.txt")["content"], "hi\n")
        self.assertTrue(lines[-1].endswith("/home"))
        self.assertEqual(self.run_cmd('echo "$K"', env={"K": "passed"})["output"].strip(), "passed")

    def test_timeout_truncation_and_exit_codes(self):
        r = self.run_cmd("sleep 30", timeout=1)
        self.assertTrue(r["timed_out"])
        self.assertLess(r["duration"], 10)
        r = self.run_cmd("yes | head -c 500000; exit 4", max_output=1000)
        self.assertEqual((r["exit_code"], r["truncated"], len(r["output"])), (4, True, 1000))

    def test_file_size_cap_follows_free_space(self):
        r = self.run_cmd("head -c 3000000 /dev/zero > big.bin; echo done", free_bytes=500_000)
        self.assertLessEqual((Path(projects.workspace(self.root, self.p)) / "big.bin").stat().st_size, 500_000)

    @unittest.skipUnless(ISOLATION, "network isolation unavailable here")
    def test_network_blocked_by_default(self):
        r = self.run_cmd("python3 -c \"import socket;socket.create_connection(('127.0.0.1',9),timeout=1)\"", network=False)
        self.assertIn("unreachable", r["output"].lower())
        with mock.patch.dict(envs._caps, {"unshare": None}):
            with self.assertRaises(envs.TaskError):
                self.run_cmd("true", network=False)


if __name__ == "__main__":
    unittest.main()
