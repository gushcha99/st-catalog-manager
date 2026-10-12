from __future__ import annotations
import hashlib
import json
import os
import secrets
import tempfile
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from .csv_io import read_csv, csv_bytes
from .domain import (
    DATA,
    SITES,
    IDS,
    CatalogError,
    dumps,
    now,
    option_titles,
    schedule_patch,
    candidate_score,
)
from .validation import validate

IMPORT_GUIDE = """Загрузка в Tilda — вручную

1. Сохраните резервную выгрузку каталога Tilda. Сначала проверьте результат на копии проекта.
2. Откройте нужный сайт → Каталог → Импортировать (или обновить) товары из CSV.
3. Выберите UTF-8 и разделитель «точка с запятой».
4. Сопоставьте Tilda UID с Tilda UID, Parent UID с родительским идентификатором,
   Editions с «Варианты», Modifications с дополнительными опциями, Tabs:N с Tabs.
5. Не включайте игнорирование Tilda UID / создание всех строк как новых товаров.
   Варианты объединяются по Parent UID; не по совпадению названий.
6. Не включайте очистку каталога или удаление отсутствующих строк (если такие действия предлагает интерфейс).
   Файл изменений содержит только затронутые семейства товаров. Полный файл содержит активные позиции.
   Отсутствие строки в CSV не удаляет товар в Tilda. Список ручного удаления приложен отдельно.
7. Проверьте цены, варианты, опции и вкладки в предпросмотре Tilda и после импорта.
8. Подтвердите загрузку в истории экспортов. Затем загрузите свежий CSV для проверки результата.

Точные названия переключателей зависят от версии интерфейса Tilda.
Документация: https://help-ru.tilda.cc/online-store/import-export
Создание файлов и локальные тесты не гарантируют, что Tilda примет их.
"""


