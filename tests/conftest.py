"""Project-wide pytest setup.

Ensures a single, real QApplication is created before any test module's
own Qt-app fixture runs. QApplication is a subclass of QCoreApplication,
so creating it first here satisfies both tests/net's QCoreApplication-based
fixture and tests/gui's QApplication-based fixture, regardless of which
test file pytest collects/runs first. Without this, a bare QCoreApplication
created first would be reused by QApplication.instance() in the gui tests,
and constructing a QWidget against it aborts the interpreter.
"""
import gc
import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def _qapp_session():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _qt_teardown():
    """Flushes deleteLater'd widgets and runs a GC pass after every test.

    Many GUI tests construct MainWindow/GpsPanel instances (each holding a
    pyqtgraph PlotWidget) without an explicit close(). Left to accumulate
    and get garbage-collected at arbitrary times, pyqtgraph's process-global
    ViewBox registry can segfault during teardown. This keeps the live-object
    count from building up across the whole session instead of chasing the
    segfault itself."""
    yield
    QApplication.processEvents()
    gc.collect()
    QApplication.processEvents()
