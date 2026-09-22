import copy
from dataclasses import replace
import hashlib
import http.client
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

import mido
from beatmate.api import server
from beatmate.creative import (
    Arrangement,
    CreativeSpec,
    CreativeEditPlan,
    mock_creative,
    creative_result,
    GENERATOR_VERSION,
)
from beatmate.creative_demo import A_TEXT, B_TEXT, C_TEXT, generate_demos
from beatmate.deepseek import DeepSeekPlanner
from beatmate.generator import generate
from beatmate.generator_v2 import generate_creative, reproduce, SCALES, PROGRESSIONS
from beatmate.llm import OpenAIPlanner, PlannerError
from beatmate.model import (
    BeatSpec,
    EditPlan,
    BAR,
    KEYS,
    TRACK_IDS,
    digest,
    validate_tracks,
)
from beatmate.render import midi_bytes, preview_bytes
from beatmate.service import Service, ConflictError


def llm_response(data):
    return dict(
        status="completed",
        output=[
            dict(
                type="message",
                content=[dict(type="output_text", text=json.dumps(data))],
            )
        ],
    )


class CreativeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = Service(Path(self.tmp.name) / "db")
        for target in ("urllib.request.urlopen", "urllib.request.OpenerDirector.open"):
            guard = patch(
                target,
                side_effect=AssertionError("Offline suite cannot use provider network"),
            )
            guard.start()
            self.addCleanup(guard.stop)

    def count(self):
        with self.service.connect() as c:
            return c.execute("SELECT COUNT(*) FROM versions").fetchone()[0]

    def create_a(self):
        return self.service.create_creative_project(
            A_TEXT, protected_tracks=list(TRACK_IDS)
        )


