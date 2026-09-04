"""Backup / restore for chatbot.db, plus a fully portable export.

Two different things live here on purpose:

- create_backup()/restore_backup() produce a ".lcbotbak" file -- really
  just a zip with a small manifest.json stamped inside it, but with its
  own extension so the app's file-picker only shows/accepts genuine
  LCBot backups, and restore_backup() refuses (with a plain-English
  error) anything that doesn't carry the right manifest. This is meant
  to stop someone accidentally selecting the wrong file and wiping
  their database with garbage -- it is NOT encryption or DRM. Anyone
  can rename a .lcbotbak to .zip and open it; the database inside is
  ordinary SQLite, openable by any SQLite browser.
- export_portable_json() is the deliberately-boring counterpart: a
  plain human-readable JSON dump of the stuff someone would actually
  want if they were leaving LCBot behind entirely (commands, quotes,
  timers, points, settings) -- not gated behind any LCBot-specific
  format at all.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.core.database import Database

BACKUP_MAGIC = "LCBOTBAK1"


class InvalidBackupError(Exception):
    """Raised when a file passed to restore_backup()/read_manifest()
    isn't a genuine LCBot backup -- message is written to be shown to
    the user as-is."""


def create_backup(db_path: str, dest_path: str, app_version: str) -> None:
    """Writes a self-contained LCBot backup to dest_path (conventionally
    ending in .lcbotbak, though nothing here enforces that). Takes a
    consistent point-in-time snapshot via sqlite3's own backup API
    rather than copying the file directly -- a raw file copy could
    catch a live WAL-mode database mid-write; the backup API exists
    specifically to avoid that."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"No database found at {db_path}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        snapshot_path = os.path.join(tmp_dir, "chatbot.db")
        src = sqlite3.connect(db_path)
        try:
            dst = sqlite3.connect(snapshot_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        manifest = {
            "magic": BACKUP_MAGIC,
            "app": "LCBot",
            "app_version": app_version,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        tmp_dest = dest_path + ".tmp"
        try:
            with zipfile.ZipFile(tmp_dest, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))
                zf.write(snapshot_path, "chatbot.db")
            os.replace(tmp_dest, dest_path)
        finally:
            if os.path.exists(tmp_dest):
                os.remove(tmp_dest)


def read_manifest(backup_path: str) -> dict:
    """Validates and returns the manifest of an .lcbotbak file without
    restoring anything -- used to show the user what they're about to
    restore (when it was created) before they confirm. Raises
    InvalidBackupError if backup_path isn't a genuine LCBot backup."""
    try:
        with zipfile.ZipFile(backup_path, "r") as zf:
            names = zf.namelist()
            if "manifest.json" not in names or "chatbot.db" not in names:
                raise InvalidBackupError(
                    "This doesn't look like an LCBot backup file (missing expected contents)."
                )
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise InvalidBackupError("This doesn't look like an LCBot backup file (unreadable contents).") from exc
    except zipfile.BadZipFile as exc:
        raise InvalidBackupError("This doesn't look like an LCBot backup file (not a valid file).") from exc
    except FileNotFoundError as exc:
        raise InvalidBackupError(f"Couldn't find {backup_path}.") from exc

    if manifest.get("magic") != BACKUP_MAGIC:
        raise InvalidBackupError("This doesn't look like an LCBot backup file.")
    return manifest


def restore_backup(backup_path: str, db_path: str) -> dict:
    """Validates the backup, moves any existing database aside as a
    safety net (never deleted automatically -- named
    "<db_path>.pre-restore-<timestamp>.bak"), and extracts the
    backup's database into place. Returns the manifest.

    The caller MUST close any open connection to db_path before
    calling this -- Windows can't overwrite a file that's still open --
    and should have the app restart afterward rather than continuing
    to use the now-stale open connection."""
    manifest = read_manifest(backup_path)  # raises before anything on disk is touched

    if os.path.exists(db_path):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safety_path = f"{db_path}.pre-restore-{stamp}.bak"
        shutil.move(db_path, safety_path)

    with zipfile.ZipFile(backup_path, "r") as zf:
        with zf.open("chatbot.db") as src, open(db_path, "wb") as dst:
            shutil.copyfileobj(src, dst)

    # -wal/-shm files belong to whatever database was at db_path
    # before -- leaving them around would have SQLite try to replay
    # old, no-longer-relevant writes against the just-restored file.
    for suffix in ("-wal", "-shm"):
        stale = db_path + suffix
        if os.path.exists(stale):
            os.remove(stale)

    return manifest


def export_portable_json(db: "Database", dest_path: str) -> None:
    """A plain JSON dump of the stuff someone would actually want if
    they're moving away from LCBot entirely -- readable in any text
    editor or by any other program, not tied to LCBot at all. Doesn't
    include secrets (those live in config.json, never in the database
    this reads from)."""
    data = {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "users": [dict(row) for row in db.query("SELECT * FROM users ORDER BY username")],
        "commands": [dict(row) for row in db.all_commands() if not row["builtin"]],
        "quotes": [dict(row) for row in db.all_quotes()],
        "timers": [dict(row) for row in db.all_timers()],
        "settings": db.all_settings(),
    }
    tmp_dest = dest_path + ".tmp"
    try:
        with open(tmp_dest, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp_dest, dest_path)
    finally:
        if os.path.exists(tmp_dest):
            os.remove(tmp_dest)
