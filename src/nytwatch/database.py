from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from nytwatch.models import (
    Batch,
    BatchStatus,
    Finding,
    FindingSource,
    FindingStatus,
    Scan,
    ScanStatus,
    new_id,
    now_iso,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS findings (
    id              TEXT PRIMARY KEY,
    scan_id         TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    severity        TEXT NOT NULL,
    category        TEXT NOT NULL,
    confidence      TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    line_start      INTEGER NOT NULL,
    line_end        INTEGER NOT NULL,
    code_snippet    TEXT NOT NULL,
    suggested_fix   TEXT,
    fix_diff        TEXT,
    can_auto_fix    INTEGER NOT NULL DEFAULT 0,
    reasoning       TEXT NOT NULL,
    test_code       TEXT,
    test_description TEXT,
    include_test    INTEGER NOT NULL DEFAULT 1,
    locations       TEXT,
    source          TEXT NOT NULL DEFAULT 'project',
    status          TEXT NOT NULL DEFAULT 'pending',
    batch_id        TEXT,
    fingerprint     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    reviewed_at     TEXT,
    FOREIGN KEY (scan_id) REFERENCES scans(id)
);

CREATE TABLE IF NOT EXISTS scans (
    id              TEXT PRIMARY KEY,
    scan_type       TEXT NOT NULL,
    system_name     TEXT,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    base_commit     TEXT NOT NULL DEFAULT '',
    files_scanned   INTEGER DEFAULT 0,
    findings_count  INTEGER DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS batches (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    branch_name     TEXT,
    build_log       TEXT,
    test_log        TEXT,
    commit_sha      TEXT,
    pr_url          TEXT,
    finding_ids     TEXT NOT NULL DEFAULT '[]',
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS source_dirs (
    path            TEXT PRIMARY KEY,
    source_type     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_findings_file ON findings(file_path);
CREATE INDEX IF NOT EXISTS idx_findings_fingerprint ON findings(fingerprint);
CREATE INDEX IF NOT EXISTS idx_findings_scan ON findings(scan_id);
CREATE INDEX IF NOT EXISTS idx_findings_batch ON findings(batch_id);
CREATE INDEX IF NOT EXISTS idx_findings_source ON findings(source);

CREATE TABLE IF NOT EXISTS scan_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id     TEXT NOT NULL,
    logged_at   TEXT NOT NULL,
    level       TEXT NOT NULL,
    logger      TEXT NOT NULL,
    message     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scan_logs_scan ON scan_logs(scan_id);

CREATE TABLE IF NOT EXISTS systems (
    name              TEXT PRIMARY KEY,
    source_dir        TEXT NOT NULL DEFAULT '',
    paths             TEXT NOT NULL DEFAULT '[]',
    min_confidence    TEXT,
    file_extensions   TEXT,
    claude_fast_mode  INTEGER,
    sort_order        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS finding_chats (
    id          TEXT PRIMARY KEY,
    finding_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (finding_id) REFERENCES findings(id)
);
CREATE INDEX IF NOT EXISTS idx_finding_chats_finding ON finding_chats(finding_id);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Open the connection eagerly so write methods can call self.conn
        # freely inside a self._lock block without re-entering the lock
        # (non-reentrant threading.Lock would deadlock otherwise).
        c = sqlite3.connect(str(self.db_path), check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        # NORMAL sync is safe in WAL mode and avoids an fsync per commit.
        c.execute("PRAGMA synchronous=NORMAL")
        self._conn: sqlite3.Connection = c
        # Single write lock serialises all DML across threads.
        self._lock = threading.Lock()

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def init_schema(self):
        with self._lock:
            self.conn.executescript(SCHEMA_SQL)
            self._migrate()
            self.conn.commit()

    def _migrate(self):
        """Apply incremental schema migrations for existing databases."""
        sys_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(systems)").fetchall()}
        if "source_dir" not in sys_cols:
            self.conn.execute(
                "ALTER TABLE systems ADD COLUMN source_dir TEXT NOT NULL DEFAULT ''"
            )

        finding_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(findings)").fetchall()}
        if "include_test" not in finding_cols:
            self.conn.execute(
                "ALTER TABLE findings ADD COLUMN include_test INTEGER NOT NULL DEFAULT 1"
            )
        if "locations" not in finding_cols:
            self.conn.execute(
                "ALTER TABLE findings ADD COLUMN locations TEXT"
            )

    def close(self):
        with self._lock:
            self._conn.close()

    # --- Config ---

    def get_config(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_config(self, key: str, value: str):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (key, value),
            )
            self.conn.commit()

    # --- Scans ---

    def insert_scan(self, scan: Scan):
        with self._lock:
            self.conn.execute(
                """INSERT INTO scans (id, scan_type, system_name, started_at,
                   completed_at, base_commit, files_scanned, findings_count, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan.id, scan.scan_type.value, scan.system_name, scan.started_at,
                    scan.completed_at, scan.base_commit, scan.files_scanned,
                    scan.findings_count, scan.status.value,
                ),
            )
            self.conn.commit()

    def update_scan(self, scan_id: str, **kwargs):
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v.value if hasattr(v, "value") else v)
        vals.append(scan_id)
        with self._lock:
            self.conn.execute(
                f"UPDATE scans SET {', '.join(sets)} WHERE id = ?", vals
            )
            self.conn.commit()

    def delete_scan(self, scan_id: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM scan_logs WHERE scan_id = ?", (scan_id,))
            self.conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
            self.conn.commit()

    def get_scan(self, scan_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM scans WHERE id = ?", (scan_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_scans(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM scans ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_running_scan(self) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM scans WHERE status = 'running' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def fail_stale_scans(self) -> int:
        """Mark any scans still in 'running' state as failed (left over from a crashed session)."""
        from nytwatch.models import now_iso
        with self._lock:
            cursor = self.conn.execute(
                "UPDATE scans SET status = 'failed', completed_at = ? WHERE status = 'running'",
                (now_iso(),),
            )
            self.conn.commit()
        return cursor.rowcount

    # --- Findings ---

    def insert_finding(self, finding: Finding):
        with self._lock:
            self.conn.execute(
                """INSERT INTO findings (id, scan_id, title, description, severity,
                   category, confidence, file_path, line_start, line_end,
                   code_snippet, suggested_fix, fix_diff, can_auto_fix, reasoning,
                   test_code, test_description, locations, source, status, batch_id, fingerprint, created_at, reviewed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    finding.id, finding.scan_id, finding.title, finding.description,
                    finding.severity.value, finding.category.value, finding.confidence.value,
                    finding.file_path, finding.line_start, finding.line_end,
                    finding.code_snippet, finding.suggested_fix, finding.fix_diff,
                    int(finding.can_auto_fix), finding.reasoning,
                    finding.test_code, finding.test_description, finding.locations,
                    finding.source.value, finding.status.value, finding.batch_id,
                    finding.fingerprint, finding.created_at, finding.reviewed_at,
                ),
            )
            self.conn.commit()

    def get_finding(self, finding_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM findings WHERE id = ?", (finding_id,)
        ).fetchone()
        if not row:
            return None
        d = {k: row[k] for k in row.keys()}
        d["locations_list"] = self._parse_locations(
            d.get("locations"), d.get("fix_diff"), d.get("file_path")
        )
        return d

    @staticmethod
    def _parse_locations(raw, fix_diff: str = None, primary_file_path: str = None) -> list:
        """Deserialise the locations JSON column into a plain list of dicts.

        If ``locations`` is NULL but ``fix_diff`` references multiple files,
        the extra file paths are inferred from the diff's ``--- a/`` headers
        so grouped findings still display all affected files.
        """
        import re as _re

        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list) and parsed:
                    return parsed
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        # Fallback: extract extra files from a multi-file unified diff
        if not fix_diff:
            return []

        primary_norm = (primary_file_path or "").replace("\\", "/")
        seen: set[str] = {primary_norm}
        extra: list[dict] = []
        for m in _re.finditer(r"^--- a/(.+)$", fix_diff, _re.MULTILINE):
            path = m.group(1).strip()
            norm = path.replace("\\", "/")
            if norm and norm != "/dev/null" and norm not in seen:
                seen.add(norm)
                extra.append({"file_path": path, "line_start": None, "line_end": None})
        return extra

    def list_findings(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        category: Optional[str] = None,
        confidence: Optional[str] = None,
        file_path: Optional[str] = None,
        source: Optional[str] = None,
        path_prefixes: Optional[list[str]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        where = []
        params: list = []

        if status:
            where.append("f.status = ?")
            params.append(status)
        if severity:
            where.append("f.severity = ?")
            params.append(severity)
        if category:
            where.append("f.category = ?")
            params.append(category)
        if confidence:
            where.append("f.confidence = ?")
            params.append(confidence)
        if file_path:
            where.append("f.file_path LIKE ?")
            params.append(f"%{file_path}%")
        if source:
            where.append("f.source = ?")
            params.append(source)
        if path_prefixes:
            # Normalise backslashes in the stored file_path so the filter works
            # on both Windows and Unix regardless of how paths were recorded.
            clauses = " OR ".join("REPLACE(f.file_path, '\\', '/') LIKE ?" for _ in path_prefixes)
            where.append(f"({clauses})")
            for p in path_prefixes:
                params.append(p.replace("\\", "/").rstrip("/") + "/%")

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.extend([limit, offset])

        rows = self.conn.execute(
            f"SELECT f.*, s.system_name FROM findings f "
            f"LEFT JOIN scans s ON f.scan_id = s.id "
            f"{clause} ORDER BY "
            f"CASE f.severity "
            f"  WHEN 'critical' THEN 0 "
            f"  WHEN 'high' THEN 1 "
            f"  WHEN 'medium' THEN 2 "
            f"  WHEN 'low' THEN 3 "
            f"  WHEN 'info' THEN 4 "
            f"END, f.created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        result = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            d["locations_list"] = self._parse_locations(
                d.get("locations"), d.get("fix_diff"), d.get("file_path")
            )
            d["locations_extra"] = len(d["locations_list"])
            result.append(d)
        return result

    def update_finding_status(self, finding_id: str, status: FindingStatus):
        updates = {"status": status.value}
        if status == FindingStatus.APPROVED or status == FindingStatus.REJECTED:
            updates["reviewed_at"] = now_iso()
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [finding_id]
        with self._lock:
            self.conn.execute(f"UPDATE findings SET {sets} WHERE id = ?", vals)
            self.conn.commit()

    def set_finding_include_test(self, finding_id: str, include_test: bool):
        with self._lock:
            self.conn.execute(
                "UPDATE findings SET include_test = ? WHERE id = ?",
                (1 if include_test else 0, finding_id),
            )
            self.conn.commit()

    def update_finding_fields(self, finding_id: str, fields: dict):
        """Update a subset of mutable finding fields (suggested_fix, fix_diff, test_code, test_description)."""
        allowed = {"suggested_fix", "fix_diff", "test_code", "test_description"}
        safe = {k: v for k, v in fields.items() if k in allowed}
        if not safe:
            return
        sets = ", ".join(f"{k} = ?" for k in safe)
        vals = list(safe.values()) + [finding_id]
        with self._lock:
            self.conn.execute(f"UPDATE findings SET {sets} WHERE id = ?", vals)
            self.conn.commit()

    # --- Finding chat ---

    def get_finding_chat(self, finding_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, finding_id, role, content, created_at FROM finding_chats "
            "WHERE finding_id = ? ORDER BY created_at ASC",
            (finding_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def insert_chat_message(self, finding_id: str, role: str, content: str) -> str:
        msg_id = new_id()
        with self._lock:
            self.conn.execute(
                "INSERT INTO finding_chats (id, finding_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (msg_id, finding_id, role, content, now_iso()),
            )
            self.conn.commit()
        return msg_id

    def set_finding_batch(self, finding_id: str, batch_id: str):
        with self._lock:
            self.conn.execute(
                "UPDATE findings SET batch_id = ? WHERE id = ?",
                (batch_id, finding_id),
            )
            self.conn.commit()

    def count_findings_for_path_prefixes(self, path_prefixes: list[str]) -> int:
        if not path_prefixes:
            return 0
        clauses = " OR ".join("file_path LIKE ?" for _ in path_prefixes)
        params = [p.replace("\\", "/").rstrip("/") + "/%" for p in path_prefixes]
        row = self.conn.execute(
            f"SELECT COUNT(*) as cnt FROM findings WHERE ({clauses})", params
        ).fetchone()
        return row["cnt"]

    def has_fingerprint(self, fingerprint: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM findings WHERE fingerprint = ? AND status IN ('pending', 'approved', 'applied', 'verified') LIMIT 1",
            (fingerprint,),
        ).fetchone()
        return row is not None

    def has_fingerprints_batch(self, fingerprints: list[str]) -> set[str]:
        """Return the subset of fingerprints already stored with a live status.

        A single IN query replaces N individual has_fingerprint() calls.
        """
        if not fingerprints:
            return set()
        placeholders = ",".join("?" * len(fingerprints))
        rows = self.conn.execute(
            f"SELECT fingerprint FROM findings "
            f"WHERE fingerprint IN ({placeholders}) "
            f"AND status IN ('pending','approved','applied','verified')",
            fingerprints,
        ).fetchall()
        return {row["fingerprint"] for row in rows}

    def delete_findings_by_filter(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        category: Optional[str] = None,
        confidence: Optional[str] = None,
        file_path: Optional[str] = None,
        source: Optional[str] = None,
        path_prefixes: Optional[list[str]] = None,
    ) -> int:
        """Delete findings matching the given filters. Returns the number of rows deleted."""
        where = []
        params: list = []

        if status:
            where.append("status = ?")
            params.append(status)
        if severity:
            where.append("severity = ?")
            params.append(severity)
        if category:
            where.append("category = ?")
            params.append(category)
        if confidence:
            where.append("confidence = ?")
            params.append(confidence)
        if file_path:
            where.append("file_path LIKE ?")
            params.append(f"%{file_path}%")
        if source:
            where.append("source = ?")
            params.append(source)
        if path_prefixes:
            clauses = " OR ".join("REPLACE(file_path, '\\', '/') LIKE ?" for _ in path_prefixes)
            where.append(f"({clauses})")
            for p in path_prefixes:
                params.append(p.replace("\\", "/").rstrip("/") + "/%")

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._lock:
            # Remove child chat messages first to satisfy the FK constraint
            # (finding_chats.finding_id REFERENCES findings.id, FK enforcement is ON)
            self.conn.execute(
                f"DELETE FROM finding_chats WHERE finding_id IN "
                f"(SELECT id FROM findings {clause})",
                params,
            )
            cursor = self.conn.execute(f"DELETE FROM findings {clause}", params)
            self.conn.commit()
        return cursor.rowcount

    def wipe_findings(self) -> int:
        """Delete ALL findings (and their associated batch links). Returns row count deleted."""
        with self._lock:
            cursor = self.conn.execute("DELETE FROM findings")
            # Orphaned batches are harmless but tidy to remove
            self.conn.execute("DELETE FROM batches")
            self.conn.commit()
        return cursor.rowcount

    def get_approved_findings(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM findings WHERE status = 'approved' ORDER BY file_path, line_start"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_by_status(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM findings GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    def count_by_severity(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT severity, COUNT(*) as cnt FROM findings WHERE status = 'pending' GROUP BY severity"
        ).fetchall()
        return {r["severity"]: r["cnt"] for r in rows}

    # --- Batches ---

    def insert_batch(self, batch: Batch):
        with self._lock:
            self.conn.execute(
                """INSERT INTO batches (id, created_at, status, branch_name,
                   build_log, test_log, commit_sha, pr_url, finding_ids, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    batch.id, batch.created_at, batch.status.value, batch.branch_name,
                    batch.build_log, batch.test_log, batch.commit_sha, batch.pr_url,
                    json.dumps(batch.finding_ids), batch.completed_at,
                ),
            )
            self.conn.commit()

    def update_batch(self, batch_id: str, **kwargs):
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            if k == "finding_ids":
                vals.append(json.dumps(v))
            elif hasattr(v, "value"):
                vals.append(v.value)
            else:
                vals.append(v)
        vals.append(batch_id)
        with self._lock:
            self.conn.execute(
                f"UPDATE batches SET {', '.join(sets)} WHERE id = ?", vals
            )
            self.conn.commit()

    def get_batch(self, batch_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if row:
            d = dict(row)
            d["finding_ids"] = json.loads(d["finding_ids"])
            return d
        return None

    def list_batches(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM batches ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["finding_ids"] = json.loads(d["finding_ids"])
            result.append(d)
        return result

    # --- Source Dirs ---

    def list_source_dirs(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT path, source_type FROM source_dirs ORDER BY path"
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_source_dir(self, path: str, source_type: str):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO source_dirs (path, source_type) VALUES (?, ?)",
                (path, source_type),
            )
            self.conn.commit()

    def delete_source_dir(self, path: str):
        with self._lock:
            self.conn.execute("DELETE FROM source_dirs WHERE path = ?", (path,))
            self.conn.commit()

    def has_source_dir(self, path: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM source_dirs WHERE path = ? LIMIT 1", (path,)
        ).fetchone()
        return row is not None

    def classify_path(self, file_path: str) -> str:
        norm_file = file_path.replace("\\", "/")
        rows = self.conn.execute(
            "SELECT path, source_type FROM source_dirs ORDER BY length(path) DESC"
        ).fetchall()
        for row in rows:
            stored = row["path"].replace("\\", "/")
            prefix = stored.rstrip("/") + "/"
            if norm_file.startswith(prefix) or norm_file.startswith(stored):
                stype = row["source_type"]
                # Normalise legacy "project"/"plugin" values to "active"
                return stype if stype == "ignored" else "active"
        return "active"

    def get_ignored_path_prefixes(self) -> list[str]:
        """Return normalised path prefixes for all directories marked 'ignored'."""
        rows = self.conn.execute(
            "SELECT path FROM source_dirs WHERE source_type = 'ignored'"
        ).fetchall()
        return [row["path"].replace("\\", "/").rstrip("/") + "/" for row in rows]

    # --- Systems ---

    def list_systems(self) -> list[dict]:
        """Return all systems ordered by source_dir then sort_order, as plain dicts."""
        rows = self.conn.execute(
            "SELECT rowid AS id, name, source_dir, paths, min_confidence, file_extensions, claude_fast_mode "
            "FROM systems ORDER BY source_dir, sort_order, name"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["paths"] = json.loads(d["paths"]) if d["paths"] else []
            d["file_extensions"] = json.loads(d["file_extensions"]) if d["file_extensions"] else None
            result.append(d)
        return result

    def list_systems_by_source_dir(self) -> dict[str, list[dict]]:
        """Return systems grouped by source_dir: {source_dir: [system_dicts]}."""
        grouped: dict[str, list[dict]] = {}
        for s in self.list_systems():
            grouped.setdefault(s["source_dir"], []).append(s)
        return grouped

    def replace_systems(self, systems: list[dict]) -> None:
        """Replace ALL systems with the given list (atomic)."""
        with self._lock:
            self.conn.execute("DELETE FROM systems")
            for i, s in enumerate(systems):
                fe = s.get("file_extensions")
                cfm = s.get("claude_fast_mode")
                self.conn.execute(
                    """INSERT INTO systems
                       (name, source_dir, paths, min_confidence, file_extensions, claude_fast_mode, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        s["name"],
                        s.get("source_dir") or "",
                        json.dumps(s.get("paths", [])),
                        s.get("min_confidence") or None,
                        json.dumps(fe) if fe is not None else None,
                        int(cfm) if cfm is not None else None,
                        i,
                    ),
                )
            self.conn.commit()

    def upsert_system(self, system: dict) -> None:
        """Insert or replace a single system, preserving sort_order if it already exists."""
        fe = system.get("file_extensions")
        cfm = system.get("claude_fast_mode")
        existing = self.conn.execute(
            "SELECT sort_order FROM systems WHERE name = ?", (system["name"],)
        ).fetchone()
        sort_order = existing["sort_order"] if existing else (
            self.conn.execute("SELECT COALESCE(MAX(sort_order)+1,0) FROM systems").fetchone()[0]
        )
        with self._lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO systems
                   (name, source_dir, paths, min_confidence, file_extensions, claude_fast_mode, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    system["name"],
                    system.get("source_dir") or "",
                    json.dumps(system.get("paths", [])),
                    system.get("min_confidence") or None,
                    json.dumps(fe) if fe is not None else None,
                    int(cfm) if cfm is not None else None,
                    sort_order,
                ),
            )
            self.conn.commit()

    def delete_system(self, name: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM systems WHERE name = ?", (name,))
            self.conn.commit()

    # --- Scan Logs ---

    def insert_scan_log(self, scan_id: str, level: str, logger_name: str, message: str):
        # No commit here — log lines are committed in batches by the next
        # meaningful write (e.g. update_scan at chunk completion).  The single
        # shared connection can read its own uncommitted rows, so the UI always
        # sees the latest logs without the overhead of an fsync per line.
        with self._lock:
            self.conn.execute(
                "INSERT INTO scan_logs (scan_id, logged_at, level, logger, message) VALUES (?, ?, ?, ?, ?)",
                (scan_id, now_iso(), level, logger_name, message),
            )

    def get_scan_findings_from(self, scan_id: str, offset: int = 0) -> list[dict]:
        """Return findings for a scan ordered by rowid, starting from offset."""
        rows = self.conn.execute(
            """SELECT * FROM findings WHERE scan_id = ?
               ORDER BY rowid LIMIT 500 OFFSET ?""",
            (scan_id, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_scan_log_counts(self) -> dict[str, int]:
        """Return {scan_id: log_line_count} for all scans that have any logs."""
        rows = self.conn.execute(
            "SELECT scan_id, COUNT(*) as cnt FROM scan_logs GROUP BY scan_id"
        ).fetchall()
        return {r["scan_id"]: r["cnt"] for r in rows}

    def get_scan_logs(self, scan_id: str, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, logged_at, level, logger, message FROM scan_logs WHERE scan_id = ? ORDER BY id LIMIT 2000 OFFSET ?",
            (scan_id, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Stats ---

    def get_stats(self) -> dict:
        status_counts = self.count_by_status()
        severity_counts = self.count_by_severity()
        scan_count = self.conn.execute("SELECT COUNT(*) as cnt FROM scans").fetchone()["cnt"]
        batch_count = self.conn.execute("SELECT COUNT(*) as cnt FROM batches").fetchone()["cnt"]
        last_scan = self.conn.execute(
            "SELECT * FROM scans ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

        return {
            "status_counts": status_counts,
            "severity_counts": severity_counts,
            "total_scans": scan_count,
            "total_batches": batch_count,
            "last_scan": dict(last_scan) if last_scan else None,
            "pending_count": status_counts.get("pending", 0),
            "approved_count": status_counts.get("approved", 0),
        }
