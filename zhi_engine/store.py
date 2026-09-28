from __future__ import annotations

import copy
import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ImportError as exc:  # pragma: no cover - exercised when dependency is missing
    psycopg = None
    dict_row = None
    Jsonb = None
    _PSYCOPG_IMPORT_ERROR = exc
else:
    _PSYCOPG_IMPORT_ERROR = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return _now()
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return _now()


class EphemeralConversationStore:
    """Process-local fallback used when a serverless deployment has no database.

    The store keeps the API functional without pretending to provide durable
    persistence. Vercel may recycle or route between function instances, so
    callers must treat this data as temporary.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._conversations: dict[str, dict[str, Any]] = {}

    def list_conversations(self) -> list[dict]:
        with self.lock:
            conversations = sorted(
                self._conversations.values(),
                key=lambda item: str(item.get("updatedAt", "")),
                reverse=True,
            )
            return [
                {
                    "id": item["id"],
                    "title": item["title"],
                    "preview": item["preview"],
                    "updatedAt": item["updatedAt"],
                }
                for item in copy.deepcopy(conversations)
            ]

    def get_conversation(self, conversation_id: str) -> dict | None:
        with self.lock:
            conversation = self._conversations.get(conversation_id)
            return copy.deepcopy(conversation) if conversation is not None else None

    def append_messages(
        self,
        conversation_id: str,
        user: dict,
        assistant: dict,
        *,
        plan: str | None = None,
        theme: str | None = None,
    ) -> dict:
        now = _now().isoformat(timespec="seconds")
        with self.lock:
            conversation = self._conversations.setdefault(
                conversation_id,
                {
                    "id": conversation_id,
                    "title": str(user.get("content", ""))[:26] or "新对话",
                    "preview": "",
                    "createdAt": now,
                    "updatedAt": now,
                    "plan": plan or "专业版",
                    "theme": theme or "light",
                    "metadata": {"persistence": "ephemeral"},
                    "messages": [],
                },
            )
            conversation["preview"] = str(assistant.get("content", ""))[:72]
            conversation["updatedAt"] = now
            if plan:
                conversation["plan"] = str(plan)
            if theme:
                conversation["theme"] = str(theme)
            conversation["messages"].extend((copy.deepcopy(user), copy.deepcopy(assistant)))
            return copy.deepcopy(conversation)

    def record_analysis(
        self,
        *,
        source: str,
        dataset: object,
        result: dict,
        conversation_id: str | None = None,
    ) -> None:
        # Deliberately do not retain potentially sensitive datasets in a
        # process-global list when durable storage has not been configured.
        return None

    def record_report(
        self,
        *,
        title: str,
        result: dict,
        report_format: str,
        conversation_id: str | None = None,
    ) -> None:
        return None


class ConversationStore:
    def __init__(self, database_url: str, legacy_json_path: Path | None = None) -> None:
        if psycopg is None:  # pragma: no cover - dependency error is environment-specific
            raise RuntimeError(
                "PostgreSQL storage requires the `psycopg[binary]` package. "
                "Install the project dependencies with `python -m pip install -e .` first."
            ) from _PSYCOPG_IMPORT_ERROR

        self.database_url = database_url.strip()
        if not self.database_url:
            raise ValueError("database_url is required")

        self.legacy_json_path = legacy_json_path
        self.lock = threading.Lock()
        self._ensure_schema()
        self._migrate_legacy_json()

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self.database_url) as conn:
            yield conn

    def _ensure_schema(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                preview TEXT NOT NULL DEFAULT '',
                plan TEXT NOT NULL DEFAULT '专业版',
                theme TEXT NOT NULL DEFAULT 'dark',
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS messages (
                id BIGSERIAL PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                payload JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_messages_conversation_created_at
            ON messages (conversation_id, created_at, id)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_conversations_updated_at
            ON conversations (updated_at DESC, id DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS analysis_runs (
                id BIGSERIAL PRIMARY KEY,
                source TEXT NOT NULL,
                conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
                dataset JSONB NOT NULL,
                result JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_analysis_runs_created_at
            ON analysis_runs (created_at DESC, id DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS reports (
                id BIGSERIAL PRIMARY KEY,
                conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
                title TEXT NOT NULL,
                report_format TEXT NOT NULL,
                result JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_reports_created_at
            ON reports (created_at DESC, id DESC)
            """,
        ]
        with self.lock:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    for statement in statements:
                        cur.execute(statement)

    def _migrate_legacy_json(self) -> None:
        path = self.legacy_json_path
        if path is None or not path.exists():
            return

        with self.lock:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM conversations")
                    count = int(cur.fetchone()[0])
                    if count:
                        return

                    try:
                        conversations = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        return
                    if not isinstance(conversations, list):
                        return

                    for item in conversations:
                        if not isinstance(item, dict):
                            continue
                        conversation_id = str(item.get("id") or uuid.uuid4())
                        title = str(item.get("title") or "新对话")
                        preview = str(item.get("preview") or "")
                        updated_at = _parse_timestamp(item.get("updatedAt"))
                        created_at = _parse_timestamp(item.get("createdAt") or item.get("updatedAt"))
                        metadata = {
                            key: value
                            for key, value in item.items()
                            if key not in {"id", "title", "preview", "messages", "updatedAt", "createdAt"}
                        }
                        cur.execute(
                            """
                            INSERT INTO conversations (
                                id, title, preview, metadata, created_at, updated_at
                            ) VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                            """,
                            (
                                conversation_id,
                                title,
                                preview,
                                Jsonb(metadata),
                                created_at,
                                updated_at,
                            ),
                        )

                        messages = item.get("messages") or []
                        if not isinstance(messages, list):
                            continue
                        for message in messages:
                            if not isinstance(message, dict):
                                continue
                            role = str(message.get("role") or "assistant")
                            content = str(message.get("content") or "")
                            created_at = _parse_timestamp(message.get("time") or message.get("createdAt"))
                            cur.execute(
                                """
                                INSERT INTO messages (conversation_id, role, content, created_at, payload)
                                VALUES (%s, %s, %s, %s, %s)
                                """,
                                (
                                    conversation_id,
                                    role,
                                    content,
                                    created_at,
                                    Jsonb(message),
                                ),
                            )
                    conn.commit()

    def list_conversations(self) -> list[dict]:
        with self.lock:
            with self._connect() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT id, title, preview, updated_at
                        FROM conversations
                        ORDER BY updated_at DESC, id DESC
                        """
                    )
                    rows = cur.fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "updatedAt": row["updated_at"].astimezone(timezone.utc).isoformat(timespec="seconds"),
                "preview": row["preview"],
            }
            for row in rows
        ]

    def get_conversation(self, conversation_id: str) -> dict | None:
        with self.lock:
            with self._connect() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT id, title, preview, plan, theme, metadata, created_at, updated_at
                        FROM conversations
                        WHERE id = %s
                        """,
                        (conversation_id,),
                    )
                    conversation = cur.fetchone()
                    if conversation is None:
                        return None

                    cur.execute(
                        """
                        SELECT role, content, created_at, payload
                        FROM messages
                        WHERE conversation_id = %s
                        ORDER BY created_at ASC, id ASC
                        """,
                        (conversation_id,),
                    )
                    messages = cur.fetchall()

        return {
            "id": conversation["id"],
            "title": conversation["title"],
            "preview": conversation["preview"],
            "updatedAt": conversation["updated_at"].astimezone(timezone.utc).isoformat(timespec="seconds"),
            "createdAt": conversation["created_at"].astimezone(timezone.utc).isoformat(timespec="seconds"),
            "plan": conversation["plan"],
            "theme": conversation["theme"],
            "metadata": conversation["metadata"] or {},
            "messages": [self._row_to_message(row) for row in messages],
        }

    def append_messages(
        self,
        conversation_id: str,
        user: dict,
        assistant: dict,
        *,
        plan: str | None = None,
        theme: str | None = None,
    ) -> dict:
        with self.lock:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO conversations (id, title, preview, created_at, updated_at)
                        VALUES (%s, %s, %s, NOW(), NOW())
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            conversation_id,
                            str(user.get("content", ""))[:26] or "新对话",
                            str(user.get("content", ""))[:72],
                        ),
                    )
                    cur.execute(
                        """
                        UPDATE conversations
                        SET title = COALESCE(NULLIF(title, ''), %s),
                            preview = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (
                            str(user.get("content", ""))[:26] or "新对话",
                            str(assistant.get("content", ""))[:72],
                            conversation_id,
                        ),
                    )
                    if plan or theme:
                        cur.execute(
                            """
                            UPDATE conversations
                            SET plan = COALESCE(%s, plan),
                                theme = COALESCE(%s, theme),
                                updated_at = NOW()
                            WHERE id = %s
                            """,
                            (
                                plan,
                                theme,
                                conversation_id,
                            ),
                        )
                    for message in (user, assistant):
                        created_at = _parse_timestamp(message.get("time"))
                        cur.execute(
                            """
                            INSERT INTO messages (conversation_id, role, content, created_at, payload)
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (
                                conversation_id,
                                str(message.get("role", "assistant")),
                                str(message.get("content", "")),
                                created_at,
                                Jsonb(message),
                            ),
                        )

        conversation = self.get_conversation(conversation_id)
        if conversation is None:  # pragma: no cover - defensive guard
            raise RuntimeError(f"conversation {conversation_id} vanished after insert")
        return conversation

    def record_analysis(
        self,
        *,
        source: str,
        dataset: object,
        result: dict,
        conversation_id: str | None = None,
    ) -> None:
        with self.lock:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO analysis_runs (source, conversation_id, dataset, result)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (
                            source,
                            conversation_id,
                            Jsonb(dataset),
                            Jsonb(result),
                        ),
                    )

    def record_report(
        self,
        *,
        title: str,
        result: dict,
        report_format: str,
        conversation_id: str | None = None,
    ) -> None:
        with self.lock:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO reports (conversation_id, title, report_format, result)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (
                            conversation_id,
                            title,
                            report_format,
                            Jsonb(result),
                        ),
                    )

    def _row_to_message(self, row: dict) -> dict:
        payload = dict(row.get("payload") or {})
        payload["role"] = row["role"]
        payload["content"] = row["content"]
        payload["time"] = payload.get("time") or row["created_at"].astimezone(timezone.utc).isoformat(timespec="seconds")
        return payload
