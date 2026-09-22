"""Deterministic provenance, deliberately independent of the MIDI keyword parser."""

import re

LABELS = {
    "purpose": "用途",
    "mood": "情绪",
    "scene": "场景",
    "style": "风格参考",
    "instruments": "配器偏好",
    "structure": "结构变化",
    "avoid": "禁止项",
}


def prepare(
    raw_text,
    prompt_mode="direct",
    constraints=None,
    creation_mode=None,
    lyrics="",
    arrangement_summary="",
    analysis_id=None,
):
    if creation_mode in ("lyrics_summary", "song_stems"):
        return prepare_workflow(
            raw_text,
            constraints,
            creation_mode,
            lyrics,
            arrangement_summary,
            analysis_id,
        )
    if arrangement_summary or analysis_id:
        raise ValueError("摘要只能用于歌词摘要伴奏模式")
    # Absent creation_mode preserves the fingerprints of legacy pending requests.
    if creation_mode is not None and creation_mode not in ("instrumental", "lyrics"):
        raise ValueError("Invalid creation_mode")
    if not isinstance(lyrics, str) or any(
        ord(c) < 32 and c not in "\n\t\r" for c in lyrics
    ):
        raise ValueError("歌词包含不支持的内容")
    if creation_mode == "lyrics":
        if not lyrics.strip():
            raise ValueError("请填写用于生成伴奏的歌词")
    elif lyrics:
        raise ValueError("请切换到「用歌词生成伴奏」后提交歌词")
    if (
        not isinstance(raw_text, str)
        or (not raw_text.strip() and creation_mode != "lyrics")
        or len(raw_text) > 1024
    ):
        raise ValueError("创作原文须为1–1024字符")
    if any(ord(c) < 32 and c not in "\n\t\r" for c in raw_text):
        raise ValueError("输入包含不支持的控制字符")
    if prompt_mode not in ("direct", "template"):
        raise ValueError("Invalid prompt_mode")
    if creation_mode is not None:
        prompt_mode = "direct"
    constraints = {} if constraints is None else constraints
    if not isinstance(constraints, dict) or set(constraints) - set(LABELS):
        raise ValueError("Unknown constraint field")
    if any(
        not isinstance(v, str)
        or len(v) > 500
        or any(ord(c) < 32 and c not in "\n\t\r" for c in v)
        for v in constraints.values()
    ):
        raise ValueError("Invalid constraint text")
    explicit = {k: v for k, v in constraints.items() if v.strip()}
    warnings = []
    if explicit:
        warnings.append(
            "原文与结构化选项均保留；可能存在冲突，请核对最终描述。系统不会判断所有语义矛盾或静默覆盖。"
        )
        raw_bpm = re.findall(r"(\d+)\s*BPM", raw_text, re.I)
        selected_bpm = re.findall(r"(\d+)\s*BPM", " ".join(explicit.values()), re.I)
        if raw_bpm and selected_bpm and set(raw_bpm) != set(selected_bpm):
            warnings.append("发现原文与选项中的BPM冲突，请修改后再提交。")
    if prompt_mode == "direct":
        final = raw_text
        if explicit:
            final += "\n\n用户明确选项：\n" + "\n".join(
                f"{LABELS[k]}：{v}" for k, v in explicit.items()
            )
    else:
        final = "创作描述：\n" + raw_text
        final += "".join(
            f"\n{LABELS[k]}：{explicit[k]}" for k in LABELS if k in explicit
        )
    if creation_mode == "lyrics":
        final = (
            "生成纯伴奏，供用户自行演唱或说唱。根据以下歌词的情绪、段落和节奏留白安排配器与结构。"
            "歌词仅作创作参考，不要演唱、说唱、念白、哼唱、和声或人声采样。\n\n"
            "歌词原文：\n"
            + lyrics
            + ("\n\n制作描述（原文）：\n" + final if final else "")
        )
    if len(final) > 1024:
        raise ValueError(
            f"最终音乐描述共{len(final)}字符，超过当前伴奏接口的1024字符上限；请精简描述或选取歌词片段。原文未截断。"
        )
    brief = dict(
        raw_text=raw_text,
        constraints=explicit,
        assumptions=[],
        final_prompt=final,
        prompt_mode=prompt_mode,
        template_version="audio-template-1" if prompt_mode == "template" else None,
        warnings=warnings,
        requires_review=bool(explicit) or creation_mode == "lyrics",
        detected_conflict=len(warnings) > 1,
    )
    if creation_mode is not None:
        brief.update(creation_mode=creation_mode, lyrics=lyrics)
    return brief


