import re
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "agentlab"
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2190-\u21FF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")


class NoEmojiTests(unittest.TestCase):
    def test_interface_uses_svg_not_emoji(self):
        """The UI must stay emoji-free: icons are inline SVG."""
        for rel in ("ui/app.js", "ui/lab.js", "ui/index.html", "ui/style.css", "library.py", "agents.py", "templates/catalog.json"):
            text = (UI / rel).read_text(encoding="utf-8")
            found = sorted(set(EMOJI.findall(text)))
            self.assertEqual(found, [], f"{rel} still contains emoji: {found}")

    def test_every_role_avatar_is_a_known_icon(self):
        from agentlab import library
        names = set(re.findall(r"^\s{2}(\w+):", (UI / "ui/lab.js").read_text(encoding="utf-8").split("ICON_PATHS = {")[1].split("};")[0], re.M))
        for role in library.catalog()["roles"]:
            self.assertIn(role["avatar"], names, f"role {role['id']} uses an icon the UI does not have")


if __name__ == "__main__":
    unittest.main()
