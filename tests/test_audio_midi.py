"""MIDI export uses existing separation results, never another paid POST."""

import http.client
import io
import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest
import uuid
import zipfile
from unittest.mock import patch
from beatmate.api import server
from beatmate.audio import AudioService, sha
from beatmate.audio_midi import midi_archive
from beatmate.audio_provider import AudioError, MockAudioAdapter, MurekaAdapter


class MidiAdapter(MockAudioAdapter):
    def __init__(self):
        self.separations = 0
        self.downloads = 0
        self.error = None
        self.no_midi = False
        self.audio_error = False

    def separate(self, choice):
        self.separations += 1
        result = super().separate(choice)
        if self.no_midi:
            result.pop("midi_zip_url")
        return result

    def download_midi(self, job):
        self.downloads += 1
        if self.error:
            raise self.error
        return super().download_midi(job)

    def download_stems(self, job):
        if self.audio_error:
            raise AudioError("test")
        return super().download_stems(job)


class MidiExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.adapter = MidiAdapter()
        self.s = AudioService(self.tmp.name, self.adapter, env={})
        self.addCleanup(self.s.close)

    def generate(self):
        self.s.create(
            "温暖吉他",
            uuid.uuid4().hex,
            creation_mode="song_stems",
            lyrics="[主歌]\n测试歌词",
            reviewed=True,
        )
        for _ in range(4):
            self.s.tick()
        return self.s.history()["stems"][0]

    def test_exports_original_zip_and_individual_midi_survive_restart(self):
        job = self.generate()
        self.assertEqual(job["midi_status"], "ready")
        self.assertEqual(len(job["midi_files"]), 2)
        self.assertNotIn("midi_zip_url", job)
        self.assertNotIn("zip_url", job)
        original = MockAudioAdapter().download_midi(job)
        files = dict(midi_archive(original))
        # ZIP timestamps can vary; downloaded local hash is the recorded provider ZIP hash.
        self.assertEqual(
            sha(self.s.midi_file(job["id"]).read_bytes()), job["midi_zip_sha256"]
        )
        for f in job["midi_files"]:
            self.assertEqual(
                self.s.midi_file(job["id"], f["id"]).read_bytes(), files[f["name"]]
            )
        again = AudioService(self.tmp.name, self.adapter, env={})
        self.addCleanup(again.close)
        again.tick()
        self.assertEqual(again.history()["stems"][0]["midi_status"], "ready")
        self.assertEqual((self.adapter.separations, self.adapter.downloads), (1, 1))
        self.assertEqual(len(again.history()["assets"]), 3)

    def test_retry_download_does_not_resubmit_or_block_audio(self):
        self.adapter.error = RuntimeError("secret-url")
        job = self.generate()
        self.assertEqual(job["status"], "ready")
        self.assertEqual(job["midi_status"], "downloading")
        self.assertNotIn("secret-url", json.dumps(job))
        self.assertEqual(len(self.s.history()["assets"]), 3)
        self.adapter.error = None
        self.s.retry_midi(job["id"])
        self.s.tick()
        self.assertEqual(self.s.history()["stems"][0]["midi_status"], "ready")
        self.assertEqual(self.adapter.separations, 1)

    def test_missing_and_corrupt_files_restore_from_saved_zip(self):
        job = self.generate()
        files = job["midi_files"]
        self.s.midi_file(job["id"], files[0]["id"]).unlink()
        self.s.midi_file(job["id"], files[1]["id"]).write_bytes(b"broken")
        self.assertEqual(self.s.history()["stems"][0]["midi_status"], "downloading")
        self.s.tick()
        for f in files:
            self.assertTrue(
                self.s.midi_file(job["id"], f["id"]).read_bytes().startswith(b"MThd")
            )
        self.assertEqual((self.adapter.separations, self.adapter.downloads), (1, 1))

    def test_no_midi_and_legacy_results_do_not_trigger_paid_calls(self):
        self.adapter.no_midi = True
        job = self.generate()
        self.assertEqual(job["midi_status"], "unavailable")
        self.assertEqual(self.adapter.downloads, 0)
        with self.assertRaises(ValueError):
            self.s.retry_midi(job["id"])
        stored = self.s._midi_job(job["id"])
        stored.pop("midi_status")
        stored.pop("midi_error")
        self.s._save_stem(stored)
        self.s.tick()
        job = self.s.history()["stems"][0]
        self.assertIn("旧分离结果", job["midi_error"])
        self.assertEqual(self.adapter.separations, 1)

    def test_midi_succeeds_even_if_audio_archive_fails(self):
        self.adapter.audio_error = True
        job = self.generate()
        self.assertEqual(job["status"], "downloading")
        self.assertEqual(job["midi_status"], "ready")

    def test_interrupted_midi_download_recovers_without_new_separation(self):
        self.adapter.error = SystemExit()
        with self.assertRaises(SystemExit):
            self.generate()
        self.adapter.error = None
        self.s.tick()
        self.assertEqual(self.s.history()["stems"][0]["midi_status"], "ready")
        self.assertEqual(self.adapter.separations, 1)

    def test_http_downloads_and_wrong_candidate_id(self):
        job = self.generate()
        httpd = server(None, 0, audio=self.s, background_audio=False)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def get(path):
            c = http.client.HTTPConnection("127.0.0.1", httpd.server_port)
            c.request("GET", path)
            r = c.getresponse()
            data = r.read()
            result = (
                r.status,
                data,
                r.getheader("Content-Type"),
                r.getheader("Content-Disposition"),
            )
            c.close()
            return result

        try:
            prefix = "/audio/stems/" + job["id"] + "/midi"
            code, data, mime, filename = get(prefix + "/download")
            self.assertEqual(code, 200)
            self.assertEqual(mime, "application/zip")
            self.assertIn(".zip", filename)
            self.assertEqual(len(midi_archive(data)), 2)
            for f in job["midi_files"]:
                code, data, mime, filename = get(prefix + "/" + f["id"] + "/download")
                self.assertEqual(code, 200)
                self.assertEqual(mime, "audio/midi")
                self.assertIn(".mid", filename)
                self.assertEqual(sha(data), f["sha256"])
            self.assertEqual(get(prefix + "/" + uuid.uuid4().hex + "/download")[0], 404)
            self.assertEqual(
                get("/audio/stems/" + uuid.uuid4().hex + "/midi/download")[0], 404
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()

    def test_provider_download_uses_allowlist_without_api_credentials(self):
        env = {
            "MUREKA_API_KEY": "secret",
            "MUREKA_MODEL": "mureka-9",
            "BEATMATE_AUDIO_LIVE": "1",
        }
        calls = []

        def transport(*args):
            calls.append(args)
            return b"zip"

        adapter = MurekaAdapter(env, transport=transport)
        adapter.download_midi({"midi_zip_url": "https://cdn.mureka.ai/midi.zip"})
        self.assertEqual(
            calls[0][:4], ("GET", "https://cdn.mureka.ai/midi.zip", {}, None)
        )
        self.assertNotIn("secret", repr(calls))
        self.assertIn("cdn.mureka.ai", calls[0][-1])


class MidiArchiveTests(unittest.TestCase):
    def test_bounds_paths_and_midi_validation(self):
        valid = midi_archive(MockAudioAdapter().download_midi({}))[0][1]

        def pack(entries):
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as z:
                for name, data in entries:
                    z.writestr(name, data)
            return out.getvalue()

        self.assertEqual(
            midi_archive(pack([("tracks/piano.MIDI", valid)]))[0][1], valid
        )
        for entries in [
            [],
            [("../x.mid", valid)],
            [("/x.mid", valid)],
            [("x\\y.mid", valid)],
            [("script.exe", b"bad")],
            [("a.mid", valid), ("A.mid", valid)],
            [("a.mid", b"<html>")],
            [("a.mid", valid[:-1])],
            [("a.mid", valid + b"extra")],
        ]:
            with self.subTest(entries=[name for name, _ in entries]):
                with self.assertRaises(AudioError):
                    midi_archive(pack(entries))
        symlink = zipfile.ZipInfo("x.mid")
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(AudioError):
            midi_archive(pack([(symlink, valid)]))
        with patch("beatmate.audio_midi.MAX_MIDI_TOTAL", 1):
            with self.assertRaises(AudioError):
                midi_archive(pack([("a.mid", valid)]))
        with patch("beatmate.audio_midi.MAX_MIDI_FILE", 1):
            with self.assertRaises(AudioError):
                midi_archive(pack([("a.mid", valid)]))
        with self.assertRaises(AudioError):
            midi_archive(pack([(str(i) + ".mid", valid) for i in range(33)]))
