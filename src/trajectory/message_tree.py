"""消息树 parentId + fork — open-webui build_fork_history + pi追加树移植（P0-10升级方向）。

调研来源（源码级，本地clone ~/agent-research-src）：
- open-webui backend/open_webui/utils/chat_fork.py build_fork_history（41行全文精读）：
  message带parentId成树；fork=从源消息沿parentId回溯到根（seen set环检测，
  parentId数据损坏不能挂死调用方），分支复制为新chat；消息不存在/空chat显式raise
- pi会话=追加树(id/parentId)（SUMMARY.md P0-10四方定案，与open-webui同构）：
  canonical agent_messages增加parent_message_id列——每条新消息parent=会话内
  上一条消息id（线性追加=树的主干），历史行迁移时按会话内id序回填
- agno idempotency_key（evolution-engine-patterns §5.2"注释即规格"）：
  fork目标主键确定性 fork:{source_session}:{message_id}，同分支点重复fork
  幂等返回既有会话（status=duplicate），绝不产生重复副本

失败可见（evolution-engine-patterns §1.1 mem0"禁止静默降级"）：
环→CycleError；消息不存在→MessageNotFoundError；空会话→EmptyChatError；
源会话不存在→SessionNotFoundError。调用方按异常类型映射HTTP状态码，绝不静默空fork。

schema迁移契约：ensure_parent_column() probe+ALTER幂等迁移（attachments列既有
惯例同款），缺列时自动ALTER+backfill历史线性链——所有读写路径（ws_chat/
soulmate_agent/import_to_db/sessions_api读端点/fork端点）都先过此函数，
任何一个先被触发即完成全库迁移。
"""

from __future__ import annotations

import sqlite3
import time

PARENT_COLUMN = "parent_message_id"


class MessageTreeError(ValueError):
    """消息树操作基类错误（fail-visible）。"""


class CycleError(MessageTreeError):
    """parent链存在环（数据损坏）——open-webui 'message branch contains a cycle'。"""


class MessageNotFoundError(MessageTreeError):
    """source_message_id不在会话消息表中。"""


class EmptyChatError(MessageTreeError):
    """会话没有任何消息可fork——open-webui 'chat has no messages to fork'。"""


class SessionNotFoundError(MessageTreeError):
    """源会话在agent_sessions中不存在。"""


def ensure_parent_column(conn: sqlite3.Connection) -> bool:
    """幂等列迁移：agent_messages缺parent_message_id列则ALTER并回填历史线性链。

    probe+ALTER模式与attachments列既有惯例同款（soulmate_agent._save_message /
    sessions_api.get_session_messages）。返回True=本轮执行了迁移。
    迁移后立即commit——ALTER+backfill对后续同库连接立即可见。
    """
    try:
        conn.execute(f"SELECT {PARENT_COLUMN} FROM agent_messages LIMIT 1")
        return False
    except sqlite3.OperationalError:
        conn.execute(f"ALTER TABLE agent_messages ADD COLUMN {PARENT_COLUMN} INTEGER")
        backfill_linear_parents(conn)
        conn.commit()
        return True


def backfill_linear_parents(conn: sqlite3.Connection) -> int:
    """历史行回填：会话内按id序，每条的parent=同会话内前一条消息id（线性主干）。

    pi追加树语义：旧数据本来就是顺序写入的，主干链即历史真实顺序。
    首条消息保持NULL（根节点）。幂等——已设置的行不动。返回回填行数。
    """
    cur = conn.execute(
        f"""UPDATE agent_messages SET {PARENT_COLUMN} = (
                SELECT m2.id FROM agent_messages m2
                WHERE m2.session_id = agent_messages.session_id
                  AND m2.id < agent_messages.id
                ORDER BY m2.id DESC LIMIT 1
            ) WHERE {PARENT_COLUMN} IS NULL"""
    )
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def build_branch(messages: list[dict], source_message_id) -> list[dict]:
    """open-webui build_fork_history语义：从source沿parent回溯到根。

    输入messages：dict列表，必须有"id"键；parent取"parent_id"或
    "parent_message_id"（sqlite Row别名兼容）。返回根→source的有序列表
    （open-webui返回reversed(branch)同序）。

    异常（fail-visible，禁止静默返回部分分支）：
    - messages为空 → EmptyChatError
    - source不在表中 → MessageNotFoundError
    - parent链成环 → CycleError（seen set检测，损坏数据不挂死）
    """
    mmap: dict[str, dict] = {}
    for m in messages:
        mid = m.get("id")
        if mid is not None:
            mmap[str(mid)] = m
    if not mmap:
        raise EmptyChatError("chat has no messages to fork")

    branch: list[dict] = []
    seen: set[str] = set()
    message_id = str(source_message_id)
    while message_id:
        if message_id in seen:
            raise CycleError(f"message branch contains a cycle (at id={message_id})")
        seen.add(message_id)
        message = mmap.get(message_id)
        if message is None:
            raise MessageNotFoundError(f"message not found: {message_id}")
        branch.append(message)
        parent = message.get("parent_id")
        if parent is None:
            parent = message.get("parent_message_id")
        message_id = str(parent) if parent else ""
    branch.reverse()
    return branch