def text_field(value, name, limit, required=False):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (required and not value.strip())
    ):
        raise ValueError(f"{name}须为{'1' if required else '0'}–{limit}字符")
    if any(ord(c) < 32 and c not in "\n\t\r" for c in value):
        raise ValueError(f"{name}含不支持的控制字符")
    return value


def workflow_source(raw_text, lyrics, constraints=None):
    text_field(lyrics, "歌词原文", 5000, True)
    text_field(raw_text, "制作描述", 1024)
    # Reuse the existing explicit-preferences validation without a total prompt cap.
    constraints = {} if constraints is None else constraints
    if not isinstance(constraints, dict) or set(constraints) - set(LABELS):
        raise ValueError("Unknown constraint field")
    for value in constraints.values():
        text_field(value, "制作偏好", 500)
    return dict(
        raw_text=raw_text,
        lyrics=lyrics,
        constraints={k: v for k, v in constraints.items() if v.strip()},
    )


def prepare_workflow(
    raw_text, constraints, creation_mode, lyrics, arrangement_summary, analysis_id
):
    source = workflow_source(raw_text, lyrics, constraints)
    conflict = False
    if creation_mode == "lyrics_summary":
        text_field(arrangement_summary, "编曲摘要（请先分析歌词或手动填写）", 980, True)
        if analysis_id is not None and (
            not isinstance(analysis_id, str)
            or not re.fullmatch(r"[a-f0-9]{32}", analysis_id)
        ):
            raise ValueError("Invalid analysis_id")
        final = (
            "纯器乐伴奏，不要唱歌、说唱、念白、哼唱、和声或人声采样。\n"
            + arrangement_summary
        )
        warnings = [
            "音乐服务仅接收下方编曲摘要；完整歌词和制作原文保留在本机，不逐句传入伴奏接口。"
        ]
    else:
        if arrangement_summary or analysis_id:
            raise ValueError("歌词歌曲模式不使用编曲摘要")
        final = raw_text
        if source["constraints"]:
            final += "\n\n用户明确选项：\n" + "\n".join(
                f"{LABELS[k]}：{v}" for k, v in source["constraints"].items()
            )
        warnings = [
            "将先生成含人声的完整歌曲，再分离伴奏和人声。分离可能有残留，不保证符合你的预设唱法。"
        ]
        raw_bpm = set(re.findall(r"(\d+)\s*BPM", raw_text, re.I))
        option_bpm = set(
            re.findall(r"(\d+)\s*BPM", " ".join(source["constraints"].values()), re.I)
        )
        conflict = bool(raw_bpm and option_bpm and raw_bpm != option_bpm)
        if conflict:
            warnings.append("发现原文与选项中的BPM冲突，请修改后再提交。")
    if len(final) > 1024:
        raise ValueError(
            f"制作描述共{len(final)}字符，超过1024上限；歌词独立计数，不占此额度。"
        )
    return dict(
        **source,
        creation_mode=creation_mode,
        prompt_mode="direct",
        template_version=None,
        final_prompt=final,
        arrangement_summary=arrangement_summary,
        analysis_id=analysis_id,
        assumptions=[],
        warnings=warnings,
        requires_review=True,
        detected_conflict=conflict,
    )
