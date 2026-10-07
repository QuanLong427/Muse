"""Durable links between turn episodes and real drafts/jobs, not authorization."""
import json
from uuid import NAMESPACE_URL, uuid5

from services import memory_store as store
from services.episode_evidence import decode_payload, entity_refs


def task_id(user_id: str, session_id: str, kind: str, anchor_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, json.dumps([user_id, session_id, kind, anchor_id], ensure_ascii=False)))


def record_task_episode(episode: dict, events: list[dict]) -> list[str]:
    """Link only observed operations; never create tasks from model prose."""
    store.init_memory_db()
    linked = set()
    conn = store._connect()
    try:
        with conn:
            for event in events:
                if event.get("phase") != "result" or event.get("name") not in {
                    "create_smart_playlist", "manage_playlist_draft", "convert_video"}:
                    continue
                payload = decode_payload(event.get("content"))
                if not isinstance(payload, dict) or payload.get("status") in {"error", "failed", "invalid", "confirmation_required"}:
                    continue
                # Do not import other drafts into this task via a list/read tool.
                if payload.get("action") in {"list", "read"}:
                    continue
                refs = entity_refs(payload)
                draft = payload.get("draft")
                job = payload.get("job") or payload.get("download_job")
                anchor = ("playlist_draft", draft["id"]) if isinstance(draft, dict) and draft.get("id") else (
                    ("download", job["id"]) if isinstance(job, dict) and job.get("id") else None)
                if not anchor:
                    continue
                # A subsequent operation on a job already associated with a draft
                # joins that draft task, instead of inventing a parallel task.
                existing = None
                for job_id in refs.get("job_id", []):
                    existing = conn.execute("""SELECT t.id FROM memory_tasks t
                        JOIN memory_task_entities e ON e.task_id=t.id
                        WHERE t.user_id=? AND t.session_id=? AND e.entity_type='job_id' AND e.entity_id=?""",
                        (episode["user_id"], episode["session_id"], job_id)).fetchone()
                    if existing:
                        break
                identifier = existing["id"] if existing else task_id(episode["user_id"], episode["session_id"], *anchor)
                now = store._now()
                conn.execute("""INSERT INTO memory_tasks VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at""",
                    (identifier, episode["user_id"], episode["session_id"], *anchor, episode["goal"], now, now))
                for key in ("draft_id", "job_id", "playlist_id", "batch_id", "target_playlist_id"):
                    for value in refs.get(key, []):
                        conn.execute("INSERT OR IGNORE INTO memory_task_entities VALUES(?,?,?)", (identifier, key, value))
                conn.execute("INSERT OR IGNORE INTO memory_task_episodes VALUES(?,?)", (identifier, episode["id"]))
                linked.add(identifier)
            if linked:
                context = {**episode["context"], "task_ids": sorted(linked)}
                conn.execute("UPDATE memory_episodes SET context_json=? WHERE id=? AND user_id=?",
                    (json.dumps(context, ensure_ascii=False), episode["id"], episode["user_id"]))
                episode["context"] = context
    finally:
        conn.close()
    return sorted(linked)


def list_tasks(user_id: str, session_id: str, limit: int = 10) -> list[dict]:
    store.init_memory_db()
    conn = store._connect()
    try:
        rows = conn.execute("SELECT * FROM memory_tasks WHERE user_id=? AND session_id=? ORDER BY updated_at DESC LIMIT ?",
                            (user_id, session_id, min(max(limit, 1), 50))).fetchall()
        tasks = []
        for row in rows:
            item = dict(row)
            refs = {}
            for link in conn.execute("SELECT entity_type,entity_id FROM memory_task_entities WHERE task_id=?", (item["id"],)):
                refs.setdefault(link["entity_type"], []).append(link["entity_id"])
            item["entity_refs"] = refs
            item["episode_ids"] = [link[0] for link in conn.execute("SELECT episode_id FROM memory_task_episodes WHERE task_id=?", (item["id"],))]
            tasks.append(item)
        return tasks
    finally:
        conn.close()
