"""Opt-in local document retrieval. File contents are evidence, never instructions."""

import os
import json
import re
import sqlite3
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from xml.etree import ElementTree

from pydantic import BaseModel, Field
from typing import Literal


class FileAccessRequest(BaseModel):
    operation: Literal["snapshot", "authorize", "remove", "refresh"] = "snapshot"
    path: str = Field(default="", max_length=4096)


class FileEvidenceService:
    TEXT_TYPES = {".txt", ".md", ".rst", ".py", ".swift", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".csv", ".tex"}
    SKIP_DIRS = {"library", "node_modules", "dist", "build", "venv", "site-packages", "applications", "system", "private", "volumes"}
    SECRET_NAMES = {".env", "id_rsa", "id_ed25519", "credentials", "credentials.json", "secrets.json", "keychain", "passwords.txt"}

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS roots (path TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE IF NOT EXISTS documents (path TEXT PRIMARY KEY, root TEXT, mtime INTEGER, size INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS scan_state (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(path UNINDEXED, title, text, tokenize='porter unicode61')")
        self.path.chmod(0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def snapshot(self) -> dict:
        with self._db() as db:
            roots = [row[0] for row in db.execute("SELECT path FROM roots ORDER BY path")]
            count = db.execute("SELECT count(*) FROM documents").fetchone()[0]
            scan = db.execute("SELECT payload FROM scan_state WHERE key='last_scan'").fetchone()
        try:
            import pypdf  # noqa: F401
            pdf = True
        except ImportError:
            pdf = False
        return {"roots": roots, "indexed_files": count, "pdf_available": pdf, "refresh": json.loads(scan[0]) if scan else None}

    @classmethod
    def safe_path(cls, path: Path) -> bool:
        if path.is_symlink():
            return False
        for part in path.parts:
            name = part.lower()
            if name.startswith(".") or name in cls.SKIP_DIRS or name in cls.SECRET_NAMES:
                return False
            if "keychain" in name or "password" in name or "secret" in name or name.endswith((".pem", ".key", ".p12", ".kdbx")):
                return False
        return True

    def handle(self, payload: dict) -> dict:
        request = FileAccessRequest.model_validate(payload)
        if request.operation in {"authorize", "remove"}:
            supplied = Path(request.path).expanduser()
            if request.operation == "authorize" and supplied.is_symlink():
                raise ValueError("Choose a real folder, not a symbolic link.")
            root = supplied.resolve() if request.operation == "authorize" else supplied.absolute()
            if request.operation == "authorize":
                if not root.is_dir() or not self.safe_path(root) or root == Path("/") or root == Path.home():
                    raise ValueError("Choose a specific project or document folder, not your entire home or a sensitive directory.")
                with self._db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    exists = db.execute("SELECT 1 FROM roots WHERE path=?", (str(root),)).fetchone()
                    if not exists and db.execute("SELECT count(*) FROM roots").fetchone()[0] >= 12:
                        raise ValueError("Folder limit reached (12). Remove a folder first.")
                    db.execute("INSERT OR IGNORE INTO roots VALUES (?)", (str(root),))
            else:
                with self._db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("DELETE FROM passages WHERE path IN (SELECT path FROM documents WHERE root=?)", (str(root),))
                    db.execute("DELETE FROM documents WHERE root=?", (str(root),))
                    db.execute("DELETE FROM roots WHERE path=?", (str(root),))
                    db.execute("DELETE FROM scan_state WHERE key='last_scan'")
        result = self.snapshot()
        if request.operation in {"authorize", "refresh"}:
            preferred = str(root) if request.operation == "authorize" else str(Path(request.path).expanduser().resolve()) if request.path else ""
            result["refresh"] = self.refresh(preferred)
            result.update(self.snapshot())
        return result

    def refresh(self, preferred_root: str = "") -> dict:
        roots = self.snapshot()["roots"]
        if preferred_root:
            if preferred_root not in roots:
                raise ValueError("Refresh is limited to approved folders.")
            roots = [preferred_root]
        deadline = time.monotonic() + 6
        visited = indexed = skipped = 0
        for root_text in roots:
            root = Path(root_text)
            complete = True
            seen = set()
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if self.safe_path(Path(directory) / d))
                if time.monotonic() > deadline or visited >= 500:
                    complete = False
                    break
                for name in sorted(files):
                    if time.monotonic() > deadline or visited >= 500:
                        complete = False
                        break
                    path = Path(directory) / name
                    if not self.safe_path(path) or path.suffix.lower() not in self.TEXT_TYPES | {".pdf", ".docx"}:
                        continue
                    visited += 1
                    seen.add(str(path))
                    try:
                        if self._index(path, root_text):
                            indexed += 1
                    except (OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError):
                        self._forget(str(path))
                        skipped += 1
                if not complete:
                    break
            if complete:
                with self._db() as db:
                    for (path,) in db.execute("SELECT path FROM documents WHERE root=?", (root_text,)).fetchall():
                        if path not in seen:
                            db.execute("DELETE FROM passages WHERE path=?", (path,))
                            db.execute("DELETE FROM documents WHERE path=?", (path,))
        result = {"visited": visited, "updated": indexed, "skipped": skipped, "partial": visited >= 500 or time.monotonic() > deadline}
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO scan_state VALUES ('last_scan',?)", (json.dumps(result),))
        return result

    def _read(self, path: Path) -> str:
        size = path.stat().st_size
        suffix = path.suffix.lower()
        if size > (8_000_000 if suffix in {".pdf", ".docx"} else 512_000):
            raise ValueError("File exceeds read limit.")
        if suffix == ".docx":
            with zipfile.ZipFile(path) as archive:
                try:
                    info = archive.getinfo("word/document.xml")
                except KeyError as exc:
                    raise ValueError("DOCX has no document XML.") from exc
                if info.file_size > 2_000_000:
                    raise ValueError("Document XML exceeds read limit.")
                tree = ElementTree.fromstring(archive.read(info))
                text = " ".join(tree.itertext())
        elif suffix == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(path)
                if reader.is_encrypted:
                    raise ValueError("Encrypted PDFs are not indexed.")
                text = "\n".join(page.extract_text() or "" for page in reader.pages[:12])
            except Exception as exc:
                raise ValueError("PDF could not be read; install pypdf for text PDFs.") from exc
        else:
            with path.open("rb") as file:
                data = file.read(512_001)
            if len(data) > 512_000:
                raise ValueError("File exceeds read limit.")
            if b"\x00" in data:
                raise ValueError("Binary file is not text.")
            text = data.decode("utf-8", errors="replace")
        # Index only a bounded excerpt; never describe this as a complete file read.
        return text[:40_000]

    def _index(self, path: Path, root: str) -> bool:
        resolved = path.resolve()
        if not self.safe_path(path) or not self.safe_path(resolved) or not resolved.is_relative_to(Path(root)):
            raise ValueError("File is outside its authorized folder.")
        stat = path.stat()
        with self._db() as db:
            old = db.execute("SELECT mtime,size FROM documents WHERE path=?", (str(path),)).fetchone()
        if old == (stat.st_mtime_ns, stat.st_size):
            return False
        text = self._read(path)
        if path.resolve() != resolved or path.stat().st_mtime_ns != stat.st_mtime_ns:
            raise ValueError("File changed during extraction; retry after it settles.")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            # A folder may have been revoked while extraction was running.
            if not db.execute("SELECT 1 FROM roots WHERE path=?", (root,)).fetchone():
                return False
            db.execute("DELETE FROM passages WHERE path=?", (str(path),))
            db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?)", (str(path), root, stat.st_mtime_ns, stat.st_size))
            for offset in range(0, len(text), 900):
                db.execute("INSERT INTO passages(path,title,text) VALUES (?,?,?)", (str(path), path.name, text[offset:offset + 1100]))
        return True

    def _forget(self, path: str):
        with self._db() as db:
            db.execute("DELETE FROM passages WHERE path=?", (path,))
            db.execute("DELETE FROM documents WHERE path=?", (path,))

    def retrieve(self, query: str) -> list[dict]:
        query = query[:4096]
        named = re.findall(r"[\w.-]+\.(?:pdf|docx|txt|md|rst|py|swift|js|ts|tsx|jsx|html|css|csv|tex)\b", query.lower())
        def matches_name(path):
            return not named or Path(path).name.lower() in query.lower() or Path(path).name.lower() in named
        stop = {"the", "and", "for", "not", "you", "are", "can", "what", "which", "where", "when", "how", "does", "about", "this", "that", "with", "from", "please", "tell", "file", "files", "document", "summarize", "explain", "read", "have", "your", "mine", "could", "would", "should", "find"}
        terms = list(dict.fromkeys(word for word in re.findall(r"\w+", query.lower()) if len(word) >= 3 and word not in stop))[:10]
        if not terms:
            return []
        expression = " OR ".join('"' + word + '"' for word in terms)
        filter_sql = ""
        params = [expression]
        if named:
            placeholders = ",".join("?" for _ in named)
            filter_sql = f" AND (lower(title) IN ({placeholders}) OR instr(?,lower(title))>0)"
            params.extend(named)
            params.append(query.lower())
        with self._db() as db:
            candidates = db.execute("SELECT path FROM passages WHERE passages MATCH ?" + filter_sql + " ORDER BY bm25(passages) LIMIT 12", params).fetchall()
        # Revalidate paths and refresh changed candidate files before using cached passages.
        for (path_text,) in dict.fromkeys(candidates):
            if not matches_name(path_text):
                continue
            try:
                with self._db() as db:
                    row = db.execute("SELECT root FROM documents WHERE path=?", (path_text,)).fetchone()
                    allowed = row and db.execute("SELECT 1 FROM roots WHERE path=?", row).fetchone()
                if not allowed:
                    self._forget(path_text)
                    continue
                self._index(Path(path_text), row[0])
            except (OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError):
                self._forget(path_text)
        with self._db() as db:
            rows = db.execute("SELECT path,title,text FROM passages WHERE passages MATCH ?" + filter_sql + " ORDER BY bm25(passages) LIMIT 6", params).fetchall()
        result = []
        seen = set()
        for path, title, text in rows:
            if path not in seen and matches_name(path):
                result.append({"path": path, "title": title, "excerpt": text[:900]})
                seen.add(path)
            if len(result) == 3:
                break
        return result
