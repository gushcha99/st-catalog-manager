import json
import zipfile
from pathlib import Path
import pytest
from st_catalog.domain import SITES, DATA, CatalogError, TextFragments, schedule_value
from st_catalog.csv_io import read_csv, csv_bytes
from st_catalog.repository import Repository
from st_catalog.service import CatalogService

INT = "6486596"
ARM = "6453690"


def course(svc, site=INT):
    return next(
        p
        for p in svc.repo.products(site)
        if p.current["Title"] == "Онлайн-курс ДипИФР (рус)"
    )


def variant(svc, site=INT):
    return svc.children(course(svc, site))[0]


def remote_file(svc, tmp_path, site=INT, patches=None, rows=None, headers=None):
    products = svc.repo.products(site)
    data = rows if rows is not None else [dict(p.base) for p in products]
    for row in data:
        row.update((patches or {}).get(row["Tilda UID"], {}))
    path = tmp_path / f"store-{site}-202610121200.csv"
    path.write_bytes(csv_bytes(headers or svc.repo.catalog(site)["headers"], data))
    return path


@pytest.mark.parametrize("site", SITES)
def test_round_trip_every_cell(svc, tmp_path, site):
    source = read_csv(next(DATA.glob(f"store-{site}-*.csv")))
    out, _ = svc.export([site], tmp_path / "out", "full", True)
    result = read_csv(out / (SITES[site][1] + ".csv"))
    assert result.headers == source.headers
    assert result.rows == source.rows
    assert svc.repo.products(site)[0].base == source.rows[0]


def test_seed_count_and_columns(svc):
    assert len(svc.repo.products()) == 556
    assert [len(svc.repo.catalog(s)["headers"]) for s in SITES] == [29, 26, 26, 26, 26]


def test_price_retains_uid_and_other_fields(svc):
    p = variant(svc)
    old = dict(p.current)
    svc.edit(p.id, {"Price": "650.00"})
    assert svc.repo.get(p.id).current == old | {"Price": "650.00"}


def test_schedule_preserves_modifications_html_tabs_and_siblings(svc):
    p = variant(svc)
    snapshot = {r.id: r.current for r in svc.repo.products()}
    svc.edit_schedule(p.id, "20.09.2027 пн 19:30-22:40 (UTC +0)")
    changed = svc.repo.get(p.id)
    assert (
        changed.current["Editions"]
        == "Ближайшие группы::20.09.2027 пн 19:30-22:40 (UTC +0)"
    )
    for r in svc.repo.products():
        if r.id != p.id:
            assert r.current == snapshot[r.id]
        else:
            assert {
                k: v for k, v in r.current.items() if k not in ("Title", "Editions")
            } == {
                k: v
                for k, v in snapshot[r.id].items()
                if k not in ("Title", "Editions")
            }


def test_new_variant_stable_uid_and_parent(svc, tmp_path):
    parent = course(svc)
    rid = svc.add_variant(parent.id, "20.09.2027", "650")
    p = svc.repo.get(rid)
    assert len(p.uid) == 12 and p.uid.isdigit()
    assert p.current["Parent UID"] == parent.uid
    assert len({r.uid for r in svc.repo.products()}) == len(svc.repo.products())
    a, _ = svc.export([INT], tmp_path / "a", risk_confirmed=True)
    b, _ = svc.export([INT], tmp_path / "b", risk_confirmed=True)
    assert (a / "st.education.csv").read_bytes() == (
        b / "st.education.csv"
    ).read_bytes()


def test_combined_changes_exports_complete_family(svc, tmp_path):
    p = variant(svc)
    svc.edit(p.id, {"Price": "650"})
    rid = svc.add_variant(course(svc).id, "22.09.2027", "640")
    out, _ = svc.export([INT], tmp_path / "out", risk_confirmed=True)
    rows = read_csv(out / "st.education.csv").rows
    assert len(rows) == 5
    assert {p.uid, svc.repo.get(rid).uid, course(svc).uid} <= {
        r["Tilda UID"] for r in rows
    }
    assert next(r for r in rows if r["Tilda UID"] == p.uid)["Price"] == "650"
    with zipfile.ZipFile(out / "Все-сайты.zip") as z:
        assert "st.education.csv" in z.namelist()


