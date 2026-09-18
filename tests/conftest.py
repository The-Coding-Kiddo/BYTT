"""Project-wide pytest setup.

Ensures a single, real QApplication is created before any test module's
own Qt-app fixture runs. QApplication is a subclass of QCoreApplication,
so creating it first here satisfies both tests/net's QCoreApplication-based
fixture and tests/gui's QApplication-based fixture, regardless of which
test file pytest collects/runs first. Without this, a bare QCoreApplication
created first would be reused by QApplication.instance() in the gui tests,
and constructing a QWidget against it aborts the interpreter.
"""
import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def _qapp_session():
    app = QApplication.instance() or QApplication([])
    yield app
