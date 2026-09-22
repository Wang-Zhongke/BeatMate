"""Mutable library metadata alongside immutable generation provenance."""

import fcntl
import json
from .audio_provider import AudioError


def music_title(value):
    if not isinstance(value, str) or len(value) > 50 or any(ord(c) < 32 for c in value):
        raise ValueError("音乐名称最多50字符，不能包含换行或控制字符")
    return value.strip()


class LibraryMixin:
    def _purged_ids(self):
        with self.connect() as c:
            return {
                r[0] for r in c.execute("SELECT id FROM audio_library WHERE purged=1")
            }

    def library_update(self, ids, action, title=None, favorite=None):
        from .audio import identifier, now

        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 500
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("请选择1–500首不同作品")
        for aid in ids:
            identifier(aid)
        if action in ("trash_failed", "restore_failed", "trash_task", "restore_task"):
            return self._failed_task_update(ids, action)
        if action not in ("rename", "favorite", "trash", "restore", "purge"):
            raise ValueError("无效作品操作")
        if action == "rename":
            if len(ids) != 1:
                raise ValueError("一次只能重命名一首作品")
            title = music_title(title)
        if action == "favorite" and type(favorite) is not bool:
            raise ValueError("收藏状态须为布尔值")
        with (self.root / "worker.lock").open("a") as lock:
            # Permanent cleanup must never race generation or file recovery.
            if action == "purge":
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise AudioError("正在处理音频，请稍后再永久删除") from None
            with self.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                for aid in ids:
                    row = c.execute(
                        "SELECT data FROM audio_results WHERE id=?", (aid,)
                    ).fetchone()
                    if not row:
                        raise KeyError("作品不存在")
                    asset = json.loads(row[0])
                    if asset["source_audio_asset_id"] != aid:
                        raise ValueError("请按作品管理整曲及其分轨")
                    existing = c.execute(
                        "SELECT title,favorite,deleted_at,purged FROM audio_library WHERE id=?",
                        (aid,),
                    ).fetchone()
                    if existing and existing[3]:
                        raise ValueError("作品已永久删除")
                    if action == "purge" and (not existing or not existing[2]):
                        raise ValueError("仅能永久删除回收站中的作品")
                    legacy = c.execute(
                        "SELECT r.data FROM audio_projects p JOIN audio_results r ON r.id=p.selected WHERE p.id=?",
                        (asset["project_id"],),
                    ).fetchone()
                    was_selected = bool(
                        legacy and json.loads(legacy[0])["source_audio_asset_id"] == aid
                    )
                    c.execute(
                        "INSERT OR IGNORE INTO audio_library(id,favorite) VALUES (?,?)",
                        (aid, int(was_selected)),
                    )
                    if action == "rename":
                        c.execute(
                            "UPDATE audio_library SET title=? WHERE id=?", (title, aid)
                        )
                    if action == "favorite":
                        c.execute(
                            "UPDATE audio_library SET favorite=? WHERE id=?",
                            (int(favorite), aid),
                        )
                    if action == "trash":
                        c.execute(
                            "UPDATE audio_library SET deleted_at=? WHERE id=?",
                            (now(), aid),
                        )
                    if action == "restore":
                        c.execute(
                            "UPDATE audio_library SET deleted_at=NULL WHERE id=?",
                            (aid,),
                        )
                    if action == "purge":
                        c.execute(
                            "UPDATE audio_library SET purged=1,favorite=0 WHERE id=?",
                            (aid,),
                        )
                        related = [
                            json.loads(r[0])
                            for r in c.execute(
                                "SELECT data FROM audio_results WHERE task=?",
                                (asset["task_id"],),
                            )
                        ]
                        for a in related:
                            if a["source_audio_asset_id"] == aid:
                                c.execute(
                                    "UPDATE audio_projects SET selected=NULL WHERE selected=?",
                                    (a["id"],),
                                )
                                c.execute(
                                    "DELETE FROM audio_notes WHERE asset=?", (a["id"],)
                                )
            if action == "purge":
                self._clean_purged_files(ids)
        return dict(ids=ids, action=action)

    def _failed_task_update(self, ids, action):
        """Soft-delete stopped tasks without changing submission state or provenance."""
        from .audio import now

        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            for tid in ids:
                row = c.execute(
                    "SELECT data FROM audio_tasks WHERE id=?", (tid,)
                ).fetchone()
                if not row:
                    raise KeyError("任务不存在")
                task = json.loads(row[0])
                allowed = (
                    ("failed", "uncertain")
                    if action in ("trash_task", "restore_task")
                    else ("failed",)
                )
                if (
                    task["status"] not in allowed
                    or c.execute(
                        "SELECT 1 FROM audio_results WHERE task=?", (tid,)
                    ).fetchone()
                ):
                    raise ValueError("仅可移除已停止且没有音频的失败或待核对记录")
                c.execute(
                    "INSERT INTO audio_task_library(id,deleted_at) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET deleted_at=excluded.deleted_at",
                    (tid, now() if action in ("trash_failed", "trash_task") else None),
                )
        return dict(ids=ids, action=action)

    def _clean_purged_files(self, ids):
        """Shared content hashes stay while another non-purged work references them."""
        purged = self._purged_ids()
        with self.connect() as c:
            assets = [
                json.loads(r[0]) for r in c.execute("SELECT data FROM audio_results")
            ]
            jobs = [
                json.loads(r[0]) for r in c.execute("SELECT data FROM audio_stem_jobs")
            ]
        keep = {a["file"] for a in assets if a["source_audio_asset_id"] not in purged}
        remove = {a["file"] for a in assets if a["source_audio_asset_id"] in ids}
        for job in jobs:
            files = {a["file"] for a in job.get("midi_files", [])}
            if job["id"] not in purged:
                keep.update(files)
            elif job["id"] in ids:
                remove.update(files)
        for name in remove - keep:
            path = self.assets / name
            if path.parent == self.assets and not path.is_symlink() and path.is_file():
                path.unlink()
        for aid in ids:
            for prefix in ("stem-", "midi-stem-"):
                path = self.root / (prefix + aid + ".zip")
                if not path.is_symlink() and path.is_file():
                    path.unlink()