def test_modifications_and_tabs(svc, tmp_path):
    parent = course(svc)
    before = parent.current
    svc.edit_schedule(variant(svc).id, "01.10.2027")
    out, _ = svc.export([INT], tmp_path / "out", risk_confirmed=True)
    data = next(
        r
        for r in read_csv(out / "st.education.csv").rows
        if r["Tilda UID"] == parent.uid
    )
    for f in ["Modifications", "Description", "Text", "Tabs:1", "Tabs:2", "Tabs:3"]:
        assert data[f] == before[f]


def test_unicode_unknown_columns_quotes_multiline(svc, tmp_path):
    p = variant(svc, ARM)
    rows = [dict(p.base) | {"Custom": 'դր. € ₽ «Русский»; "a"\nвторая строка'}]
    headers = svc.repo.catalog(ARM)["headers"] + ["Custom"]
    path = remote_file(svc, tmp_path, ARM, rows=rows, headers=headers)
    svc.import_csv(path)
    assert svc.repo.get(p.id).current["Custom"] == rows[0]["Custom"]
    out, _ = svc.export([ARM], tmp_path / "out", "full", True)
    again = read_csv(out / "am.st.education.csv")
    assert (
        next(r for r in again.rows if r["Tilda UID"] == p.uid)["Custom"]
        == rows[0]["Custom"]
    )


def test_mapping_and_three_selected_sites(svc):
    courses = [course(svc, s) for s in SITES]
    group = svc.save_mapping([p.id for p in courses])
    assert len(svc.mapped(courses[0].id)) == 5
    before = {p.id: p.current for p in svc.repo.products()}
    selected = courses[:3]
    svc.batch_edit({p.id: {"Title": "Обновлённый курс"} for p in selected})
    for p in svc.repo.products():
        assert p.current == (
            before[p.id] | {"Title": "Обновлённый курс"}
            if p.id in {x.id for x in selected}
            else before[p.id]
        )


def test_restart_persistence_and_fresh_session_ack(svc):
    p = variant(svc)
    svc.edit(p.id, {"Price": "987"})
    path = svc.repo.path
    svc.repo.close()
    svc.repo = Repository(path)
    new = CatalogService(svc.repo)
    assert new.repo.get(p.id).current["Price"] == "987"
    with pytest.raises(CatalogError):
        new.edit(p.id, {"Price": "999"})


def test_risk_guard(svc, tmp_path):
    svc.edit_ack.clear()
    with pytest.raises(CatalogError):
        svc.edit(variant(svc).id, {"Price": "999"})
    with pytest.raises(CatalogError):
        svc.export([INT], tmp_path)


def test_three_way_nonconflicting_and_conflict(svc, tmp_path):
    p = variant(svc)
    svc.edit(p.id, {"Price": "650", "SKU": "LOCAL"})
    path = remote_file(
        svc, tmp_path, patches={p.uid: {"Price": "630", "Brand": "REMOTE"}}
    )
    conflicts = svc.import_csv(path)
    assert len(conflicts) == 1
    assert conflicts[0]["field"] == "Price"
    assert svc.repo.get(p.id).current["Price"] == "650"
    assert svc.repo.get(p.id).current["Brand"] == "REMOTE"
    assert svc.repo.get(p.id).current["SKU"] == "LOCAL"
    with pytest.raises(CatalogError):
        svc.export([INT], tmp_path / "out", risk_confirmed=True)
    svc.resolve(conflicts[0]["id"], "remote")
    assert svc.repo.get(p.id).current["Price"] == "630"
    svc.export([INT], tmp_path / "out", risk_confirmed=True)


def test_keep_local_conflict_updates_baseline(svc, tmp_path):
    p = variant(svc)
    svc.edit(p.id, {"Price": "650"})
    conflicts = svc.import_csv(
        remote_file(svc, tmp_path, patches={p.uid: {"Price": "630"}})
    )
    svc.resolve(conflicts[0]["id"], "local")
    assert svc.repo.get(p.id).base["Price"] == "630"
    assert svc.repo.get(p.id).current["Price"] == "650"


