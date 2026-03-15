"""
Camada de persistência SQLite — substitui os arquivos JSON.
WAL mode para leituras concorrentes + escritas atômicas.

Tabelas: conversations, tasks, schedules, analytics
"""

import json
import sqlite3
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class BotDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_tables()

    @property
    def _conn(self) -> sqlite3.Connection:
        """Conexão por-thread para segurança."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False,
            )
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_tables(self):
        with self._lock:
            c = self._conn
            c.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    user_id INTEGER PRIMARY KEY,
                    messages TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    steps TEXT NOT NULL DEFAULT '[]',
                    current_step INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'in_progress',
                    progress TEXT NOT NULL DEFAULT '',
                    context TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    hour INTEGER NOT NULL,
                    minute INTEGER NOT NULL DEFAULT 0,
                    weekdays TEXT NOT NULL DEFAULT 'all',
                    day_of_month INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analytics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    bot TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    tool_calls INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS approved_users (
                    user_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '',
                    approved_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions_archive (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_name TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    messages TEXT NOT NULL,
                    archived_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_archive_date ON sessions_archive(archived_at);
                CREATE INDEX IF NOT EXISTS idx_analytics_ts ON analytics(ts);
                CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id);
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            """)

    # ── Migração de JSON legado ──────────────────────────────────────────────

    def migrate_from_json(self, bot_dir: Path):
        """Importa dados dos arquivos JSON antigos (se existirem) para o SQLite."""
        migrated = []

        # Conversations
        conv_file = bot_dir / "conversations.json"
        if conv_file.exists():
            try:
                data = json.loads(conv_file.read_text(encoding="utf-8"))
                for uid_str, msgs in data.items():
                    self.save_conversation(int(uid_str), msgs)
                conv_file.rename(conv_file.with_suffix(".json.bak"))
                migrated.append("conversations")
            except Exception as e:
                logger.warning(f"Falha ao migrar conversations.json: {e}")

        # Tasks
        tasks_file = bot_dir / "tasks.json"
        if tasks_file.exists():
            try:
                data = json.loads(tasks_file.read_text(encoding="utf-8"))
                for tid, t in data.items():
                    self._conn.execute(
                        "INSERT OR IGNORE INTO tasks (id, user_id, title, description, steps, "
                        "current_step, status, progress, context, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (tid, t["user_id"], t["title"], t.get("description", ""),
                         json.dumps(t.get("steps", [])), t.get("current_step", 0),
                         t.get("status", "in_progress"), t.get("progress", ""),
                         json.dumps(t.get("context", {})),
                         t.get("created_at", datetime.now().isoformat()),
                         t.get("updated_at", datetime.now().isoformat())),
                    )
                self._conn.commit()
                tasks_file.rename(tasks_file.with_suffix(".json.bak"))
                migrated.append("tasks")
            except Exception as e:
                logger.warning(f"Falha ao migrar tasks.json: {e}")

        # Schedules
        sched_file = bot_dir / "schedules.json"
        if sched_file.exists():
            try:
                data = json.loads(sched_file.read_text(encoding="utf-8"))
                for s in data:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO schedules (id, user_id, hour, minute, weekdays, message, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (s["id"], s.get("user_id", 0), s["hour"], s.get("minute", 0),
                         s.get("weekdays", "all"), s["message"],
                         s.get("created_at", datetime.now().isoformat())),
                    )
                self._conn.commit()
                sched_file.rename(sched_file.with_suffix(".json.bak"))
                migrated.append("schedules")
            except Exception as e:
                logger.warning(f"Falha ao migrar schedules.json: {e}")

        # Analytics JSONL
        analytics_file = bot_dir / "analytics.jsonl"
        if analytics_file.exists():
            try:
                count = 0
                with open(analytics_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        entry = json.loads(line)
                        self._conn.execute(
                            "INSERT INTO analytics (ts, bot, user_id, input_tokens, output_tokens, "
                            "tool_calls, latency_ms, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (entry.get("ts", ""), entry.get("bot", ""),
                             entry.get("user_id", 0), entry.get("input_tokens", 0),
                             entry.get("output_tokens", 0), entry.get("tool_calls", 0),
                             entry.get("latency_ms", 0), entry.get("error", "")),
                        )
                        count += 1
                self._conn.commit()
                analytics_file.rename(analytics_file.with_suffix(".jsonl.bak"))
                migrated.append(f"analytics ({count} events)")
            except Exception as e:
                logger.warning(f"Falha ao migrar analytics.jsonl: {e}")

        # Approved users
        users_file = bot_dir / "approved_users.json"
        if users_file.exists():
            try:
                data = json.loads(users_file.read_text(encoding="utf-8"))
                for uid_str, info in data.items():
                    self._conn.execute(
                        "INSERT OR IGNORE INTO approved_users (user_id, name, username, approved_at) "
                        "VALUES (?, ?, ?, ?)",
                        (int(uid_str), info.get("name", ""), info.get("username", ""),
                         datetime.now().isoformat()),
                    )
                self._conn.commit()
                users_file.rename(users_file.with_suffix(".json.bak"))
                migrated.append("approved_users")
            except Exception as e:
                logger.warning(f"Falha ao migrar approved_users.json: {e}")

        if migrated:
            logger.info(f"[db] Migração JSON → SQLite concluída: {', '.join(migrated)}")

    # ── Conversations ────────────────────────────────────────────────────────

    def load_conversation(self, user_id: int) -> list:
        row = self._conn.execute(
            "SELECT messages FROM conversations WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row:
            return json.loads(row["messages"])
        return []

    def save_conversation(self, user_id: int, messages: list):
        self._conn.execute(
            "INSERT INTO conversations (user_id, messages, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET messages = excluded.messages, updated_at = excluded.updated_at",
            (user_id, json.dumps(messages, ensure_ascii=False), datetime.now().isoformat()),
        )
        self._conn.commit()

    def clear_conversation(self, user_id: int):
        self._conn.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
        self._conn.commit()

    def archive_conversation(self, user_id: int, messages: list, bot_name: str):
        """Arquiva sessão atual antes de limpar. Ignora se não há mensagens."""
        if not messages:
            return
        self._conn.execute(
            "INSERT INTO sessions_archive (bot_name, user_id, messages, archived_at) VALUES (?, ?, ?, ?)",
            (bot_name, user_id, json.dumps(messages, ensure_ascii=False), datetime.now().isoformat()),
        )
        self._conn.commit()

    def get_archived_sessions(self, date_str: str, bot_name: str) -> list[dict]:
        """Retorna sessões arquivadas de um dia específico (YYYY-MM-DD) para um bot."""
        rows = self._conn.execute(
            "SELECT user_id, messages, archived_at FROM sessions_archive "
            "WHERE bot_name = ? AND date(archived_at) = ? ORDER BY archived_at ASC",
            (bot_name, date_str),
        ).fetchall()
        return [{"user_id": r["user_id"], "messages": json.loads(r["messages"]), "archived_at": r["archived_at"]} for r in rows]

    def delete_old_archives(self, keep_days: int = 30):
        """Remove sessões arquivadas com mais de keep_days dias."""
        cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat()
        cur = self._conn.execute("DELETE FROM sessions_archive WHERE archived_at < ?", (cutoff,))
        self._conn.commit()
        return cur.rowcount

    # ── Tasks ────────────────────────────────────────────────────────────────

    def task_create(self, user_id: int, tid: str, title: str,
                    description: str, steps: list[str]) -> str:
        now = datetime.now().isoformat()
        self._conn.execute(
            "INSERT INTO tasks (id, user_id, title, description, steps, "
            "current_step, status, progress, context, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, 'in_progress', '', '{}', ?, ?)",
            (tid, user_id, title, description, json.dumps(steps), now, now),
        )
        self._conn.commit()
        return tid

    def task_update(self, tid: str, **kwargs) -> bool:
        row = self._conn.execute("SELECT id FROM tasks WHERE id = ?", (tid,)).fetchone()
        if not row:
            return False
        sets, vals = [], []
        for k, v in kwargs.items():
            if k in ("steps", "context"):
                v = json.dumps(v, ensure_ascii=False)
            sets.append(f"{k} = ?")
            vals.append(v)
        sets.append("updated_at = ?")
        vals.append(datetime.now().isoformat())
        vals.append(tid)
        self._conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", vals)
        self._conn.commit()
        return True

    def task_get(self, tid: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        return self._row_to_task(row) if row else None

    def tasks_for_user(self, user_id: int, status: str | None = None) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE user_id = ? AND status = ? ORDER BY updated_at DESC",
                (user_id, status),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def tasks_interrupted(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE status = 'in_progress'"
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def _row_to_task(self, row) -> dict:
        return {
            "id": row["id"], "user_id": row["user_id"],
            "title": row["title"], "description": row["description"],
            "steps": json.loads(row["steps"]),
            "current_step": row["current_step"], "status": row["status"],
            "progress": row["progress"],
            "context": json.loads(row["context"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    # ── Schedules ────────────────────────────────────────────────────────────

    def schedule_add(self, sid: str, user_id: int, hour: int, minute: int,
                     weekdays: str, message: str, day_of_month: int = 0):
        self._conn.execute(
            "INSERT INTO schedules (id, user_id, hour, minute, weekdays, day_of_month, message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, user_id, hour, minute, weekdays, day_of_month, message, datetime.now().isoformat()),
        )
        self._conn.commit()

    def schedule_remove(self, sid: str):
        self._conn.execute("DELETE FROM schedules WHERE id = ?", (sid,))
        self._conn.commit()

    def schedule_list(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM schedules").fetchall()
        return [dict(r) for r in rows]

    # ── Analytics ────────────────────────────────────────────────────────────

    def log_event(self, bot: str, user_id: int, input_tokens: int,
                  output_tokens: int, tool_calls: int, latency_ms: int,
                  error: str = ""):
        self._conn.execute(
            "INSERT INTO analytics (ts, bot, user_id, input_tokens, output_tokens, "
            "tool_calls, latency_ms, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), bot, user_id, input_tokens,
             output_tokens, tool_calls, latency_ms, error),
        )
        self._conn.commit()

    # ── Approved Users ──────────────────────────────────────────────────────

    def load_approved(self) -> dict[int, dict]:
        rows = self._conn.execute("SELECT * FROM approved_users").fetchall()
        return {r["user_id"]: {"name": r["name"], "username": r["username"]} for r in rows}

    def approve_user(self, user_id: int, name: str, username: str):
        self._conn.execute(
            "INSERT INTO approved_users (user_id, name, username, approved_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET name = excluded.name, username = excluded.username",
            (user_id, name, username, datetime.now().isoformat()),
        )
        self._conn.commit()

    def revoke_user(self, user_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM approved_users WHERE user_id = ?", (user_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def is_approved(self, user_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM approved_users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row is not None

    # ── Analytics ────────────────────────────────────────────────────────────

    def get_summary(self, days: int = 1) -> dict:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        row = self._conn.execute("""
            SELECT
                COUNT(*) as msgs,
                COALESCE(SUM(input_tokens), 0) as input_tokens,
                COALESCE(SUM(output_tokens), 0) as output_tokens,
                COALESCE(SUM(tool_calls), 0) as tool_calls,
                COALESCE(SUM(CASE WHEN error != '' THEN 1 ELSE 0 END), 0) as errors
            FROM analytics WHERE ts >= ?
        """, (cutoff,)).fetchone()
        inp = row["input_tokens"]
        out = row["output_tokens"]
        cost = (inp * 3.0 / 1_000_000) + (out * 15.0 / 1_000_000)
        return {
            "msgs": row["msgs"], "input_tokens": inp,
            "output_tokens": out, "tool_calls": row["tool_calls"],
            "errors": row["errors"], "cost_usd": round(cost, 4),
        }
