"""Colas durables para una instancia. Requiere un disco persistente en producci�n."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Repository:
    def __init__(self, path, account="demo", max_attempts=5):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path, self.account, self.max_attempts = str(path), account, max_attempts
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS inbox (
                  id INTEGER PRIMARY KEY, account TEXT NOT NULL, provider_id TEXT NOT NULL,
                  phone TEXT NOT NULL, message TEXT NOT NULL, attachment INTEGER NOT NULL,
                  received_at REAL NOT NULL, available_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                  attempts INTEGER NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
                  batch_key TEXT,
                  image_json TEXT,
                  UNIQUE(account, provider_id));
                CREATE TABLE IF NOT EXISTS outbox (
                  id INTEGER PRIMARY KEY, account TEXT NOT NULL, phone TEXT NOT NULL,
                  body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                  available_at REAL NOT NULL, lease_until REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions (
                  account TEXT NOT NULL, phone TEXT NOT NULL, state TEXT NOT NULL,
                  PRIMARY KEY(account,phone));
                CREATE TABLE IF NOT EXISTS history (
                  id INTEGER PRIMARY KEY, account TEXT NOT NULL, phone TEXT NOT NULL,
                  role TEXT NOT NULL, content TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS inbox_due ON inbox(account,status,available_at);
                CREATE INDEX IF NOT EXISTS outbox_due ON outbox(account,status,available_at);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(inbox)")}
            if "batch_key" not in columns:
                db.execute("ALTER TABLE inbox ADD COLUMN batch_key TEXT")
            if "image_json" not in columns:
                db.execute("ALTER TABLE inbox ADD COLUMN image_json TEXT")
            outbox_columns = {row[1] for row in db.execute("PRAGMA table_info(outbox)")}
            if "dedupe_key" not in outbox_columns:
                db.execute("ALTER TABLE outbox ADD COLUMN dedupe_key TEXT")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS outbox_alert_key "
                "ON outbox(account,dedupe_key) WHERE dedupe_key IS NOT NULL"
            )
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def enqueue(self, provider_id, phone, message, attachment=False, delay=0, image=None):
        now = time.time()
        with self.transaction() as db:
            result = db.execute(
                """INSERT OR IGNORE INTO inbox
                (account,provider_id,phone,message,attachment,received_at,available_at,image_json)
                VALUES (?,?,?,?,?,?,?,?)""",
                (self.account, str(provider_id), phone, message, int(attachment), now, now + delay, json.dumps(image) if image else None),
            )
            return result.rowcount == 1

    def claim_batch(self):
        now = time.time()
        with self.transaction() as db:
            db.execute(
                "UPDATE inbox SET status='pending' WHERE account=? AND status='processing' AND lease_until<?",
                (self.account, now),
            )
            first = db.execute(
                """SELECT i.* FROM inbox i WHERE i.account=? AND i.status='pending'
                AND i.available_at<=? AND NOT EXISTS (
                  SELECT 1 FROM inbox newer WHERE newer.account=i.account AND newer.phone=i.phone
                  AND newer.status='pending' AND newer.available_at>?)
                AND NOT EXISTS (SELECT 1 FROM outbox o WHERE o.account=i.account AND o.phone=i.phone
                                AND o.status IN ('pending','sending','failed'))
                AND NOT EXISTS (SELECT 1 FROM inbox blocked WHERE blocked.account=i.account
                                AND blocked.phone=i.phone AND blocked.status IN ('failed','processing'))
                ORDER BY i.id LIMIT 1""",
                (self.account, now, now),
            ).fetchone()
            if not first:
                return []
            if first["batch_key"]:
                rows = db.execute(
                    "SELECT * FROM inbox WHERE account=? AND batch_key=? AND status='pending' ORDER BY id",
                    (self.account, first["batch_key"]),
                ).fetchall()
                batch_key = first["batch_key"]
            else:
                rows = db.execute(
                    "SELECT * FROM inbox WHERE account=? AND phone=? AND status='pending' AND batch_key IS NULL ORDER BY id LIMIT 50",
                    (self.account, first["phone"]),
                ).fetchall()
                batch_key = ":".join(str(row["id"]) for row in rows)
            for row in rows:
                db.execute(
                    "UPDATE inbox SET status='processing',attempts=attempts+1,lease_until=?,batch_key=? WHERE id=?",
                    (now + 600, batch_key, row["id"]),
                )
            return [dict(row) for row in rows]

    def session(self, phone):
        with self.transaction() as db:
            row = db.execute(
                "SELECT state FROM sessions WHERE account=? AND phone=?", (self.account, phone)
            ).fetchone()
            return json.loads(row["state"]) if row else {}

    def recent_history(self, phone, limit=12):
        with self.transaction() as db:
            rows = db.execute(
                "SELECT role,content FROM history WHERE account=? AND phone=? ORDER BY id DESC LIMIT ?",
                (self.account, phone, limit),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    def complete(self, batch, reply, state, history_reply=None, support_alert=None):
        phone = batch[0]["phone"]
        with self.transaction() as db:
            # Una pausa administrativa durante la generaci�n no debe ser sobrescrita.
            current = db.execute(
                "SELECT state FROM sessions WHERE account=? AND phone=?", (self.account, phone)
            ).fetchone()
            was_paused = bool(current and json.loads(current["state"]).get("human_paused"))
            if was_paused:
                paused_state = json.loads(current["state"])
                if state.get("last_order_id"):
                    paused_state["last_order_id"] = state["last_order_id"]
                state, reply = paused_state, None
            db.execute(
                "INSERT OR REPLACE INTO sessions(account,phone,state) VALUES (?,?,?)",
                (self.account, phone, json.dumps(state, ensure_ascii=False)),
            )
            if reply:
                for offset in range(0, len(reply), 3500):
                    db.execute(
                        "INSERT INTO outbox(account,phone,body,available_at) VALUES (?,?,?,?)",
                        (self.account, phone, reply[offset : offset + 3500], time.time()),
                    )
            if support_alert and state.get("human_paused") and not was_paused:
                recipient, body, dedupe_key = support_alert
                db.execute(
                    "INSERT OR IGNORE INTO outbox(account,phone,body,available_at,dedupe_key) "
                    "VALUES (?,?,?,?,?)",
                    (self.account, recipient, body, time.time(), dedupe_key),
                )
            for role, content in (
                ("user", " / ".join(row["message"] for row in batch)),
                ("assistant", history_reply if history_reply is not None else reply),
            ):
                if content:
                    db.execute(
                        "INSERT INTO history(account,phone,role,content) VALUES (?,?,?,?)",
                        (self.account, phone, role, content),
                    )
            for row in batch:
                db.execute("UPDATE inbox SET status='done',lease_until=0,image_json=NULL WHERE id=?", (row["id"],))

    def fail_batch(self, batch):
        with self.transaction() as db:
            for row in batch:
                attempts = db.execute(
                    "SELECT attempts FROM inbox WHERE id=?", (row["id"],)
                ).fetchone()[0]
                db.execute(
                    "UPDATE inbox SET status=?,available_at=?,lease_until=0 WHERE id=?",
                    (
                        "failed" if attempts >= self.max_attempts else "pending",
                        time.time() + min(300, 2**attempts * 5),
                        row["id"],
                    ),
                )

    def claim_outbox(self):
        now = time.time()
        with self.transaction() as db:
            db.execute(
                "UPDATE outbox SET status='pending' WHERE account=? AND status='sending' AND lease_until<?",
                (self.account, now),
            )
            row = db.execute(
                """SELECT o.* FROM outbox o WHERE o.account=? AND o.status='pending' AND o.available_at<=?
                AND NOT EXISTS (SELECT 1 FROM outbox older WHERE older.account=o.account AND older.phone=o.phone
                                AND older.id<o.id AND older.status IN ('pending','sending','failed'))
                ORDER BY o.id LIMIT 1""",
                (self.account, now),
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE outbox SET status='sending',attempts=attempts+1,lease_until=? WHERE id=?",
                (now + 90, row["id"]),
            )
            return dict(row)

    def finish_outbox(self, row_id, accepted):
        with self.transaction() as db:
            row = db.execute(
                "SELECT attempts FROM outbox WHERE id=? AND account=?", (row_id, self.account)
            ).fetchone()
            attempts = row["attempts"]
            status = (
                "sent" if accepted else ("failed" if attempts >= self.max_attempts else "pending")
            )
            # Borrar credenciales del spool despu�s de la aceptaci�n por el proveedor.
            db.execute(
                "UPDATE outbox SET status=?,body=CASE WHEN ? THEN '' ELSE body END,available_at=?,lease_until=0 WHERE id=?",
                (status, accepted, time.time() + min(300, 2**attempts * 5), row_id),
            )

    def cancel_outbox(self, row_id):
        with self.transaction() as db:
            db.execute(
                "UPDATE outbox SET status='cancelled',body='',lease_until=0 WHERE id=? AND account=?",
                (row_id, self.account),
            )

    def pause(self, phone, paused):
        state = self.session(phone)
        state.update(human_paused=paused, manual_paused=paused, pending=None)
        if paused:
            state.pop("recharge_amount", None)
            state.pop("recharge_focus", None)
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO sessions(account,phone,state) VALUES (?,?,?)",
                (self.account, phone, json.dumps(state)),
            )
            if paused:
                db.execute(
                    "UPDATE outbox SET status='cancelled',body='' WHERE account=? AND phone=? AND status='pending' AND dedupe_key IS NULL",
                    (self.account, phone),
                )

    def stats(self):
        with self.transaction() as db:
            return {
                table: {
                    row["status"]: row["count"]
                    for row in db.execute(
                        f"SELECT status,count(*) AS count FROM {table} WHERE account=? GROUP BY status",
                        (self.account,),
                    )
                }
                for table in ("inbox", "outbox")
            }

    def paused_conversations(self):
        with self.transaction() as db:
            return [
                {"phone": row["phone"]}
                for row in db.execute(
                    "SELECT phone,state FROM sessions WHERE account=?", (self.account,)
                )
                if json.loads(row["state"]).get("human_paused")
            ]

    def retry_failed(self):
        with self.transaction() as db:
            for table in ("inbox", "outbox"):
                db.execute(
                    f"UPDATE {table} SET status='pending',attempts=0,available_at=?,lease_until=0 WHERE account=? AND status='failed'",
                    (time.time(), self.account),
                )