def test_armenia_diagnostics_and_no_auto_fix(svc, tmp_path):
    issues = svc.issues(ARM)
    assert any(i.field == "External ID" and i.inherited for i in issues)
    assert any("Parent UID отсутствует" in i.message for i in issues)
    assert not [i for i in issues if i.severity == "error"]
    p = next(p for p in svc.repo.products(ARM) if p.uid == "551524150583")
    assert p.current["Parent UID"] == ""
    svc.export([ARM], tmp_path / "out", "full", True)


def test_new_invalid_values_block_export(svc, tmp_path):
    p = variant(svc)
    svc.edit(p.id, {"Price": "NaN"})
    with pytest.raises(CatalogError):
        svc.export([INT], tmp_path / "out", risk_confirmed=True)
    assert not list((tmp_path / "out").glob("*.csv"))


def test_partial_missing_rows_and_columns_preserved(svc, tmp_path):
    p = variant(svc)
    original = {r.id: r.current for r in svc.repo.products(INT)}
    cols = ["Tilda UID", "Title", "Parent UID", "Editions", "Price"]
    row = {h: p.base[h] for h in cols} | {"Price": "700"}
    svc.import_csv(remote_file(svc, tmp_path, rows=[row], headers=cols))
    assert len(svc.repo.products(INT)) == 116
    assert svc.repo.get(p.id).current == original[p.id] | {"Price": "700"}
    assert "Tabs:3" in svc.repo.catalog(INT)["headers"]
    assert not svc.repo.conflicts()


def test_full_missing_requires_resolution(svc, tmp_path):
    p = variant(svc)
    rows = [dict(r.base) for r in svc.repo.products(INT) if r.id != p.id]
    cs = svc.import_csv(remote_file(svc, tmp_path, rows=rows), complete=True)
    assert len(cs) == 1 and cs[0]["field"] == "__missing__"
    assert not svc.repo.get(p.id).absent
    svc.resolve(cs[0]["id"], "remote")
    assert svc.repo.get(p.id).absent
    assert svc.repo.get(p.id).base == p.base


def test_delete_confirm_and_full_verification(svc, tmp_path):
    p = variant(svc)
    svc.mark_delete(p.id)
    svc.mark_delete(p.id, "done")
    assert not svc.repo.get(p.id).absent
    rows = [dict(r.base) for r in svc.repo.products(INT) if r.id != p.id]
    svc.import_csv(remote_file(svc, tmp_path, rows=rows), complete=True)
    assert svc.repo.get(p.id).absent
    assert svc.repo.get(p.id).deletion == "verified"


def test_exported_new_variant_not_silently_cancelled(svc, tmp_path):
    rid = svc.add_variant(course(svc).id, "25.09.2027", "10")
    svc.export([INT], tmp_path / "out", risk_confirmed=True)
    svc.mark_delete(rid)
    assert not svc.repo.get(rid).absent
    assert svc.repo.get(rid).deletion == "pending"


def test_unexported_new_variant_can_cancel(svc):
    rid = svc.add_variant(course(svc).id, "25.09.2027", "10")
    svc.mark_delete(rid)
    assert svc.repo.get(rid).absent
    assert svc.repo.get(rid).deletion == "cancelled"


def test_backup_restore(svc, tmp_path):
    p = variant(svc)
    backup = svc.repo.backup(tmp_path / "saved.sqlite3")
    svc.edit(p.id, {"Price": "123"})
    svc.repo.restore(backup)
    assert svc.repo.get(p.id).current == p.current


def test_history_export_confirm_verify(svc, tmp_path):
    p = variant(svc)
    svc.edit(p.id, {"Price": "650"})
    _, ids = svc.export([INT], tmp_path / "out", risk_confirmed=True)
    assert (
        svc.repo.db.execute(
            "SELECT status FROM exports WHERE id=?", (ids[0],)
        ).fetchone()[0]
        == "Экспортировано"
    )
    assert svc.repo.get(p.id).base == p.base
    svc.confirm_export(ids[0])
    assert svc.repo.get(p.id).base == p.base
    svc.import_csv(remote_file(svc, tmp_path, patches={p.uid: {"Price": "650"}}))
    h = svc.repo.db.execute(
        "SELECT status FROM history WHERE product_id=? AND field='Price'", (p.id,)
    ).fetchall()
    assert h[-1][0] == "Проверено по Tilda"
    assert not svc.repo.get(p.id).changed


