import tempfile
import unittest

from agentlab import agents, llm, rooms, usage


class Router:
    """Each agent (by model name) answers from its own script."""

    def __init__(self, scripts):
        self.scripts, self.calls = scripts, []

    def __call__(self, provider, system, msgs, tools, **kw):
        self.calls.append((provider["model"], system, msgs))
        text = self.scripts[provider["model"]].pop(0)
        return {"text": text, "tool_calls": [], "usage": {"input": 20, "output": 10}, "stop": "end", "model": provider["model"]}


class RoomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        mk = lambda n, role="": agents.create(self.root, {"name": n, "role": role, "provider": {"type": "mock", "model": n.lower()}})["id"]
        self.ana, self.bob, self.cy = mk("Ana", "manager"), mk("Bob", "developer"), mk("Cy")

    def test_validation(self):
        for spec in ({"agents": [self.ana]}, {"agents": [self.ana, self.ana]}, {"agents": [self.ana, "nope"]},
                     {"agents": [self.ana, self.bob], "mode": "x"}, {"agents": [self.ana, self.bob], "manager": self.cy}):
            with self.assertRaises(Exception):
                rooms.create(self.root, spec)

    def test_rotation_mentions_and_transcript(self):
        room = rooms.create(self.root, {"name": "R", "agents": [self.ana, self.bob, self.cy]})
        rooms.post(self.root, room["id"], "Hello team, what should we build?")
        r = Router({"ana": ["Let's ask @Cy", "ok"], "bob": ["I agree"], "cy": ["A game!"]})
        speakers = [rooms.step(self.root, room["id"], r)["name"] for _ in range(4)]
        self.assertEqual(speakers, ["Ana", "Cy", "Ana", "Bob"])
        # Cy saw Ana's message labelled with her name, as a user turn
        cy_msgs = next(c[2] for c in r.calls if c[0] == "cy")
        self.assertIn("[Ana]: Let's ask @Cy", cy_msgs[-1]["content"])
        self.assertIn("Bob (developer)", next(c[1] for c in r.calls if c[0] == "cy"))
        got = rooms.get(self.root, room["id"])
        self.assertEqual(got["metrics"]["turns"], 4)
        self.assertEqual({k["key"]: k["calls"] for k in usage.summary(self.root, 1)["by_kind"]}["room"], 4)

    def test_task_mode_manager_first_and_done(self):
        room = rooms.create(self.root, {"agents": [self.bob, self.ana], "mode": "task", "manager": self.ana, "goal": "Name a product"})
        r = Router({"ana": ["Plan: Bob proposes.", "Final: Zapp\nDONE"], "bob": ["Zapp?"]})
        rooms.run(self.root, room["id"], chat_fn=r, background=False)
        got = rooms.get(self.root, room["id"])
        self.assertEqual([m["name"] for m in got["messages"]], ["Ana", "Bob", "Ana"])
        self.assertEqual(got["status"], "done")
        self.assertEqual(got["result"]["text"], "Final: Zapp")
        self.assertIn("You are the manager", r.calls[0][1])
        self.assertIsNone(rooms.step(self.root, room["id"], r))
        rooms.post(self.root, room["id"], "Another one?")
        self.assertEqual(rooms.get(self.root, room["id"])["status"], "idle")

    def test_errors_are_posted_and_listing(self):
        room = rooms.create(self.root, {"agents": [self.ana, self.bob], "max_turns": 3})

        def boom(*a, **k):
            raise llm.LLMError("offline")
        rooms.run(self.root, room["id"], chat_fn=boom, background=False)
        got = rooms.get(self.root, room["id"])
        self.assertIn("offline", got["messages"][-1]["text"])
        self.assertEqual(rooms.list_rooms(self.root)[0]["id"], room["id"])
        rooms.delete(self.root, room["id"])
        self.assertEqual(rooms.list_rooms(self.root), [])


if __name__ == "__main__":
    unittest.main()
