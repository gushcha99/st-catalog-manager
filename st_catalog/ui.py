from __future__ import annotations
import json
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QComboBox,
    QTreeWidget,
    QTreeWidgetItem,
    QListWidget,
    QTabWidget,
    QFormLayout,
    QPlainTextEdit,
    QTextBrowser,
    QDialogButtonBox,
    QCheckBox,
    QMessageBox,
    QFileDialog,
    QInputDialog,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)
from .domain import (
    SITES,
    IDS,
    CatalogError,
    TextFragments,
    schedule_value,
    schedule_patch,
)
from .csv_io import read_csv
from .service import IMPORT_GUIDE

LABELS = {
    "Title": "Название",
    "Category": "Категория",
    "Brand": "Бренд",
    "SKU": "Артикул",
    "Mark": "Метка",
    "Price": "Цена",
    "Price Old": "Старая цена",
    "Quantity": "Количество",
    "Photo": "Ссылки на изображения",
    "Description": "Описание",
    "Text": "Подробный текст",
    "Modifications": "Дополнительные опции",
    "SEO title": "SEO: заголовок",
    "SEO descr": "SEO: описание",
    "SEO keywords": "SEO: ключевые слова",
    "Url": "Ссылка на товар",
    "Editions": "Свойства вариантов (исходное значение)",
}
STYLE = """
QWidget { font-family: "Segoe UI", "Arial"; font-size: 13px; color: #203044; }
QMainWindow, QDialog { background: #f5f7fa; }
QLabel#brand { font-size: 24px; font-weight: 700; color: #113d4a; }
QLabel#subtitle { color: #617084; }
QLabel#warning { background: #fff2d5; color: #74521a; padding: 12px; border-radius: 6px; }
QPushButton { background: white; border: 1px solid #cdd7e0; border-radius: 6px; padding: 9px 13px; }
QPushButton:hover { background: #eaf3f5; }
QPushButton#primary { background: #087f8c; color: white; border: 0; font-weight: 600; }
QPushButton:disabled { background: #e6e9ec; color: #8a939d; }
QLineEdit, QComboBox, QPlainTextEdit, QTextBrowser { background: white; border: 1px solid #cdd7e0; border-radius: 4px; padding: 7px; }
QTreeWidget, QTableWidget { background: white; alternate-background-color: #f4f8fa; border: 1px solid #e0e6ed; }
QTreeWidget::item { min-height: 35px; }
QHeaderView::section { background: #eaf0f4; padding: 9px; border: 0; font-weight: 600; }
QListWidget { background: #123e4b; color: #e4edf2; border: 0; padding: 8px; }
QListWidget::item { padding: 13px 10px; border-radius: 5px; }
QListWidget::item:selected { background: #246476; color: white; }
QTabBar::tab { padding: 10px 14px; }
"""


def button(text, callback, primary=False):
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    b.clicked.connect(callback)
    return b


def error(parent, exc):
    QMessageBox.warning(parent, "Не удалось выполнить действие", str(exc))


def table(headers, rows, editable=False):
    t = QTableWidget(len(rows), len(headers))
    t.setHorizontalHeaderLabels(headers)
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            t.setItem(i, j, item)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    if not editable:
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    t.horizontalHeader().setStretchLastSection(True)
    t.resizeColumnsToContents()
    for col in range(len(headers)):
        t.setColumnWidth(col, min(360, max(110, t.columnWidth(col))))
    return t