def test_undo_to_last_remote(svc):
    p = variant(svc)
    svc.edit(p.id, {"Price": "123", "Description": "<b>Новый</b>"})
    svc.undo(p.id)
    assert svc.repo.get(p.id).current == p.base


def test_html_text_fragment_edit_preserves_markup():
    source = '<div class="a" style="color: red"><b>Привет</b>&nbsp;<iframe src="x"></iframe><em data-id="7">Մարդ</em></div>'
    p = TextFragments(source)
    assert p.replace([v for _, _, v in p.fragments]) == source
    values = [v for _, _, v in p.fragments]
    values[0] = "Текст & знак"
    edited = p.replace(values)
    assert edited == source.replace("Привет", "Текст &amp; знак")


def test_ids_protected_and_batch_atomic(svc):
    p = variant(svc)
    with pytest.raises(CatalogError):
        svc.edit(p.id, {"Tilda UID": "123"})
    other = variant(svc, ARM)
    with pytest.raises(CatalogError):
        svc.batch_edit({p.id: {"Price": "99"}, other.id: {"Tilda UID": "123"}})
    assert svc.repo.get(p.id).current == p.current


def test_multi_site_plan_atomic_on_bad_addition(svc):
    p = variant(svc)
    count = len(svc.repo.products())
    with pytest.raises(CatalogError):
        svc.batch_plan(
            {p.id: {"Price": "99"}},
            [(course(svc).id, "ok", "10"), (course(svc, ARM).id, "bad;value", "20")],
        )
    assert len(svc.repo.products()) == count
    assert svc.repo.get(p.id).current == p.current


def test_filename_and_urls_disagree(svc, tmp_path):
    doc = read_csv(next(DATA.glob(f"store-{INT}-*.csv")))
    doc.filename = "store-6453690-202610121200.csv"
    assert doc.detect_site() is None


