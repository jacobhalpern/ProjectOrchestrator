from __future__ import annotations

import sys


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QWidget, QVBoxLayout
    except ImportError:
        print("PySide6 is not installed. Run: pip install -e .[gui]")
        return 1

    app = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle("ProjectOrchestrator")
    central = QWidget()
    layout = QVBoxLayout(central)
    layout.addWidget(QLabel("ProjectOrchestrator GUI shell"))
    layout.addWidget(QLabel("MVP note: workflow logic belongs in services, not GUI callbacks."))
    window.setCentralWidget(central)
    window.resize(900, 600)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
