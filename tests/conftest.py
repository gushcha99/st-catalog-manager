import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from st_catalog.repository import Repository
from st_catalog.service import CatalogService
from st_catalog.domain import SITES


@pytest.fixture
def svc(tmp_path):
    repo = Repository(tmp_path / "catalog.sqlite3")
    service = CatalogService(repo)
    service.acknowledge(SITES)
    yield service
    repo.close()


@pytest.fixture(scope="session")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
