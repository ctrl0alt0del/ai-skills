"""Ownership and independent-loading regressions for graph builder/app skills."""

import ast
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILDER = Path(os.environ.get("BEHAVIOR_BUILDER_SKILL_DIR", ROOT / "skills/behavior-knowledge-graph-builder"))
REVIEW = Path(os.environ.get("BEHAVIOR_REVIEW_SKILL_DIR", os.environ.get(
    "BEHAVIOR_SKILL_DIR", ROOT / "skills/behavior-graph-review-app")))


class SkillBoundaryTests(unittest.TestCase):
    def load_schema(self):
        spec = importlib.util.spec_from_file_location("_builder_schema_boundary_test", BUILDER / "scripts/graph_schema.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_graph_schema_is_identical_and_has_only_pure_dependencies(self):
        builder_schema = (BUILDER / "scripts/graph_schema.py").read_bytes()
        self.assertEqual(builder_schema, (REVIEW / "scripts/graph_schema.py").read_bytes())
        tree = ast.parse(builder_schema)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add((node.module or "").split(".")[0])
        self.assertLessEqual(imports, {"datetime", "hashlib", "json", "pathlib", "re"})
        defined = {node.name for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))}
        self.assertIn("validate_graph", defined)
        self.assertIn("GraphError", defined)
        self.assertTrue(defined.isdisjoint({"Store", "normalize_feedback", "advance_feedback", "review_digest", "make_server"}))

    def test_builder_does_not_ship_review_app_runtime(self):
        runtime_files = {
            "graph_review.py", "graph_model.py", "review_actions.py", "graph_contract.py",
            "graph_repositories.py", "document_repositories.py", "graph_connection.py",
            "repository_config.py", "filesystem_repository.py", "sqlite_repository.py",
            "s3_repository.py", "http_repository.py",
        }
        for filename in runtime_files:
            with self.subTest(filename=filename):
                self.assertFalse((BUILDER / "scripts" / filename).exists())
                self.assertTrue((REVIEW / "scripts" / filename).is_file())
        for relative in ("assets/review-app", "assets/repository-configs",
                         "references/repository-config.md", "references/repository-contract.md",
                         "references/chat-review.md", "references/runtime-contract.md"):
            with self.subTest(relative=relative):
                self.assertFalse((BUILDER / relative).exists())
                self.assertTrue((REVIEW / relative).exists())
        self.assertTrue((BUILDER / "references/graph-schema.md").is_file())

    def test_pure_validator_preserves_reference_and_json_guards(self):
        schema = self.load_schema()
        graph = json.loads((BUILDER / "assets/example-graph.json").read_text())
        self.assertEqual(schema.validate_graph(graph), graph)
        invalid = deepcopy(graph)
        invalid["dependencies"][0]["to"] = "missing-node"
        with self.assertRaises(schema.GraphError):
            schema.validate_graph(invalid)
        invalid = deepcopy(graph)
        invalid["nodes"][0]["review_status"] = "approved"
        with self.assertRaises(schema.GraphError):
            schema.validate_graph(invalid)
        for text in ('{"x":1,"x":2}', '{"x":NaN}'):
            with self.subTest(text=text), self.assertRaises(schema.GraphError):
                schema.parse_json(text)

    def test_review_app_runtime_loads_without_installed_builder_path(self):
        code = '''
import json,sys
from pathlib import Path
app=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(app/'scripts'))
import graph_schema,graph_contract,graph_model,graph_review
assert Path(graph_schema.__file__).resolve()==app/'scripts/graph_schema.py'
assert graph_model.ReviewError is graph_schema.GraphError
assert graph_contract.ReviewError is graph_schema.GraphError
graph=graph_schema.read_graph(app/'assets/example-graph.json')
assert graph_review.DIST==app/'assets/review-app/dist'
assert graph_review.load_dist(graph_review.DIST)
print(json.dumps({'nodes':len(graph['nodes']),'standalone':True}))
'''
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run([sys.executable, "-c", code, str(REVIEW)], cwd=directory,
                                       env={**os.environ, "PYTHONPATH": "", "PYTHONDONTWRITEBYTECODE": "1"},
                                       text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["standalone"])


if __name__ == "__main__":
    unittest.main()
