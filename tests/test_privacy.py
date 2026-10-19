import os
import tempfile
import unittest
from unittest import mock

from agentlab import agents, privacy


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.a = agents.create(self.root, {"name": "Secret", "provider": {"type": "mock"}})

    def test_lock_unlock_cycle(self):
        aid = self.a["id"]
        self.assertTrue(privacy.unlocked(self.root, aid, []))
        with self.assertRaises(privacy.PrivacyError):
            privacy.set_password(self.root, aid, "short")
        privacy.set_password(self.root, aid, "correct horse")
        raw = open(os.path.join(self.root, ".agentlab", "private.json")).read()
        self.assertNotIn("correct horse", raw)
        self.assertFalse(privacy.unlocked(self.root, aid, []))
        with self.assertRaises(privacy.PrivacyError):
            privacy.unlock(self.root, aid, "wrong")
        tok = privacy.unlock(self.root, aid, "correct horse")["token"]
        self.assertTrue(privacy.unlocked(self.root, aid, [tok]))
        privacy.require(self.root, aid, [tok])
        with self.assertRaises(privacy.PrivacyError):  # changing needs the current password
            privacy.set_password(self.root, aid, "new password", current="nope")
        privacy.set_password(self.root, aid, "new password", current="correct horse")
        self.assertFalse(privacy.unlocked(self.root, aid, [tok]))  # old tokens die
        privacy.remove_password(self.root, aid, "new password")
        self.assertFalse(privacy.is_private(self.root, aid))
        self.assertEqual(privacy.redact_agent(self.a)["provider"]["model"], "hidden")

    def test_tokens_expire_and_bruteforce_backoff(self):
        aid = self.a["id"]
        privacy.set_password(self.root, aid, "correct horse")
        tok = privacy.unlock(self.root, aid, "correct horse")["token"]
        with mock.patch("agentlab.privacy.time.time", return_value=__import__("time").time() + privacy.TTL + 1):
            self.assertFalse(privacy.unlocked(self.root, aid, [tok]))
        for _ in range(5):
            with self.assertRaises(privacy.PrivacyError):
                privacy.unlock(self.root, aid, "bad")
        with self.assertRaises(privacy.PrivacyError) as cm:
            privacy.unlock(self.root, aid, "correct horse")
        self.assertEqual(cm.exception.code, 429)


if __name__ == "__main__":
    unittest.main()