def test_malformed_csv_does_not_mutate(svc, tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("Tilda UID;Title;Parent UID;Editions\n1;x;;\n1;y;;\n", encoding="utf8")
    with pytest.raises(CatalogError):
        svc.import_csv(p, INT)
    assert len(svc.repo.products()) == 556


def test_no_false_guarantee_from_import_date(svc, tmp_path):
    svc.import_csv(next(DATA.glob(f"store-{INT}-*.csv")))
    assert INT not in svc.edit_ack
    assert svc.repo.catalog(INT)["export_date"].startswith("2026-10-09")


def test_old_export_confirmation_does_not_confirm_newer_edits(svc, tmp_path):
    p = variant(svc)
    svc.edit(p.id, {"Price": "650"})
    _, old_ids = svc.export([INT], tmp_path / "old", risk_confirmed=True)
    svc.edit(p.id, {"Price": "700"})
    svc.edit(p.id, {"Price": "650"})
    _, new_ids = svc.export([INT], tmp_path / "new", risk_confirmed=True)
    svc.confirm_export(old_ids[0])
    row = svc.repo.db.execute(
        "SELECT status FROM history WHERE product_id=? AND field='Price' ORDER BY id DESC LIMIT 1",
        (p.id,),
    ).fetchone()
    assert row[0] == "Экспортировано"


def test_duplicate_variant_blocks_export(svc, tmp_path):
    p = variant(svc)
    value = schedule_value(course(svc).current, p.current)[1]
    svc.add_variant(course(svc).id, value, "650")
    with pytest.raises(CatalogError):
        svc.export([INT], tmp_path / "out", risk_confirmed=True)


def test_delete_new_parent_cascades_cancel(svc):
    parent = svc.add_product(INT, "Новый курс")
    svc.edit(parent, {"Editions": 'product_options:[{"title":"Группа","params":[]}]'})
    child = svc.add_variant(parent, "Новая группа", "10")
    svc.mark_delete(parent)
    assert svc.repo.get(parent).absent and svc.repo.get(child).absent


def test_import_is_transactional_on_failure(svc, tmp_path, monkeypatch):
    p = variant(svc)
    path = remote_file(svc, tmp_path, patches={p.uid: {"Price": "999"}})

    def fail(*args):
        raise RuntimeError("simulated persistence failure")

    monkeypatch.setattr(svc, "_log_import", fail)
    with pytest.raises(RuntimeError):
        svc.import_csv(path)
    assert svc.repo.get(p.id).current == p.current


def test_failed_export_does_not_mark_status_or_leave_files(svc, tmp_path, monkeypatch):
    import st_catalog.service as module

    p = variant(svc)
    svc.edit(p.id, {"Price": "650"})

    def fail(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        svc.export([INT], tmp_path / "out", risk_confirmed=True)
    assert not list((tmp_path / "out").iterdir())
    assert svc.repo.db.execute("SELECT COUNT(*) FROM exports").fetchone()[0] == 0
    assert (
        svc.repo.db.execute(
            "SELECT status FROM history WHERE field='Price'"
        ).fetchone()[0]
        == "Сохранено"
    )


def test_new_variant_remote_roundtrip_becomes_verified(svc, tmp_path):
    rid = svc.add_variant(course(svc).id, "05.10.2027", "610")
    out, _ = svc.export([INT], tmp_path / "out", risk_confirmed=True)
    svc.import_csv(out / "st.education.csv")
    assert not svc.repo.get(rid).is_new
    assert not svc.repo.get(rid).changed


def test_malformed_html_preserved_when_only_price_changes(svc, tmp_path):
    p = variant(svc)
    raw = '<custom attr="a; &quot;" style="x:y">д\u00a0р\r\n <b>not closed'
    svc.edit(p.id, {"Description": raw})
    svc.edit(p.id, {"Price": "700"})
    out, _ = svc.export([INT], tmp_path / "out", risk_confirmed=True)
    assert (
        next(
            r
            for r in read_csv(out / "st.education.csv").rows
            if r["Tilda UID"] == p.uid
        )["Description"]
        == raw
    )


def test_dangling_parent_from_remote_is_diagnosed(svc, tmp_path):
    p = variant(svc)
    path = remote_file(svc, tmp_path, patches={p.uid: {"Parent UID": "999999999999"}})
    svc.import_csv(path)
    assert any(
        i.severity == "error" and i.field == "Parent UID" for i in svc.issues(INT)
    )


def test_end_to_end_dipifr_five_sites_restart_and_export(svc, tmp_path):
    before = {p.id: dict(p.current) for p in svc.repo.products()}
    parents = [course(svc, site) for site in SITES]
    svc.save_mapping([p.id for p in parents])
    patches = {}
    additions = []
    from st_catalog.domain import schedule_patch

    for p in parents:
        child = svc.children(p)[0]
        value = f"20.09.2027 — группа {SITES[p.site][0]}"
        patches[child.id] = schedule_patch(p.current, child.current, value)
        additions.append((p.id, f"27.09.2027 — новая группа {SITES[p.site][0]}", "650"))
    created = svc.batch_plan(patches, additions)
    assert len(created) == 5
    for p in svc.repo.products():
        if p.id in before:
            assert p.current == before[p.id] | patches.get(p.id, {})
    svc.repo.close()
    svc.repo = Repository(tmp_path / "catalog.sqlite3")
    restarted = CatalogService(svc.repo)
    assert len(restarted.mapped(parents[0].id)) == 5
    out, exports = restarted.export(
        list(SITES), tmp_path / "final", risk_confirmed=True
    )
    assert len(exports) == 5
    for site, (_, domain) in SITES.items():
        rows = read_csv(out / (domain + ".csv")).rows
        parent = course(restarted, site)
        children = restarted.children(parent)
        assert len(rows) == len(children) + 1
        assert (
            next(r for r in rows if r["Tilda UID"] == parent.uid) == before[parent.id]
        )
        for child in children:
            assert next(r for r in rows if r["Tilda UID"] == child.uid) == child.current
    assert len(restarted.repo.products()) == 561
