import tempfile
import unittest

from agentlab import agents, llm, model_tests, models, playground, projects, widgets


def reply(text, calls=None):
    return {"text": text, "tool_calls": calls or [], "usage": {"input": 10, "output": 5}, "stop": "end", "model": "m"}


class WidgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_validate_chart_and_errors(self):
        w = widgets.validate({"type": "chart", "labels": ["a", "b"], "series": [{"name": "s", "data": [1, "2"]}], "evil": 1})
        self.assertEqual(w["series"][0]["data"], [1.0, 2.0])
        self.assertNotIn("evil", w)
        for bad in ({"type": "nope"}, {"type": "chart", "labels": ["a"], "series": [{"data": [1, 2]}]},
                    {"type": "chart", "labels": ["a"], "series": [{"data": [float("nan")]}]}, {"type": "html", "html": " "},
                    {"type": "form", "fields": [{"name": "a"}], "submit": {"template": "{zzz}"}}):
            with self.assertRaises(widgets.WidgetError):
                widgets.validate(bad)

    def test_extract_keeps_valid_and_reports_invalid(self):
        text = 'Hi\n```widget\n{"type":"table","columns":["a"],"rows":[["1"]]}\n```\n```widget\n{bad\n```\n```widget-html\n<b>x</b>\n```\nbye'
        clean, found, errors = widgets.extract(text)
        self.assertEqual([w["type"] for w in found], ["table", "html"])
        self.assertEqual(len(errors), 1)
        self.assertNotIn("```", clean)

    def test_offline_templates_all_valid(self):
        for k, (title, words, html) in widgets.TEMPLATES.items():
            self.assertTrue(widgets.generate_offline(f"make a {words[0]}"), k)
        self.assertIsNone(widgets.generate_offline("tell me a story"))

    def test_store_and_export(self):
        rec = widgets.save(self.root, {"type": "markdown", "title": "Notes <b>", "text": "hi"})
        self.assertEqual(widgets.get(self.root, rec["id"])["title"], "Notes <b>")
        self.assertEqual(len(widgets.list_widgets(self.root)), 1)
        with self.assertRaises(widgets.WidgetError):
            widgets.get(self.root, "../etc")
        pid = projects.create(self.root, "P")["id"]
        widgets.export_to_project(self.root, rec["id"], pid)
        html = widgets.to_html(rec["spec"])
        self.assertNotIn("<b>", html.split("<h3>")[1].split("</h3>")[0])
        widgets.delete(self.root, rec["id"])
        self.assertEqual(widgets.list_widgets(self.root), [])


class PlaygroundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_offline_demo_builds_widget(self):
        r = playground.chat(self.root, {"messages": [{"role": "user", "content": "make me a calculator"}]})
        self.assertEqual(r["widgets"][0]["type"], "html")
        self.assertIn("input_tokens", r["insights"])

    def test_chat_with_attachment_and_widget_extraction(self):
        seen = {}

        def fake(provider, system, msgs, tools, **kw):
            seen.update(system=system, last=msgs[-1]["content"])
            return reply('ok\n```widget\n{"type":"stats","items":[{"label":"a","value":"1"}]}\n```')
        p = llm.PRESETS["mock"]
        r = playground.chat(self.root, {"provider": {**p, "script": [1]}, "messages": [{"role": "user", "content": "hi"}], "attachments": [{"name": "a.txt", "content": "DATA"}]}, fake)
        self.assertIn("DATA", seen["last"])
        self.assertIn("widget", seen["system"])
        self.assertEqual(r["widgets"][0]["type"], "stats")
        self.assertEqual(r["text"], "ok")

    def test_validation(self):
        for spec in ({"messages": []}, {"messages": [{"role": "assistant", "content": "x"}]},
                     {"messages": [{"role": "user", "content": "x"}], "attachments": [{"name": "b", "content": 5}]}):
            with self.assertRaises(playground.PlaygroundError):
                playground.chat(self.root, spec)

    def test_compare_reports_per_target_errors(self):
        def fake(provider, system, msgs, tools, **kw):
            if provider["model"] == "bad":
                raise llm.LLMError("boom")
            return reply("fine")
        p = {**llm.PRESETS["mock"], "script": [1]}
        r = playground.compare(self.root, {"messages": [{"role": "user", "content": "hi"}], "targets": [{"provider": {**p, "model": "good"}, "label": "g"}, {"provider": {**p, "model": "bad"}, "label": "b"}]}, fake)
        self.assertEqual([x["ok"] for x in r["results"]], [True, False])
        self.assertEqual(r["fastest"], "g")
        self.assertIsNone(r["results"][0]["agreement"])  # needs two successful answers
        r = playground.compare(self.root, {"messages": [{"role": "user", "content": "hi"}], "targets": [{"provider": {**p, "model": "a"}}, {"provider": {**p, "model": "b"}}]}, fake)
        self.assertEqual(r["results"][0]["agreement"], 1.0)


class ModelTestsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_battery_unreachable_model(self):
        def fake(*a, **k):
            raise llm.LLMError("down")
        r = model_tests.run_battery(self.root, {**llm.PRESETS["mock"], "script": [1]}, fake)
        self.assertEqual(r["recommendation"]["tool_mode"], "none")
        self.assertEqual(r["tests"][0]["status"], "error")
        self.assertTrue(all(t["status"] == "skip" for t in r["tests"][1:]))
        self.assertIn(r["key"], model_tests.last_reports(self.root))

    def test_battery_text_only_model_and_models_overview(self):
        def fake(provider, system, msgs, tools, **kw):
            return reply("42")
        p = {**llm.PRESETS["mock"], "script": [1]}
        r = model_tests.run_battery(self.root, p, fake)
        self.assertEqual(r["tests"][0]["status"], "pass")
        self.assertEqual(r["tests"][4]["status"], "fail")
        agents.create(self.root, {"name": "A", "provider": p})
        ov = models.overview(self.root)
        self.assertTrue(ov["models"])


if __name__ == "__main__":
    unittest.main()