class CatalogService:
    def __init__(self, repository, seed=True):
        self.repo = repository
        self.edit_ack = (
            set()
        )  # In-memory only: a new process always starts a new editing session.
        if seed and not repository.catalogs():
            self.seed()

    def seed(self):
        documents = {s: read_csv(next(DATA.glob(f"store-{s}-*.csv"))) for s in SITES}
        manifest = json.loads((DATA / "manifest.json").read_text("utf-8"))
        for name, digest in manifest.items():
            if hashlib.sha256((DATA / name).read_bytes()).hexdigest() != digest:
                raise CatalogError(
                    "Контрольная сумма исходного CSV не совпадает: " + name
                )
        with self.repo.transaction():
            for site, doc in documents.items():
                name, domain = SITES[site]
                self.repo.db.execute(
                    "INSERT INTO catalogs VALUES(?,?,?,?,?,?)",
                    (site, name, domain, dumps(doc.headers), now(), doc.export_date),
                )
                for n, row in enumerate(doc.rows):
                    self.repo.insert(site, n, row)
                self._log_import(site, doc, True)

    def _log_import(self, site, doc, complete):
        self.repo.db.execute(
            "INSERT INTO imports(site,at,filename,export_date,complete,snapshot) VALUES(?,?,?,?,?,?)",
            (
                site,
                now(),
                doc.filename,
                doc.export_date,
                complete,
                dumps({"headers": doc.headers, "rows": doc.rows}),
            ),
        )

    def acknowledge(self, sites):
        self.edit_ack.update(sites)

    def require_ack(self, sites):
        if set(sites) - self.edit_ack:
            raise CatalogError(
                "Возможно, данные каталога устарели. Требуется подтверждение риска."
            )

    def children(self, p):
        return [
            r
            for r in self.repo.products(p.site)
            if r.current.get("Parent UID") == p.uid
        ]

    def parent(self, p):
        return next(
            (
                r
                for r in self.repo.products(p.site)
                if r.uid == p.current.get("Parent UID")
            ),
            None,
        )

    def _patch(self, p, patch, advanced=False):
        if p.absent or p.deletion:
            raise CatalogError(
                "Сначала отмените пометку удаления / восстановите отсутствующую запись."
            )
        if self.repo.conflicts(p.site):
            raise CatalogError("Сначала разрешите конфликты этого каталога.")
        if set(patch) & IDS:
            raise CatalogError(
                "Идентификаторы существующих записей защищены от редактирования."
            )
        headers = self.repo.catalog(p.site)["headers"]
        if set(patch) - set(headers) or any(
            not isinstance(v, str) for v in patch.values()
        ):
            raise CatalogError("Неизвестное поле или неверный тип значения.")
        for field, value in patch.items():
            old = p.current.get(field, "")
            if value != old:
                self.repo.db.execute(
                    "UPDATE history SET status='Заменено последующим изменением' WHERE product_id=? AND field=? AND status='Сохранено'",
                    (p.id, field),
                )
                p.current[field] = value
                self.repo.audit(p, field, old, value)
        self.repo.save(p)

    def edit(self, product_id, patch):
        p = self.repo.get(product_id)
        self.require_ack([p.site])
        with self.repo.transaction():
            self._patch(p, patch)

    def edit_schedule(self, product_id, value, price=None):
        p = self.repo.get(product_id)
        parent = self.parent(p)
        if not parent:
            raise CatalogError("У варианта нет однозначного родителя.")
        patch = schedule_patch(parent.current, p.current, value)
        if price is not None:
            patch["Price"] = price
        self.edit(product_id, patch)

    def _new_uid(self):
        used = {p.uid for p in self.repo.products(include_absent=True)}
        while True:
            uid = str(secrets.randbelow(900_000_000_000) + 100_000_000_000)
            if uid not in used:
                return uid

    def add_variant(self, parent_id, value, price, template_id=None):
        parent = self.repo.get(parent_id)
        self.require_ack([parent.site])
        if (
            parent.current.get("Parent UID")
            or parent.absent
            or parent.deletion
            or self.repo.conflicts(parent.site)
        ):
            raise CatalogError("Выберите активный основной товар без конфликтов.")
        titles = option_titles(parent.current)
        if (
            len(titles) != 1
            or not value.strip()
            or any(c in value for c in (";", "\n", "\r"))
        ):
            raise CatalogError(
                "Для создания варианта требуется одно распознанное свойство и непустое значение без ;."
            )
        headers = self.repo.catalog(parent.site)["headers"]
        row = {h: "" for h in headers}
        if template_id is not None:
            template = self.repo.get(template_id)
            if (
                template.site != parent.site
                or template.current.get("Parent UID") != parent.uid
            ):
                raise CatalogError("Шаблон не является вариантом этого товара.")
            row.update(template.current)
        uid = self._new_uid()
        ext = uuid.uuid4().hex[:20]
        used_ext = {
            p.current.get("External ID")
            for p in self.repo.products(include_absent=True)
        }
        while ext in used_ext:
            ext = uuid.uuid4().hex[:20]
        row.update(
            {
                "Tilda UID": uid,
                "Parent UID": parent.uid,
                "Title": parent.current["Title"] + " - " + value,
                "Editions": titles[0] + ":" + value,
                "Price": price,
            }
        )
        if "External ID" in headers:
            row["External ID"] = ext
        if "Url" in headers:
            parts = urlsplit(parent.current.get("Url", ""))
            query = [
                (k, v)
                for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k != "editionuid"
            ] + [("editionuid", uid)]
            row["Url"] = (
                urlunsplit(
                    (
                        parts.scheme,
                        parts.netloc,
                        parts.path,
                        urlencode(query),
                        parts.fragment,
                    )
                )
                if parts.netloc
                else ""
            )
        with self.repo.transaction():
            pos = (
                max(
                    (p.position for p in self.repo.products(parent.site, True)),
                    default=0,
                )
                + 1
            )
            rid = self.repo.insert(parent.site, pos, row, True)
            self.repo.audit(self.repo.get(rid), "__new__", "", dumps(row))
        return rid

    def add_product(self, site, title):
        self.require_ack([site])
        if not title.strip() or self.repo.conflicts(site):
            raise CatalogError("Укажите название и разрешите конфликты.")
        row = {h: "" for h in self.repo.catalog(site)["headers"]}
        row.update({"Tilda UID": self._new_uid(), "Title": title})
        if "External ID" in row:
            row["External ID"] = uuid.uuid4().hex
        with self.repo.transaction():
            rid = self.repo.insert(
                site,
                max((p.position for p in self.repo.products(site, True)), default=0)
                + 1,
                row,
                True,
            )
            self.repo.audit(self.repo.get(rid), "__new__", "", dumps(row))
        return rid

    def import_csv(self, path, site=None, complete=False):
        doc = read_csv(path)
        detected = doc.detect_site()
        if site is None:
            site = detected
        if site not in SITES:
            raise CatalogError(
                "Не удалось однозначно определить сайт. Выберите его вручную."
            )
        if detected and detected != site:
            raise CatalogError("Выбранный сайт не совпадает с сайтом файла.")
        if self.repo.conflicts(site):
            raise CatalogError("Сначала разрешите предыдущие конфликты каталога.")
        self.repo.backup()
        with self.repo.transaction():
            headers = self.repo.catalog(site)["headers"]
            headers += [h for h in doc.headers if h not in headers]
            # Omitted columns in a partial/older schema are not commands to clear them.
            existing = {p.uid: p for p in self.repo.products(site, True)}
            position = max((p.position for p in existing.values()), default=0)
            seen = set()
            for row in doc.rows:
                uid = row["Tilda UID"]
                seen.add(uid)
                if uid not in existing:
                    position += 1
                    self.repo.insert(
                        site, position, {h: row.get(h, "") for h in headers}
                    )
                    continue
                p = existing[uid]
                p.absent = False
                for field, remote in row.items():
                    base, local = p.base.get(field, ""), p.current.get(field, "")
                    if local == remote:
                        self.repo.db.execute(
                            "UPDATE history SET status='Проверено по Tilda' WHERE product_id=? AND field=? AND new=? AND status NOT IN ('Отменено','Проверено по Tilda')",
                            (p.id, field, remote),
                        )
                    elif local == base:
                        p.current[field] = remote
                        self.repo.audit(p, field, local, remote, "Получено из Tilda")
                    elif remote != base:
                        self.repo.db.execute(
                            "INSERT INTO conflicts(site,product_id,field,base,local,remote) VALUES(?,?,?,?,?,?)",
                            (site, p.id, field, base, local, remote),
                        )
                    p.base[field] = remote
                # A UID present remotely has reached Tilda even when some fields now conflict.
                p.is_new = False
                self.repo.save(p)
                if p.current == p.base:
                    self.repo.db.execute(
                        "UPDATE history SET status='Проверено по Tilda' WHERE product_id=? AND field='__new__' AND status!='Отменено'",
                        (p.id,),
                    )
            if complete:
                for uid, p in existing.items():
                    if uid not in seen and not p.is_new and not p.absent:
                        if p.deletion in ("pending", "done") and not p.changed:
                            p.absent = True
                            p.deletion = "verified"
                            self.repo.save(p)
                            self.repo.audit(
                                p,
                                "__delete__",
                                "done",
                                "verified",
                                "Проверено по Tilda",
                            )
                        else:
                            self.repo.db.execute(
                                "INSERT INTO conflicts(site,product_id,field,base,local,remote) VALUES(?,?,?,?,?,?)",
                                (
                                    site,
                                    p.id,
                                    "__missing__",
                                    dumps(p.base),
                                    dumps(p.current),
                                    "Отсутствует в полной выгрузке",
                                ),
                            )
            self.repo.db.execute(
                "UPDATE catalogs SET headers=?,imported_at=?,export_date=? WHERE id=?",
                (dumps(headers), now(), doc.export_date, site),
            )
            self._log_import(site, doc, complete)
        self.edit_ack.discard(
            site
        )  # The age of a file is never inferred from its upload date.
        return self.repo.conflicts(site)

    def resolve(self, conflict_id, choice):
        if choice not in ("local", "remote"):
            raise CatalogError("Неверный вариант разрешения конфликта.")
        c = self.repo.db.execute(
            "SELECT * FROM conflicts WHERE id=? AND resolved=0", (conflict_id,)
        ).fetchone()
        if not c:
            raise CatalogError("Конфликт уже разрешён.")
        p = self.repo.get(c["product_id"])
        with self.repo.transaction():
            if c["field"] == "__missing__":
                if choice == "remote":
                    p.absent = True
                    p.deletion = "verified"
                self.repo.audit(
                    p, "__missing__", c["local"], choice, "Решение пользователя"
                )
            else:
                old = p.current.get(c["field"], "")
                p.current[c["field"]] = c[choice]
                self.repo.audit(
                    p,
                    c["field"],
                    old,
                    c[choice],
                    "Проверено по Tilda" if choice == "remote" else "Сохранено",
                )
            self.repo.save(p)
            self.repo.db.execute(
                "UPDATE conflicts SET resolved=1 WHERE id=?", (conflict_id,)
            )

    def mark_delete(self, product_id, state="pending"):
        if state not in ("pending", "done", ""):
            raise CatalogError("Неверный статус удаления.")
        p = self.repo.get(product_id)
        self.require_ack([p.site])
        family = [p] + self.children(p)
        with self.repo.transaction():
            for r in family:
                old = r.deletion
                # Once exported, a new UID might already be in Tilda: do not silently discard it.
                exported = any(
                    str(r.id) in json.loads(e["snapshot"])
                    for e in self.repo.db.execute(
                        "SELECT snapshot FROM exports WHERE site=?", (r.site,)
                    )
                )
                if r.is_new and not exported and state:
                    r.absent = True
                    r.deletion = "cancelled"
                else:
                    r.deletion = state
                self.repo.save(r)
                self.repo.audit(r, "__delete__", old, r.deletion)

    def undo(self, product_id):
        p = self.repo.get(product_id)
        self.require_ack([p.site])
        if self.repo.conflicts(p.site):
            raise CatalogError("Сначала разрешите конфликты.")
        if p.is_new:
            return self.mark_delete(product_id)
        with self.repo.transaction():
            for f in set(p.current) | set(p.base):
                if p.current.get(f, "") != p.base.get(f, ""):
                    self.repo.audit(
                        p, f, p.current.get(f, ""), p.base.get(f, ""), "Отменено"
                    )
            p.current = dict(p.base)
            p.deletion = ""
            self.repo.save(p)
            self.repo.db.execute(
                "UPDATE history SET status='Отменено' WHERE product_id=? AND status IN ('Сохранено','Подготовлено к экспорту','Экспортировано')",
                (p.id,),
            )

    def issues(self, site):
        return validate(self.repo.products(site), self.repo.conflicts(site))

    def differences(self, site):
        result = []
        for p in self.repo.products(site):
            if p.deletion:
                result.append(
                    (p.id, p.current["Title"], "Ручное удаление", "", p.deletion)
                )
            if p.is_new:
                result.append(
                    (
                        p.id,
                        p.current["Title"],
                        "Новый вариант"
                        if p.current.get("Parent UID")
                        else "Новый товар",
                        "",
                        p.uid,
                    )
                )
            for f in self.repo.catalog(site)["headers"]:
                old, new = p.base.get(f, ""), p.current.get(f, "")
                if old != new:
                    result.append((p.id, p.current["Title"], f, old, new))
        return result

    def export_rows(self, site, mode="changes"):
        if mode not in ("changes", "full"):
            raise CatalogError("Неизвестный режим экспорта.")
        rows = [p for p in self.repo.products(site) if not p.deletion]
        if mode == "full":
            return rows
        selected = {p.uid for p in rows if p.changed}
        roots = {
            p.current.get("Parent UID") or p.uid for p in rows if p.uid in selected
        }
        # Export complete families, including unchanged siblings, conservatively preserving variant sets.
        return [
            p for p in rows if p.uid in roots or p.current.get("Parent UID") in roots
        ]

    def export(
        self, sites, directory, mode="changes", risk_confirmed=False, zip_output=True
    ):
        if not risk_confirmed:
            raise CatalogError("Подтвердите риск устаревших данных перед экспортом.")
        sites = list(dict.fromkeys(sites))
        if not sites:
            raise CatalogError("Не выбраны сайты.")
        plans = {}
        for site in sites:
            errors = [i for i in self.issues(site) if i.severity == "error"]
            if errors:
                raise CatalogError(
                    SITES[site][0] + ": " + "\n".join(i.message for i in errors)
                )
            rows = self.export_rows(site, mode)
            selected_uids = {p.uid for p in rows}
            if any(
                p.current.get("Parent UID")
                and p.current["Parent UID"] not in selected_uids
                for p in rows
            ):
                raise CatalogError(
                    "Экспорт оставил вариант без родителя. Проверьте пометки удаления."
                )
            plans[site] = rows
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        run = directory / (
            "ST-export-" + now().replace(":", "-") + "-" + uuid.uuid4().hex[:6]
        )
        temp = Path(tempfile.mkdtemp(prefix=".st-export-", dir=directory))
        export_ids = []
        try:
            with self.repo.transaction():
                for site, rows in plans.items():
                    name = SITES[site][1] + ".csv"
                    headers = self.repo.catalog(site)["headers"]
                    content = csv_bytes(
                        headers,
                        [{h: p.current.get(h, "") for h in headers} for p in rows],
                    )
                    (temp / name).write_bytes(content)
                    # Re-read and compare every cell before publishing files.
                    check = read_csv(temp / name)
                    if check.rows != [
                        {h: p.current.get(h, "") for h in headers} for p in rows
                    ]:
                        raise CatalogError(
                            "Проверка экспортированного CSV не пройдена."
                        )
                    snapshot = {str(p.id): p.current for p in rows}
                    eid = self.repo.db.execute(
                        "INSERT INTO exports(at,site,path,mode,snapshot,status) VALUES(?,?,?,?,?,?)",
                        (
                            now(),
                            site,
                            str(run / name),
                            mode,
                            dumps(snapshot),
                            "Подготовлено к экспорту",
                        ),
                    ).lastrowid
                    export_ids.append(eid)
                    for p in rows:
                        if p.changed:
                            self.repo.audit(
                                p, "__export__", "", str(eid), "Подготовлено к экспорту"
                            )
                deletions = []
                for site in sites:
                    for p in self.repo.products(site):
                        if p.deletion:
                            deletions.append(
                                {
                                    "Сайт": SITES[site][0],
                                    "Товар": p.current["Title"],
                                    "UID": p.uid,
                                    "Ссылка": p.current.get("Url", ""),
                                    "Действие": "Удалить вручную"
                                    if p.deletion == "pending"
                                    else "Удаление подтверждено пользователем; ожидается полная выгрузка",
                                }
                            )
                (temp / "Удалить-вручную.csv").write_bytes(
                    csv_bytes(["Сайт", "Товар", "UID", "Ссылка", "Действие"], deletions)
                )
                (temp / "Инструкция.txt").write_text(
                    "Режим: " + mode + "\n\n" + IMPORT_GUIDE, encoding="utf-8"
                )
                report = [
                    {
                        "site": SITES[s][0],
                        "changes": self.differences(s),
                        "warnings": [i.__dict__ for i in self.issues(s)],
                    }
                    for s in sites
                ]
                (temp / "Отчёт.json").write_text(dumps(report), encoding="utf-8")
                if zip_output:
                    with zipfile.ZipFile(
                        temp / "Все-сайты.zip", "w", zipfile.ZIP_DEFLATED
                    ) as z:
                        for f in sorted(temp.iterdir()):
                            if f.suffix != ".zip":
                                z.write(f, f.name)
                history_max = self.repo.db.execute(
                    "SELECT COALESCE(MAX(id),0) FROM history"
                ).fetchone()[0]
                os.replace(temp, run)
                for eid, (site, rows) in zip(export_ids, plans.items()):
                    self.repo.db.execute(
                        "UPDATE exports SET status='Экспортировано',history_max=? WHERE id=?",
                        (history_max, eid),
                    )
                    for p in rows:
                        for field, value in p.current.items():
                            self.repo.db.execute(
                                "UPDATE history SET status='Экспортировано' WHERE product_id=? AND field=? AND new=? AND status='Сохранено'",
                                (p.id, field, value),
                            )
                        self.repo.db.execute(
                            "UPDATE history SET status='Экспортировано' WHERE product_id=? AND field IN ('__new__','__export__') AND status IN ('Сохранено','Подготовлено к экспорту')",
                            (p.id,),
                        )
            return run, export_ids
        except Exception:
            import shutil

            shutil.rmtree(temp, ignore_errors=True)
            shutil.rmtree(run, ignore_errors=True)
            raise

    def confirm_export(self, export_id):
        e = self.repo.db.execute(
            "SELECT * FROM exports WHERE id=?", (export_id,)
        ).fetchone()
        if not e:
            raise CatalogError("Экспорт не найден.")
        with self.repo.transaction():
            self.repo.db.execute(
                "UPDATE exports SET status='Импорт подтвержден' WHERE id=?",
                (export_id,),
            )
            for rid, row in json.loads(e["snapshot"]).items():
                p = self.repo.get(int(rid))
                # Never mark later edits as part of an older upload.
                for field, value in row.items():
                    if p.current.get(field) == value:
                        self.repo.db.execute(
                            "UPDATE history SET status='Импорт подтвержден' WHERE product_id=? AND field=? AND new=? AND id<=? AND status='Экспортировано'",
                            (p.id, field, value, e["history_max"]),
                        )
                self.repo.audit(
                    p,
                    "__import_confirmation__",
                    "",
                    str(export_id),
                    "Импорт подтвержден",
                )

    def candidates(self, product_id):
        p = self.repo.get(product_id)
        return sorted(
            [
                (
                    candidate_score(
                        p.current,
                        r.current,
                        len(self.children(p)),
                        len(self.children(r)),
                    ),
                    r,
                )
                for r in self.repo.products()
                if r.site != p.site
                and not r.current.get("Parent UID")
                and not r.deletion
            ],
            key=lambda x: x[0],
            reverse=True,
        )

    def save_mapping(self, product_ids):
        rows = [self.repo.get(i) for i in product_ids]
        if (
            len(rows) < 2
            or len({p.site for p in rows}) != len(rows)
            or any(p.current.get("Parent UID") for p in rows)
        ):
            raise CatalogError(
                "Выберите по одному основному товару минимум на двух сайтах."
            )
        group = uuid.uuid4().hex
        with self.repo.transaction():
            for p in rows:
                self.repo.db.execute("DELETE FROM mappings WHERE product_id=?", (p.id,))
                self.repo.db.execute(
                    "INSERT INTO mappings VALUES(?,?,?)", (group, p.site, p.id)
                )
                self.repo.audit(
                    p, "__mapping__", "", group, "Соответствие подтверждено"
                )
        return group

    def mapped(self, product_id):
        row = self.repo.db.execute(
            "SELECT group_id FROM mappings WHERE product_id=?", (product_id,)
        ).fetchone()
        if not row:
            return [self.repo.get(product_id)]
        return [
            self.repo.get(r[0])
            for r in self.repo.db.execute(
                "SELECT product_id FROM mappings WHERE group_id=?", (row[0],)
            )
        ]

    def batch_edit(self, patches):
        rows = [self.repo.get(rid) for rid in patches]
        self.require_ack([p.site for p in rows])
        with self.repo.transaction():
            for p in rows:
                self._patch(p, patches[p.id])

    def batch_plan(self, patches, additions):
        """Apply multi-site edits and variant additions in one SQLite transaction."""
        sites = {self.repo.get(rid).site for rid in patches}
        sites.update(self.repo.get(rid).site for rid, _, _ in additions)
        self.require_ack(sites)
        with self.repo.transaction():
            self.batch_edit(patches)
            created = [
                self.add_variant(rid, value, price) for rid, value, price in additions
            ]
        return created

    def unexported_count(self, site):
        snapshots = [
            json.loads(e[0])
            for e in self.repo.db.execute(
                "SELECT snapshot FROM exports WHERE site=? ORDER BY id DESC", (site,)
            )
        ]
        count = 0
        for p in self.repo.products(site):
            if not p.changed or p.deletion:
                continue
            latest = next(
                (
                    snapshot[str(p.id)]
                    for snapshot in snapshots
                    if str(p.id) in snapshot
                ),
                None,
            )
            if latest != p.current:
                count += 1
        return count
