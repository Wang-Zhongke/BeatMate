"""Offline A/B/C artifacts and explicit pending human listening record."""

import json
from pathlib import Path
import tempfile
from .model import BAR, digest
from .service import Service
from .render import midi_bytes, preview_bytes

A_TEXT = "做一个伤感、emo、引人共鸣，XXXTentacion类型的beat。凌晨一个人回家，想念一个人，但不想太压抑，给旋律Rap留空间。克制、留白较多，不要太密。76 BPM，16小节，A小调，seed 7。"
B_TEXT = "伤感但有推动力，带一点不甘，比刚才更有推动力，但不要很吵。给旋律Rap留空间。76 BPM，16小节，A小调，seed 7。"
C_TEXT = "只让后半段旋律更克制，鼓和贝斯不要动。"


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def export_version(version, output, raw_input=None):
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    (out / "input.txt").write_text(
        raw_input
        or version.get("creative_brief", {}).get(
            "raw_text", "Legacy snapshot: original input was not recorded."
        )
    )
    if "creative_brief" in version:
        write_json(out / "creative_brief.json", version["creative_brief"])
        write_json(out / "explanation.json", version["explanation"])
    write_json(out / "spec.json", version["spec"])
    write_json(out / "version.json", version)
    (out / "beat.mid").write_bytes(midi_bytes(version))
    (out / "preview.wav").write_bytes(preview_bytes(version))
    return out


def generate_demos(output):
    root = Path(output)
    if root.exists():
        raise ValueError(
            "Demo output already exists; choose a new directory to avoid overwriting"
        )
    with tempfile.TemporaryDirectory(prefix="beatmate-creative-demo-") as tmp:
        service = Service(Path(tmp) / "db")
        a = service.create_creative_project(
            A_TEXT, protected_tracks=["kick", "snare", "hihat", "bass", "chords"]
        )
        b = service.create_creative_project(
            B_TEXT,
            reference_project_id=a["project_id"],
            reference_version=a["version_id"],
        )
        proposal = service.plan_project_edit(
            a["project_id"], C_TEXT, base_version=a["version_id"]
        )
        if proposal["status"] != "ready":
            raise ValueError("Demo edit plan was not ready")
        c = service.edit_project(
            a["project_id"],
            proposal["base_version"],
            proposal["plan"],
            proposal["protected_tracks"],
        )
        export_version(a, root / "A")
        export_version(b, root / "B")
        export_version(c, root / "C", C_TEXT)
        write_json(root / "C" / "proposal.json", proposal)
        start, end = (
            (proposal["plan"]["start_bar"] - 1) * BAR,
            proposal["plan"]["end_bar"] * BAR,
        )
        differences = []
        for before, after in zip(a["tracks"], c["tracks"]):
            old_ids = {n["id"] for n in before["notes"]}
            new_ids = {n["id"] for n in after["notes"]}
            differences.append(
                dict(
                    track_id=before["id"],
                    before_count=len(old_ids),
                    after_count=len(new_ids),
                    removed_ids=sorted(old_ids - new_ids),
                    added_ids=sorted(new_ids - old_ids),
                    before_hash=digest(before),
                    after_hash=digest(after),
                    outside_unchanged=[
                        n for n in before["notes"] if not start <= n["start_tick"] < end
                    ]
                    == [
                        n for n in after["notes"] if not start <= n["start_tick"] < end
                    ],
                )
            )
        write_json(
            root / "C" / "diff.json",
            dict(
                parent_id=a["version_id"],
                version_id=c["version_id"],
                plan=proposal["plan"],
                tracks=differences,
                protection=c["audit"]["protected_hashes"],
            ),
        )
        # C's creation brief remains immutable provenance from A; edit intent is separate.
        write_json(
            root / "C" / "edit_intent.json",
            dict(
                raw_text=C_TEXT,
                resolved_scope=proposal["plan"],
                interpretation=proposal["interpretation"],
                creation_brief_inherited_from=a["version_id"],
            ),
        )
        from .creative import extract_brief

        edit_brief = extract_brief(C_TEXT)
        edit_brief.assumptions = [
            dict(
                parameter="operation",
                value="thin_notes",
                source="system_inference",
                reason="本轮将更克制限制为旋律减密；未宣称改变音色情绪。",
            ),
            dict(
                parameter="value",
                value=50,
                source="system_inference",
                reason="使用已实现的确定性保留锚点规则。",
            ),
        ]
        write_json(root / "C" / "edit_brief.json", edit_brief.to_dict())
        write_json(root / "C" / "before-version.json", a)
        (root / "C" / "before.mid").write_bytes(midi_bytes(a))
        write_json(
            root / "manifest.json",
            dict(
                planner="mock",
                generator_version=a["generator_version"],
                A=a["version_id"],
                B=b["version_id"],
                C=c["version_id"],
                note="B使用A作为可信创作参考；C是A的真实新版本。所有文件为离线规则生成。",
            ),
        )
    write_json(
        root / "listening-review.json",
        dict(
            status="PENDING_HUMAN_REVIEW",
            reviewer=None,
            date=None,
            cases={
                name: dict(
                    emotion_match=None,
                    rap_space=None,
                    musical_quality=None,
                    comments=None,
                )
                for name in ("A", "B", "C")
            },
            instruction="请人工试听A/B/C，描述情绪匹配、是否好听、是否适合录Rap及修改偏好；没有自动共鸣评分。",
        ),
    )
    (root / "README.md").write_text(
        """# Creative A/B/C试听案例\n\nA：克制、伤感、留白较多；B：参考A增加鼓密度和段落推动，鼓力度仍soft；C：只对A后半段melody执行thin_notes。\n\n每个目录包含原始输入、CreativeBrief、完整标准化规格、实际参数说明、完整快照、MIDI和参考WAV。C的creative_brief是继承的创作原话，新增编辑原话在input.txt/edit_intent.json；diff.json含逐轨变化及保护hash。\n\n音色建议尚不代表实际乐器音色。人工试听记录listening-review.json仍待填写；自动测试不替代听感评价。\n"""
    )
    return root
