"""Executable config, mixed storage, shared CLI, and public-config integration."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote

SKILL = Path(os.environ.get("BEHAVIOR_SKILL_DIR", Path(__file__).resolve().parents[1] / "skills" / "behavior-knowledge-graph-builder"))
sys.path.insert(0, str(SKILL / "scripts"))
sys.dont_write_bytecode = True
from graph_connection import read_connection, write_connection
from graph_model import ReviewError, Store, digest
from graph_review import main, make_server
from document_repositories import graph_key, review_key
from filesystem_repository import FilesystemDocumentStore
from sqlite_repository import SQLiteDocumentStore
from repository_config import load_repository_config, validate_config, DEFAULT_CLIENT_CONFIG
from test_graph_repositories import app_dist, request, running_server


class RepositoryConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.graph = json.loads((SKILL / "assets/example-graph.json").read_text())
        self.graph_file = self.root / "proposal.json"
        self.graph_file.write_text(json.dumps(self.graph))
        self.config_path = self.root / "config.json"
        self.graph_id = "booking:one/α"
        self.revision = "snapshot:first/opaque"
        self.config = {
            "schema_version": "behavior-review-config/1",
            "repositories": {
                "graphFiles": {"type": "filesystem", "path": "./documents"},
                "reviewDb": {"type": "sqlite", "path": "./review-db/reviews.sqlite3"},
            },
            "bindings": {"graphs": "graphFiles", "reviews": "reviewDb"},
            "graph_id": self.graph_id,
        }
        self.save_config()

    def save_config(self):
        self.config_path.write_text(json.dumps(self.config))

    def cli(self, *arguments, expected=0):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = main([str(value) for value in arguments])
        self.assertEqual(status, expected, errors.getvalue())
        return json.loads(output.getvalue() if status == 0 else errors.getvalue())

    def provision(self):
        self.cli("register-graph", "--config", self.config_path, "--graph", self.graph_file,
                 "--revision", self.revision)
        return self.cli("init-review", "--config", self.config_path)

    def test_validation_and_loading_do_not_create_storage(self):
        result = self.cli("config-check", "--config", self.config_path)
        self.assertEqual(result["bindings"], {
            "graphs": {"repository": "graphFiles", "type": "filesystem"},
            "reviews": {"repository": "reviewDb", "type": "sqlite"},
        })
        configured = load_repository_config(self.config_path)
        with self.assertRaises(ReviewError):
            configured.repositories.graphs.list_graphs()
        self.assertFalse((self.root / "documents").exists())
        self.assertFalse((self.root / "review-db").exists())

    def test_independent_bindings_are_durable_and_paths_are_config_relative(self):
        self.provision()
        configured = load_repository_config(self.config_path)
        self.assertIsInstance(configured.repositories.graphs.store, FilesystemDocumentStore)
        self.assertIsInstance(configured.repositories.reviews.store, SQLiteDocumentStore)
        state = configured.repositories.reviews.create_annotation(self.graph_id, {
            "target_type": "graph", "target_id": None, "kind": "question", "text": "Clarify duplicate submissions."
        }, 1)
        reopened = load_repository_config(self.config_path)
        self.assertEqual(reopened.repositories.reviews.get_review(self.graph_id)["annotations"], state["annotations"])
        self.assertTrue((self.root / "documents" / graph_key(self.graph_id, self.revision)).is_file())
        self.assertFalse((self.root / "documents" / review_key(self.graph_id)).exists())
        document, _ = configured.repositories.reviews.store.read(review_key(self.graph_id))
        self.assertNotIn("graph", document["state"])
        actual = subprocess.run([sys.executable, str(SKILL / "scripts/graph_review.py"),
                                 "feedback-status", "--config", str(self.config_path)],
                                cwd=self.root.parent, text=True, capture_output=True, check=False)
        self.assertEqual(actual.returncode, 0, actual.stderr)
        self.assertEqual(json.loads(actual.stdout)["review"]["annotations"], state["annotations"])

    def test_bindings_can_be_reversed_without_changing_application(self):
        self.config["bindings"] = {"graphs": "reviewDb", "reviews": "graphFiles"}
        self.save_config()
        self.provision()
        configured = load_repository_config(self.config_path)
        self.assertIsInstance(configured.repositories.graphs.store, SQLiteDocumentStore)
        self.assertIsInstance(configured.repositories.reviews.store, FilesystemDocumentStore)
        self.assertTrue((self.root / "documents" / review_key(self.graph_id)).is_file())
        self.assertEqual(configured.repositories.reviews.get_review(self.graph_id)["graph_revision"], self.revision)

    def test_only_bound_adapters_instantiate(self):
        self.config["repositories"]["unusedCloud"] = {"type": "s3", "bucket": "unused-test-bucket", "region": "us-east-1"}
        self.save_config()
        with patch("s3_repository.S3DocumentStore", side_effect=AssertionError("unused S3 adapter was created")) as remote:
            configured = load_repository_config(self.config_path)
        remote.assert_not_called()
        self.assertEqual(configured.types["unusedCloud"], "s3")

    def test_unknown_fields_types_and_missing_bindings_fail_before_storage(self):
        invalid = []
        value = deepcopy(self.config); value["bindings"]["reviews"] = "missing"; invalid.append(value)
        value = deepcopy(self.config); value["bindings"]["annotation"] = "reviewDb"; invalid.append(value)
        value = deepcopy(self.config); value["repositories"]["graphFiles"]["type"] = "postgresql"; invalid.append(value)
        value = deepcopy(self.config); value["repositories"]["reviewDb"]["password"] = "secret"; invalid.append(value)
        value = deepcopy(self.config); value["schema_version"] = "wrong"; invalid.append(value)
        value = deepcopy(self.config); value["repositories"]["toString"] = {"type": "sqlite", "path": "x"}; invalid.append(value)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ReviewError):
                validate_config(value)
        self.assertFalse((self.root / "documents").exists())

    def test_shared_connection_resolves_the_same_config_and_review(self):
        self.provision()
        connection_path = self.root / "handoff" / "connection.json"
        result = self.cli("connect", "--connection", connection_path, "--config", self.config_path)
        saved = json.loads(connection_path.read_text())
        self.assertEqual(saved["repository"], {"kind": "config", "path": "../config.json"})
        self.assertEqual(result["graph_id"], self.graph_id)
        connection = read_connection(connection_path)
        self.assertEqual(connection.config_path, self.config_path)
        self.assertEqual(connection.client_config, DEFAULT_CLIENT_CONFIG)
        self.assertEqual(self.cli("feedback-status", "--connection", connection_path)["graph"]["revision"], self.revision)
        self.cli("connect", "--connection", connection_path, "--config", self.config_path)

    def test_later_catalog_revision_does_not_change_review_pin(self):
        self.provision()
        changed = deepcopy(self.graph)
        changed["project"]["name"] = "A later proposal"
        self.graph_file.write_text(json.dumps(changed))
        self.cli("register-graph", "--config", self.config_path, "--graph", self.graph_file,
                 "--revision", "second-revision")
        status = self.cli("feedback-status", "--config", self.config_path)
        self.assertEqual(status["graph"]["revision"], self.revision)
        self.assertEqual(status["graph"]["digest"], digest(self.graph))
        advanced = self.cli("propose", "--config", self.config_path, "--graph", self.graph_file,
                            "--revision", "reviewed-second", "--summary", "A deliberate next round",
                            "--expect-version", 1)
        self.assertEqual(advanced["round"], 2)
        self.assertEqual(advanced["graph_revision"], "reviewed-second")
        self.assertEqual(self.cli("feedback-status", "--config", self.config_path)["graph"]["graph"], changed)

    def test_agent_commands_preserve_source_and_feedback_processing(self):
        self.provision()
        state = self.cli("annotate", "--config", self.config_path, "--target-type", "graph",
                         "--kind", "question", "--text", "What happens after a retry?", "--source", "chat",
                         "--source-ref", "test-conversation", "--expect-version", 1)
        note = state["annotations"][0]
        state = self.cli("resolve", "--config", self.config_path, "--id", note["id"],
                         "--disposition", "addressed", "--text", "The existing contract handles the retry.",
                         "--source", "agent", "--expect-version", state["state_version"])
        self.assertEqual(state["annotations"][0]["feedback_revision"], 1)
        pending = self.cli("pending-feedback", "--config", self.config_path)
        self.assertEqual(len(pending["annotations"]), 1)
        processed = self.cli("mark-processed", "--config", self.config_path, "--id", note["id"],
                             "--feedback-revision", 1, "--outcome", "addressed", "--summary", "Recorded the answer.",
                             "--processor", "test-agent", "--expect-version", state["state_version"])
        self.assertEqual(processed["annotations"][0]["processing_receipt"]["graph_revision"], self.revision)
        self.assertEqual(self.cli("pending-feedback", "--config", self.config_path)["annotations"], [])

    def test_import_session_preserves_history_and_never_overwrites_review(self):
        source = Store(self.root / "legacy")
        source.initialize("Existing review")
        state = source.propose(self.graph, "First round", 1)
        state = source.annotate("graph", None, "question", "Existing feedback", "ui", None, state["state_version"])
        state = source.propose(self.graph, "Second round", state["state_version"])
        imported = self.cli("import-session", "--config", self.config_path, "--session", source.directory)
        self.assertEqual(imported["graph_id"], state["session_id"])
        self.assertEqual(imported["state_version"], state["state_version"])
        self.assertEqual(imported["annotations"], state["annotations"])
        configured = load_repository_config(self.config_path)
        self.assertEqual(configured.repositories.graphs.get_graph(state["session_id"], "1")["graph"], self.graph)
        self.cli("import-session", "--config", self.config_path, "--session", source.directory, expected=1)
        self.assertEqual(configured.repositories.reviews.get_review(state["session_id"])["state_version"], state["state_version"])

    def test_session_repository_remains_available_from_config(self):
        source = Store(self.root / "legacy")
        source.initialize("Existing review")
        state = source.propose(self.graph, "Existing proposal", 1)
        self.config.update(repositories={"old": {"type": "session", "session": "./legacy"}},
                           bindings={"graphs": "old", "reviews": "old"}, graph_id=state["session_id"])
        self.save_config()
        self.assertEqual(self.cli("feedback-status", "--config", self.config_path)["review"]["graph_revision"], "1")

    def test_legacy_review_cannot_silently_author_to_another_graph_store(self):
        self.config["repositories"]["legacy"] = {"type": "session", "session": "./legacy"}
        self.config["bindings"]["reviews"] = "legacy"
        with self.assertRaisesRegex(ReviewError, "same named repository"):
            validate_config(self.config)

    def test_connect_checks_selected_graph_inherited_from_config(self):
        self.provision()
        self.config["graph_id"] = "a-different-graph"
        self.save_config()
        connection_path = self.root / "invalid-selection.json"
        error = self.cli("connect", "--connection", connection_path, "--config", self.config_path, expected=1)
        self.assertIn("not in this repository", error["error"])
        self.assertFalse(connection_path.exists())

    def test_local_authoring_does_not_ignore_graph_selection(self):
        source = Store(self.root / "legacy")
        source.initialize("Existing review")
        source.propose(self.graph, "Existing proposal", 1)
        self.cli("annotate", "--session", source.directory, "--graph-id", "wrong-graph",
                 "--target-type", "graph", "--kind", "question", "--text", "Wrong target",
                 "--source", "chat", "--expect-version", 2, expected=1)
        self.assertEqual(source.state()["state_version"], 2)
        self.assertEqual(source.state()["annotations"], [])

    def test_server_exposes_only_public_config_and_feedback_routes(self):
        self.provision()
        self.config["repositories"]["privateApi"] = {
            "type": "http", "base_url": "https://private-backend.example", "auth_token_env": "PRIVATE_REVIEW_TOKEN"
        }
        self.save_config()
        configured = load_repository_config(self.config_path)
        dist = app_dist(self.root / "dist")
        with running_server(configured.repositories, dist) as url:
            status, public, _ = request(url, "/config.json")
            self.assertEqual(status, 200)
            self.assertEqual(public, DEFAULT_CLIENT_CONFIG)
            self.assertNotIn("PRIVATE_REVIEW_TOKEN", json.dumps(public))
            self.assertNotIn("private-backend", json.dumps(public))
            prefix = "/api/graphs/" + quote(self.graph_id, safe="")
            _, bootstrap, _ = request(url, "/api/bootstrap")
            token = bootstrap["csrf_token"]
            status, state, _ = request(url, prefix + "/annotations", "POST", {
                "expected_version": 1, "target_type": "graph", "target_id": None,
                "kind": "question", "text": "Feedback from the app"
            }, token)
            self.assertEqual(status, 200)
            note = state["annotations"][0]
            status, inbox, _ = request(url, prefix + "/pending-feedback")
            self.assertEqual(status, 200)
            self.assertEqual(inbox["annotations"][0]["id"], note["id"])
            payload = {"expected_version": state["state_version"], "annotation_id": note["id"],
                       "feedback_revision": 1, "outcome": "needs_clarification", "summary": "Asked in chat.",
                       "processor": "test-agent"}
            self.assertEqual(request(url, prefix + "/mark-processed", "POST", payload)[0], 403)
            self.assertEqual(request(url, prefix + "/mark-processed", "POST", payload, token)[0], 200)
            self.assertEqual(request(url, prefix + "/mark-processed", "POST", payload, token)[0], 409)
            self.assertEqual(request(url, prefix + "/pending-feedback")[1]["annotations"], [])

    def test_public_configuration_rejects_credentials_and_read_only_review_binding(self):
        self.config["client"] = deepcopy(DEFAULT_CLIENT_CONFIG)
        self.config["client"]["repositories"]["local"]["auth_token_env"] = "SECRET"
        with self.assertRaises(ReviewError):
            validate_config(self.config)
        for ending in ("?", "#"):
            self.config["client"] = deepcopy(DEFAULT_CLIENT_CONFIG)
            self.config["client"]["repositories"]["local"]["base_url"] = "https://api.example" + ending
            with self.subTest(ending=ending), self.assertRaises(ReviewError):
                validate_config(self.config)
        self.config["client"] = {
            "schema_version": "behavior-review-client-config/1",
            "repositories": {"files": {"type": "json", "url": "https://graphs.example/catalog.json"}},
            "bindings": {"graphs": "files", "reviews": "files"},
        }
        with self.assertRaises(ReviewError):
            validate_config(self.config)

    def test_explicit_public_endpoints_get_exact_csp_sources(self):
        self.provision()
        self.config["client"] = {
            "schema_version": "behavior-review-client-config/1",
            "repositories": {"api": {"type": "http", "base_url": ""},
                             "catalog": {"type": "json", "url": "https://graphs.example/catalog.json"}},
            "bindings": {"graphs": "catalog", "reviews": "api"},
        }
        self.save_config()
        configured = load_repository_config(self.config_path)
        server, url = make_server(configured.repositories, dist=app_dist(self.root / "dist"),
                                  client_config=configured.client_config)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            status, value, headers = request(url, "/config.json")
            self.assertEqual(status, 200)
            self.assertEqual(value, self.config["client"])
            self.assertIn("connect-src 'self' https://graphs.example;", headers["Content-Security-Policy"])
            self.assertNotIn("*", headers["Content-Security-Policy"])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
