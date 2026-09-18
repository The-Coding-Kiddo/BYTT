"""Entry point: python -m bytt.app"""
import sys
from PySide6.QtWidgets import QApplication
from bytt.config import load_config
from bytt.gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    config = load_config()
    window = MainWindow(config)
    window.resize(1200, 800)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