class BriefTests(CreativeCase):
    def test_explicit_provenance_and_assumptions(self):
        result = mock_creative(
            "凌晨一个人回家，想念一个人，XXXTentacion类型的beat。76 BPM，8小节，D小调，seed 12。"
        )
        brief = result["brief"]
        self.assertEqual(result["spec"]["bpm"], 76)
        self.assertEqual(result["spec"]["key"], "D")
        self.assertEqual(result["spec"]["seed"], 12)
        self.assertIn("凌晨一个人回家", brief["explicit"]["scene"])
        for entries in brief["explicit"].values():
            for quote in entries:
                self.assertIn(quote, brief["raw_text"])
        assumptions = {x["parameter"]: x for x in brief["assumptions"]}
        self.assertNotIn("bpm", assumptions)
        self.assertIn("arrangement.timbre_hint", assumptions)
        self.assertTrue(
            all(x["source"] == "system_inference" for x in brief["assumptions"])
        )
        self.assertTrue(brief["reference_interpretations"])

    def test_negative_constraints_change_notes(self):
        dense = mock_creative("伤感但有推动力，旋律适中。")
        sparse = mock_creative("伤感但有推动力，不要太密，不要太压抑，不要很吵。")
        a = sparse["spec"]["arrangement"]
        self.assertEqual(
            (
                a["melody_density"],
                a["drum_density"],
                a["harmony"],
                a["bass_presence"],
                a["drum_velocity"],
            ),
            ("sparse", "sparse", "resolving", "low", "soft"),
        )
        self.assertNotEqual(
            generate_creative(CreativeSpec.parse(dense["spec"])),
            generate_creative(CreativeSpec.parse(sparse["spec"])),
        )
        self.assertTrue(sparse["brief"]["explicit"]["prohibitions"])

    def test_conflicting_constraints_not_silently_repaired(self):
        for text in (
            "80 BPM，90 BPM",
            "0小节",
            "3/4拍",
            "约2分30秒",
            "不要太密，旋律适中",
            "不要分解和弦",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.service.create_creative_project(text)
        self.assertEqual(self.count(), 0)
        data = CreativeSpec(bpm=90).to_dict()
        with self.assertRaises(ValueError):
            self.service.create_creative_project("76 BPM", spec=data)
        self.assertEqual(self.count(), 0)

    def test_no_fixed_artist_template(self):
        a = mock_creative("XXXTentacion类型，伤感、克制，不要太密。")
        b = mock_creative("XXXTentacion类型，伤感但有推动力，不要很吵。")
        self.assertNotEqual(a["spec"]["arrangement"], b["spec"]["arrangement"])
        self.assertNotEqual(
            generate_creative(CreativeSpec.parse(a["spec"])),
            generate_creative(CreativeSpec.parse(b["spec"])),
        )

    def test_unknown_description_is_disclosed(self):
        result = mock_creative("像一只漂浮在火山口的怀表")
        self.assertIn("像一只漂浮在火山口的怀表", result["brief"]["explicit"]["other"])
        self.assertTrue(any("未识别" in x for x in result["brief"]["limitations"]))
        self.assertTrue(result["brief"]["assumptions"])

    def test_relative_creation_uses_trusted_reference(self):
        a = self.create_a()
        result = self.service.parse_creative(
            "带一点不甘，比刚才更有推动力，但不要很吵。",
            a["project_id"],
            a["version_id"],
        )
        self.assertEqual(result["spec"]["bpm"], 76)
        self.assertEqual(result["spec"]["bars"], 16)
        self.assertEqual(result["spec"]["arrangement"]["drum_density"], "moderate")
        self.assertEqual(result["spec"]["arrangement"]["drum_velocity"], "soft")
        self.assertTrue(
            all("参考项目" in x["reason"] for x in result["brief"]["assumptions"])
        )
        self.assertEqual(self.count(), 1)

    def test_llm_constraints_and_schema_no_snapshots(self):
        calls = []
        expected = mock_creative("76 BPM，8小节，不要太密")["spec"]
        planner = DeepSeekPlanner(
            environ={"DEEPSEEK_API_KEY": "test-only"},
            transport=lambda payload: (calls.append(payload) or llm_response(expected)),
        )
        self.service.planner = planner
        parsed = self.service.parse_creative("76 BPM，8小节，不要太密")
        self.assertEqual(parsed["spec"], expected)
        self.assertTrue(calls[0]["text"]["format"]["strict"])
        self.assertNotIn(
            "creative_brief", calls[0]["text"]["format"]["schema"]["properties"]
        )
        self.assertNotIn("tools", calls[0])
        self.assertNotIn("test-only", json.dumps(parsed))
        for bad in [
            dict(expected, bpm=90),
            dict(expected, assumptions=["user wants 90"]),
            dict(expected, tracks=[]),
        ]:
            self.service.planner = DeepSeekPlanner(
                environ={"DEEPSEEK_API_KEY": "test-only"},
                transport=lambda _, bad=bad: llm_response(bad),
            )
            with self.assertRaises(PlannerError):
                self.service.create_creative_project("76 BPM，8小节，不要太密")
        self.assertEqual(self.count(), 0)

    def test_llm_does_not_override_prohibitions(self):
        data = CreativeSpec(
            arrangement=Arrangement(drum_velocity="strong", drum_density="moderate")
        ).to_dict()
        planner = OpenAIPlanner("test-only", "test-model", lambda _: llm_response(data))
        with self.assertRaises(PlannerError):
            planner.parse_creative("不要很吵，不要太密")


class GeneratorTests(CreativeCase):
    def test_every_active_field_changes_real_structure(self):
        baseline = CreativeSpec(
            bars=8,
            seed=99,
            arrangement=Arrangement(vocal_space="balanced", chord_style="block"),
        )
        original = generate_creative(baseline)
        cases = {
            "harmony": ("descending", {"chords", "bass", "melody"}),
            "chord_style": ("arpeggio", {"chords"}),
            "melody_density": ("sparse", {"melody"}),
            "melody_variation": ("moderate", {"melody"}),
            "drum_density": ("moderate", {"kick", "snare", "hihat"}),
            "drum_velocity": ("strong", {"kick", "snare", "hihat"}),
            "section_variation": ("lift", {"kick", "snare", "hihat", "melody"}),
            "bass_presence": ("medium", {"bass"}),
            "vocal_space": ("roomy", {"chords", "melody"}),
        }
        for field, (value, targets) in cases.items():
            with self.subTest(field=field):
                changed = generate_creative(
                    replace(
                        baseline,
                        arrangement=replace(baseline.arrangement, **{field: value}),
                    )
                )
                actual = {a["id"] for a, b in zip(original, changed) if a != b}
                self.assertTrue(actual)
                self.assertTrue(actual <= targets, (field, actual))
                if field in (
                    "melody_density",
                    "drum_density",
                    "bass_presence",
                    "vocal_space",
                ):
                    self.assertTrue(
                        any(
                            len(a["notes"]) != len(b["notes"])
                            for a, b in zip(original, changed)
                        )
                    )
                if field == "drum_velocity":
                    self.assertEqual(
                        [
                            [
                                {k: v for k, v in n.items() if k != "velocity"}
                                for n in t["notes"]
                            ]
                            for t in original
                        ],
                        [
                            [
                                {k: v for k, v in n.items() if k != "velocity"}
                                for n in t["notes"]
                            ]
                            for t in changed
                        ],
                    )

    def test_advice_only_timbre_does_not_claim_rendering(self):
        spec = CreativeSpec()
        a = generate_creative(spec)
        b = generate_creative(
            replace(
                spec, arrangement=replace(spec.arrangement, timbre_hint="nylon_guitar")
            )
        )
        self.assertEqual(a, b)
        v = self.service.create_creative_project("吉他，伤感")
        hint = next(
            x
            for x in v["explanation"]["applied_choices"]
            if x["parameter"] == "timbre_hint"
        )
        self.assertEqual(hint["status"], "advice_only")

    def test_seed_reproduction_motif_scale_range_and_optional_track(self):
        s = CreativeSpec(bars=8, seed=10)
        tracks = generate_creative(s)
        self.assertEqual(tracks, generate_creative(s))
        self.assertNotEqual(tracks, generate_creative(replace(s, seed=11)))
        melody = tracks[-1]["notes"]
        for n in melody:
            self.assertTrue(60 <= n["pitch"] <= 83)
            self.assertIn((n["pitch"] - KEYS.index(s.key)) % 12, SCALES[s.mode])
            self.assertLessEqual(n["start_tick"] % BAR + n["duration_tick"], BAR)
        # Same chord and phrase phase every four bars -> exact repeated phrase.
        shape = lambda bar: [
            (n["pitch"], n["start_tick"] % BAR, n["duration_tick"])
            for n in melody
            if n["start_tick"] // BAR == bar
        ]
        self.assertEqual(shape(0), shape(4))
        for bar in range(s.bars):
            first = next(n for n in melody if n["start_tick"] // BAR == bar)
            degree = PROGRESSIONS[s.arrangement.harmony][bar % 4]
            chord_tones = {
                (KEYS.index(s.key) + SCALES[s.mode][(degree + i) % 7]) % 12
                for i in (0, 2, 4)
            }
            self.assertIn(first["pitch"] % 12, chord_tones)
        without = replace(s, arrangement=replace(s.arrangement, melody_enabled=False))
        self.assertEqual([t["id"] for t in generate_creative(without)], list(TRACK_IDS))

    def test_extreme_specs_valid(self):
        for key in KEYS:
            for mode in ("major", "minor"):
                s = CreativeSpec(
                    bars=32,
                    bpm=220,
                    key=key,
                    mode=mode,
                    swing=0.45,
                    arrangement=Arrangement(
                        drum_density="moderate",
                        drum_velocity="strong",
                        section_variation="lift",
                        melody_variation="moderate",
                    ),
                )
                validate_tracks(generate_creative(s), s, expected_track_ids=s.track_ids)

    def test_six_track_midi_roundtrip_and_reference_wav(self):
        v = self.service.create_creative_project("76 BPM，2小节，伤感")
        midi = mido.MidiFile(file=io.BytesIO(midi_bytes(v)))
        self.assertEqual(
            (midi.type, midi.ticks_per_beat, len(midi.tracks)), (1, 480, 7)
        )
        self.assertEqual(midi.tracks[-1].name, "melody")
        for track, source in zip(midi.tracks[1:], v["tracks"]):
            tick, active, notes = 0, {}, []
            for m in track:
                tick += m.time
                if m.type == "note_on" and m.velocity:
                    self.assertNotIn((m.channel, m.note), active)
                    active[(m.channel, m.note)] = (tick, m.velocity)
                elif m.type == "note_off":
                    start, vel = active.pop((m.channel, m.note))
                    notes.append((m.channel, m.note, start, tick - start, vel))
            self.assertFalse(active)
            self.assertEqual(
                sorted(notes),
                sorted(
                    (
                        source["channel"],
                        n["pitch"],
                        n["start_tick"],
                        n["duration_tick"],
                        n["velocity"],
                    )
                    for n in source["notes"]
                ),
            )
            self.assertEqual(tick, 2 * BAR)
        with wave.open(io.BytesIO(preview_bytes(v)), "rb") as wav:
            self.assertAlmostEqual(
                wav.getnframes() / wav.getframerate(), 2 * 4 * 60 / 76, places=4
            )
            self.assertGreater(len(set(wav.readframes(wav.getnframes()))), 1)


class CreativeEditingTests(CreativeCase):
    def test_thinning_scope_anchors_protection_and_reproduction(self):
        before = self.create_a()
        proposal = self.service.plan_project_edit(before["project_id"], C_TEXT)
        self.assertEqual(proposal["status"], "ready")
        self.assertEqual(
            proposal["plan"],
            CreativeEditPlan("melody", 9, 16, "thin_notes", 50).to_dict(),
        )
        after = self.service.edit_project(
            before["project_id"],
            proposal["base_version"],
            proposal["plan"],
            proposal["protected_tracks"],
        )
        for a, b in zip(before["tracks"], after["tracks"]):
            if a["id"] != "melody":
                self.assertEqual(a, b)
            else:
                self.assertLess(len(b["notes"]), len(a["notes"]))
                self.assertEqual(
                    [n for n in a["notes"] if n["start_tick"] < 8 * BAR],
                    [n for n in b["notes"] if n["start_tick"] < 8 * BAR],
                )
                for bar in range(8, 16):
                    first = next(n for n in a["notes"] if n["start_tick"] // BAR == bar)
                    self.assertIn(first, b["notes"])
                self.assertTrue(all(n in a["notes"] for n in b["notes"]))
        self.assertEqual(after["spec"], before["spec"])
        self.assertEqual(after["parent_id"], before["version_id"])
        self.assertEqual(reproduce(after), after["tracks"])
        self.assertEqual(
            self.service.get_version(before["project_id"], before["version_id"]), before
        )
        self.assertTrue(
            all(
                h["before"] == h["after"]
                for h in after["audit"]["protected_hashes"].values()
            )
        )
        self.assertEqual(self.count(), 2)
        with self.assertRaises(ConflictError):
            self.service.edit_project(
                before["project_id"], before["version_id"], proposal["plan"]
            )

    def test_ambiguous_and_unsupported_edit_only_suggests(self):
        v = self.create_a()
        for text in ("让旋律更克制", "更温暖", "让第9-16小节旋律更空灵", "更有推动力"):
            result = self.service.plan_project_edit(v["project_id"], text)
            self.assertEqual(result["status"], "suggestion")
            self.assertIsNone(result["plan"])
        self.assertEqual(self.count(), 1)

    def test_explicit_scope_wins_and_missing_scope_not_completed(self):
        v = self.create_a()
        reordered = self.service.plan_project_edit(
            v["project_id"], "鼓和贝斯不要动，只让后半段旋律更克制。"
        )
        self.assertEqual(reordered["status"], "ready")
        result = self.service.plan_project_edit(
            v["project_id"],
            "只让后半段旋律更克制",
            track_id="melody",
            start_bar=3,
            end_bar=4,
        )
        self.assertEqual(
            (result["plan"]["start_bar"], result["plan"]["end_bar"]), (3, 4)
        )
        self.assertEqual(result["scope_source"], "caller")
        result = self.service.plan_project_edit(
            v["project_id"], C_TEXT, track_id="melody"
        )
        self.assertEqual(result["status"], "suggestion")

    def test_missing_protected_invalid_and_boundary_fail_without_version(self):
        v = self.create_a()
        plans = [
            CreativeEditPlan("melody", 1, 17, "thin_notes", 50).to_dict(),
            dict(
                track_id="hihat",
                start_bar=1,
                end_bar=2,
                operation="thin_notes",
                value=50,
            ),
            dict(
                track_id="melody",
                start_bar=1,
                end_bar=2,
                operation="thin_notes",
                value=30,
            ),
        ]
        for plan in plans:
            with self.assertRaises(ValueError):
                self.service.edit_project(v["project_id"], v["version_id"], plan)
        plan = CreativeEditPlan("melody", 2, 2, "thin_notes", 50).to_dict()
        with self.assertRaises(ValueError):
            self.service.edit_project(
                v["project_id"], v["version_id"], plan, protected_tracks=["melody"]
            )
        crossing = copy.deepcopy(v)
        crossing["tracks"][-1]["notes"][0]["duration_tick"] = BAR + 60
        with (
            patch.object(self.service, "get_version", return_value=crossing),
            self.assertRaisesRegex(ValueError, "boundary-crossing"),
        ):
            self.service.edit_project(v["project_id"], v["version_id"], plan)
        self.assertEqual(self.count(), 1)
        disabled = self.service.create_creative_project("不要主旋律，8小节")
        with self.assertRaises(ValueError):
            self.service.edit_project(
                disabled["project_id"], disabled["version_id"], plan
            )
        old = self.service.create_project(spec={})
        with self.assertRaises(ValueError):
            self.service.edit_project(old["project_id"], old["version_id"], plan)
        self.assertEqual(self.count(), 3)

    def test_cannot_thin_only_anchor_notes(self):
        v = self.create_a()
        p = CreativeEditPlan("melody", 1, 16, "thin_notes", 50).to_dict()
        after = self.service.edit_project(v["project_id"], v["version_id"], p)
        with self.assertRaisesRegex(ValueError, "No removable"):
            self.service.edit_project(v["project_id"], after["version_id"], p)
        self.assertEqual(self.count(), 2)

    def test_melody_legacy_operations_and_density_restriction(self):
        v = self.service.create_creative_project("8小节")
        for operation, value in [("velocity", -4), ("transpose", 2), ("mute", 0)]:
            v = self.service.edit_project(
                v["project_id"],
                v["version_id"],
                CreativeEditPlan("melody", 1, 1, operation, value).to_dict(),
            )
            self.assertEqual(reproduce(v), v["tracks"])
        with self.assertRaises(ValueError):
            CreativeEditPlan("melody", 1, 2, "density", 8)
        self.assertEqual(
            [n for n in v["tracks"][-1]["notes"] if n["start_tick"] < BAR], []
        )

    def test_trusted_llm_scope_protection_and_no_operation_substitution(self):
        v = self.create_a()
        calls = []
        self.service.planner = DeepSeekPlanner(
            environ={"DEEPSEEK_API_KEY": "test-only"},
            transport=lambda payload: (
                calls.append(payload)
                or llm_response(dict(operation="thin_notes", value=50))
            ),
        )
        result = self.service.plan_project_edit(
            v["project_id"], C_TEXT, track_id="melody", start_bar=2, end_bar=3
        )
        self.assertEqual(result["plan"]["start_bar"], 2)
        self.assertIn(v["version_id"], calls[0]["instructions"])
        self.assertIn("protected_tracks", calls[0]["instructions"])
        for bad in [
            dict(operation="thin_notes", value=50, track_id="kick"),
            dict(operation="transpose", value=1),
            dict(operation="velocity", value=-20),
        ]:
            self.service.planner = DeepSeekPlanner(
                environ={"DEEPSEEK_API_KEY": "test-only"},
                transport=lambda _, bad=bad: llm_response(bad),
            )
            with self.assertRaises(PlannerError):
                self.service.plan_project_edit(v["project_id"], C_TEXT)
        self.assertEqual(self.count(), 1)


class CompatibilityTests(CreativeCase):
    def test_checked_in_legacy_snapshots_and_exports_unchanged(self):
        for name in ("v1", "v2"):
            source = Path("examples/legacy-midi/demo") / f"{name}.json"
            original = source.read_bytes()
            v = json.loads(original)
            mid = (source.parent / f"{name}.mid").read_bytes()
            wav = (source.parent / f"{name}.wav").read_bytes()
            self.assertEqual(midi_bytes(v), mid)
            self.assertEqual(preview_bytes(v), wav)
            with self.service.connect() as c:
                c.execute(
                    "INSERT OR IGNORE INTO projects VALUES (?,?)",
                    (v["project_id"], v["version_id"]),
                )
                c.execute(
                    "INSERT INTO versions VALUES (?,?,?)",
                    (v["version_id"], v["project_id"], original.decode()),
                )
            loaded = self.service.get_version(v["project_id"], v["version_id"])
            self.assertEqual(loaded, v)
            self.assertEqual(source.read_bytes(), original)
            self.assertNotIn("arrangement", loaded["spec"])
            self.assertNotIn("generator_version", loaded)
        new = self.service.create_project(spec=dict(seed=7))
        self.assertEqual(new["schema_version"], 1)
        self.assertEqual(len(new["tracks"]), 5)
        self.assertEqual(new["tracks"], generate(BeatSpec(seed=7)))

    def test_old_data_stays_raw_in_database_after_v2_write(self):
        old = self.service.create_project(spec={})
        with self.service.connect() as c:
            raw = c.execute(
                "SELECT data FROM versions WHERE id=?", (old["version_id"],)
            ).fetchone()[0]
        self.create_a()
        with self.service.connect() as c:
            self.assertEqual(
                c.execute(
                    "SELECT data FROM versions WHERE id=?", (old["version_id"],)
                ).fetchone()[0],
                raw,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE versions SET data=?", ("{}",))

    def test_demo_complete_artifacts_and_human_review_pending(self):
        out = generate_demos(Path(self.tmp.name) / "examples")
        for name in ("A", "B", "C"):
            for file in (
                "input.txt",
                "creative_brief.json",
                "spec.json",
                "explanation.json",
                "version.json",
                "beat.mid",
                "preview.wav",
            ):
                self.assertTrue((out / name / file).is_file())
        a = json.loads((out / "A/version.json").read_text())
        b = json.loads((out / "B/version.json").read_text())
        c = json.loads((out / "C/version.json").read_text())
        self.assertNotEqual(a["tracks"], b["tracks"])
        self.assertEqual(c["parent_id"], a["version_id"])
        self.assertEqual(reproduce(c), c["tracks"])
        self.assertTrue(
            all(
                t["outside_unchanged"]
                for t in json.loads((out / "C/diff.json").read_text())["tracks"]
            )
        )
        self.assertEqual(
            json.loads((out / "listening-review.json").read_text())["status"],
            "PENDING_HUMAN_REVIEW",
        )
        with self.assertRaises(ValueError):
            generate_demos(out)


class CreativeHTTPTests(CreativeCase):
    def test_parse_create_plan_commit_export_and_reject_client_snapshot(self):
        httpd = server(self.service, 0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def request(method, path, body=None):
            conn = http.client.HTTPConnection(
                "127.0.0.1", httpd.server_port, timeout=10
            )
            try:
                conn.request(
                    method,
                    path,
                    None if body is None else json.dumps(body),
                    {"Content-Type": "application/json"},
                )
                r = conn.getresponse()
                data = r.read()
                return r.status, json.loads(data) if r.getheader(
                    "Content-Type"
                ) == "application/json" else data
            finally:
                conn.close()

        try:
            code, parsed = request(
                "POST", "/creative/parse", dict(text="克制，不要太密，4小节")
            )
            self.assertEqual(code, 200)
            code, v = request(
                "POST",
                "/creative/projects",
                dict(
                    text="克制，不要太密，4小节",
                    spec=parsed["spec"],
                    protected_tracks=list(TRACK_IDS),
                ),
            )
            self.assertEqual(code, 201)
            pid = v["project_id"]
            self.assertEqual(
                request(
                    "POST",
                    f"/projects/{pid}/plan",
                    dict(text=C_TEXT, project_state={"bars": 99}),
                )[0],
                400,
            )
            code, p = request("POST", f"/projects/{pid}/plan", dict(text=C_TEXT))
            self.assertEqual(code, 200)
            code, after = request(
                "POST",
                f"/projects/{pid}/edits",
                {k: p[k] for k in ("base_version", "plan", "protected_tracks")},
            )
            self.assertEqual(code, 201)
            self.assertEqual(
                request("GET", f"/projects/{pid}/versions/{v['version_id']}")[1], v
            )
            for suffix, magic in [("midi", b"MThd"), ("preview", b"RIFF")]:
                code, data = request(
                    "GET", f"/projects/{pid}/versions/{after['version_id']}/{suffix}"
                )
                self.assertEqual(code, 200)
                self.assertTrue(data.startswith(magic))
            self.assertEqual(
                request("POST", f"/projects/{pid}/plan", dict(text="更温暖"))[1][
                    "status"
                ],
                "suggestion",
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()
