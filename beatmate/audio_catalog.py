"""Bounded library queries and a durable change counter for lightweight polling."""


def install_catalog(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS audio_catalog_revision(id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL);
        INSERT OR IGNORE INTO audio_catalog_revision VALUES(1,0);
        CREATE INDEX IF NOT EXISTS audio_results_source ON audio_results(json_extract(data,'$.source_audio_asset_id'));
        CREATE INDEX IF NOT EXISTS audio_results_task ON audio_results(task);
        CREATE INDEX IF NOT EXISTS audio_stems_task ON audio_stem_jobs(task);
    """)
    for table in (
        "audio_tasks",
        "audio_results",
        "audio_library",
        "audio_task_library",
        "audio_stem_jobs",
        "audio_projects",
        "audio_settings",
        "audio_notes",
    ):
        for action in ("INSERT", "UPDATE", "DELETE"):
            c.execute(f"""CREATE TRIGGER IF NOT EXISTS catalog_{table}_{action.lower()} AFTER {action} ON {table}
                          BEGIN UPDATE audio_catalog_revision SET value=value+1 WHERE id=1; END""")


class CatalogMixin:
    def catalog_revision(self):
        with self.connect() as c:
            c.execute("BEGIN")
            version = c.execute(
                "SELECT value FROM audio_catalog_revision WHERE id=1"
            ).fetchone()[0]
            active = c.execute(
                "SELECT EXISTS(SELECT 1 FROM audio_tasks WHERE json_extract(data,'$.status') IN ('queued','submitting','generating','downloading'))"
            ).fetchone()[0]
            if not active:
                active = c.execute("""SELECT EXISTS(SELECT 1 FROM audio_stem_jobs j LEFT JOIN audio_library l ON l.id=j.id
                    WHERE COALESCE(l.purged,0)=0 AND (json_extract(j.data,'$.status') IN ('queued','submitting','downloading')
                    OR json_extract(j.data,'$.midi_status')='downloading'))""").fetchone()[
                    0
                ]
        return dict(
            revision=version, active=bool(active), instance=self.health.started_at
        )

    def catalog_page(
        self,
        offset=0,
        limit=30,
        view="music",
        filter="all",
        search="",
        sort="newest",
        include_ids=None,
        task_offset=0,
    ):
        from .audio import identifier

        if (
            type(offset) is not int
            or not 0 <= offset <= 10000000
            or type(task_offset) is not int
            or not 0 <= task_offset <= 10000000
        ):
            raise ValueError("无效页码")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("每页须为 1–50 首")
        if (
            view not in ("studio", "music", "favorites", "trash")
            or filter not in ("all", "favorites", "mock")
            or sort not in ("newest", "oldest", "title")
        ):
            raise ValueError("无效曲库筛选")
        if not isinstance(search, str) or len(search) > 200:
            raise ValueError("搜索词最多 200 字符")
        if include_ids is None:
            include_ids = []
        if not isinstance(include_ids, list) or len(include_ids) > 50:
            raise ValueError("保留试听作品最多 50 首")
        for aid in include_ids:
            identifier(aid)
        # This token precedes the snapshot: concurrent writes cause a subsequent refresh,
        # rather than stamping an older page with a newer version it has not observed.
        revision = self.catalog_revision()
        with self.connect() as c:
            c.create_function("casefold", 1, lambda x: str(x or "").casefold())
            c.execute("BEGIN")
            title = "COALESCE(NULLIF(l.title,''),NULLIF(json_extract(t.data,'$.title'),''),'未命名作品')"
            favorite = "COALESCE(l.favorite,CASE WHEN json_extract(chosen.data,'$.source_audio_asset_id')=r.id THEN 1 ELSE 0 END)"
            joins = """ FROM audio_results r JOIN audio_tasks t ON t.id=r.task
                      LEFT JOIN audio_library l ON l.id=r.id LEFT JOIN audio_projects p ON p.id=r.project
                      LEFT JOIN audio_results chosen ON chosen.id=p.selected """
            where = [
                "json_extract(r.data,'$.source_audio_asset_id')=r.id",
                "COALESCE(l.purged,0)=0",
                "l.deleted_at IS NOT NULL"
                if view == "trash"
                else "l.deleted_at IS NULL",
            ]
            if view != "trash" and (view == "favorites" or filter == "favorites"):
                where.append(f"{favorite}=1")
            if view != "trash" and filter == "mock":
                where.append("json_extract(t.data,'$.provider')='mock'")
            where.append(f"instr(casefold({title}),?)>0")
            params = [search.strip().casefold()]
            clause = " WHERE " + " AND ".join(where)
            total = c.execute("SELECT COUNT(*)" + joins + clause, params).fetchone()[0]
            offset = min(offset, ((total - 1) // limit) * limit if total else 0)
            order = {
                "newest": "json_extract(t.data,'$.created_at') DESC,r.id DESC",
                "oldest": "json_extract(t.data,'$.created_at'),r.id",
                "title": f"casefold({title}),r.id",
            }[sort]
            page_ids = [
                r[0]
                for r in c.execute(
                    "SELECT r.id"
                    + joins
                    + clause
                    + " ORDER BY "
                    + order
                    + " LIMIT ? OFFSET ?",
                    params + [limit, offset],
                )
            ]
            task_limit = 10
            task_where = [
                "instr(casefold(COALESCE(NULLIF(json_extract(t.data,'$.title'),''),'未命名作品')),?)>0"
            ]
            task_params = [search.strip().casefold()]
            if view == "favorites" or filter == "favorites":
                task_where.append("0")
            if filter == "mock" and view != "trash":
                task_where.append("json_extract(t.data,'$.provider')='mock'")
            # A stopped task with no audio remains manageable; a task with audio is
            # included only while its original/stem/MIDI workflow needs attention.
            task_where.append(
                """((NOT EXISTS(SELECT 1 FROM audio_results r WHERE r.task=t.id)
                 AND d.deleted_at IS """
                + ("NOT NULL" if view == "trash" else "NULL")
                + """)"""
                + (
                    ""
                    if view == "trash"
                    else """ OR
                 (EXISTS(SELECT 1 FROM audio_results r LEFT JOIN audio_library l ON l.id=r.id
                   WHERE r.task=t.id AND json_extract(r.data,'$.source_audio_asset_id')=r.id
                     AND COALESCE(l.purged,0)=0 AND l.deleted_at IS NULL)
                  AND (json_extract(t.data,'$.status')!='ready' OR EXISTS(
                    SELECT 1 FROM audio_stem_jobs j LEFT JOIN audio_library l ON l.id=j.id WHERE j.task=t.id
                      AND COALESCE(l.purged,0)=0 AND l.deleted_at IS NULL
                      AND (json_extract(j.data,'$.status') NOT IN ('ready','skipped') OR json_extract(j.data,'$.midi_status')='downloading'))))"""
                )
                + ")"
            )
            task_from = (
                " FROM audio_tasks t LEFT JOIN audio_task_library d ON d.id=t.id WHERE "
                + " AND ".join(task_where)
            )
            task_total = c.execute(
                "SELECT COUNT(*)" + task_from, task_params
            ).fetchone()[0]
            task_offset = min(
                task_offset,
                ((task_total - 1) // task_limit) * task_limit if task_total else 0,
            )
            task_ids = [
                r[0]
                for r in c.execute(
                    "SELECT t.id"
                    + task_from
                    + " ORDER BY t.rowid DESC LIMIT ? OFFSET ?",
                    task_params + [task_limit, task_offset],
                )
            ]
            # Pin the current listening queue independently of the visible page.
            all_ids = list(dict.fromkeys(page_ids + include_ids))
            for tid in task_ids:
                all_ids.extend(
                    r[0]
                    for r in c.execute(
                        "SELECT id FROM audio_results WHERE task=? AND json_extract(data,'$.source_audio_asset_id')=id",
                        (tid,),
                    )
                )
        history = self.history(
            source_ids=list(dict.fromkeys(all_ids)), extra_task_ids=task_ids
        )
        history.update(
            page=dict(ids=page_ids, offset=offset, limit=limit, total=total),
            activity=dict(
                ids=task_ids, offset=task_offset, limit=task_limit, total=task_total
            ),
            **revision,
        )
        return history
