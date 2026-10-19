import json
import tempfile
import unittest
import zipfile

from agentlab import builders, docbuilder, packager, projects


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.pid = projects.create(self.root, "Weather Tool")["id"]
        w = lambda p, c: projects.write_file(self.root, self.pid, p, c)
        w("weather.py", 'import json\nimport requests\nimport yaml\nfrom helpers import fmt\n\n\ndef forecast(city, days=3):\n    """Get the forecast for a city."""\n    match city:\n        case _:\n            return fmt(requests.get(city))\n\n\nclass Client:\n    """A client."""\n\n\nif __name__ == "__main__":\n    print(forecast("Paris"))\n')
        w("helpers.py", "def fmt(x):\n    return str(x)\n")
        w("requirements.txt", "requests>=2\nflask\n")
        w(".env", "API_KEY=supersecret\n")
        w("debug.log", "noise")
        w("big.js", "var a = 1;\n" * 20000)


class DocTests(Case):
    def test_readme_api_and_html(self):
        out = docbuilder.build(self.root, self.pid, ["readme", "api", "guide", "changelog", "contributing"], description="Forecasts in your terminal.")
        readme = out["files"]["README.md"]
        self.assertIn("# Weather Tool", readme)
        self.assertIn("python3 weather.py", readme)
        self.assertIn("forecast(city, days)", out["files"]["docs/API.md"])
        self.assertIn("Get the forecast", out["files"]["docs/API.md"])
        html = projects.read_file(self.root, self.pid, "docs/html/readme.html")["content"]
        self.assertIn("<h1>Weather Tool</h1>", html)
        self.assertIn("<pre><code>", html)
        with self.assertRaises(docbuilder.DocError):
            docbuilder.build(self.root, self.pid, ["nope"])

    def test_api_from_openapi_and_markdown(self):
        spec = {"info": {"title": "Pets"}, "paths": {"/pets/{id}": {"get": {"summary": "Get a pet", "parameters": [{"name": "id", "in": "path", "required": True}]},
                                                     "post": {"summary": "Add", "requestBody": {"content": {"application/json": {"schema": {"properties": {"name": {"type": "string"}}, "required": ["name"]}}}}}}}}
        projects.write_file(self.root, self.pid, "openapi.json", json.dumps(spec))
        md = docbuilder.build(self.root, self.pid, ["api"], openapi_path="openapi.json")["files"]["docs/API.md"]
        self.assertIn("## `GET /pets/{id}`", md)
        self.assertIn("| `name` | string | yes |", md)
        self.assertIn("GET /api/health", docbuilder.build(self.root, self.pid, ["api"], include_agentlab_api=True, write=False)["files"]["docs/API.md"])
        h = docbuilder.md_to_html("# T\n\n- a **b**\n- c\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n<script>x</script> [l](https://e.com)")
        self.assertIn("<li>a <b>b</b></li>", h)
        self.assertIn("<td>1</td>", h)
        self.assertNotIn("<script>x", h)
        self.assertIn('<a href="https://e.com">l</a>', h)


class PackTests(Case):
    def test_check(self):
        r = packager.check(self.root, self.pid)
        texts = " ".join(i["text"] for i in r["compatibility"] + r["optimisation"])
        self.assertEqual(r["python"]["works_on"][0], "3.10")  # match statement
        self.assertIn("'yaml' is imported but not declared", texts)
        self.assertNotIn("'requests' is imported but not declared", texts)
        self.assertNotIn("'helpers'", texts)  # local module
        self.assertIn("'flask' is declared but never imported", texts)
        self.assertTrue(any(i["path"] == ".env" and i["level"] == "error" for i in r["optimisation"]))
        self.assertTrue(any("unminified" in i["text"] for i in r["optimisation"]))
        self.assertLess(r["score"], 100)

    def test_package_leaves_out_secrets_and_adds_installers(self):
        out = packager.package(self.root, self.pid, "1.2.0")
        self.assertIn(".env", out["skipped"])
        self.assertIn("debug.log", out["skipped"])
        z = zipfile.ZipFile(projects.resolve(self.root, self.pid, out["path"]))
        names = z.namelist()
        self.assertIn("weather-tool-1.2.0/install.sh", names)
        self.assertFalse(any(n.endswith(".env") for n in names))
        sh = z.read("weather-tool-1.2.0/install.sh").decode()
        self.assertIn("pip install -r requirements.txt", sh)
        self.assertEqual((z.getinfo("weather-tool-1.2.0/install.sh").external_attr >> 16) & 0o777, 0o755)
        with self.assertRaises(packager.PackError):
            packager.package(self.root, self.pid, "latest")

    def test_requirements_generated_and_static_site(self):
        projects.delete_path(self.root, self.pid, "requirements.txt")
        out = packager.package(self.root, self.pid)
        self.assertIn("requirements.txt", out["added"])
        z = zipfile.ZipFile(projects.resolve(self.root, self.pid, out["path"]))
        reqs = z.read("weather-tool-0.1.0/requirements.txt").decode()
        self.assertEqual(reqs.split(), ["pyyaml", "requests"])
        site = builders.create(self.root, "landing")["project"]["id"]
        self.assertEqual(packager.check(self.root, site)["kind"], "static-site")
        self.assertIn("http.server", zipfile.ZipFile(projects.resolve(self.root, site, packager.package(self.root, site)["path"])).read("landing-page-0.1.0/install.sh").decode())


if __name__ == "__main__":
    unittest.main()
