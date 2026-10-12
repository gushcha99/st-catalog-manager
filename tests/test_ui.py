import os
import pytest

if os.environ.get("ST_SKIP_QT") == "1":
    pytest.skip(
        "Qt cannot inspect CPU features inside this macOS sandbox; run GUI tests on Windows",
        allow_module_level=True,
    )
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from st_catalog.ui import MainWindow, RiskDialog, ProductDialog, HTMLField
from st_catalog.domain import SITES


def test_risk_checkbox_required(app):
    dialog = RiskDialog()
    assert not dialog.proceed.isEnabled()
    dialog.check.setChecked(True)
    assert dialog.proceed.isEnabled()
    dialog.check.setChecked(False)
    assert not dialog.proceed.isEnabled()
    dialog.close()


def test_window_search_hierarchy(app, svc):
    w = MainWindow(svc)
    assert w.tree.topLevelItemCount() == 194
    w.search.setText("ДипИФР")
    assert 5 <= w.tree.topLevelItemCount() < 30
    w.nav.setCurrentRow(1)
    assert w.tree.topLevelItemCount() >= 1
    p = next(svc.repo.products("6486596").__iter__())
    dialog = ProductDialog(svc, p.id, w)
    dialog.fields["Price"].setText("50")
    dialog.save()
    assert svc.repo.get(p.id).current["Price"] == "50"
    w.close()


def test_html_open_save_no_change(app):
    raw = '<div class="z" style="font-size:12px">А &amp; Բ<br /><iframe src="x"></iframe></div>'
    w = HTMLField(raw)
    w.tabs.setCurrentIndex(1)
    w.tabs.setCurrentIndex(0)
    assert w.value() == raw


def test_card_noop_preserves_nbsp_crlf_and_html(app, svc):
    p = next(p for p in svc.repo.products("6486596") if p.current["Parent UID"])
    raw = '<div data-x="1">А\u00a0Б\r\n<br />&nbsp;</div>'
    svc.edit(p.id, {"Description": raw})
    before = svc.repo.get(p.id).current
    dialog = ProductDialog(svc, p.id)
    dialog.fields["Description"].tabs.setCurrentIndex(1)
    dialog.save()
    assert svc.repo.get(p.id).current == before
