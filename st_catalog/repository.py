from __future__ import annotations
import json
import os
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from .domain import Product, CatalogError, dumps, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalogs (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL,
 headers TEXT NOT NULL, imported_at TEXT NOT NULL, export_date TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS products (
 id INTEGER PRIMARY KEY, site TEXT NOT NULL REFERENCES catalogs(id), position INTEGER NOT NULL,
 original TEXT NOT NULL, base TEXT NOT NULL, current TEXT NOT NULL,
 is_new INTEGER NOT NULL DEFAULT 0, deletion TEXT NOT NULL DEFAULT '', absent INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS product_site ON products(site);
CREATE TABLE IF NOT EXISTS history (
 id INTEGER PRIMARY KEY, at TEXT NOT NULL, site TEXT NOT NULL, product_id INTEGER,
 field TEXT NOT NULL, old TEXT NOT NULL, new TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports (
 id INTEGER PRIMARY KEY, site TEXT NOT NULL, at TEXT NOT NULL, filename TEXT NOT NULL,
 export_date TEXT NOT NULL, complete INTEGER NOT NULL, snapshot TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conflicts (
 id INTEGER PRIMARY KEY, site TEXT NOT NULL, product_id INTEGER NOT NULL,
 field TEXT NOT NULL, base TEXT NOT NULL, local TEXT NOT NULL, remote TEXT NOT NULL,
 resolved INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS exports (
 id INTEGER PRIMARY KEY, at TEXT NOT NULL, site TEXT NOT NULL, path TEXT NOT NULL,
 mode TEXT NOT NULL, snapshot TEXT NOT NULL, status TEXT NOT NULL, history_max INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS mappings (
 group_id TEXT NOT NULL, site TEXT NOT NULL, product_id INTEGER NOT NULL UNIQUE REFERENCES products(id),
 PRIMARY KEY(group_id, site));
PRAGMA user_version=1;
"""


def user_directory():
    if sys.platform == "win32":
        root = Path(
            os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        )
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(
            os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        )
    return root / "STCatalogManager"


class Repository:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        if self.db.execute("PRAGMA user_version").fetchone()[0] > 1:
            self.db.close()
            raise CatalogError("База создана более новой версией приложения.")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        if "history_max" not in {
            r[1] for r in self.db.execute("PRAGMA table_info(exports)")
        }:
            self.db.execute(
                "ALTER TABLE exports ADD COLUMN history_max INTEGER NOT NULL DEFAULT 0"
            )
            self.db.commit()
        self.db.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def transaction(self):
        token = "sp_" + uuid.uuid4().hex
        self.db.execute("SAVEPOINT " + token)
        try:
            yield
            self.db.execute("RELEASE " + token)
        except BaseException:
            self.db.execute("ROLLBACK TO " + token)
            self.db.execute("RELEASE " + token)
            raise

    def close(self):
        self.db.close()

    def catalogs(self):
        return [
            dict(r) for r in self.db.execute("SELECT * FROM catalogs ORDER BY rowid")
        ]

    def catalog(self, site):
        row = self.db.execute("SELECT * FROM catalogs WHERE id=?", (site,)).fetchone()
        if row is None:
            raise CatalogError("Каталог не найден.")
        result = dict(row)
        result["headers"] = json.loads(result["headers"])
        return result

    @staticmethod
    def decode(row):
        return Product(
            row["id"],
            row["site"],
            row["position"],
            *[json.loads(row[k]) for k in ("original", "base", "current")],
            bool(row["is_new"]),
            row["deletion"],
            bool(row["absent"]),
        )

    def products(self, site=None, include_absent=False):
        query = "SELECT * FROM products WHERE 1=1"
        args = []
        if site:
            query += " AND site=?"
            args.append(site)
        if not include_absent:
            query += " AND absent=0"
        return [
            self.decode(r)
            for r in self.db.execute(query + " ORDER BY position,id", args)
        ]

    def get(self, product_id):
        row = self.db.execute(
            "SELECT * FROM products WHERE id=?", (product_id,)
        ).fetchone()
        if row is None:
            raise CatalogError("Товар не найден.")
        return self.decode(row)

    def insert(self, site, position, row, is_new=False):
        data = dumps(row)
        return self.db.execute(
            "INSERT INTO products(site,position,original,base,current,is_new) VALUES(?,?,?,?,?,?)",
            (
                site,
                position,
                "{}" if is_new else data,
                "{}" if is_new else data,
                data,
                is_new,
            ),
        ).lastrowid

    def save(self, p):
        self.db.execute(
            "UPDATE products SET base=?,current=?,is_new=?,deletion=?,absent=? WHERE id=?",
            (dumps(p.base), dumps(p.current), p.is_new, p.deletion, p.absent, p.id),
        )

    def audit(self, p, field, old, new, status="Сохранено"):
        self.db.execute(
            "INSERT INTO history(at,site,product_id,field,old,new,status) VALUES(?,?,?,?,?,?,?)",
            (now(), p.site, p.id, field, old, new, status),
        )

    def conflicts(self, site=None):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM conflicts WHERE resolved=0"
                + (" AND site=?" if site else ""),
                (site,) if site else (),
            )
        ]

    def backup(self, destination=None):
        folder = self.path.parent / "backups"
        folder.mkdir(exist_ok=True)
        path = (
            Path(destination)
            if destination
            else folder
            / (now().replace(":", "-") + "-" + uuid.uuid4().hex[:6] + ".sqlite3")
        )
        if path.resolve() == self.path.resolve():
            raise CatalogError("Резервная копия не может заменять рабочую базу.")
        with sqlite3.connect(path) as target:
            self.db.backup(target)
        if destination is None:
            for old in sorted(
                folder.glob("*.sqlite3"), key=lambda x: x.stat().st_mtime
            )[:-30]:
                old.unlink()
        return path

    def restore(self, source):
        source = Path(source)
        if source.resolve() == self.path.resolve() or not source.is_file():
            raise CatalogError("Выберите существующую резервную копию.")
        # Validate before touching the live database. backup() keeps the current connection valid.
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
            tables = {
                r[0]
                for r in src.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if (
                src.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or not {
                    "catalogs",
                    "products",
                    "history",
                    "imports",
                    "exports",
                    "mappings",
                    "conflicts",
                }
                <= tables
                or src.execute("PRAGMA user_version").fetchone()[0] != 1
            ):
                raise CatalogError(
                    "Это не совместимая резервная копия ST Catalog Manager."
                )
            for r in src.execute("SELECT original,base,current FROM products"):
                for raw in r:
                    obj = json.loads(raw)
                    if not isinstance(obj, dict) or any(
                        not isinstance(k, str) or not isinstance(v, str)
                        for k, v in obj.items()
                    ):
                        raise CatalogError("Повреждённые данные резервной копии.")
            self.backup()
            src.backup(self.db)
