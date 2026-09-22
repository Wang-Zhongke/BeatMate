import concurrent.futures
import http.client
import json
from pathlib import Path
import socket
import ssl
import sqlite3
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch, MagicMock
from beatmate.audio import AudioService, inspect_audio
from beatmate.audio_input import prepare
from beatmate.audio_provider import (
    MurekaAdapter,
    MockAudioAdapter,
    AudioError,
    Rejected,
    BeforeSubmissionError,
    public_target,
    https_request,
)
from beatmate.api import server
from beatmate.service import ConflictError


class Fake(MockAudioAdapter):
    def __init__(self):
        self.submits = 0
        self.queries = 0
        self.downloads = 0
        self.submit_error = None
        self.query_error = None
        self.download_error = None
        self.bad_data = None

    def submit(self, t):
        self.submits += 1
        if self.submit_error:
            raise self.submit_error
        return super().submit(t)

    def query(self, t):
        self.queries += 1
        if self.query_error:
            raise self.query_error
        return super().query(t)

    def download(self, c):
        self.downloads += 1
        if self.download_error:
            raise self.download_error
        return self.bad_data if self.bad_data is not None else super().download(c)


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.adapter = Fake()
        self.env = {"BEATMATE_AUDIO_MAX_CANDIDATES": "2"}
        self.s = AudioService(self.tmp.name, self.adapter, self.env)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def create(self, **kw):
        return self.s.create(
            "150秒，64小节，伤感、克制，给Rap留白。", uuid.uuid4().hex, **kw
        )

    def finish(self):
        for _ in range(4):
            self.s.tick()

    def test_direct_and_template_without_planner_keys(self):
        text = "  150秒，64小节，自由拍号，留给低声Rap。\n"
        for mode in ("direct", "template"):
            b = prepare(text, mode)
            self.assertEqual(b["raw_text"], text)
            self.assertEqual(b["assumptions"], [])
            self.assertIn(text, b["final_prompt"])
            self.assertNotIn("boom_bap", b["final_prompt"])
        self.assertEqual(prepare(text)["final_prompt"], text)
        self.assertNotIn("配器", prepare(text, "template")["final_prompt"])
        self.assertIn(
            "用途：录Rap",
            prepare(text, "template", {"purpose": "录Rap"})["final_prompt"],
        )

    def test_review_and_conflicting_bpm(self):
        with self.assertRaises(ValueError):
            self.create(constraints={"mood": "明亮"})
        self.create(constraints={"mood": "明亮"}, reviewed=True)
        with self.assertRaises(ValueError):
            self.s.create(
                "80 BPM",
                uuid.uuid4().hex,
                constraints={"structure": "100 BPM"},
                reviewed=True,
            )

    def test_creation_modes_preserve_original_text(self):
        raw = "  克制的吉他，留给Rap。\n"
        brief = prepare(raw, "template", creation_mode="instrumental")
        self.assertEqual(brief["final_prompt"], raw)
        self.assertEqual(brief["prompt_mode"], "direct")
        lyrics = "  [主歌]\n手机亮了又暗\n\n[副歌]\n把没说出口的话留下  "
        brief = prepare(raw, creation_mode="lyrics", lyrics=lyrics)
        self.assertEqual(brief["raw_text"], raw)
        self.assertEqual(brief["lyrics"], lyrics)
        self.assertIn("歌词原文：\n" + lyrics, brief["final_prompt"])
        self.assertTrue(brief["final_prompt"].endswith(raw))
        self.assertIn("不要演唱", brief["final_prompt"])
        self.assertFalse(brief["detected_conflict"])
        self.assertTrue(brief["requires_review"])
        self.assertIn(
            lyrics, prepare("", creation_mode="lyrics", lyrics=lyrics)["final_prompt"]
        )

    def test_lyrics_validation_and_total_length_before_submission(self):
        for extra in (
            {"creation_mode": "lyrics"},
            {"creation_mode": "lyrics", "lyrics": "  "},
            {"creation_mode": "instrumental", "lyrics": "不能静默忽略"},
            {"creation_mode": "song"},
            {"creation_mode": "lyrics", "lyrics": 42},
            {"creation_mode": "lyrics", "lyrics": "坏\x00字符"},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.s.create("伴奏", uuid.uuid4().hex, reviewed=True, **extra)
        overhead = (
            len(prepare("", creation_mode="lyrics", lyrics="字")["final_prompt"]) - 1
        )
        self.assertEqual(
            len(
                prepare("", creation_mode="lyrics", lyrics="字" * (1024 - overhead))[
                    "final_prompt"
                ]
            ),
            1024,
        )
        with self.assertRaisesRegex(ValueError, "1025字符.*原文未截断"):
            self.s.create(
                "",
                uuid.uuid4().hex,
                creation_mode="lyrics",
                lyrics="字" * (1025 - overhead),
                reviewed=True,
            )
        self.assertEqual(self.s.history()["tasks"], [])
        self.assertEqual(self.adapter.submits, 0)

    def test_lyrics_history_and_retries_preserve_request(self):
        rid = uuid.uuid4().hex
        args = dict(
            creation_mode="lyrics", lyrics="[主歌]\n我还没说出口", reviewed=True
        )
        task = self.s.create("", rid, **args)
        self.assertEqual(self.s.create("", rid, **args)["id"], task["id"])
        with self.assertRaises(ConflictError):
            self.s.create("", rid, **dict(args, lyrics="改过的歌词"))
        self.finish()
        restored = AudioService(self.tmp.name, self.adapter, self.env)
        self.assertEqual(restored.get(task["id"])["brief"]["lyrics"], args["lyrics"])
        self.assertEqual(restored.get(task["id"])["status"], "ready")
        self.assertEqual(self.adapter.submits, 1)
        restored.close()

    def test_lyrics_remain_on_instrumental_provider_endpoint(self):
        adapter = MurekaAdapter(
            {
                "MUREKA_API_KEY": "test",
                "MUREKA_MODEL": "mureka-9",
                "BEATMATE_AUDIO_LIVE": "1",
            }
        )
        brief = prepare("", creation_mode="lyrics", lyrics="凌晨的路灯")
        with patch.object(adapter, "request", return_value={"id": "test"}) as request:
            adapter.submit({"requested_model": "mureka-9", "brief": brief, "n": 1})
        self.assertEqual(
            request.call_args.args[:2], ("POST", "/v1/instrumental/generate")
        )
        self.assertEqual(request.call_args.args[2]["prompt"], brief["final_prompt"])
        self.assertNotIn("lyrics", request.call_args.args[2])

    def test_legacy_pending_fingerprint_survives_new_modes(self):
        rid = uuid.uuid4().hex
        task = self.s.create("老草稿", rid, prompt_mode="template")
        restored = AudioService(self.tmp.name, self.adapter, self.env)
        self.assertEqual(
            restored.create("老草稿", rid, prompt_mode="template")["id"], task["id"]
        )
        self.assertNotIn("creation_mode", task["brief"])
        restored.close()

    def test_invalid_input(self):
        for text in ("", "x" * 1025, "x\x00"):
            with self.assertRaises(ValueError):
                prepare(text)
        with self.assertRaises(ValueError):
            prepare("x", constraints={"notes": []})
        with self.assertRaises(ValueError):
            self.create(n=True)

    def test_multiple_candidates_immutable_selection_and_restart(self):
        t = self.create(n=2)
        self.finish()
        h = self.s.history()
        self.assertEqual(len(h["assets"]), 2)
        self.assertEqual(self.s.get(t["id"])["status"], "ready")
        a, b = h["assets"]
        self.assertNotEqual(a["sha256"], b["sha256"])
        self.s.select(t["project_id"], a["id"])
        later = self.create(project_id=t["project_id"])
        self.finish()
        self.assertEqual(
            self.s.history()["projects"][0]["selected_audio_asset_id"], a["id"]
        )
        self.s = AudioService(self.tmp.name, self.adapter, self.env)
        self.assertEqual(len(self.s.history()["assets"]), 3)
        self.s.select(t["project_id"], b["id"])
        with self.s.connect() as c:
            for table in ("audio_results", "audio_requests"):
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute("UPDATE " + table + " SET data=?", ("{}",))

    def test_concurrent_idempotency_and_changed_content(self):
        rid = uuid.uuid4().hex
        with concurrent.futures.ThreadPoolExecutor(5) as ex:
            tasks = list(ex.map(lambda _: self.s.create("test", rid), range(5)))
        self.assertEqual(len({t["id"] for t in tasks}), 1)
        self.finish()
        self.assertEqual(self.adapter.submits, 1)
        with self.assertRaises(ConflictError):
            self.s.create("changed", rid)

    def test_restart_queued_and_generating(self):
        t = self.create()
        self.s = AudioService(self.tmp.name, self.adapter, self.env)
        self.s.tick()
        self.assertEqual(self.s.get(t["id"])["status"], "generating")
        self.s = AudioService(self.tmp.name, self.adapter, self.env)
        self.finish()
        self.assertEqual(self.adapter.submits, 1)

    def test_submit_uncertain_never_resubmits(self):
        self.adapter.submit_error = TimeoutError("secret-key-must-not-leak")
        t = self.create()
        self.finish()
        self.s = AudioService(self.tmp.name, self.adapter, self.env)
        self.finish()
        self.assertEqual(self.adapter.submits, 1)
        self.assertEqual(self.s.get(t["id"])["status"], "uncertain")
        self.assertNotIn("secret-key", json.dumps(self.s.history()))
        with self.assertRaises(ValueError):
            self.s.retry(t["id"])

    def test_crash_submitting_with_and_without_id(self):
        t = self.create()
        t["status"] = "submitting"
        self.s.save(t)
        self.finish()
        self.assertEqual(self.s.get(t["id"])["status"], "uncertain")
        self.assertEqual(self.adapter.submits, 0)
        t = self.create()
        t.update(status="submitting", provider_task_id="existing")
        self.s.save(t)
        self.finish()
        self.assertEqual(self.s.get(t["id"])["status"], "ready")
        self.assertEqual(self.adapter.submits, 0)

    def test_query_transient_failure_recovery(self):
        t = self.create()
        self.s.tick()
        self.adapter.query_error = TimeoutError()
        self.s.tick()
        self.assertEqual(self.s.get(t["id"])["status"], "generating")
        self.adapter.query_error = None
        self.s.retry(t["id"])
        self.finish()
        self.assertEqual(self.adapter.submits, 1)
        self.assertEqual(self.s.get(t["id"])["status"], "ready")

    def test_download_failure_retry_queries_same_task(self):
        t = self.create(n=2)
        self.s.tick()
        self.s.tick()
        self.adapter.download_error = AudioError("expired link")
        self.s.tick()
        self.assertEqual(self.s.get(t["id"])["status"], "downloading")
        self.s = AudioService(self.tmp.name, self.adapter, self.env)
        self.adapter.download_error = None
        self.s.retry(t["id"])
        self.finish()
        self.assertEqual(self.adapter.submits, 1)
        self.assertEqual(len(self.s.history()["assets"]), 2)
        self.assertGreaterEqual(self.adapter.queries, 2)

    def test_bad_files_never_ready(self):
        valid = self.adapter.download({"index": 0})
        for bad in (b"", b"<html>Oops</html>", valid[:-50], b"RIFF" + b"\x00" * 100):
            with self.subTest(size=len(bad)):
                t = self.create()
                self.adapter.bad_data = bad
                self.finish()
                self.assertEqual(self.s.get(t["id"])["status"], "downloading")
                self.assertFalse(
                    any(a["task_id"] == t["id"] for a in self.s.history()["assets"])
                )

    def test_official_optional_index_saved_without_regenerating(self):
        t = self.create(n=2)
        self.s.tick()
        original = self.adapter.query

        def response(task):
            result = original(task)
            for candidate in result["choices"]:
                candidate.pop("index")
            return result

        with patch.object(self.adapter, "query", side_effect=response):
            self.finish()
        assets = self.s.history()["assets"]
        self.assertEqual(self.s.get(t["id"])["status"], "ready")
        self.assertEqual([a["index"] for a in assets], [0, 1])
        self.assertTrue(all(a["index_source"] == "response_order" for a in assets))
        self.assertEqual(len({a["provider_candidate_id"] for a in assets}), 2)
        self.assertEqual(self.adapter.submits, 1)

    def test_query_contract_error_is_specific_and_safe(self):
        t = self.create()
        self.s.tick()
        bad = {
            "id": self.s.get(t["id"])["provider_task_id"],
            "status": "succeeded",
            "choices": [
                {
                    "id": "candidate",
                    "index": "invalid",
                    "url": "https://cdn.mureka.ai/test.wav",
                    "duration": 4000,
                }
            ],
            "secret": "DO_NOT_STORE",
        }
        with patch.object(self.adapter, "query", return_value=bad):
            self.s.tick()
        result = self.s.get(t["id"])
        self.assertEqual(result["status"], "generating")
        self.assertIn("index", result["error"])
        self.assertEqual(
            result["response_summary"]["candidate_fields"][0]["index"], "str"
        )
        self.assertNotIn("DO_NOT_STORE", json.dumps(result))
        self.assertEqual(self.adapter.submits, 1)

    def test_bad_response_keeps_confirmed_id(self):
        t = self.create()
        with patch.object(
            self.adapter,
            "submit",
            return_value={"id": "known", "status": "succeeded", "choices": []},
        ):
            self.s.tick()
        self.assertEqual(self.s.get(t["id"])["provider_task_id"], "known")
        self.assertEqual(self.s.get(t["id"])["status"], "generating")

    def test_format_hash_and_safe_paths(self):
        self.create()
        self.finish()
        a = self.s.history()["assets"][0]
        p, _ = self.s.file(a["id"])
        self.assertEqual(inspect_audio(p), ("wav", 4.0))
        with self.assertRaises(ValueError):
            self.s.file("../../etc/passwd")
        p.chmod(0o644)
        p.write_bytes(b"bad")
        with self.assertRaises(AudioError):
            self.s.file(a["id"])
        self.assertFalse(self.s.history()["assets"][0]["available"])

    def test_foreign_asset_selection_rejected(self):
        t = self.create()
        self.finish()
        a = self.s.history()["assets"][0]
        other = self.create()
        with self.assertRaises(ValueError):
            self.s.select(other["project_id"], a["id"])

    def test_saved_budget_survives_restart_and_overrides_environment(self):
        self.assertEqual(self.s.budget()["max_submissions"], 1)
        saved = self.s.set_budget(100)
        self.assertEqual(saved["remaining_submissions"], 100)
        again = AudioService(
            self.tmp.name,
            self.adapter,
            dict(self.env, BEATMATE_AUDIO_MAX_SUBMISSIONS="500"),
        )
        self.assertEqual(again.budget()["max_submissions"], 100)
        self.assertEqual(again.config()["budget_source"], "saved")
        again.set_budget(1000)
        self.assertEqual(self.s.budget()["max_submissions"], 1000)
        self.assertEqual(self.adapter.submits, 0)
        self.assertEqual(self.s.history()["tasks"], [])

    def test_saved_budget_enforced_immediately_and_invalid_updates_atomic(self):
        env = {
            "BEATMATE_AUDIO_PROVIDER": "mureka",
            "MUREKA_API_KEY": "secret",
            "MUREKA_MODEL": "mureka-9",
            "BEATMATE_AUDIO_LIVE": "1",
        }
        real = AudioService(self.tmp.name, env=env)
        first = real.create("test", uuid.uuid4().hex)
        real.set_budget(2)
        second = AudioService(self.tmp.name, env=env)
        second.create("test", uuid.uuid4().hex)
        self.assertEqual(real.budget()["used_submissions"], 2)
        with self.assertRaises(ValueError):
            real.create("third", uuid.uuid4().hex)
        self.assertEqual(real.create("test", first["request_id"])["id"], first["id"])
        for value in (0, -1, 2**53, True, 1.5, "3", None):
            with self.assertRaises(ValueError):
                real.set_budget(value)
        self.assertEqual(real.budget()["max_submissions"], 2)
        real.set_budget(1)
        self.assertEqual(real.budget()["remaining_submissions"], 0)
        self.assertEqual(len(real.history()["tasks"]), 2)
        self.assertEqual(self.adapter.submits, 0)

    def test_explicit_real_config_and_budget(self):
        env = {"BEATMATE_AUDIO_PROVIDER": "mureka"}
        s = AudioService(Path(self.tmp.name) / "real", env=env)
        self.assertFalse(s.config()["configured"])
        with self.assertRaises(ValueError):
            s.create("test", uuid.uuid4().hex)
        env.update(
            MUREKA_API_KEY="secret", MUREKA_MODEL="mureka-9", BEATMATE_AUDIO_LIVE="1"
        )
        s = AudioService(Path(self.tmp.name) / "real", env=env)
        s.create("test", uuid.uuid4().hex)
        with self.assertRaises(ValueError):
            s.create("test", uuid.uuid4().hex)
        self.assertNotIn("secret", json.dumps(s.history()) + json.dumps(s.config()))

    def test_provider_isolation(self):
        t = self.create()
        real = AudioService(self.tmp.name, env={"BEATMATE_AUDIO_PROVIDER": "mureka"})
        real.tick()
        self.assertEqual(self.s.get(t["id"])["status"], "queued")

    def test_partial_candidate_download_keeps_first_result(self):
        t = self.create(n=2)
        self.s.tick()
        self.s.tick()
        original = self.adapter.download

        def partial(choice):
            if choice["index"] == 1:
                raise AudioError("download failed")
            return original(choice)

        with patch.object(self.adapter, "download", side_effect=partial):
            self.s.tick()
        first = self.s.history()["assets"][0]
        self.s.retry(t["id"])
        self.finish()
        self.assertEqual(len(self.s.history()["assets"]), 2)
        self.assertEqual(self.s.asset(first["id"])["sha256"], first["sha256"])
        self.assertEqual(self.adapter.submits, 1)

    def test_repair_missing_ready_asset_same_bytes(self):
        t = self.create()
        self.finish()
        a = self.s.history()["assets"][0]
        path, _ = self.s.file(a["id"])
        path.unlink()
        self.assertEqual(self.s.history()["tasks"][0]["status"], "downloading")
        self.finish()
        self.assertTrue(self.s.file(a["id"])[0].exists())
        self.assertEqual(self.adapter.submits, 1)

    def test_two_workers_cannot_submit_same_request(self):
        t = self.create()
        second = AudioService(self.tmp.name, self.adapter, self.env)
        entered = threading.Event()
        release = threading.Event()
        original = self.adapter.submit

        def slow(task):
            entered.set()
            release.wait(3)
            return original(task)

        with patch.object(self.adapter, "submit", side_effect=slow):
            thread = threading.Thread(target=self.s.tick)
            thread.start()
            self.assertTrue(entered.wait(2))
            second.tick()
            release.set()
            thread.join()
        self.finish()
        self.assertEqual(self.adapter.submits, 1)

    def test_certificate_failure_before_submit_is_definite_without_retry(self):
        self.adapter.submit_error = BeforeSubmissionError("tls_certificate")
        t = self.create()
        self.finish()
        result = self.s.get(t["id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_code"], "tls_certificate")
        self.assertIn("SSL_CERT_FILE", result["error"])
        self.assertEqual(self.adapter.submits, 1)

    def test_connection_failure_during_query_does_not_claim_generation_was_unsent(self):
        self.adapter.query_error = BeforeSubmissionError("dns_resolution")
        t = self.create()
        self.s.tick()
        self.s.tick()
        task = self.s.get(t["id"])
        self.assertEqual(task["status"], "generating")
        self.assertIn("原生成任务已提交", task["error"])
        self.assertIn("进度查询", task["error"])
        self.assertNotIn("生成请求尚未发送", task["error"])
        self.assertEqual(self.adapter.submits, 1)

    def test_connection_stage_codes_before_http(self):
        from urllib.parse import urlsplit

        target = (urlsplit("https://api.mureka.ai/test"), "8.8.8.8")
        for stage in ("dns_resolution", "tcp_connect", "tls_handshake"):
            with (
                patch(
                    "beatmate.audio_provider.public_target", return_value=target
                ) as resolve,
                patch("beatmate.audio_provider.socket.create_connection") as connect,
                patch("beatmate.audio_provider.ssl.create_default_context") as context,
            ):
                if stage == "dns_resolution":
                    resolve.side_effect = socket.gaierror()
                if stage == "tcp_connect":
                    connect.side_effect = TimeoutError()
                if stage == "tls_handshake":
                    context.return_value.wrap_socket.side_effect = ssl.SSLError()
                with self.assertRaises(BeforeSubmissionError) as caught:
                    https_request(
                        "POST",
                        "https://api.mureka.ai/test",
                        {},
                        b"{}",
                        1024,
                        ("api.mureka.ai",),
                    )
                self.assertEqual(caught.exception.code, stage)

    def test_definite_rejection_not_mock_success(self):
        self.adapter.submit_error = Rejected("authentication rejected", 401)
        t = self.create()
        self.finish()
        self.assertEqual(self.s.get(t["id"])["status"], "failed")
        self.assertEqual(self.s.get(t["id"])["provider_http_status"], 401)
        self.assertEqual(self.s.history()["assets"], [])

    def test_smoke_defaults_skip_without_network(self):
        from beatmate.smoke_mureka import main

        with patch(
            "beatmate.audio_provider.https_request",
            side_effect=AssertionError("network forbidden"),
        ):
            with patch("builtins.print") as out:
                self.assertEqual(main([]), 0)
                self.assertEqual(json.loads(out.call_args[0][0])["status"], "SKIPPED")


class ContractTests(unittest.TestCase):
    def test_exact_request_auth_model_n_and_query(self):
        calls = []

        def transport(*args):
            calls.append(args)
            return b'{"id":"123","status":"queued","model":"mureka-9"}'

        adapter = MurekaAdapter(
            {
                "MUREKA_API_KEY": "SENSITIVE",
                "MUREKA_MODEL": "mureka-9",
                "BEATMATE_AUDIO_LIVE": "1",
            },
            transport,
        )
        t = {
            "requested_model": "mureka-9",
            "brief": {"final_prompt": "150 seconds free instrumental"},
            "n": 1,
            "provider_task_id": "123",
        }
        adapter.submit(t)
        adapter.query(t)
        self.assertEqual(calls[0][1], "https://api.mureka.ai/v1/instrumental/generate")
        self.assertEqual(calls[0][2]["Authorization"], "Bearer SENSITIVE")
        self.assertEqual(
            json.loads(calls[0][3]),
            {
                "model": "mureka-9",
                "prompt": t["brief"]["final_prompt"],
                "n": 1,
                "stream": False,
            },
        )
        self.assertEqual(
            calls[1][0:2], ("GET", "https://api.mureka.ai/v1/instrumental/query/123")
        )
        adapter.download(
            {
                "wav_url": "https://cdn.mureka.ai/a.wav",
                "url": "https://cdn.mureka.ai/a.mp3",
            }
        )
        self.assertEqual(calls[2][1], "https://cdn.mureka.ai/a.wav")
        self.assertEqual(calls[2][2], {})

    def test_model_auto_and_region_rejected(self):
        for change in (
            {"MUREKA_MODEL": "auto"},
            {"MUREKA_BASE_URL": "https://api.other.com"},
        ):
            a = MurekaAdapter(
                dict(
                    MUREKA_API_KEY="x",
                    MUREKA_MODEL="mureka-9",
                    BEATMATE_AUDIO_LIVE="1",
                    **{},
                )
                | change
            )
            with self.assertRaises(AudioError):
                a.request("POST", "/v1/instrumental/generate", {})

    def test_ssrf_and_private_dns(self):
        for url in (
            "http://cdn.mureka.ai/a",
            "https://localhost/a",
            "https://cdn.mureka.ai:80/a",
            "https://x@cdn.mureka.ai/a",
            "file:///etc/passwd",
        ):
            with self.assertRaises(AudioError):
                public_target(url, ("cdn.mureka.ai",))
        with patch(
            "socket.getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
            ],
        ):
            with self.assertRaises(AudioError):
                public_target("https://cdn.mureka.ai/a", ("cdn.mureka.ai",))

    def test_transport_rejects_redirect_empty_and_incomplete_without_retry(self):
        for code, body, length in (
            (302, b"x", "1"),
            (200, b"", "0"),
            (200, b"x", "10"),
            (200, b"12345", "5"),
        ):
            response = MagicMock()
            response.status = code
            response.getheader.return_value = length
            response.read.return_value = body
            connection = MagicMock()
            connection.getresponse.return_value = response
            with patch(
                "beatmate.audio_provider.public_target",
                return_value=(
                    __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(
                        "https://api.mureka.ai/test"
                    ),
                    "8.8.8.8",
                ),
            ):
                with (
                    patch(
                        "beatmate.audio_provider.http.client.HTTPSConnection",
                        return_value=connection,
                    ),
                    patch("beatmate.audio_provider.socket.create_connection"),
                    patch("beatmate.audio_provider.ssl.create_default_context"),
                ):
                    with self.assertRaises(AudioError):
                        https_request(
                            "POST",
                            "https://api.mureka.ai/test",
                            {},
                            b"{}",
                            4,
                            ("api.mureka.ai",),
                        )
                    self.assertEqual(connection.request.call_count, 1)
                    connection.close.assert_called_once()

    def test_tls_failure_occurs_before_http_request(self):
        connection = MagicMock()
        context = MagicMock()
        context.wrap_socket.side_effect = ssl.SSLCertVerificationError(
            "sensitive transport detail"
        )
        with patch(
            "beatmate.audio_provider.public_target",
            return_value=(
                __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(
                    "https://api.mureka.ai/test"
                ),
                "8.8.8.8",
            ),
        ):
            with (
                patch(
                    "beatmate.audio_provider.http.client.HTTPSConnection",
                    return_value=connection,
                ),
                patch("beatmate.audio_provider.socket.create_connection"),
                patch(
                    "beatmate.audio_provider.ssl.create_default_context",
                    return_value=context,
                ),
            ):
                with self.assertRaises(BeforeSubmissionError) as caught:
                    https_request(
                        "POST",
                        "https://api.mureka.ai/test",
                        {},
                        b"{}",
                        1024,
                        ("api.mureka.ai",),
                    )
                self.assertEqual(caught.exception.code, "tls_certificate")
                self.assertNotIn("sensitive", str(caught.exception))
                connection.request.assert_not_called()

    def test_stem_waits_longer_and_timeout_is_diagnosed_without_retry(self):
        from urllib.parse import urlsplit

        for path, timeout in [("/v1/song/stem", 600), ("/v1/song/generate", None)]:
            connection = MagicMock()
            context = MagicMock()
            connection.getresponse.side_effect = TimeoutError("secret-url-key")
            url = "https://api.mureka.ai" + path
            with (
                patch(
                    "beatmate.audio_provider.public_target",
                    return_value=(urlsplit(url), "8.8.8.8"),
                ),
                patch(
                    "beatmate.audio_provider.http.client.HTTPSConnection",
                    return_value=connection,
                ),
                patch("beatmate.audio_provider.socket.create_connection"),
                patch(
                    "beatmate.audio_provider.ssl.create_default_context",
                    return_value=context,
                ),
            ):
                with self.assertRaises(AudioError) as caught:
                    https_request("POST", url, {}, b"{}", 1024, ("api.mureka.ai",))
                self.assertEqual(
                    str(caught.exception),
                    "Network timeout after request started; response not confirmed",
                )
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(connection.request.call_count, 1)
                if timeout:
                    context.wrap_socket.return_value.settimeout.assert_called_once_with(
                        timeout
                    )
                else:
                    context.wrap_socket.return_value.settimeout.assert_not_called()
                connection.close.assert_called_once()

    def test_invalid_provider_json_is_not_success(self):
        adapter = MurekaAdapter(
            {
                "MUREKA_API_KEY": "secret",
                "MUREKA_MODEL": "mureka-9",
                "BEATMATE_AUDIO_LIVE": "1",
            },
            lambda *a: b"<html>error</html>",
        )
        with self.assertRaises(AudioError):
            adapter.request("POST", "/v1/instrumental/generate", {})