def fork_session(
    db_path: str, source_session_id: str, message_id, agent_id: str | None = None
) -> dict:
    """把source会话中根→message_id的分支链复制为新会话（open-webui fork语义）。

    - 新会话主键确定性 fork:{source}:{message_id}（agno幂等键）：同分支点重复调用
      返回status=duplicate且零重复写入
    - 新消息行在新会话内parent重新链接（首条NULL，链式指向新行id）——分支复制后
      是独立的追加树，后续在新会话继续聊天parent自然续链
    - 只复制分支链上的消息，source会话中fork点之后/兄弟分支的消息不带入
      （open-webui build_fork_history同款语义）

    返回stats dict：status(forked|duplicate)/fork_session_id/source_session_id/
    fork_point_message_id/messages_copied/title(forked时)。
    """
    source_session_id = str(source_session_id)
    target_id = f"fork:{source_session_id}:{message_id}"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_parent_column(conn)
        # attachments列probe（fork INSERT引用该列，老schema库同样自愈）
        try:
            conn.execute("SELECT attachments FROM agent_messages LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE agent_messages ADD COLUMN attachments TEXT")
            conn.commit()
        # kilocode #9记忆marker列（fork随消息复制——审计标记跟消息走）
        try:
            conn.execute("SELECT metadata FROM agent_messages LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE agent_messages ADD COLUMN metadata TEXT")
            conn.commit()
        src = conn.execute(
            "SELECT id, agent_id, title FROM agent_sessions WHERE id = ?",
            (source_session_id,),
        ).fetchone()
        if src is None:
            raise SessionNotFoundError(f"session not found: {source_session_id}")
        rows = conn.execute(
            "SELECT id, role, content, timestamp, attachments, metadata, "
            f"{PARENT_COLUMN} AS parent_id "
            "FROM agent_messages WHERE session_id = ? ORDER BY id",
            (source_session_id,),
        ).fetchall()
        branch = build_branch([dict(r) for r in rows], message_id)

        existing = conn.execute(
            "SELECT id, message_count FROM agent_sessions WHERE id = ?",
            (target_id,),
        ).fetchone()
        if existing:
            # 幂等：同分支点重复fork返回既有会话，零写入（agno idempotency_key）
            return {
                "status": "duplicate",
                "fork_session_id": target_id,
                "source_session_id": source_session_id,
                "fork_point_message_id": message_id,
                "messages_copied": existing["message_count"] or 0,
            }

        now = time.time()
        src_title = src["title"] or "New Chat"
        title = ("Fork: " + src_title)[:80]
        conn.execute(
            "INSERT INTO agent_sessions "
            "(id, agent_id, title, created_at, last_activity_at, message_count, archived) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (
                target_id,
                agent_id or src["agent_id"] or "imported",
                title,
                now,
                now,
                len(branch),
            ),
        )
        prev_id = None
        for m in branch:
            cur = conn.execute(
                "INSERT INTO agent_messages "
                "(session_id, role, content, timestamp, attachments, parent_message_id, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    target_id,
                    m["role"],
                    m["content"],
                    m["timestamp"],
                    m.get("attachments"),
                    prev_id,
                    m.get("metadata"),
                ),
            )
            prev_id = cur.lastrowid
        conn.commit()
        return {
            "status": "forked",
            "fork_session_id": target_id,
            "source_session_id": source_session_id,
            "fork_point_message_id": message_id,
            "messages_copied": len(branch),
            "title": title,
        }
    finally:
        conn.close()
