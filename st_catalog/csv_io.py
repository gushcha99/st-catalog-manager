from __future__ import annotations
import csv
import io
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from .domain import CatalogError, SITES

csv.field_size_limit(16 * 1024 * 1024)


@dataclass
class CSVDocument:
    headers: list[str]
    rows: list[dict[str, str]]
    filename: str = ""

    def detect_site(self):
        name_ids = {s for s in SITES if re.search(rf"(?<!\d){s}(?!\d)", self.filename)}
        hosts = set()
        for row in self.rows:
            host = (
                (urlsplit(row.get("Url", "")).hostname or "")
                .lower()
                .removeprefix("www.")
            )
            hosts.update(s for s, (_, domain) in SITES.items() if host == domain)
        evidence = name_ids | hosts
        return next(iter(evidence)) if len(evidence) == 1 else None

    @property
    def export_date(self):
        match = re.search(r"-(\d{12})(?:\D|$)", self.filename)
        if not match:
            return ""
        from datetime import datetime

        try:
            return datetime.strptime(match[1], "%Y%m%d%H%M").isoformat(
                timespec="minutes"
            )
        except ValueError:
            return ""


def read_csv(path) -> CSVDocument:
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, delimiter=";", strict=True)
            headers = next(reader)
            if (
                not headers
                or any(not x for x in headers)
                or len(set(headers)) != len(headers)
            ):
                raise CatalogError("Пустые или повторяющиеся названия колонок.")
            if not {"Tilda UID", "Title", "Parent UID", "Editions"} <= set(headers):
                raise CatalogError(
                    "Отсутствуют обязательные колонки Tilda. Нужен CSV UTF-8 с разделителем ;."
                )
            rows = []
            for i, values in enumerate(reader, 2):
                if len(values) != len(headers):
                    raise CatalogError(
                        f"Запись {i}: {len(values)} полей вместо {len(headers)}."
                    )
                rows.append(dict(zip(headers, values)))
    except (UnicodeError, csv.Error, StopIteration) as exc:
        raise CatalogError(f"Не удалось прочитать CSV без потерь: {exc}") from exc
    uids = Counter(r["Tilda UID"] for r in rows)
    if "" in uids or any(n > 1 for n in uids.values()):
        raise CatalogError(
            "Пустой или повторяющийся Tilda UID. Импорт отменён, база не изменена."
        )
    return CSVDocument(headers, rows, path.name)


def csv_bytes(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=headers,
        delimiter=";",
        lineterminator="\r\n",
        extrasaction="raise",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return stream.getvalue().encode("utf-8", errors="strict")
