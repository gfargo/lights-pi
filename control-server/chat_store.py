"""SQLite-backed chat history for lights-pi.

Each function takes an explicit db_path (str or Path) so they are side-effect-
free in tests (callers pass a temp file) while the Flask app passes CHAT_DB_PATH.

Messages are stored with their content serialised as JSON — this preserves both
plain strings and Anthropic-style block arrays (tool_use / tool_result) without
any lossy conversion.
"""

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import uuid


# Module-level FTS5 flag; set once on first DB open.
_fts5_available: bool | None = None


def _has_fts5(conn: sqlite3.Connection) -> bool:
    global _fts5_available
    if _fts5_available is not None:
        return _fts5_available
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts5_probe")
        _fts5_available = True
    except sqlite3.OperationalError:
        _fts5_available = False
    return _fts5_available


def _connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path) -> None:
    """Create tables, indexes, and FTS5 virtual table if they do not exist."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    try:
        with conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id          TEXT PRIMARY KEY,
                    started_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    title       TEXT NOT NULL DEFAULT '',
                    summary     TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    role            TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    created_at      TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conv
                    ON messages(conversation_id);
            """)

            if _has_fts5(conn):
                conn.executescript("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                        USING fts5(content, content='messages', content_rowid='id');

                    CREATE TRIGGER IF NOT EXISTS messages_ai
                        AFTER INSERT ON messages BEGIN
                            INSERT INTO messages_fts(rowid, content)
                            VALUES (new.id, new.content);
                        END;

                    CREATE TRIGGER IF NOT EXISTS messages_ad
                        AFTER DELETE ON messages BEGIN
                            INSERT INTO messages_fts(messages_fts, rowid, content)
                            VALUES ('delete', old.id, old.content);
                        END;

                    CREATE TRIGGER IF NOT EXISTS messages_au
                        AFTER UPDATE OF content ON messages BEGIN
                            INSERT INTO messages_fts(messages_fts, rowid, content)
                            VALUES ('delete', old.id, old.content);
                            INSERT INTO messages_fts(rowid, content)
                            VALUES (new.id, new.content);
                        END;
                """)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(UTC).isoformat()


def _encode(content) -> str:
    return json.dumps(content)


def _decode(raw: str):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def derive_title(text: str, max_len: int = 60) -> str:
    """Return a short title derived from the first user message text."""
    t = (text or "").strip()
    if not t:
        return "New conversation"
    return t if len(t) <= max_len else t[: max_len - 1] + "…"


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------

def create_conversation(db_path, title: str = "") -> str:
    """Insert a new conversation row and return its UUID."""
    conv_id = str(uuid.uuid4())
    now = _now()
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO conversations (id, started_at, updated_at, title)"
                " VALUES (?,?,?,?)",
                (conv_id, now, now, title),
            )
    finally:
        conn.close()
    return conv_id


def conversation_exists(db_path, conv_id: str) -> bool:
    """Return True if a conversation with this ID exists in the DB."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id=?", (conv_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def delete_conversation(db_path, conv_id: str) -> None:
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
    finally:
        conn.close()


def update_summary(db_path, conv_id: str, summary: str) -> None:
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE conversations SET summary=?, updated_at=? WHERE id=?",
                (summary, _now(), conv_id),
            )
    finally:
        conn.close()


def update_title(db_path, conv_id: str, title: str) -> None:
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE conversations SET title=? WHERE id=?",
                (title, conv_id),
            )
    finally:
        conn.close()


def list_conversations(db_path) -> list:
    """Return all conversations sorted by most-recently-updated."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, title, summary, started_at, updated_at"
            " FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Message CRUD
# ---------------------------------------------------------------------------

def append_messages(db_path, conv_id: str, msgs: list) -> None:
    """Append message rows to a conversation and bump its updated_at."""
    if not msgs:
        return
    now = _now()
    conn = _connect(db_path)
    try:
        with conn:
            for msg in msgs:
                conn.execute(
                    "INSERT INTO messages"
                    " (conversation_id, role, content, created_at)"
                    " VALUES (?,?,?,?)",
                    (conv_id, msg["role"], _encode(msg.get("content", "")), now),
                )
            conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                (now, conv_id),
            )
    finally:
        conn.close()


def get_messages(db_path, conv_id: str) -> list:
    """Return all messages for a conversation in insertion order."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT role, content FROM messages"
            " WHERE conversation_id=? ORDER BY id",
            (conv_id,),
        ).fetchall()
    finally:
        conn.close()
    return [{"role": r["role"], "content": _decode(r["content"])} for r in rows]


def message_count(db_path, conv_id: str) -> int:
    """Return the number of stored messages for a conversation."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id=?", (conv_id,)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_conversations(db_path, query: str) -> list:
    """FTS5 search over message content; falls back to LIKE if FTS5 is absent.

    Returns a list of {conversation_id, title, snippets} dicts,
    grouped by conversation, newest hits first.
    """
    query = (query or "").strip()
    if not query:
        return []

    conn = _connect(db_path)
    try:
        if _has_fts5(conn):
            rows = conn.execute(
                """
                SELECT m.conversation_id, c.title,
                       snippet(messages_fts, 0, '<mark>', '</mark>', '…', 20) AS snippet
                FROM messages_fts
                JOIN messages m ON messages_fts.rowid = m.id
                JOIN conversations c ON c.id = m.conversation_id
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT 40
                """,
                (query,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT m.conversation_id, c.title,
                       SUBSTR(m.content, 1, 200) AS snippet
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE m.content LIKE ?
                ORDER BY m.created_at DESC
                LIMIT 40
                """,
                (f"%{query}%",),
            ).fetchall()
    finally:
        conn.close()

    seen: dict = {}
    for r in rows:
        cid = r["conversation_id"]
        if cid not in seen:
            seen[cid] = {"conversation_id": cid, "title": r["title"], "snippets": []}
        seen[cid]["snippets"].append(r["snippet"])
    return list(seen.values())


# ---------------------------------------------------------------------------
# Fork
# ---------------------------------------------------------------------------

def fork_conversation(db_path, conv_id: str, upto_index: int | None = None) -> str:
    """Create a new conversation that copies messages up to upto_index (exclusive).

    If upto_index is None, all messages are copied.
    Returns the new conversation ID.
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT role, content FROM messages"
            " WHERE conversation_id=? ORDER BY id",
            (conv_id,),
        ).fetchall()
        parent = conn.execute(
            "SELECT title FROM conversations WHERE id=?", (conv_id,)
        ).fetchone()
    finally:
        conn.close()

    if upto_index is not None:
        rows = rows[:upto_index]
    parent_title = (parent["title"] if parent else "") + " (fork)"

    new_id = create_conversation(db_path, title=parent_title)
    if rows:
        append_messages(
            db_path, new_id,
            [{"role": r["role"], "content": _decode(r["content"])} for r in rows],
        )
    return new_id
