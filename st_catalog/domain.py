from __future__ import annotations
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

SITES = {
    "6486596": ("International", "st.education"),
    "6457193": ("Kazakhstan", "kz.st.education"),
    "6457199": ("Uzbekistan", "uz.st.education"),
    "6453690": ("Armenia", "am.st.education"),
    "6893339": ("Azerbaijan", "az.st.education"),
}
DATA = Path(__file__).parent / "data"
IDS = {"Tilda UID", "External ID", "Parent UID"}


class CatalogError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass
class Product:
    id: int
    site: str
    position: int
    original: dict[str, str]
    base: dict[str, str]
    current: dict[str, str]
    is_new: bool = False
    deletion: str = ""
    absent: bool = False

    @property
    def uid(self):
        return self.current.get("Tilda UID", "")

    @property
    def changed(self):
        return self.is_new or self.current != self.base


@dataclass(frozen=True)
class Issue:
    severity: str
    row_id: int
    field: str
    message: str
    inherited: bool = False


def option_titles(parent: dict) -> list[str]:
    raw = parent.get("Editions", "")
    if not raw.startswith("product_options:"):
        raise CatalogError(
            "Свойства родителя не распознаны. Используйте расширенный редактор."
        )
    try:
        items = json.loads(raw[len("product_options:") :])
        if not isinstance(items, list) or not items:
            raise ValueError()
        titles = [item["title"] for item in items]
        if any(not isinstance(t, str) or not t for t in titles) or len(
            set(titles)
        ) != len(titles):
            raise ValueError()
        return titles
    except (ValueError, KeyError, TypeError):
        raise CatalogError("Некорректная структура product_options.") from None


def schedule_value(parent: dict, child: dict) -> tuple[str, str]:
    titles = option_titles(parent)
    if len(titles) != 1:
        raise CatalogError(
            "Несколько свойств: доступно только редактирование исходного Editions."
        )
    title = titles[0]
    prefix = title + ":"
    raw = child.get("Editions", "")
    if not raw.startswith(prefix) or ";" in raw[len(prefix) :]:
        raise CatalogError(
            "Значение варианта нельзя однозначно разобрать; исходное значение сохранено."
        )
    return title, raw[len(prefix) :]


def schedule_patch(parent: dict, child: dict, value: str) -> dict:
    title, old = schedule_value(parent, child)
    if not value.strip() or ";" in value or "\n" in value or "\r" in value:
        raise CatalogError("Введите непустое расписание без ; и переносов строк.")
    patch = {"Editions": title + ":" + value}
    # Update the generated title only when the exact original convention is recognized.
    if child.get("Title") == parent.get("Title", "") + " - " + old:
        patch["Title"] = parent["Title"] + " - " + value
    return patch


def normalized_path(url: str) -> str:
    path = urlsplit(url).path.rstrip("/").casefold()
    return re.sub(r"^/(ru|en|hy|az)(?=/)", "", path)


def candidate_score(a: dict, b: dict, a_children: int, b_children: int) -> int:
    score = 0
    if normalized_path(a.get("Url", "")) and normalized_path(
        a.get("Url", "")
    ) == normalized_path(b.get("Url", "")):
        score += 4
    if a.get("Title", "").strip().casefold() == b.get("Title", "").strip().casefold():
        score += 4
    if (
        re.sub(r"^\d+\s+", "", a.get("Category", "")).casefold()
        == re.sub(r"^\d+\s+", "", b.get("Category", "")).casefold()
    ):
        score += 1
    if bool(a_children) == bool(b_children):
        score += 1
    if a.get("External ID") and a.get("External ID") == b.get("External ID"):
        score += 1
    return score


class TextFragments(HTMLParser):
    """Expose text nodes while retaining every byte of surrounding HTML markup."""

    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.offsets = [0]
        for line in source.split("\n"):
            self.offsets.append(self.offsets[-1] + len(line) + 1)
        self.fragments = []
        self.hidden = 0
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if data.strip() and not self.hidden:
            line, col = self.getpos()
            start = self.offsets[line - 1] + col
            self.fragments.append((start, start + len(data), data))

    def replace(self, values):
        if len(values) != len(self.fragments):
            raise CatalogError("Набор текстовых фрагментов изменился.")
        source = self.source
        for (start, end, old), value in reversed(list(zip(self.fragments, values))):
            if old != value:
                source = source[:start] + html.escape(value, quote=False) + source[end:]
        return source


def parse_editions(parent: dict, child: dict) -> dict[str, str]:
    """Conservative validation of multiple properties, keyed by the parent's exact titles."""
    titles = option_titles(parent)
    if len(titles) == 1:
        title, value = schedule_value(parent, child)
        return {title: value}
    result = {}
    for segment in child.get("Editions", "").split(";"):
        matches = [t for t in titles if segment.startswith(t + ":")]
        if len(matches) != 1 or matches[0] in result:
            raise CatalogError("Неоднозначные или повторяющиеся свойства Editions.")
        title = matches[0]
        value = segment[len(title) + 1 :]
        if not value.strip():
            raise CatalogError("Пустое значение свойства Editions.")
        result[title] = value
    if set(result) != set(titles):
        raise CatalogError("Editions не соответствует набору свойств родителя.")
    return result
