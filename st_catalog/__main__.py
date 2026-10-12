from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path
from .repository import Repository, user_directory
from .service import CatalogService


def main():
    parser = argparse.ArgumentParser(description="ST Catalog Manager")
    parser.add_argument(
        "--data-dir", type=Path, help="Отдельная папка базы для тестирования"
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Запуск интерфейса и автоматическое завершение",
    )
    parser.add_argument(
        "--screenshot", type=Path, help="Сохранить снимок главного окна при smoke-test"
    )
    args = parser.parse_args()
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import QLockFile, QTimer, QTranslator, QLibraryInfo
    from .ui import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("ST Catalog Manager")
    app.setOrganizationName("ST Education")
    translator = QTranslator(app)
    translator.load("qtbase_ru", QLibraryInfo.path(QLibraryInfo.TranslationsPath))
    app.installTranslator(translator)
    folder = args.data_dir or user_directory()
    folder.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(folder / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.warning(
            None,
            "ST Catalog Manager",
            "Эта база уже открыта в другом экземпляре приложения.",
        )
        return 1
    repo = None
    try:
        repo = Repository(folder / "catalog.sqlite3")
        service = CatalogService(repo)
        repo.backup()
        window = MainWindow(service)
        window.show()
        if args.smoke_test:

            def finish():
                if args.screenshot:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    window.grab().save(str(args.screenshot))
                window.close()
                app.quit()

            QTimer.singleShot(1000, finish)
        return app.exec()
    except Exception as exc:
        import traceback

        (folder / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        QMessageBox.critical(
            None,
            "Ошибка запуска",
            str(exc) + "\nПодробности: " + str(folder / "error.log"),
        )
        return 1
    finally:
        if repo is not None:
            repo.close()
        lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