class AudioHTTPTests(unittest.TestCase):
    def test_complete_flow_http_restart_and_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = AudioService(tmp, env={"BEATMATE_AUDIO_MAX_CANDIDATES": "2"})
            httpd = server(None, 0, audio=s, background_audio=False)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()

            def req(method, path, body=None, headers=None):
                c = http.client.HTTPConnection("127.0.0.1", httpd.server_port)
                c.request(
                    method,
                    path,
                    json.dumps(body) if body is not None else None,
                    headers or {"Content-Type": "application/json"},
                )
                r = c.getresponse()
                raw = r.read()
                code = r.status
                typ = r.getheader("Content-Type")
                c.close()
                return code, json.loads(raw) if typ == "application/json" else raw

            try:
                self.assertEqual(req("GET", "/")[0], 200)
                self.assertEqual(req("GET", "/audio/config")[1]["provider"], "mock")
                self.assertEqual(
                    req("POST", "/audio/budget", {"max_submissions": 4})[1][
                        "remaining_submissions"
                    ],
                    4,
                )
                self.assertEqual(req("GET", "/audio/budget")[1]["max_submissions"], 4)
                self.assertEqual(
                    req("POST", "/audio/budget", {"max_submissions": 100})[0], 200
                )
                self.assertEqual(
                    req(
                        "POST",
                        "/audio/budget",
                        {"max_submissions": 5},
                        headers={
                            "Content-Type": "application/json",
                            "Origin": "https://bad.example",
                        },
                    )[0],
                    403,
                )
                self.assertEqual(s.history()["tasks"], [])
                self.assertEqual(
                    req("POST", "/audio/preview", {"raw_text": "150秒，64小节"})[0], 200
                )
                body = {"raw_text": "emo", "request_id": uuid.uuid4().hex, "n": 2}
                code, t = req("POST", "/audio/tasks", body)
                self.assertEqual(code, 202)
                self.assertEqual(req("POST", "/audio/tasks", body)[1]["id"], t["id"])
                for _ in range(4):
                    s.tick()
                h = req("GET", "/audio/history")[1]
                self.assertEqual(len(h["assets"]), 2)
                a = h["assets"][0]
                for suffix in ("", "/download"):
                    code, data = req("GET", "/audio/assets/" + a["id"] + suffix)
                    self.assertEqual(code, 200)
                    self.assertTrue(data.startswith(b"RIFF"))
                code, data = req(
                    "GET", "/audio/assets/" + a["id"], headers={"Range": "bytes=0-3"}
                )
                self.assertEqual((code, data), (206, b"RIFF"))
                self.assertEqual(
                    req(
                        "POST",
                        "/audio/projects/" + t["project_id"] + "/select",
                        {"asset_id": a["id"]},
                    )[0],
                    200,
                )
                again = AudioService(tmp, env={})
                self.assertEqual(
                    again.history()["projects"][0]["selected_audio_asset_id"], a["id"]
                )
                self.assertEqual(
                    req(
                        "GET",
                        "/audio/history",
                        headers={"Origin": "https://bad.example"},
                    )[0],
                    403,
                )
                self.assertEqual(
                    req(
                        "POST",
                        "/audio/preview",
                        {"raw_text": "x"},
                        headers={
                            "Content-Type": "application/json",
                            "Origin": f"http://127.0.0.1:{httpd.server_port}",
                        },
                    )[0],
                    200,
                )
                self.assertEqual(
                    req("GET", "/audio/history", headers={"Host": "bad.example"})[0],
                    403,
                )
                self.assertEqual(
                    req("POST", "/audio/tasks", dict(body, key="secret"))[0], 400
                )
                self.assertIn(
                    "lyrics", req("GET", "/audio/config")[1]["creation_modes"]
                )
                lyric_body = {
                    "raw_text": "",
                    "creation_mode": "lyrics",
                    "lyrics": "[主歌]\n灯还亮着",
                }
                code, preview = req("POST", "/audio/preview", lyric_body)
                self.assertEqual(code, 200)
                self.assertEqual(preview["lyrics"], lyric_body["lyrics"])
                self.assertEqual(
                    req(
                        "POST",
                        "/audio/tasks",
                        dict(lyric_body, request_id=uuid.uuid4().hex),
                    )[0],
                    400,
                )
                code, task = req(
                    "POST",
                    "/audio/tasks",
                    dict(lyric_body, request_id=uuid.uuid4().hex, reviewed=True),
                )
                self.assertEqual(code, 202)
                self.assertEqual(task["brief"], preview)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join()
                s.close()
