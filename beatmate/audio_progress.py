"""Presentation-only recovery guidance; it never changes submission state."""


def task_progress(task):
    status = task["status"]
    confirmed = bool(task.get("provider_task_id"))
    steps = {
        "queued": "等待提交",
        "submitting": "提交生成请求",
        "generating": "等待生成结果",
        "downloading": "保存原始音频",
        "ready": "原始音频已保存",
        "failed": "生成已停止",
        "uncertain": "提交待核对",
    }
    advice = {
        "queued": "等待后台处理。",
        "submitting": "正在等待供应商响应，请勿重复提交。",
        "generating": "继续查询原任务，不会重新提交生成。",
        "downloading": "恢复原始文件下载，不会重新生成。",
        "ready": "可试听并下载；歌曲分离进度见音轨。",
        "failed": "查看失败原因，调整配置后再决定是否重新创作。",
        "uncertain": "先核对供应商账户与原请求；系统不会自动重发。",
    }
    return dict(
        stage=steps.get(status, status),
        last_success="原始音频已保存"
        if status == "ready"
        else "供应商已返回任务 ID"
        if confirmed
        else "本地请求已保存",
        next_action=advice.get(status, ""),
        can_resume=status in ("generating", "downloading"),
        last_attempt_at=task.get("last_attempt_at"),
        provider_task_id=task.get("provider_task_id"),
        next_attempt=task.get("next_attempt")
        if status in ("generating", "downloading")
        else None,
    )
