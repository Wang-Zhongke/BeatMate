"""Planners return data only; the service owns validation and mutation."""

import re
from typing import Protocol
from .model import BeatSpec, EditPlan


class Planner(Protocol):
    def parse_intent(self, text: str) -> dict: ...
    def plan_edit(
        self, text: str, track_id: str, start_bar: int, end_bar: int
    ) -> dict: ...


def checked_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError("text must contain 1..4000 characters")
    return text.lower()


class MockPlanner:
    """Always available, deterministic, never calls a network service."""

    def parse_creative(self, text, reference=None):
        from .creative import mock_creative

        return mock_creative(text, reference)

    def parse_intent(self, text):
        text = checked_text(text)
        data = {}
        if "trap" in text:
            data.update(style="trap", bpm=140, swing=0)
        elif "boom" in text or "嘻哈" in text:
            data["style"] = "boom_bap"
        for field, pattern in [
            ("bpm", r"(\d+)\s*bpm"),
            ("bars", r"(\d+)\s*(?:bars?|小节)"),
            ("seed", r"seed\s*[=:]?\s*(\d+)"),
        ]:
            match = re.search(pattern, text)
            if match:
                data[field] = int(match[1])
        match = re.search(r"\b([a-g]#?)\s*(minor|major)\b", text)
        if match:
            data.update(key=match[1].upper(), mode=match[2])
        if "无摇摆" in text or "straight" in text:
            data["swing"] = 0
        spec = BeatSpec.parse(data)
        return dict(
            spec=spec.to_dict(),
            planner="mock",
            assumptions=[
                "Mock 仅识别 trap/boom bap、数字 BPM/小节、C minor 等调性、seed；其他描述不影响生成。",
                f"最终参数（含默认值）：{spec.to_dict()}",
            ],
        )

    def plan_edit(self, text, track_id, start_bar, end_bar):
        text = checked_text(text)
        number = re.search(r"[+-]?\d+", text)
        value = int(number[0]) if number else None
        if any(x in text for x in ("mute", "静音", "删除")):
            op, value = "mute", 0
        elif any(x in text for x in ("density", "加密", "密度")):
            op, value = "density", 16 if value is None else value
        elif any(x in text for x in ("velocity", "力度", "轻一点", "重一点")):
            op, value = (
                "velocity",
                (-10 if "轻" in text else 10) if value is None else value,
            )
        elif any(x in text for x in ("transpose", "移调", "升高", "降低")):
            op, value = "transpose", 12 if value is None else value
            if "降低" in text:
                value = -abs(value)
        else:
            raise ValueError(
                "Mock 无法解析此编辑；请指定加密16、力度-10、移调+12或静音"
            )
        return EditPlan(track_id, start_bar, end_bar, op, value).to_dict()
