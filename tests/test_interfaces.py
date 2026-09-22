import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from beatmate.api import server
from beatmate.service import Service
from beatmate.llm import OpenAIPlanner, PlannerError
from beatmate.model import BeatSpec
from beatmate.planner import MockPlanner


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = Service(Path(self.tmp.name) / "db")
        self.server = server(self.service, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, body=None, raw=None, headers=None):
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5
        )
        try:
            conn.request(
                method,
                path,
                raw
                if raw is not None
                else json.dumps(body)
                if body is not None
                else None,
                headers or {"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            data = response.read()
            return response.status, json.loads(data) if response.getheader(
                "Content-Type"
            ) == "application/json" else data
        finally:
            conn.close()

    def test_http_complete_flow(self):
        code, parsed = self.request("POST", "/parse", {"text": "trap 140 BPM 4小节"})
        self.assertEqual(code, 200)
        code, v = self.request(
            "POST", "/projects", {"spec": parsed["spec"], "protected_tracks": ["kick"]}
        )
        self.assertEqual(code, 201)
        pid, vid = v["project_id"], v["version_id"]
        code, plan = self.request(
            "POST",
            "/plan",
            dict(text="加密32", track_id="hihat", start_bar=3, end_bar=4),
        )
        self.assertEqual(code, 200)
        code, new = self.request(
            "POST", f"/projects/{pid}/edits", dict(base_version=vid, plan=plan)
        )
        self.assertEqual(code, 201)
        self.assertEqual(
            self.request(
                "POST", f"/projects/{pid}/edits", dict(base_version=vid, plan=plan)
            )[0],
            409,
        )
        self.assertEqual(self.request("GET", f"/projects/{pid}/versions/{vid}")[1], v)
        self.assertEqual(self.request("GET", f"/projects/{pid}")[1], new)
        for suffix, magic in [("midi", b"MThd"), ("preview", b"RIFF")]:
            code, data = self.request(
                "GET", f"/projects/{pid}/versions/{new['version_id']}/{suffix}"
            )
            self.assertEqual(code, 200)
            self.assertTrue(data.startswith(magic))

    def test_http_input_errors(self):
        for body in ["{", "[]", "null", '{"text":"x","extra":1}']:
            self.assertEqual(self.request("POST", "/projects", raw=body)[0], 400)
        self.assertEqual(self.request("POST", "/projects", raw="x" * 65537)[0], 400)
        self.assertEqual(self.request("GET", "/missing")[0], 404)
        self.assertEqual(self.request("GET", "/projects/" + "0" * 32)[0], 404)
        self.assertEqual(
            self.request(
                "POST",
                "/parse",
                {"text": "x"},
                headers={
                    "Origin": "https://other.example",
                    "Content-Type": "application/json",
                },
            )[0],
            403,
        )


class PlannerTests(unittest.TestCase):
    def response(self, data):
        return dict(
            status="completed",
            output=[
                dict(
                    type="message",
                    content=[dict(type="output_text", text=json.dumps(data))],
                )
            ],
        )

    def test_llm_spec_and_scope(self):
        calls = []

        def transport(payload):
            calls.append(payload)
            return self.response(
                BeatSpec().to_dict()
                if len(calls) == 1
                else dict(operation="density", value=32)
            )

        planner = OpenAIPlanner("test-key", "user-configured-model", transport)
        self.assertEqual(
            planner.parse_intent("暗色 boom bap")["spec"], BeatSpec().to_dict()
        )
        plan = planner.plan_edit("加密", "hihat", 3, 4)
        self.assertEqual(
            (plan["track_id"], plan["start_bar"], plan["end_bar"]), ("hihat", 3, 4)
        )
        self.assertTrue(calls[0]["text"]["format"]["strict"])
        self.assertFalse(calls[0]["store"])
        self.assertNotIn("test-key", json.dumps(calls))

    def test_llm_rejects_invalid_refusal_incomplete_and_expanded_scope(self):
        for response in [
            dict(status="incomplete"),
            dict(status="completed", output=[]),
            dict(
                status="completed",
                output=[dict(type="message", content=[dict(type="refusal")])],
            ),
            self.response(dict(operation="density", value=32, track_id="kick")),
            self.response(dict(operation="density", value=999)),
        ]:
            planner = OpenAIPlanner(
                "key", "model", lambda _, response=response: response
            )
            with self.assertRaises(PlannerError):
                planner.plan_edit("edit", "hihat", 1, 2)

    def test_mock_explicit_fallback_and_unknown_edit(self):
        planner = MockPlanner()
        self.assertTrue(planner.parse_intent("温暖而朦胧")["assumptions"])
        with self.assertRaises(ValueError):
            planner.plan_edit("more emotional", "hihat", 1, 2)
