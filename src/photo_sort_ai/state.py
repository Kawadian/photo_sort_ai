from __future__ import annotations

import csv
import json
import sqlite3
import time
from pathlib import Path


class RunLock:
    """OS-owned lock: released on a crash, shared across scan/watch/GUI."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.handle = (directory / "worker.lock").open("a+b")
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if __import__("os").name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            raise RuntimeError("同じ保存先で別の処理が実行中です。先に停止してください。") from error

    def close(self):
        if self.handle.closed:
            return
        self.handle.seek(0)
        if __import__("os").name == "nt":
            import msvcrt
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "results.sqlite3", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS results (
                path TEXT PRIMARY KEY, stamp TEXT NOT NULL, signature TEXT NOT NULL,
                output_signature TEXT NOT NULL, result TEXT NOT NULL, status TEXT NOT NULL,
                updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS failures (
                path TEXT PRIMARY KEY, stamp TEXT NOT NULL, signature TEXT NOT NULL,
                attempts INTEGER NOT NULL, retry_at REAL NOT NULL, error TEXT NOT NULL);
        """)

    def get(self, path: Path):
        row = self.db.execute("SELECT * FROM results WHERE path=?", (str(path),)).fetchone()
        return dict(row) if row else None

    def save(self, path: Path, stamp: str, signature: str, output_signature: str, result: dict, status: str):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?)",
                            (str(path), stamp, signature, output_signature,
                             json.dumps(result, ensure_ascii=False), status, time.time()))
            self.db.execute("DELETE FROM failures WHERE path=?", (str(path),))

    def can_retry(self, path: Path, stamp: str, signature: str, max_retries: int) -> bool:
        row = self.db.execute("SELECT * FROM failures WHERE path=?", (str(path),)).fetchone()
        if not row or row["stamp"] != stamp or row["signature"] != signature:
            return True
        return row["attempts"] < max_retries and row["retry_at"] <= time.time()

    def fail(self, path: Path, stamp: str, signature: str, error: str, retry_seconds: float):
        row = self.db.execute("SELECT * FROM failures WHERE path=?", (str(path),)).fetchone()
        attempts = row["attempts"] + 1 if row and row["stamp"] == stamp and row["signature"] == signature else 1
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO failures VALUES (?,?,?,?,?,?)",
                            (str(path), stamp, signature, attempts,
                             time.time() + retry_seconds * min(attempts, 10), error[:4000]))

    def clear_failures(self):
        with self.db:
            self.db.execute("DELETE FROM failures")

    def rows(self, limit: int | None = None):
        query = "SELECT * FROM results ORDER BY updated DESC"
        parameters = ()
        if limit is not None:
            query += " LIMIT ?"
            parameters = (limit,)
        return [dict(row) for row in self.db.execute(query, parameters)]

    def failures(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM failures ORDER BY retry_at")]

    def close(self):
        self.db.close()


def csv_text(value) -> str:
    text = str(value)
    return "'" + text if text[:1] in {"=", "+", "-", "@", "\t", "\r", "\n"} else text


def export_csv(store: Store, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["path", "AI_rating", "score", "status", "seconds", "source", "reason", "details"])
        for row in store.rows():
            result = json.loads(row["result"])
            writer.writerow([csv_text(row["path"]), result["rating"], round(result["score"], 3),
                             row["status"], result.get("seconds", ""), result.get("source", ""),
                             csv_text(result["reason"]), csv_text(json.dumps(result["details"], ensure_ascii=False))])


def duplicate_groups(store: Store, distance: int = 4) -> list[dict]:
    """BK-tree search + union-find. Reports candidates without touching ratings."""
    if not 0 <= distance <= 16:
        raise ValueError("類似判定距離は0〜16にしてください。")
    rows = []
    for row in store.rows():
        result = json.loads(row["result"])
        if "hash64" in result and Path(row["path"]).is_file():
            rows.append((row, result))
    parents = list(range(len(rows)))

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    tree = None
    for i, (_, result) in enumerate(rows):
        value = int(result["hash64"], 16)
        if tree is None:
            tree = [value, [i], {}]
            continue
        stack = [tree]
        while stack:
            node = stack.pop()
            d = (value ^ node[0]).bit_count()
            if d <= distance:
                for j in node[1]:
                    parents[root(i)] = root(j)
            stack.extend(child for length, child in node[2].items() if d - distance <= length <= d + distance)
        node = tree
        while True:
            d = (value ^ node[0]).bit_count()
            if d == 0:
                node[1].append(i)
                break
            if d not in node[2]:
                node[2][d] = [value, [i], {}]
                break
            node = node[2][d]
    groups = {}
    for i, (row, result) in enumerate(rows):
        groups.setdefault(root(i), []).append({"path": row["path"], "rating": result["rating"], "score": result["score"]})
    output = []
    for members in groups.values():
        if len(members) > 1:
            members.sort(key=lambda item: (-item["score"], item["path"]))
            output.append({"recommended": members[0]["path"], "members": members})
    return output
