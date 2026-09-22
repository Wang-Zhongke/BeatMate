"""Opt-in live smoke test: at most two Responses calls, never touches user DB."""

import argparse
import io
import json
import os
from pathlib import Path
import tempfile
import uuid

import mido

from .deepseek import DeepSeekPlanner
from .llm import PlannerError
from .model import BAR, BeatSpec, EditPlan
from .render import midi_bytes
from .service import Service


def run_smoke(*, enabled=False, output="output/deepseek-smoke", environ=None):
    env = os.environ if environ is None else environ
    if not enabled:
        return dict(status="SKIPPED", reason="Explicit --run is required", calls=0)
    if not env.get("DEEPSEEK_API_KEY", "").strip():
        return dict(
            status="SKIPPED", reason="DEEPSEEK_API_KEY is not configured", calls=0
        )
    calls = 0
    try:
        planner = DeepSeekPlanner(environ=env)
        transport = planner.transport

        def limited(payload):
            nonlocal calls
            if calls >= 2:
                raise PlannerError("Smoke call limit exceeded")
            calls += 1
            return transport(payload)

        planner.transport = limited
        parsed = planner.parse_intent(
            "生成 boom_bap beat：90 BPM，4小节，C minor，swing 0.12，seed 7。所有参数严格按此描述。"
        )
        expected_spec = BeatSpec(bars=4, seed=7).to_dict()
        if parsed["spec"] != expected_spec:
            raise ValueError(
                "BeatSpec did not preserve the explicit requested parameters"
            )
        plan = planner.plan_edit(
            "将踩镲密度改成每小节16个音符，其他内容不变。", "hihat", 3, 4
        )
        if plan != EditPlan("hihat", 3, 4, "density", 16).to_dict():
            raise ValueError("EditPlan did not preserve the explicit requested edit")

        # Both plans validated before touching an isolated temporary project.
        with tempfile.TemporaryDirectory(
            prefix="beatmate-deepseek-smoke-"
        ) as temporary:
            service = Service(Path(temporary) / "state.sqlite3")
            before = service.create_project(
                spec=parsed["spec"],
                protected_tracks=["kick", "snare", "bass", "chords"],
            )
            after = service.edit_project(
                before["project_id"], before["version_id"], plan
            )
            if (
                after["parent_id"] != before["version_id"]
                or after["version_id"] == before["version_id"]
            ):
                raise ValueError("Version lineage failed")
            if (
                service.get_version(before["project_id"], before["version_id"])
                != before
            ):
                raise ValueError("Historical version changed")
            for old, new in zip(before["tracks"], after["tracks"]):
                if old["id"] != "hihat" and old != new:
                    raise ValueError("Protected track changed")
                if old["id"] == "hihat":
                    if [n for n in old["notes"] if n["start_tick"] < 2 * BAR] != [
                        n for n in new["notes"] if n["start_tick"] < 2 * BAR
                    ]:
                        raise ValueError("Out-of-scope notes changed")
                    if len(new["notes"]) - len(old["notes"]) != 16:
                        raise ValueError("Expected density edit was not executed")
            for value in after["audit"]["protected_hashes"].values():
                if value["before"] != value["after"]:
                    raise ValueError("Protected hash mismatch")
            artifacts = {}
            for name, version in [("before", before), ("after", after)]:
                data = midi_bytes(version)
                decoded = mido.MidiFile(file=io.BytesIO(data))
                if (decoded.type, decoded.ticks_per_beat, len(decoded.tracks)) != (
                    1,
                    480,
                    6,
                ):
                    raise ValueError("MIDI export failed validation")
                for source, track in zip(version["tracks"], decoded.tracks[1:]):
                    active, recovered, tick = {}, [], 0
                    for event in track:
                        tick += event.time
                        if event.type == "note_on" and event.velocity:
                            active[(event.channel, event.note)] = (tick, event.velocity)
                        elif event.type == "note_off" or (
                            event.type == "note_on" and not event.velocity
                        ):
                            start, velocity = active.pop((event.channel, event.note))
                            recovered.append(
                                (
                                    event.channel,
                                    event.note,
                                    start,
                                    tick - start,
                                    velocity,
                                )
                            )
                    expected = [
                        (
                            source["channel"],
                            n["pitch"],
                            n["start_tick"],
                            n["duration_tick"],
                            n["velocity"],
                        )
                        for n in source["notes"]
                    ]
                    if (
                        active
                        or sorted(recovered) != sorted(expected)
                        or tick != 4 * BAR
                    ):
                        raise ValueError("MIDI note roundtrip mismatch")
                artifacts[name] = data
        # New unique directory; no existing examples, DB or exports are overwritten.
        destination = Path(output) / uuid.uuid4().hex
        destination.mkdir(parents=True, exist_ok=False)
        for name, data in artifacts.items():
            (destination / f"{name}.mid").write_bytes(data)
        report = dict(
            status="PASS",
            calls=calls,
            provider="deepseek",
            output=str(destination.resolve()),
            checks=[
                "BeatSpec",
                "EditPlan",
                "scope",
                "protected_tracks",
                "history",
                "MIDI_note_roundtrip",
            ],
        )
        (destination / "report.json").write_text(json.dumps(report, indent=2))
        return report
    except PlannerError as error:
        return dict(
            status="SKIPPED" if error.code == "network_unavailable" else "FAIL",
            reason=str(error),
            calls=calls,
        )
    except ValueError as error:
        return dict(status="FAIL", reason=str(error), calls=calls)
    except (OSError, KeyError):
        return dict(
            status="FAIL",
            reason="Local smoke validation or artifact output failed",
            calls=calls,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Explicitly allow up to two paid DeepSeek requests",
    )
    parser.add_argument("--output", default="output/deepseek-smoke")
    args = parser.parse_args()
    result = run_smoke(enabled=args.run, output=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
