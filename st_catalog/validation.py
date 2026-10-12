from __future__ import annotations
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from .domain import Issue, CatalogError, option_titles, schedule_value, parse_editions


def faults(rows):
    """Return stable field-level signatures; no mutation or normalization."""
    result = []
    index = {rid: data for rid, data in rows}
    uids = Counter(d.get("Tilda UID", "") for _, d in rows)
    external = Counter(d.get("External ID") for _, d in rows if d.get("External ID"))
    by_uid = {d.get("Tilda UID"): d for _, d in rows}
    combinations = Counter(
        (d.get("Parent UID"), d.get("Editions")) for _, d in rows if d.get("Parent UID")
    )
    for rid, d in rows:

        def add(field, code, message, critical=True):
            result.append((rid, field, code, message, critical))

        uid = d.get("Tilda UID", "")
        if not uid or uids[uid] > 1:
            add("Tilda UID", "uid", "Пустой или повторяющийся Tilda UID.")
        if external[d.get("External ID")] > 1:
            add(
                "External ID",
                "duplicate_external",
                "Повтор External ID. Не игнорируйте Tilda UID при импорте.",
                False,
            )
        parent = d.get("Parent UID", "")
        if not d.get("Title", "").strip():
            add("Title", "empty_title", "Название товара не заполнено.")
        if parent:
            if combinations[(parent, d.get("Editions"))] > 1:
                add(
                    "Editions",
                    "duplicate_variant",
                    "Повторяется сочетание свойств вариантов одного товара.",
                )
            if (
                parent == uid
                or parent not in by_uid
                or by_uid[parent].get("Parent UID")
            ):
                add(
                    "Parent UID",
                    "parent",
                    "Родитель отсутствует, ссылается на себя или сам является вариантом.",
                )
            else:
                try:
                    titles = option_titles(by_uid[parent])
                    if len(titles) == 1:
                        _, value = schedule_value(by_uid[parent], d)
                        if not value.strip():
                            raise CatalogError("Пустое значение свойства.")
                    else:
                        parse_editions(by_uid[parent], d)
                        add(
                            "Editions",
                            "multi",
                            "Несколько свойств: требуется ручная проверка Editions.",
                            False,
                        )
                except CatalogError as exc:
                    add("Editions", "edition", str(exc))
        elif d.get("Editions", "").startswith("product_options:"):
            try:
                option_titles(d)
            except CatalogError as exc:
                add("Editions", "options", str(exc))
        elif d.get("Editions"):
            add(
                "Editions",
                "unlinked",
                "Есть значение варианта без Parent UID. Проверьте структуру.",
                False,
            )
        if not parent and re.search(r" - \d{2}\.\d{2}\.", d.get("Title", "")):
            add(
                "Parent UID",
                "suspect_variant",
                "Название похоже на вариант, но Parent UID отсутствует. Автоматическое объединение запрещено.",
                False,
            )
        for field in (
            "Price",
            "Price Old",
            "Quantity",
            "Weight",
            "Length",
            "Width",
            "Height",
        ):
            value = d.get(field, "")
            if not value:
                continue
            try:
                num = Decimal(value)
                if (
                    not num.is_finite()
                    or num < 0
                    or (
                        field not in ("Price", "Price Old")
                        and num != num.to_integral_value()
                    )
                ):
                    raise InvalidOperation()
            except InvalidOperation:
                add(
                    field,
                    "number",
                    "Ожидается неотрицательное число"
                    + (" (целое)." if field not in ("Price", "Price Old") else "."),
                )
        raw = d.get("Modifications", "")
        if raw:
            for group in raw.split("|"):
                if (
                    ":" not in group
                    or not group.split(":", 1)[0]
                    or not group.split(":", 1)[1]
                ):
                    add(
                        "Modifications",
                        "modification",
                        "Не распознана структура дополнительных опций.",
                    )
                    break
                for option in group.split(":", 1)[1].split(";"):
                    if "=" in option:
                        try:
                            amount = Decimal(option.rsplit("=", 1)[1])
                            if not amount.is_finite():
                                raise InvalidOperation()
                        except InvalidOperation:
                            add(
                                "Modifications",
                                "modifier_price",
                                "Некорректная цена дополнительной опции.",
                            )
        for field, value in d.items():
            try:
                value.encode("utf-8", errors="strict")
            except UnicodeError:
                add(
                    field,
                    "encoding",
                    "Значение невозможно записать в UTF-8 без потерь.",
                )
            if "\x00" in value:
                add(field, "null", "Недопустимый нулевой символ.")
    return result


def validate(products, conflicts=()):
    original_faults = {
        (rid, field, code)
        for rid, field, code, _, _ in faults(
            [(p.id, p.original) for p in products if p.original]
        )
    }
    issues = []
    for rid, field, code, message, critical in faults(
        [(p.id, p.current) for p in products]
    ):
        p = next(p for p in products if p.id == rid)
        inherited = (rid, field, code) in original_faults and p.current.get(
            field
        ) == p.original.get(field)
        # A malformed variant caused by an edited parent is a new fault even if its own field is unchanged.
        if field == "Editions" and p.current.get("Parent UID"):
            parent = next(
                (x for x in products if x.uid == p.current["Parent UID"]), None
            )
            if parent and parent.current.get("Editions") != parent.original.get(
                "Editions"
            ):
                inherited = False
        issues.append(
            Issue(
                "warning" if inherited or not critical else "error",
                rid,
                field,
                message,
                inherited,
            )
        )
    for c in conflicts:
        issues.append(
            Issue("error", c["product_id"], c["field"], "Не разрешён конфликт с CSV.")
        )
    return issues
