import tempfile
import time
import unittest

from agentlab import project_env, projects, terminal


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.pid = projects.create(self.root, "T")["id"]
        projects.write_file(self.root, self.pid, "hello.txt", "hi")
        self.addCleanup(terminal.close_all)

    def read_until(self, sid, needle, offset=0, timeout=10):
        out, end = "", time.time() + timeout
        while needle not in terminal.strip_ansi(out) and time.time() < end:
            r = terminal.read(sid, offset, 1)
            out += r["data"]
            offset = r["offset"]
        self.assertIn(needle, terminal.strip_ansi(out))
        return offset

    def test_shell_runs_in_workspace_with_project_env(self):
        project_env.save(self.root, self.pid, {"vars": {"GREETING": "bonjour"}})
        s = terminal.open_session(self.root, self.pid)
        off = terminal.read(s["id"], 0, 2)["offset"]
        terminal.write(s["id"], "ls; echo \"v=$GREETING\"; pwd\n")
        off = self.read_until(s["id"], "v=bonjour", off)
        self.read_until(s["id"], "hello.txt", 0)
        self.read_until(s["id"], str(projects.workspace(self.root, self.pid).resolve().name), 0)
        terminal.resize(s["id"], 120, 40)
        terminal.write(s["id"], "stty size\n")
        self.read_until(s["id"], "40 120", off)
        self.assertEqual(len(terminal.list_sessions()), 1)

    def test_flags_destructive_lines_and_closes(self):
        s = terminal.open_session(self.root, self.pid)
        r = terminal.write(s["id"], "echo rm -rf / --no-preserve-root")  # not yet entered
        self.assertIsNone(r["warning"])
        self.assertIsNone(terminal.write(s["id"], "\x03")["warning"])  # Ctrl-C discards the line
        self.assertTrue(terminal.write(s["id"], "true || rm -rf /x\x7f\n")["warning"])  # backspace is applied: rm -rf /
        terminal.write(s["id"], "exit\n")
        end = time.time() + 5
        while terminal.read(s["id"], 0, 0.5)["alive"] and time.time() < end:
            pass
        self.assertFalse(terminal.read(s["id"], 0, 0)["alive"])
        terminal.close(s["id"])
        with self.assertRaises(terminal.TerminalError):
            terminal.read(s["id"])

    def test_strip_ansi(self):
        self.assertEqual(terminal.strip_ansi("\x1b[1;34mdir\x1b[0m $ \x1b]0;title\x07ok"), "dir $ ok")


if __name__ == "__main__":
    unittest.main()
