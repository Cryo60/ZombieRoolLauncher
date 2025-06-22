from PyQt6.QtWidgets import QLineEdit, QApplication, QMessageBox
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QDesktopServices
from PyQt6.QtCore import Qt, QUrl

import os

class DragDropLineEdit(QLineEdit):
    """
    A QLineEdit subclass that supports drag and drop for a single file.
    When a file is dropped, its path is set as the line edit's text.
    """
    def __init__(self, parent=None, allowed_extensions=None):
        super().__init__(parent)
        self.setReadOnly(True) # Make it read-only as content is set by drop
        self.setAcceptDrops(True)
        self.allowed_extensions = allowed_extensions if allowed_extensions is not None else []
        self.is_valid_drop = False # Flag to track if the current drag event is valid

    def dragEnterEvent(self, event: QDragEnterEvent):
        """
        Handles drag enter events. Accepts the drag if it contains a URL for a local file
        and matches allowed extensions.
        """
        if event.mimeData().hasUrls():
            # Check if there's exactly one URL and it's a local file
            urls = event.mimeData().urls()
            if len(urls) == 1 and urls[0].isLocalFile():
                file_path = urls[0].toLocalFile()
                _, ext = os.path.splitext(file_path)
                if not self.allowed_extensions or ext.lower() in [e.lower() for e in self.allowed_extensions]:
                    event.acceptProposedAction()
                    self.is_valid_drop = True
                    # Optional: Change visual feedback for valid drop
                    self.setStyleSheet("border: 2px dashed #2ECC71;")
                    return
        event.ignore()
        self.is_valid_drop = False
        self.setStyleSheet("") # Reset style if invalid

    def dragLeaveEvent(self, event: QDragEnterEvent):
        """
        Handles drag leave events. Resets the style.
        """
        self.setStyleSheet("")
        event.accept()

    def dropEvent(self, event: QDropEvent):
        """
        Handles drop events. Sets the file path as the line edit's text.
        """
        if self.is_valid_drop:
            file_path = event.mimeData().urls()[0].toLocalFile()
            self.setText(file_path)
            event.acceptProposedAction()
        else:
            event.ignore()
        self.setStyleSheet("") # Reset style after drop
