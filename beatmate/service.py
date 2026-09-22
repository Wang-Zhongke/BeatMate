import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import sqlite3
import uuid
from .model import BeatSpec, EditPlan, BAR, TRACK_IDS, digest, validate_tracks
from .generator import generate, hats
from .planner import MockPlanner


class ConflictError(Exception):
    pass


def protection(value, allowed=TRACK_IDS):
    if not isinstance(value, (list, tuple)) or any(
        type(x) is not str or x not in allowed for x in value
    ):
        raise ValueError("Invalid protected_tracks")
    return sorted(set(value))


class Service:
    def __init__(self, db=".beatmate/state.sqlite3", planner=None):
        self.db = str(db)
        self.planner = planner or MockPlanner()
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, head TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, project TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS immutable_version_update BEFORE UPDATE ON versions BEGIN SELECT RAISE(ABORT, 'immutable version'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_version_delete BEFORE DELETE ON versions BEGIN SELECT RAISE(ABORT, 'immutable version'); END;
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=15)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create_project(self, text=None, spec=None, protected_tracks=()):
        if (text is None) == (spec is None):
            raise ValueError("Supply exactly one of text or spec")
        parsed = (
            self.planner.parse_intent(text)
            if text is not None
            else dict(spec=spec, planner="structured", assumptions=[])
        )
        spec = BeatSpec.parse(parsed["spec"])
        tracks = generate(spec)
        validate_tracks(tracks, spec)
        version = dict(
            schema_version=1,
            project_id=uuid.uuid4().hex,
            version_id=uuid.uuid4().hex,
            parent_id=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            spec=spec.to_dict(),
            tracks=tracks,
            protected_tracks=protection(protected_tracks),
            audit=dict(
                action="create",
                planner=parsed["planner"],
                assumptions=parsed["assumptions"],
            ),
        )
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO projects VALUES (?,?)",
                (version["project_id"], version["version_id"]),
            )
            conn.execute(
                "INSERT INTO versions VALUES (?,?,?)",
                (version["version_id"], version["project_id"], json.dumps(version)),
            )
        return version

    def creative_context(self, project_id=None, version_id=None):
        if project_id is None:
            if version_id is not None:
                raise ValueError("reference_version requires reference_project_id")
            return None
        version = self.get_version(project_id, version_id)
        return dict(
            project_id=project_id,
            version_id=version["version_id"],
            spec=version["spec"],
            track_ids=[t["id"] for t in version["tracks"]],
            protected_tracks=version["protected_tracks"],
        )

    def parse_creative(self, text, reference_project_id=None, reference_version=None):
        from .creative import creative_result

        reference = self.creative_context(reference_project_id, reference_version)
        parsed = self.planner.parse_creative(text, reference)
        # Rebuild provenance locally; never accept a provider-authored snapshot/brief.
        return creative_result(text, parsed["spec"], parsed["planner"], reference)

    def create_creative_project(
        self,
        text,
        spec=None,
        protected_tracks=(),
        reference_project_id=None,
        reference_version=None,
    ):
        from .creative import CreativeSpec, creative_result, GENERATOR_VERSION
        from .generator_v2 import generate_creative, explain

        reference = self.creative_context(reference_project_id, reference_version)
        parsed = (
            self.parse_creative(text, reference_project_id, reference_version)
            if spec is None
            else creative_result(text, spec, "structured", reference)
        )
        spec = CreativeSpec.parse(parsed["spec"])
        tracks = generate_creative(spec)
        version = dict(
            schema_version=2,
            project_id=uuid.uuid4().hex,
            version_id=uuid.uuid4().hex,
            parent_id=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            spec=spec.to_dict(),
            generator_version=GENERATOR_VERSION,
            creative_brief=parsed["brief"],
            edit_history=[],
            tracks=tracks,
            protected_tracks=protection(protected_tracks, spec.track_ids),
            audit=dict(
                action="create",
                planner=parsed["planner"],
                assumptions=parsed["brief"]["assumptions"],
                reference_version=reference["version_id"] if reference else None,
            ),
        )
        version["explanation"] = explain(version)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO projects VALUES (?,?)",
                (version["project_id"], version["version_id"]),
            )
            conn.execute(
                "INSERT INTO versions VALUES (?,?,?)",
                (version["version_id"], version["project_id"], json.dumps(version)),
            )
        return version

    def plan_project_edit(
        self,
        project_id,
        text,
        base_version=None,
        track_id=None,
        start_bar=None,
        end_bar=None,
        protected_tracks=(),
    ):
        from .creative_edit import plan_project_edit

        version = self.get_version(project_id, base_version)
        return plan_project_edit(
            self.planner, version, text, track_id, start_bar, end_bar, protected_tracks
        )

    def get_version(self, project_id, version_id=None):
        if not isinstance(project_id, str) or not re.fullmatch(
            r"[a-f0-9]{32}", project_id
        ):
            raise ValueError("Invalid project_id")
        if version_id is not None and (
            not isinstance(version_id, str)
            or not re.fullmatch(r"[a-f0-9]{32}", version_id)
        ):
            raise ValueError("Invalid version_id")
        with self.connect() as conn:
            row = conn.execute(
                "SELECT head FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if not row:
                raise KeyError("Project not found")
            row = conn.execute(
                "SELECT data FROM versions WHERE project=? AND id=?",
                (project_id, version_id or row[0]),
            ).fetchone()
            if not row:
                raise KeyError("Version not found")
            return json.loads(row[0])

    def edit_project(self, project_id, base_version, plan, protected_tracks=()):
        if not isinstance(base_version, str) or not re.fullmatch(
            r"[a-f0-9]{32}", base_version
        ):
            raise ValueError("Invalid base_version")
        parent = self.get_version(project_id, base_version)
        modern = parent.get("schema_version") == 2
        if modern:
            from .creative import CreativeSpec, CreativeEditPlan

            spec, plan = (
                CreativeSpec.parse(parent["spec"]),
                CreativeEditPlan.parse(plan),
            )
            track_ids = spec.track_ids
        else:
            spec, plan, track_ids = (
                BeatSpec.parse(parent["spec"]),
                EditPlan.parse(plan),
                TRACK_IDS,
            )
        if plan.track_id not in track_ids:
            raise ValueError("Target track does not exist in this project")
        protected = protection(
            parent["protected_tracks"] + protection(protected_tracks, track_ids),
            track_ids,
        )
        if plan.track_id in protected:
            raise ValueError("Track is protected")
        if plan.end_bar > spec.bars:
            raise ValueError("Edit range exceeds project")
        start, end = (plan.start_bar - 1) * BAR, plan.end_bar * BAR
        child = copy.deepcopy(parent)
        target = next(t for t in child["tracks"] if t["id"] == plan.track_id)
        for n in target["notes"]:
            if n["start_tick"] < end and n["start_tick"] + n["duration_tick"] > start:
                if (
                    n["start_tick"] < start
                    or n["start_tick"] + n["duration_tick"] > end
                ):
                    raise ValueError("Edit intersects a boundary-crossing note")
        selected = lambda n: start <= n["start_tick"] < end
        removed_note_ids = []
        if plan.operation == "thin_notes":
            from .generator_v2 import thin_notes

            target["notes"], removed_note_ids = thin_notes(target["notes"], start, end)
        elif plan.operation in ("mute", "density"):
            target["notes"] = [n for n in target["notes"] if not selected(n)]
            if plan.operation == "density":
                for bar in range(plan.start_bar - 1, plan.end_bar):
                    target["notes"].extend(hats(spec, bar, plan.value))
        else:
            for n in target["notes"]:
                if selected(n):
                    n["velocity" if plan.operation == "velocity" else "pitch"] += (
                        plan.value
                    )
        target["notes"].sort(key=lambda n: (n["start_tick"], n["pitch"]))
        validate_tracks(child["tracks"], spec, expected_track_ids=track_ids)
        verified = {}
        for before, after in zip(parent["tracks"], child["tracks"]):
            if before["id"] != plan.track_id:
                if digest(before) != digest(after):
                    raise ValueError("Unselected track changed")
            else:
                if {k: v for k, v in before.items() if k != "notes"} != {
                    k: v for k, v in after.items() if k != "notes"
                }:
                    raise ValueError("Track metadata changed")
                if [n for n in before["notes"] if not selected(n)] != [
                    n for n in after["notes"] if not selected(n)
                ]:
                    raise ValueError("Out-of-range notes changed")
            if before["id"] in protected:
                verified[before["id"]] = dict(
                    before=digest(before), after=digest(after)
                )
                if verified[before["id"]]["before"] != verified[before["id"]]["after"]:
                    raise ValueError("Protected track changed")
        child.update(
            version_id=uuid.uuid4().hex,
            parent_id=base_version,
            created_at=datetime.now(timezone.utc).isoformat(),
            protected_tracks=protected,
            audit=dict(
                action="edit",
                plan=plan.to_dict(),
                protected_hashes=verified,
                parent_hash=digest(parent),
                scope_verified=True,
            ),
        )
        if modern:
            from .generator_v2 import explain

            child["edit_history"].append(dict(plan=plan.to_dict()))
            child["audit"]["removed_note_ids"] = removed_note_ids
            child["explanation"] = explain(child)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT head FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if row[0] != base_version:
                raise ConflictError("Stale base_version; reload current head")
            conn.execute(
                "INSERT INTO versions VALUES (?,?,?)",
                (child["version_id"], project_id, json.dumps(child)),
            )
            conn.execute(
                "UPDATE projects SET head=? WHERE id=?",
                (child["version_id"], project_id),
            )
        return child
