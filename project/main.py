import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel
from PySide6.QtCore import Qt

def create_main_window() -> QMainWindow:
    window = QMainWindow()
    label = QLabel("hello world", parent=window)
    label.setAlignment(Qt.AlignCenter)
    window.setCentralWidget(label)
    return window

def main() -> None:
    app = QApplication(sys.argv)
    window = create_main_window()
    window.resize(400, 300)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
