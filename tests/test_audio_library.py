"""Library mutations preserve paid provenance and avoid restoring purged files."""

import fcntl
import http.client
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from beatmate.audio import AudioService
from beatmate.audio_provider import MockAudioAdapter, AudioError
from beatmate.api import server
from beatmate.service import ConflictError


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = AudioService(
            self.tmp.name, MockAudioAdapter(), {"BEATMATE_AUDIO_MAX_CANDIDATES": "2"}
        )
        self.addCleanup(self.s.close)

    def create(self, **kw):
        task = self.s.create("夜晚的木吉他", uuid.uuid4().hex, reviewed=True, **kw)
        for _ in range(4):
            self.s.tick()
        return task, [
            a
            for a in self.s.history()["assets"]
            if a["task_id"] == task["id"] and a["id"] == a["source_audio_asset_id"]
        ]

    def test_titles_are_independent_and_idempotent(self):
        rid = uuid.uuid4().hex
        t = self.s.create("保留原话", rid, title="音乐名称")
        self.assertEqual(t["title"], "音乐名称")
        self.assertEqual(t["brief"]["final_prompt"], "保留原话")
        self.assertEqual(
            self.s.create("保留原话", rid, title="音乐名称")["id"], t["id"]
        )
        with self.assertRaises(ConflictError):
            self.s.create("保留原话", rid, title="另一名称")
        legacy = uuid.uuid4().hex
        first = self.s.create("旧请求", legacy)
        self.assertEqual(self.s.create("旧请求", legacy, title="")["id"], first["id"])
        for title in ("字" * 51, "hello\nworld", None):
            with self.assertRaises(ValueError):
                self.s.create("音乐", uuid.uuid4().hex, title=title)

    def test_metadata_restore_and_restart_preserve_original(self):
        task, assets = self.create(title="原名", n=2)
        aid = assets[0]["id"]
        self.s.select(task["project_id"], aid)
        self.s.library_update([aid], "rename", title="新名")
        self.assertTrue(self.s.history()["library"][0]["favorite"])
        self.s.library_update([aid], "favorite", favorite=True)
        self.s.library_update([aid], "trash")
        self.assertTrue(self.s.history()["library"][0]["deleted_at"])
        self.assertEqual(self.s.get(task["id"])["title"], "原名")
        self.s.library_update([aid], "restore")
        other = AudioService(self.tmp.name, MockAudioAdapter(), {})
        self.addCleanup(other.close)
        meta = other.history()["library"][0]
        self.assertEqual(meta["title"], "新名")
        self.assertTrue(meta["favorite"])
        self.assertIsNone(meta["deleted_at"])
        self.assertEqual(len(other.history()["assets"]), 2)

    def test_purge_stems_midi_and_no_resurrection(self):
        task, assets = self.create(
            title="歌词歌", creation_mode="song_stems", lyrics="[主歌]\n深夜的灯还亮着"
        )
        aid = assets[0]["id"]
        h = self.s.history()
        job = h["stems"][0]
        paths = [self.s.file(a["id"])[0] for a in h["assets"]]
        paths += [
            self.s.midi_file(aid),
            *[self.s.midi_file(aid, f["id"]) for f in job["midi_files"]],
        ]
        before = self.s.budget()
        self.s.select(task["project_id"], aid)
        with self.assertRaises(ValueError):
            self.s.library_update([aid], "purge")
        self.s.library_update([aid], "trash")
        self.s.library_update([aid], "purge")
        self.assertTrue(all(not p.exists() for p in paths))
        for _ in range(3):
            self.s.tick()
        self.assertEqual(self.s.history()["assets"], [])
        self.assertEqual(self.s.history()["tasks"], [])
        self.assertEqual(self.s.history()["stems"], [])
        self.assertEqual(self.s.budget(), before)
        self.assertIsNone(self.s.history()["projects"][0]["selected_audio_asset_id"])
        with self.assertRaises(AudioError):
            self.s.file(aid)
        with self.assertRaises(AudioError):
            self.s.midi_file(aid)
        with self.s.connect() as c:
            self.assertEqual(
                c.execute("SELECT count(*) FROM audio_requests").fetchone()[0], 1
            )
            self.assertEqual(
                c.execute("SELECT count(*) FROM audio_results").fetchone()[0], 3
            )

    def test_shared_bytes_kept_until_last_reference_and_partial_candidate_purge(self):
        first, aa = self.create(n=2)
        second, bb = self.create(n=2)
        path = self.s.file(aa[0]["id"])[0]
        self.assertEqual(path, self.s.file(bb[0]["id"])[0])
        self.s.library_update([aa[0]["id"]], "trash")
        self.s.library_update([aa[0]["id"]], "purge")
        self.s.tick()
        self.assertTrue(path.exists())
        self.assertEqual(len(self.s.history()["assets"]), 3)
        self.assertIn(first["id"], [t["id"] for t in self.s.history()["tasks"]])
        self.s.library_update([bb[0]["id"]], "trash")
        self.s.library_update([bb[0]["id"]], "purge")
        self.assertFalse(path.exists())

    def test_transaction_and_worker_lock(self):
        _, assets = self.create()
        aid = assets[0]["id"]
        with self.assertRaises(KeyError):
            self.s.library_update([aid, uuid.uuid4().hex], "trash")
        self.assertEqual(self.s.history()["library"], [])
        self.s.library_update([aid], "trash")
        with (self.s.root / "worker.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(AudioError):
                self.s.library_update([aid], "purge")
        self.assertEqual(len(self.s.history()["assets"]), 1)

    def test_failed_task_trash_restore_preserves_billing_and_uncertain_records(self):
        from beatmate.audio_provider import BeforeSubmissionError
        from unittest.mock import patch

        with patch.object(
            self.s.adapter, "submit", side_effect=BeforeSubmissionError("tcp_connect")
        ):
            failed, _ = self.create()
        tid = failed["id"]
        before = self.s.budget()
        self.s.library_update([tid], "trash_failed")
        self.assertTrue(
            next(t for t in self.s.history()["tasks"] if t["id"] == tid)["deleted_at"]
        )
        self.s.library_update([tid], "restore_failed")
        self.assertIsNone(
            next(t for t in self.s.history()["tasks"] if t["id"] == tid)["deleted_at"]
        )
        self.assertEqual(self.s.budget(), before)
        with patch.object(
            self.s.adapter, "submit", side_effect=AudioError("ambiguous")
        ):
            uncertain, _ = self.create()
        with self.assertRaises(ValueError):
            self.s.library_update([uncertain["id"]], "trash_failed")
        self.s.library_update([uncertain["id"]], "trash_task")
        self.assertTrue(
            next(t for t in self.s.history()["tasks"] if t["id"] == uncertain["id"])[
                "deleted_at"
            ]
        )
        self.s.library_update([uncertain["id"]], "restore_task")
        self.assertIsNone(
            next(t for t in self.s.history()["tasks"] if t["id"] == uncertain["id"])[
                "deleted_at"
            ]
        )
        self.s.tick()
        self.assertEqual(self.s.get(uncertain["id"])["status"], "uncertain")
        ready, _ = self.create()
        with self.assertRaises(ValueError):
            self.s.library_update([ready["id"]], "trash_failed")
        with self.assertRaises(ValueError):
            self.s.library_update([ready["id"]], "trash_task")
        with self.s.connect() as c:
            self.assertEqual(
                c.execute("SELECT count(*) FROM audio_requests").fetchone()[0], 3
            )

    def test_http_title_static_library_and_origin(self):
        httpd = server(None, 0, audio=self.s, background_audio=False)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def request(method, path, body=None, origin=None):
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port)
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            conn.request(method, path, json.dumps(body) if body else None, headers)
            r = conn.getresponse()
            raw = r.read()
            status = r.status
            conn.close()
            return status, raw

        try:
            for path in (
                "/",
                "/audio/ui/studio.css",
                "/audio/ui/studio.js",
                "/audio/ui/cover.png",
            ):
                self.assertEqual(request("GET", path)[0], 200)
            code, raw = request(
                "POST",
                "/audio/tasks",
                dict(raw_text="音乐描述", title="新标题", request_id=uuid.uuid4().hex),
            )
            self.assertEqual(code, 202)
            self.assertEqual(json.loads(raw)["title"], "新标题")
            for _ in range(4):
                self.s.tick()
            aid = self.s.history()["assets"][0]["id"]
            body = dict(ids=[aid], action="trash")
            self.assertEqual(
                request("POST", "/audio/library", body, "https://evil.example")[0], 403
            )
            self.assertEqual(request("POST", "/audio/library", body)[0], 200)
            self.assertTrue(self.s.history()["library"][0]["deleted_at"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()