class RiskDialog(QDialog):
    def __init__(self, parent=None, export=False):
        super().__init__(parent)
        self.setWindowTitle("Возможно, данные каталога устарели")
        self.resize(610, 280)
        layout = QVBoxLayout(self)
        text = QLabel(
            "Возможно, данные каталога устарели\n\nВы используете данные из последней сохраненной выгрузки Tilda.\n"
            "С момента ее загрузки товары могли измениться непосредственно в Tilda.\n"
            "Продолжение работы с этими данными может привести к перезаписи более актуальной информации.\n\n"
            "Дата загрузки файла не доказывает его актуальность."
            + ("\nПеред экспортом подтверждение требуется повторно." if export else "")
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        self.check = QCheckBox(
            "Я понимаю риск и подтверждаю продолжение работы с сохраненными данными."
        )
        layout.addWidget(self.check)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.proceed = buttons.addButton("Продолжить", QDialogButtonBox.AcceptRole)
        self.proceed.setEnabled(False)
        self.check.toggled.connect(self.proceed.setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class HTMLField(QWidget):
    """Safe visual text-node editor. Never serializes QTextDocument back to HTML."""

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.original = source
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.raw = QPlainTextEdit(source)
        self.raw.document().setModified(False)
        self._exact_source = source
        self._source_revision = self.raw.document().revision()
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        self.preview.setOpenLinks(False)
        self.tabs.addTab(self.raw, "Исходный HTML")
        visual = QWidget()
        vl = QVBoxLayout(visual)
        note = QLabel(
            "Безопасный визуальный режим: просмотр и правка текстовых фрагментов.\nТеги, атрибуты и стили сохраняются. Разметка редактируется во вкладке HTML."
        )
        note.setWordWrap(True)
        vl.addWidget(note)
        vl.addWidget(self.preview)
        vl.addWidget(button("Изменить текстовые фрагменты…", self.edit_fragments))
        self.tabs.addTab(visual, "Визуальный режим")
        self.tabs.currentChanged.connect(
            lambda _: self.preview.setHtml(self.raw.toPlainText())
        )
        self.preview.setHtml(source)
        layout.addWidget(self.tabs)

    def edit_fragments(self):
        parser = TextFragments(self.value())
        dialog = QDialog(self)
        dialog.setWindowTitle("Текстовые фрагменты — разметка сохранится")
        dialog.resize(800, 550)
        layout = QVBoxLayout(dialog)
        t = table(["Текст"], [[v] for _, _, v in parser.fragments], True)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(t)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            text = parser.replace([t.item(i, 0).text() for i in range(t.rowCount())])
            self.raw.setPlainText(text)
            self.raw.document().setModified(True)
            self._exact_source = text
            self._source_revision = self.raw.document().revision()
            self.preview.setHtml(text)

    def value(self):
        if self.raw.document().revision() == self._source_revision:
            return self._exact_source
        return self.raw.document().toRawText().replace("\u2029", "\n")


class ProductDialog(QDialog):
    def __init__(self, service, product_id, parent=None):
        super().__init__(parent)
        self.service = service
        self.p = service.repo.get(product_id)
        self.fields = {}
        self.schedule = None
        self.setWindowTitle(
            SITES[self.p.site][0] + " — " + self.p.current.get("Title", "")
        )
        self.resize(950, 730)
        layout = QVBoxLayout(self)
        heading = QLabel(self.p.current.get("Title", ""))
        heading.setWordWrap(True)
        heading.setObjectName("brand")
        layout.addWidget(heading)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        basic = QWidget()
        form = QFormLayout(basic)
        for f in (
            "Title",
            "Category",
            "SKU",
            "Brand",
            "Price",
            "Price Old",
            "Quantity",
            "Photo",
        ):
            if f in self.p.current:
                edit = QLineEdit(self.p.current[f])
                self.fields[f] = edit
                form.addRow(LABELS[f], edit)
        parent_product = service.parent(self.p)
        if parent_product:
            try:
                title, value = schedule_value(parent_product.current, self.p.current)
                self.schedule = QLineEdit(value)
                form.addRow(title.rstrip(":"), self.schedule)
            except CatalogError as exc:
                form.addRow(QLabel(str(exc)))
        tabs.addTab(basic, "Основное")
        for f in ("Description", "Text"):
            if f in self.p.current:
                editor = HTMLField(self.p.current[f])
                self.fields[f] = editor
                tabs.addTab(editor, LABELS[f])
        if not self.p.current.get("Parent UID"):
            groups = QWidget()
            gl = QVBoxLayout(groups)
            self.group_rows = service.children(self.p)
            vals = []
            for p in self.group_rows:
                try:
                    value = schedule_value(self.p.current, p.current)[1]
                except CatalogError:
                    value = p.current.get("Title", "")
                vals.append(
                    [
                        value,
                        p.current.get("Price", ""),
                        p.uid,
                        "Удаление"
                        if p.deletion
                        else "Изменён"
                        if p.changed
                        else "Сохранён",
                    ]
                )
            gt = table(["Группа / вариант", "Цена", "UID", "Статус"], vals)
            gt.cellDoubleClicked.connect(lambda row, _: self.open_child(row))
            gl.addWidget(gt)
            gl.addWidget(
                QLabel(
                    "Двойной щелчок — редактор группы. Новые группы добавляются из главного окна."
                )
            )
            tabs.addTab(groups, "Расписание и варианты")
        adv = QWidget()
        af = QFormLayout(adv)
        note = QLabel(
            "Расширенные значения сохраняются точно. Меняйте синтаксис только после проверки.\nИдентификаторы защищены; соответствия сайтов задаются отдельно."
        )
        note.setWordWrap(True)
        af.addRow(note)
        for f in service.repo.catalog(self.p.site)["headers"]:
            if f in self.fields:
                continue
            value = self.p.current.get(f, "")
            if f in IDS:
                ed = QLineEdit(value)
                ed.setReadOnly(True)
            elif f == "Editions" and self.schedule is not None:
                ed = QLineEdit(value)
                ed.setReadOnly(True)
            elif (
                f in ("Modifications", "Editions")
                or f.startswith("Tabs:")
                or len(value) > 150
            ):
                ed = QPlainTextEdit(value)
                ed.document().setModified(False)
                ed.setMaximumHeight(105)
                self.fields[f] = ed
            else:
                ed = QLineEdit(value)
                self.fields[f] = ed
            af.addRow(LABELS.get(f, f), ed)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(adv)
        tabs.addTab(scroll, "SEO, опции и другие поля")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def open_child(self, row):
        # Child dialogs save independently; parent saves only explicitly changed fields.
        ProductDialog(self.service, self.group_rows[row].id, self).exec()

    def save(self):
        try:
            patch = {}
            for f, ed in self.fields.items():
                if isinstance(ed, QPlainTextEdit) and not ed.document().isModified():
                    continue
                value = (
                    ed.value()
                    if isinstance(ed, HTMLField)
                    else ed.document().toRawText().replace("\u2029", "\n")
                    if isinstance(ed, QPlainTextEdit)
                    else ed.text()
                )
                if value != self.p.current.get(f, ""):
                    patch[f] = value
            if self.schedule is not None:
                parent = self.service.parent(self.p)
                old = schedule_value(parent.current, self.p.current)[1]
                if self.schedule.text() != old:
                    generated = schedule_patch(
                        parent.current, self.p.current, self.schedule.text()
                    )
                    if "Title" in patch:
                        generated.pop("Title", None)
                    patch.update(generated)
            if patch:
                risky = [
                    f
                    for f in patch
                    if f in ("Description", "Text", "Modifications", "Editions")
                    or f.startswith("Tabs:")
                ]
                if (
                    risky
                    and QMessageBox.question(
                        self,
                        "Подтвердите изменение",
                        "Будут изменены поля: "
                        + ", ".join(risky)
                        + ".\nПроверьте предпросмотр HTML и синтаксис опций. Сохранить?",
                    )
                    != QMessageBox.Yes
                ):
                    return
                self.service.edit(self.p.id, patch)
            self.accept()
        except Exception as exc:
            error(self, exc)


class MainWindow(QMainWindow):
    def __init__(self, service):
        super().__init__()
        self.service = service
        self.repo = service.repo
        self.setWindowTitle("ST Catalog Manager")
        self.resize(1400, 850)
        self.setMinimumSize(1060, 680)
        self.setAcceptDrops(True)
        self.setStyleSheet(STYLE)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        self.nav = QListWidget()
        self.nav.setFixedWidth(200)
        self.nav.addItems(
            ["Все товары"]
            + [s[0] for s in SITES.values()]
            + [
                "История изменений",
                "Импорт CSV",
                "Экспорт CSV",
                "Удаление в Tilda",
                "Настройки",
            ]
        )
        layout.addWidget(self.nav)
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(24, 22, 24, 18)
        layout.addWidget(content)
        top = QHBoxLayout()
        title = QLabel("ST Catalog Manager")
        title.setObjectName("brand")
        top.addWidget(title)
        top.addStretch()
        top.addWidget(button("Загрузить CSV", self.import_files))
        top.addWidget(button("Подготовить CSV для Tilda", self.export_dialog, True))
        cl.addLayout(top)
        subtitle = QLabel(
            "ST Education & Knowledge  ·  Пять каталогов  ·  Локальная база"
        )
        subtitle.setObjectName("subtitle")
        cl.addWidget(subtitle)
        self.banner = QLabel()
        self.banner.setObjectName("warning")
        self.banner.setWordWrap(True)
        cl.addWidget(self.banner)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти курс или учебную группу…")
        self.category = QComboBox()
        filters.addWidget(self.search, 3)
        filters.addWidget(self.category, 1)
        cl.addLayout(filters)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            [
                "Название / расписание",
                "Категория",
                "Сайт",
                "Вариантов",
                "Цена",
                "Статус",
            ]
        )
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setColumnWidth(0, 460)
        self.tree.setColumnWidth(1, 150)
        self.tree.setColumnWidth(2, 110)
        self.tree.setColumnWidth(3, 80)
        self.tree.setColumnWidth(4, 95)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit_selected())
        cl.addWidget(self.tree, 1)
        actions = QHBoxLayout()
        for text, fn in [
            ("Редактировать", self.edit_selected),
            ("Добавить группу", self.add_variant),
            ("Копировать вариант", self.copy_variant),
            ("Региональные версии", self.regions),
            ("Удаление", self.delete_selected),
            ("Отменить изменения", self.undo_selected),
        ]:
            actions.addWidget(button(text, fn))
        cl.addLayout(actions)
        bottom = QHBoxLayout()
        bottom.addWidget(button("Новый товар", self.add_product))
        bottom.addWidget(button("Диагностика", self.diagnostics))
        bottom.addWidget(button("Разрешить конфликты", self.resolve_conflicts))
        bottom.addStretch()
        self.count = QLabel()
        bottom.addWidget(self.count)
        cl.addLayout(bottom)
        self.nav.currentRowChanged.connect(self.navigate)
        self.search.textChanged.connect(self.refresh)
        self.category.currentIndexChanged.connect(self.refresh)
        self.nav.setCurrentRow(0)
        self.reload_categories()
        self.refresh()

    def current_site(self):
        i = self.nav.currentRow()
        return list(SITES)[i - 1] if 1 <= i <= 5 else None

    def navigate(self, index):
        if index <= 5:
            self.reload_categories()
            self.refresh()
        elif index == 6:
            self.history()
        elif index == 7:
            self.import_files()
        elif index == 8:
            self.export_dialog()
        elif index == 9:
            self.deletions()
        elif index == 10:
            self.settings()

    def reload_categories(self):
        old = self.category.currentText()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("Все категории")
        self.category.addItems(
            sorted(
                {
                    p.current.get("Category", "")
                    for p in self.repo.products(self.current_site())
                    if p.current.get("Category")
                }
            )
        )
        idx = self.category.findText(old)
        self.category.setCurrentIndex(max(0, idx))
        self.category.blockSignals(False)

    def refresh(self, *_):
        self.tree.clear()
        rows = self.repo.products(self.current_site())
        query = self.search.text().casefold()
        category = self.category.currentText()
        children = {}
        for p in rows:
            children.setdefault((p.site, p.current.get("Parent UID", "")), []).append(p)
        known = {(p.site, p.uid) for p in rows}
        visible = 0

        def status(p):
            if p.deletion:
                return (
                    "Удалить вручную"
                    if p.deletion == "pending"
                    else "Удаление подтверждено"
                )
            if p.is_new:
                return "Новый"
            if p.changed:
                last = self.repo.db.execute(
                    "SELECT snapshot,status FROM exports WHERE site=? ORDER BY id DESC",
                    (p.site,),
                ).fetchall()
                for e in last:
                    if json.loads(e["snapshot"]).get(str(p.id)) == p.current:
                        return e["status"]
                return "Сохранено · изменения"
            return "Без локальных изменений"

        def item(p, parent=None):
            nonlocal visible
            row = QTreeWidgetItem(
                [
                    p.current.get("Title", ""),
                    p.current.get("Category", ""),
                    SITES[p.site][0],
                    str(len(children.get((p.site, p.uid), []))),
                    p.current.get("Price", ""),
                    status(p),
                ]
            )
            row.setData(0, Qt.UserRole, p.id)
            if p.changed:
                row.setForeground(5, QColor("#087f8c"))
            if p.deletion:
                row.setForeground(5, QColor("#ad6120"))
            row.setToolTip(0, p.current.get("Title", "") + "\nUID: " + p.uid)
            if parent:
                parent.addChild(row)
            else:
                self.tree.addTopLevelItem(row)
            visible += 1
            return row

        for p in rows:
            parent_uid = p.current.get("Parent UID", "")
            if parent_uid and (p.site, parent_uid) in known:
                continue
            variants = children.get((p.site, p.uid), [])
            if (
                category
                and category != "Все категории"
                and p.current.get("Category", "") != category
            ):
                continue
            if query and not any(
                query in x.current.get("Title", "").casefold() for x in [p] + variants
            ):
                continue
            root = item(p)
            for v in variants:
                item(v, root)
            if query:
                root.setExpanded(True)
        self.count.setText(
            f"{visible} записей · {sum(p.changed for p in rows)} изменены"
        )
        conflicts = len(self.repo.conflicts(self.current_site()))
        self.banner.setText(
            "Актуальность Tilda не подтверждена. Перед редактированием и экспортом потребуется подтверждение риска."
            + (f"  Конфликтов: {conflicts}." if conflicts else "")
        )

    def selected(self):
        items = self.tree.selectedItems()
        if len(items) != 1:
            raise CatalogError("Выберите одну запись в списке.")
        return self.repo.get(items[0].data(0, Qt.UserRole))

    def allow(self, sites):
        if set(sites) - self.service.edit_ack:
            if not RiskDialog(self).exec():
                return False
            self.service.acknowledge(sites)
        return True

    def edit_selected(self):
        try:
            p = self.selected()
            if self.allow([p.site]):
                ProductDialog(self.service, p.id, self).exec()
                self.reload_categories()
                self.refresh()
        except Exception as exc:
            error(self, exc)

    def add_variant(self, template=None):
        try:
            p = self.selected()
            parent = self.service.parent(p) if p.current.get("Parent UID") else p
            if not parent:
                raise CatalogError("Родитель не найден.")
            if not self.allow([parent.site]):
                return
            dialog = QDialog(self)
            dialog.setWindowTitle("Новая учебная группа / вариант")
            dialog.resize(620, 220)
            form = QFormLayout(dialog)
            value = QLineEdit()
            price = QLineEdit(p.current.get("Price", "") if template else "")
            form.addRow("Группа / значение свойства", value)
            form.addRow("Цена без символа валюты", price)
            note = QLabel(
                "Расписание и часовой пояс задаются для выбранного сайта. Автопереноса времени нет."
            )
            note.setWordWrap(True)
            form.addRow(note)
            buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            form.addRow(buttons)
            if dialog.exec():
                self.service.add_variant(
                    parent.id,
                    value.text(),
                    price.text(),
                    template_id=p.id if template else None,
                )
                self.refresh()
        except Exception as exc:
            error(self, exc)

    def copy_variant(self):
        try:
            if not self.selected().current.get("Parent UID"):
                raise CatalogError("Выберите существующий вариант.")
            self.add_variant(template=True)
        except Exception as exc:
            error(self, exc)

    def add_product(self):
        site = self.current_site()
        if not site:
            name, ok = QInputDialog.getItem(
                self, "Новый товар", "Сайт", [v[0] for v in SITES.values()], 0, False
            )
            if not ok:
                return
            site = next(s for s, v in SITES.items() if v[0] == name)
        if not self.allow([site]):
            return
        title, ok = QInputDialog.getText(self, "Новый товар", "Название")
        if ok:
            try:
                rid = self.service.add_product(site, title)
                ProductDialog(self.service, rid, self).exec()
                self.refresh()
            except Exception as exc:
                error(self, exc)

    def import_files(self, files=None):
        if not files:
            files, _ = QFileDialog.getOpenFileNames(
                self, "Выберите одну или несколько выгрузок", "", "CSV (*.csv)"
            )
        for path in files:
            try:
                doc = read_csv(path)
                site = doc.detect_site()
                if not site:
                    name, ok = QInputDialog.getItem(
                        self,
                        "Подтвердите сайт",
                        Path(path).name + "\nСайт не определён однозначно:",
                        [v[0] for v in SITES.values()],
                        0,
                        False,
                    )
                    if not ok:
                        continue
                    site = next(s for s, v in SITES.items() if v[0] == name)
                dialog = QDialog(self)
                dialog.setWindowTitle("Импорт — " + SITES[site][0])
                l = QVBoxLayout(dialog)
                l.addWidget(
                    QLabel(
                        f"{Path(path).name}\n{len(doc.rows)} строк, {len(doc.headers)} колонок\nДата в имени: {doc.export_date or 'не определена'}"
                    )
                )
                full = QCheckBox(
                    "Это полная выгрузка всех разделов, вариантов и полей каталога"
                )
                l.addWidget(full)
                l.addWidget(
                    QLabel(
                        "По умолчанию — частичная выгрузка. Отсутствующие записи сохранятся."
                    )
                )
                bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
                bb.accepted.connect(dialog.accept)
                bb.rejected.connect(dialog.reject)
                l.addWidget(bb)
                if dialog.exec():
                    conflicts = self.service.import_csv(path, site, full.isChecked())
                    QMessageBox.information(
                        self,
                        "Импорт завершён",
                        f"{SITES[site][0]}: данные сохранены. Конфликтов: {len(conflicts)}.\nЛокальные изменения не перезаписаны без решения.",
                    )
            except Exception as exc:
                error(self, exc)
        self.reload_categories()
        self.refresh()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and all(
            u.isLocalFile() and u.toLocalFile().lower().endswith(".csv")
            for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.import_files([u.toLocalFile() for u in event.mimeData().urls()])
        event.acceptProposedAction()

    def resolve_conflicts(self):
        for c in self.repo.conflicts():
            p = self.repo.get(c["product_id"])
            dialog = QDialog(self)
            dialog.setWindowTitle("Конфликт — " + SITES[p.site][0])
            dialog.resize(850, 500)
            l = QVBoxLayout(dialog)
            l.addWidget(QLabel(p.current["Title"] + "\nПоле: " + c["field"]))
            l.addWidget(
                table(
                    ["Предыдущее", "В программе", "В CSV"],
                    [[c["base"], c["local"], c["remote"]]],
                )
            )
            if c["field"] == "__missing__":
                l.addWidget(
                    QLabel(
                        "Подтверждение отсутствия скроет запись из рабочего каталога. История и исходные данные сохранятся."
                    )
                )
            bb = QDialogButtonBox(QDialogButtonBox.Cancel)
            local = bb.addButton("Оставить локальное", QDialogButtonBox.ActionRole)
            remote = bb.addButton(
                "Принять CSV / отсутствие", QDialogButtonBox.ActionRole
            )
            local.clicked.connect(lambda: dialog.done(2))
            remote.clicked.connect(lambda: dialog.done(3))
            bb.rejected.connect(dialog.reject)
            l.addWidget(bb)
            result = dialog.exec()
            if result == 0:
                break
            try:
                self.service.resolve(c["id"], "local" if result == 2 else "remote")
            except Exception as exc:
                error(self, exc)
        self.refresh()

    def diagnostics(self):
        rows = []
        for s in [self.current_site()] if self.current_site() else SITES:
            for i in self.service.issues(s):
                rows.append(
                    [
                        SITES[s][0],
                        "Ошибка" if i.severity == "error" else "Предупреждение",
                        self.repo.get(i.row_id).current["Title"],
                        i.field,
                        i.message,
                        "Исходная" if i.inherited else "Текущая",
                    ]
                )
        self.show_table(
            "Диагностика",
            ["Сайт", "Уровень", "Товар", "Поле", "Сообщение", "Происхождение"],
            rows,
        )

    def show_table(self, title, headers, rows):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(1100, 620)
        l = QVBoxLayout(dialog)
        t = table(headers, rows)
        l.addWidget(t)
        l.addWidget(button("Закрыть", dialog.accept))
        dialog.exec()

    def export_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Подготовить CSV для Tilda")
        dialog.resize(680, 440)
        layout = QVBoxLayout(dialog)
        checks = {}
        for s, (name, _) in SITES.items():
            check = QCheckBox(name)
            check.setChecked(self.current_site() in (None, s))
            checks[s] = check
            layout.addWidget(check)
        mode = QComboBox()
        mode.addItems(["Только изменения и связанные семейства", "Полный каталог"])
        layout.addWidget(mode)
        zip_check = QCheckBox("Также упаковать файлы в ZIP")
        zip_check.setChecked(True)
        layout.addWidget(zip_check)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton(
            "Проверить и посмотреть изменения", QDialogButtonBox.AcceptRole
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if not dialog.exec():
            return
        sites = [s for s, c in checks.items() if c.isChecked()]
        if not sites:
            return
        mode_value = "changes" if mode.currentIndex() == 0 else "full"
        if (
            mode_value == "full"
            and QMessageBox.warning(
                self,
                "Полный экспорт",
                "Полный экспорт может перезаписать более новые данные Tilda. Продолжить подготовку?",
                QMessageBox.Yes | QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        rows = []
        errors = []
        for s in sites:
            for rid, title, field, old, new in self.service.differences(s):
                rows.append([SITES[s][0], title, LABELS.get(field, field), old, new])
            for issue in self.service.issues(s):
                if issue.severity == "error":
                    errors.append(SITES[s][0] + ": " + issue.message)
                rows.append(
                    [
                        SITES[s][0],
                        self.repo.get(issue.row_id).current["Title"],
                        "ОШИБКА" if issue.severity == "error" else "Предупреждение",
                        "",
                        issue.message,
                    ]
                )
        preview = QDialog(self)
        preview.setWindowTitle("Было → Стало · проверка перед экспортом")
        preview.resize(1150, 660)
        pl = QVBoxLayout(preview)
        pl.addWidget(table(["Сайт", "Товар", "Поле", "Было", "Стало"], rows))
        pl.addWidget(
            QLabel(
                "В CSV попадут полные строки и связанные варианты. Пометки удаления — в отдельный список."
            )
        )
        pb = QDialogButtonBox(QDialogButtonBox.Cancel)
        go = pb.addButton("Создать файлы", QDialogButtonBox.AcceptRole)
        go.setEnabled(not errors)
        if errors:
            warning = QLabel(
                "Экспорт заблокирован: исправьте ошибки / разрешите конфликты. Можно выбрать только незатронутые сайты."
            )
            warning.setWordWrap(True)
            pl.addWidget(warning)
        pb.accepted.connect(preview.accept)
        pb.rejected.connect(preview.reject)
        pl.addWidget(pb)
        if not preview.exec():
            return
        if not RiskDialog(self, export=True).exec():
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Куда сохранить CSV и инструкцию"
        )
        if not folder:
            return
        try:
            path, _ = self.service.export(
                sites, folder, mode_value, True, zip_check.isChecked()
            )
            done = QDialog(self)
            done.setWindowTitle("Файлы созданы. Импорт в Tilda ещё не выполнен.")
            done.resize(780, 630)
            dl = QVBoxLayout(done)
            text = QPlainTextEdit(IMPORT_GUIDE + "\nПапка: " + str(path))
            text.setReadOnly(True)
            dl.addWidget(text)
            dl.addWidget(
                button(
                    "Открыть папку",
                    lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))),
                )
            )
            dl.addWidget(button("Готово", done.accept, True))
            done.exec()
            self.refresh()
        except Exception as exc:
            error(self, exc)

    def history(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("История изменений и экспортов")
        dialog.resize(1150, 650)
        layout = QVBoxLayout(dialog)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        records = self.repo.db.execute(
            "SELECT * FROM history ORDER BY id DESC"
        ).fetchall()
        tabs.addTab(
            table(
                ["Дата (UTC)", "Сайт", "Товар", "Поле", "Было", "Стало", "Статус"],
                [
                    [
                        r["at"],
                        SITES[r["site"]][0],
                        self.repo.get(r["product_id"]).current.get("Title", ""),
                        r["field"],
                        r["old"],
                        r["new"],
                        r["status"],
                    ]
                    for r in records
                ],
            ),
            "Изменения",
        )
        exports = list(self.repo.db.execute("SELECT * FROM exports ORDER BY id DESC"))
        page = QWidget()
        el = QVBoxLayout(page)
        et = table(
            ["Дата (UTC)", "Сайт", "Режим", "Путь", "Статус"],
            [
                [e["at"], SITES[e["site"]][0], e["mode"], e["path"], e["status"]]
                for e in exports
            ],
        )
        el.addWidget(et)

        def confirm():
            i = et.currentRow()
            if i < 0:
                return
            if (
                QMessageBox.question(
                    dialog,
                    "Подтверждение",
                    "Вы успешно загрузили именно этот CSV в Tilda?\nЭто ручное подтверждение, не проверка каталога.",
                )
                == QMessageBox.Yes
            ):
                self.service.confirm_export(exports[i]["id"])
                et.item(i, 4).setText("Импорт подтвержден")

        el.addWidget(button("Подтвердить импорт выбранного файла в Tilda", confirm))
        tabs.addTab(page, "Экспорты")
        imports = list(self.repo.db.execute("SELECT * FROM imports ORDER BY id DESC"))
        tabs.addTab(
            table(
                [
                    "Загружено (UTC)",
                    "Сайт",
                    "Файл",
                    "Дата в имени (пояс неизвестен)",
                    "Полнота",
                ],
                [
                    [
                        r["at"],
                        SITES[r["site"]][0],
                        r["filename"],
                        r["export_date"],
                        "Полная" if r["complete"] else "Частичная",
                    ]
                    for r in imports
                ],
            ),
            "Загрузки CSV",
        )
        layout.addWidget(button("Закрыть", dialog.accept))
        dialog.exec()
        self.refresh()

    def delete_selected(self):
        try:
            p = self.selected()
            if (
                self.allow([p.site])
                and QMessageBox.question(
                    self,
                    "Ручное удаление",
                    "Пометить запись и её варианты для удаления в Tilda?\nCSV не удаляет товары. Будет подготовлена отдельная инструкция.",
                )
                == QMessageBox.Yes
            ):
                self.service.mark_delete(p.id)
                self.refresh()
        except Exception as exc:
            error(self, exc)

    def undo_selected(self):
        try:
            p = self.selected()
            if (
                self.allow([p.site])
                and QMessageBox.question(
                    self,
                    "Отмена локальных изменений",
                    "Вернуть последнее известное состояние CSV? История сохранится.",
                )
                == QMessageBox.Yes
            ):
                self.service.undo(p.id)
                self.refresh()
        except Exception as exc:
            error(self, exc)

    def deletions(self):
        products = [p for p in self.repo.products() if p.deletion]
        dialog = QDialog(self)
        dialog.setWindowTitle("Ручное удаление в Tilda")
        dialog.resize(1000, 560)
        l = QVBoxLayout(dialog)
        t = table(
            ["Сайт", "Товар", "UID", "Ссылка", "Статус"],
            [
                [
                    SITES[p.site][0],
                    p.current["Title"],
                    p.uid,
                    p.current.get("Url", ""),
                    p.deletion,
                ]
                for p in products
            ],
        )
        l.addWidget(t)

        def update(state):
            i = t.currentRow()
            if i < 0:
                return
            p = products[i]
            if not self.allow([p.site]):
                return
            self.service.mark_delete(p.id, state)
            dialog.accept()
            self.refresh()

        l.addWidget(
            QLabel(
                "Подтверждение пользователя не удаляет запись из базы. Нужна свежая полная выгрузка."
            )
        )
        l.addWidget(button("Удаление в Tilda выполнено", lambda: update("done")))
        l.addWidget(button("Снять пометку", lambda: update("")))
        l.addWidget(button("Закрыть", dialog.reject))
        dialog.exec()

    def settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Состояние каталогов и резервные копии")
        dialog.resize(1100, 570)
        l = QVBoxLayout(dialog)
        rows = []
        for c in self.repo.catalogs():
            rows.append(
                [
                    c["name"],
                    c["imported_at"],
                    c["export_date"],
                    "Не подтверждена",
                    sum(p.changed for p in self.repo.products(c["id"])),
                    self.service.unexported_count(c["id"]),
                ]
            )
        l.addWidget(
            table(
                [
                    "Сайт",
                    "Последняя загрузка (UTC)",
                    "Дата в имени CSV",
                    "Актуальность",
                    "Локальных изменений",
                    "Не включены в CSV",
                ],
                rows,
            )
        )
        label = QLabel(
            "База: "
            + str(self.repo.path)
            + "\nАвтокопии: при запуске, перед импортом/восстановлением и при закрытии. Хранятся последние 30."
        )
        label.setWordWrap(True)
        l.addWidget(label)

        def backup():
            path, _ = QFileDialog.getSaveFileName(
                dialog,
                "Сохранить переносимую копию",
                "STCatalogManager.sqlite3",
                "SQLite (*.sqlite3)",
            )
            if path:
                try:
                    self.repo.backup(path)
                    QMessageBox.information(
                        dialog,
                        "Готово",
                        "Резервная копия сохранена. Её можно восстановить на другом компьютере.",
                    )
                except Exception as exc:
                    error(dialog, exc)

        def restore():
            path, _ = QFileDialog.getOpenFileName(
                dialog, "Восстановить базу", "", "SQLite (*.sqlite3)"
            )
            if (
                path
                and QMessageBox.question(
                    dialog,
                    "Восстановление",
                    "Заменить рабочую базу этой копией? Перед заменой будет создана резервная копия.",
                )
                == QMessageBox.Yes
            ):
                try:
                    self.repo.restore(path)
                    self.service.edit_ack.clear()
                    self.reload_categories()
                    self.refresh()
                    dialog.accept()
                except Exception as exc:
                    error(dialog, exc)

        l.addWidget(button("Сохранить резервную копию / перенести базу", backup))
        l.addWidget(button("Восстановить из копии", restore))
        l.addWidget(button("Закрыть", dialog.accept))
        dialog.exec()

    def regions(self):
        try:
            p = self.selected()
            if p.current.get("Parent UID"):
                p = self.service.parent(p)
            if not p:
                raise CatalogError("Родитель не найден.")
            dialog = QDialog(self)
            dialog.setWindowTitle("Региональные версии — подтвердите соответствия")
            dialog.resize(1000, 640)
            l = QVBoxLayout(dialog)
            note = QLabel(
                "Совпадения — предложения, их нужно подтвердить. UID и External ID не используются как общий ключ.\nВыберите по одному основному товару на нужных сайтах. Расписания задаются отдельно для каждого региона."
            )
            note.setWordWrap(True)
            l.addWidget(note)
            mapped = {r.site: r.id for r in self.service.mapped(p.id)}
            scores = {r.id: score for score, r in self.service.candidates(p.id)}
            combos = {}
            for s, (name, _) in SITES.items():
                line = QHBoxLayout()
                line.addWidget(QLabel(name))
                combo = QComboBox()
                combo.addItem("Не выбран", None)
                rows = [
                    r
                    for r in self.repo.products(s)
                    if not r.current.get("Parent UID") and not r.deletion
                ]
                rows.sort(key=lambda r: scores.get(r.id, 0), reverse=True)
                for r in rows:
                    combo.addItem(r.current["Title"] + "  [" + r.uid + "]", r.id)
                if s in mapped:
                    combo.setCurrentIndex(combo.findData(mapped[s]))
                line.addWidget(combo, 1)
                combos[s] = combo
                l.addLayout(line)

            def save_map():
                ids = [c.currentData() for c in combos.values() if c.currentData()]
                try:
                    self.service.save_mapping(ids)
                    dialog.accept()
                    self.batch_dialog(ids)
                except Exception as exc:
                    error(dialog, exc)

            l.addWidget(
                button(
                    "Подтвердить соответствия и открыть редактор сайтов", save_map, True
                )
            )
            l.addWidget(button("Закрыть", dialog.reject))
            dialog.exec()
            self.refresh()
        except Exception as exc:
            error(self, exc)

    def batch_dialog(self, ids):
        products = [self.repo.get(i) for i in ids]
        if not self.allow([p.site for p in products]):
            return
        d = QDialog(self)
        d.setWindowTitle("Изменения выбранных сайтов")
        d.resize(1100, 700)
        l = QVBoxLayout(d)
        l.addWidget(
            QLabel(
                "Выберите сайты галочками. Значения и время вводятся отдельно; другие поля останутся прежними."
            )
        )
        tabs = QTabWidget()
        l.addWidget(tabs)
        basic = QWidget()
        bl = QVBoxLayout(basic)
        field = QComboBox()
        field.addItems(["Название", "Цена", "Категория", "SEO: заголовок"])
        fields = ["Title", "Price", "Category", "SEO title"]
        bl.addWidget(field)
        grid = table(
            ["Изменить", "Сайт", "Новое значение"],
            [["", SITES[p.site][0], p.current["Title"]] for p in products],
            True,
        )
        for i in range(len(products)):
            grid.item(i, 0).setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            grid.item(i, 0).setCheckState(Qt.Unchecked)
            grid.item(i, 1).setFlags(Qt.ItemIsEnabled)
        field.currentIndexChanged.connect(
            lambda idx: [
                grid.item(i, 2).setText(p.current.get(fields[idx], ""))
                for i, p in enumerate(products)
            ]
        )
        bl.addWidget(grid)
        tabs.addTab(basic, "Одно поле на нескольких сайтах")
        sched = QWidget()
        sl = QVBoxLayout(sched)
        rows = []
        variants = []
        for p in products:
            for child in self.service.children(p):
                if child.deletion:
                    continue
                try:
                    value = schedule_value(p.current, child.current)[1]
                except CatalogError:
                    continue
                variants.append((p, child))
                rows.append(
                    ["", SITES[p.site][0], value, child.current.get("Price", "")]
                )
        sg = table(
            ["Изменить", "Сайт", "Расписание (с часовым поясом)", "Цена"], rows, True
        )
        for i in range(len(rows)):
            sg.item(i, 0).setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            sg.item(i, 0).setCheckState(Qt.Unchecked)
            sg.item(i, 1).setFlags(Qt.ItemIsEnabled)
        sg.setColumnWidth(2, 550)
        sl.addWidget(sg)
        tabs.addTab(sched, "Расписание существующих групп")
        new_page = QWidget()
        nl = QVBoxLayout(new_page)
        ng = table(
            ["Добавить", "Сайт", "Новая группа / расписание", "Цена"],
            [["", SITES[p.site][0], "", ""] for p in products],
            True,
        )
        for i in range(len(products)):
            ng.item(i, 0).setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            ng.item(i, 0).setCheckState(Qt.Unchecked)
            ng.item(i, 1).setFlags(Qt.ItemIsEnabled)
        ng.setColumnWidth(2, 550)
        nl.addWidget(ng)
        tabs.addTab(new_page, "Новые группы")

        def apply():
            try:
                patches = {}
                additions = []
                for i, p in enumerate(products):
                    if grid.item(i, 0).checkState() == Qt.Checked:
                        patches[p.id] = {
                            fields[field.currentIndex()]: grid.item(i, 2).text()
                        }
                    if ng.item(i, 0).checkState() == Qt.Checked:
                        additions.append(
                            (p.id, ng.item(i, 2).text(), ng.item(i, 3).text())
                        )
                for i, (parent, child) in enumerate(variants):
                    if sg.item(i, 0).checkState() == Qt.Checked:
                        patches[child.id] = schedule_patch(
                            parent.current, child.current, sg.item(i, 2).text()
                        ) | {"Price": sg.item(i, 3).text()}
                preview_rows = []
                for rid, patch in patches.items():
                    p = self.repo.get(rid)
                    for f, v in patch.items():
                        if v != p.current.get(f, ""):
                            preview_rows.append(
                                [
                                    SITES[p.site][0],
                                    p.current["Title"],
                                    LABELS.get(f, f),
                                    p.current.get(f, ""),
                                    v,
                                ]
                            )
                for rid, value, price in additions:
                    p = self.repo.get(rid)
                    preview_rows.append(
                        [
                            SITES[p.site][0],
                            p.current["Title"],
                            "Новая группа",
                            "",
                            value + " · " + price,
                        ]
                    )
                if not preview_rows:
                    return
                self.show_table(
                    "Проверьте изменения",
                    ["Сайт", "Товар", "Поле", "Было", "Стало"],
                    preview_rows,
                )
                if (
                    QMessageBox.question(
                        d, "Применить", "Сохранить перечисленные изменения?"
                    )
                    != QMessageBox.Yes
                ):
                    return
                # The entire multi-site plan commits atomically through nested savepoints.
                self.service.batch_plan(patches, additions)
                d.accept()
            except Exception as exc:
                error(d, exc)

        l.addWidget(button("Посмотреть и применить выбранные изменения", apply, True))
        l.addWidget(button("Закрыть", d.reject))
        d.exec()

    def closeEvent(self, event):
        try:
            self.repo.backup()
        except Exception as exc:
            error(self, exc)
            event.ignore()
            return
        event.accept()
