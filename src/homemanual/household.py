"""Local "My home" appliance records, stored in SQLite on this computer.

One household, one installation, one file (`data/household.db` by default).
There are no accounts, no address fields, no uploads and no network calls. Each
record names an appliance (a nickname), the appliance type and the exact
curated model from the downloaded manual library, so asking a question about a
saved appliance binds that single manual before the search runs.

Safety properties enforced here:

* every statement is parameterised - user text is never concatenated into SQL;
* nicknames are stripped, length-bounded and control-character free;
* a model is only accepted when it is an exact curated alias of a manual that
  is actually in the library, so an unknown model is refused instead of being
  silently mapped to a different appliance's manual;
* writes run inside a transaction and roll back on failure, and the schema
  migration is idempotent.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA_VERSION = 1
MAX_NICKNAME_CHARS = 40
MAX_MODEL_CHARS = 40
MAX_APPLIANCES = 60


class HouseholdError(RuntimeError):
    """A refused household operation. `code` is safe to branch on in the UI."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_nickname(value: object) -> str:
    if not isinstance(value, str):
        raise HouseholdError("invalid_name", "The name must be text.")
    name = " ".join(value.split())
    if not name:
        raise HouseholdError("invalid_name", "Give the appliance a name, for example kitchen dishwasher.")
    if len(name) > MAX_NICKNAME_CHARS:
        raise HouseholdError(
            "invalid_name", f"Names are limited to {MAX_NICKNAME_CHARS} characters."
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise HouseholdError("invalid_name", "Names cannot contain control characters.")
    return name


class HouseholdStore:
    """Thin, transactional wrapper over the local SQLite file."""

    def __init__(self, path: Path | None = None, index=None) -> None:
        self.path = Path(path) if path else config.household_db_path()
        self.index = index
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    # -- schema -------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def migrate(self) -> None:
        """Create or upgrade the schema. Safe to run on every startup."""
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS appliances (
                        id TEXT PRIMARY KEY,
                        nickname TEXT NOT NULL,
                        category TEXT NOT NULL,
                        model TEXT NOT NULL,
                        doc_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_appliances_created "
                    "ON appliances(created_at)"
                )
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                connection.execute("COMMIT")
            except sqlite3.Error as exc:
                connection.execute("ROLLBACK")
                raise HouseholdError("storage_error", f"Could not prepare the local store: {exc}") from exc
            finally:
                connection.close()

    # -- validation ---------------------------------------------------
    def _resolve_model(self, category: str, model: str) -> str:
        if not isinstance(model, str):
            raise HouseholdError("invalid_model", "Choose a model from the list.")
        model = model.strip()
        if not model or len(model) > MAX_MODEL_CHARS:
            raise HouseholdError("invalid_model", "Choose a model from the list.")
        if category not in config.CATEGORIES:
            raise HouseholdError("invalid_category", f"Unknown appliance type: {category}")
        if self.index is None:
            raise HouseholdError("storage_error", "The manual library is not loaded.")
        matches = [
            doc_id
            for doc_id in self.index.docs_matching_model(model)
            if (doc := self.index.doc(doc_id)) and doc.category == category
        ]
        if not matches:
            raise HouseholdError(
                "unsupported_model",
                f"There is no {config.CATEGORY_LABELS.get(category, category).lower()} manual "
                f"in this library for {model}, so it was not saved. Pick a model from the list "
                "or search the library directly.",
            )
        return matches[0]

    # -- reads --------------------------------------------------------
    def _annotate(self, row: sqlite3.Row) -> dict:
        doc = self.index.doc(row["doc_id"]) if self.index else None
        still_known = False
        if self.index is not None:
            for doc_id in self.index.docs_matching_model(row["model"]):
                candidate = self.index.doc(doc_id)
                if candidate is not None and candidate.category == row["category"]:
                    still_known = True
                    break
        return {
            "id": row["id"],
            "nickname": row["nickname"],
            "category": row["category"],
            "category_label": config.CATEGORY_LABELS.get(row["category"], row["category"]),
            "model": row["model"],
            "doc_id": row["doc_id"],
            "brand": doc.brand if doc else "",
            "title": doc.title if doc else "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "supported": still_known,
        }

    def list(self) -> list[dict]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM appliances ORDER BY created_at, id"
            ).fetchall()
        finally:
            connection.close()
        return [self._annotate(row) for row in rows]

    def get(self, appliance_id: str) -> dict | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM appliances WHERE id = ?", (appliance_id,)
            ).fetchone()
        finally:
            connection.close()
        return self._annotate(row) if row else None

    def resolve(self, appliance_id: str) -> tuple[str, str]:
        """Return (category, model) for a saved appliance, or raise."""
        record = self.get(appliance_id)
        if record is None:
            raise HouseholdError(
                "unknown_appliance", "That saved appliance no longer exists. Add it again."
            )
        if not record["supported"]:
            raise HouseholdError(
                "unsupported_appliance",
                f"The library no longer has a manual for {record['model']}, so this saved "
                "appliance cannot be used. Remove it or pick another model.",
            )
        return record["category"], record["model"]

    # -- writes -------------------------------------------------------
    def add(self, nickname: object, category: object, model: object) -> dict:
        name = _clean_nickname(nickname)
        if not isinstance(category, str) or category not in config.CATEGORIES:
            raise HouseholdError("invalid_category", "Choose an appliance type from the list.")
        doc_id = self._resolve_model(category, model)  # type: ignore[arg-type]
        stamp = _now()
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                count = connection.execute("SELECT COUNT(*) FROM appliances").fetchone()[0]
                if count >= MAX_APPLIANCES:
                    raise HouseholdError(
                        "too_many",
                        f"This installation already holds {MAX_APPLIANCES} saved appliances.",
                    )
                appliance_id = self._new_id(connection)
                connection.execute(
                    "INSERT INTO appliances "
                    "(id, nickname, category, model, doc_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (appliance_id, name, category, model.strip(), doc_id, stamp, stamp),
                )
                connection.execute("COMMIT")
            except sqlite3.Error as exc:
                connection.execute("ROLLBACK")
                raise HouseholdError("storage_error", f"Could not save the appliance: {exc}") from exc
            except HouseholdError:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
        return self.get(appliance_id)  # type: ignore[return-value]

    def rename(self, appliance_id: object, nickname: object) -> dict:
        if not isinstance(appliance_id, str):
            raise HouseholdError("invalid_id", "Unknown saved appliance.")
        name = _clean_nickname(nickname)
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    "UPDATE appliances SET nickname = ?, updated_at = ? WHERE id = ?",
                    (name, _now(), appliance_id),
                )
                if cursor.rowcount == 0:
                    raise HouseholdError("unknown_appliance", "That saved appliance no longer exists.")
                connection.execute("COMMIT")
            except sqlite3.Error as exc:
                connection.execute("ROLLBACK")
                raise HouseholdError("storage_error", f"Could not rename the appliance: {exc}") from exc
            except HouseholdError:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
        return self.get(appliance_id)  # type: ignore[return-value]

    def remove(self, appliance_id: object) -> bool:
        """Remove a saved appliance record. Never touches the source PDFs."""
        if not isinstance(appliance_id, str):
            raise HouseholdError("invalid_id", "Unknown saved appliance.")
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    "DELETE FROM appliances WHERE id = ?", (appliance_id,)
                )
                removed = cursor.rowcount > 0
                connection.execute("COMMIT")
            except sqlite3.Error as exc:
                connection.execute("ROLLBACK")
                raise HouseholdError("storage_error", f"Could not remove the appliance: {exc}") from exc
            finally:
                connection.close()
        return removed

    def _new_id(self, connection: sqlite3.Connection) -> str:
        for _ in range(8):
            candidate = f"app_{secrets.token_hex(6)}"
            exists = connection.execute(
                "SELECT 1 FROM appliances WHERE id = ?", (candidate,)
            ).fetchone()
            if not exists:
                return candidate
        raise HouseholdError("storage_error", "Could not allocate a unique appliance id.")
