import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass

from course_data import CourseError


@dataclass(frozen=True)
class Submission:
    key: str
    chapter: str
    phase: str
    payload: dict


def create_records(database):
    database.execute("""CREATE TABLE IF NOT EXISTS submission_records (
        key TEXT PRIMARY KEY, scope TEXT NOT NULL, chapter TEXT NOT NULL,
        phase TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}'
    )""")


def has_table(database, name):
    return database.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def row_to_submission(row):
    return Submission(*row[:3], json.loads(row[3]))


def find_attempt(path, key):
    if not key or not path.is_file():
        return None
    with closing(sqlite3.connect(path)) as database:
        if has_table(database, "submission_records"):
            row = database.execute(
                "SELECT key, chapter, phase, payload FROM submission_records WHERE key=?", (key,)
            ).fetchone()
            if row:
                return row_to_submission(row)
        if has_table(database, "attempts") and database.execute(
            "SELECT 1 FROM attempts WHERE key=?", (key,)
        ).fetchone():
            return Submission(key, "", "legacy", {})
    return None


def pending_attempt(path, scope):
    if not scope or not path.is_file():
        return None
    with closing(sqlite3.connect(path)) as database:
        if not has_table(database, "submission_records"):
            return None
        row = database.execute(
            "SELECT key, chapter, phase, payload FROM submission_records WHERE scope=? "
            "AND phase IN ('prepared', 'pending') ORDER BY rowid LIMIT 1", (scope,)
        ).fetchone()
    return row_to_submission(row) if row else None


def reserve_attempt(path, key, scope, chapter):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY)")
        create_records(database)
        try:
            database.execute("INSERT INTO attempts (key) VALUES (?)", (key,))
            database.execute(
                "INSERT INTO submission_records (key, scope, chapter, phase) VALUES (?, ?, ?, 'prepared')",
                (key, scope, chapter),
            )
        except sqlite3.IntegrityError as error:
            raise CourseError("本章节已有提交记录，请重新运行核对平台状态") from error


def begin_submission(path, key, payload):
    with closing(sqlite3.connect(path)) as database, database:
        changed = database.execute(
            "UPDATE submission_records SET phase='pending', payload=? WHERE key=? AND phase='prepared'",
            (json.dumps(payload, ensure_ascii=False), key),
        ).rowcount
        if changed != 1:
            raise CourseError("无法确认本次提交记录，已停止")


def resolve_attempt(path, key, phase, reason=""):
    with closing(sqlite3.connect(path)) as database, database:
        create_records(database)
        database.execute(
            "INSERT INTO submission_records (key, scope, chapter, phase, payload) VALUES (?, '', '', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET phase=excluded.phase, payload=excluded.payload",
            (key, phase, json.dumps({"reason": reason}, ensure_ascii=False)),
        )


def release_attempt(path, key):
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("DELETE FROM submission_records WHERE key=?", (key,))
        database.execute("DELETE FROM attempts WHERE key=?", (key,))
