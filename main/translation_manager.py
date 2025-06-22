from PyQt6.QtCore import QTranslator, QLocale, QLibraryInfo
from PyQt6.QtWidgets import QApplication
import os

class TranslationManager:
    """
    Manages loading and applying Qt translations (.qm files).
    This class is prepared for future integration with Qt Linguist.
    """
    def __init__(self):
        self.translator_app = QTranslator() # For application-specific translations
        self.translator_qt = QTranslator()  # For built-in Qt widget translations

    def load_translation(self, lang_code):
        """
        Loads and applies translation files for the given language code.
        Assumes .qm files are in a 'translations' subfolder.
        """
        app = QApplication.instance()
        if not app:
            print("ERROR: QApplication instance not found for translation manager.")
            return

        # Remove existing translators
        app.removeTranslator(self.translator_app)
        app.removeTranslator(self.translator_qt)

        # Load application specific translations
        translations_dir = os.path.join(os.path.dirname(__file__), '..', 'translations') # Adjust path if needed
        if os.path.exists(translations_dir):
            app_translation_file = f"launcher_{lang_code}.qm"
            if self.translator_app.load(app_translation_file, translations_dir):
                app.installTranslator(self.translator_app)
                print(f"DEBUG: Loaded app translation: {app_translation_file}")
            else:
                print(f"WARNING: Could not load app translation file: {os.path.join(translations_dir, app_translation_file)}")
        else:
            print(f"WARNING: Translations directory not found: {translations_dir}")

        # Load built-in Qt translations (e.g., for standard dialogs)
        qt_translator_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        if self.translator_qt.load(f"qt_{lang_code}", qt_translator_path):
            app.installTranslator(self.translator_qt)
            print(f"DEBUG: Loaded Qt translation: qt_{lang_code}.qm")
        else:
            print(f"WARNING: Could not load Qt translation file for {lang_code} from {qt_translator_path}")

    @staticmethod
    def get_system_locale_language():
        """
        Attempts to get the system's preferred language code (e.g., 'en', 'fr').
        """
        locale = QLocale.system()
        # QLocale.language() returns a QLocale.Language enum, QLocale.name() returns "en_US", "fr_FR" etc.
        # We want the simple language code
        return locale.name().split('_')[0].lower()

    @staticmethod
    def tr(context, text):
        """
        Helper for translation. In a real QTranslator setup, this would use
        QCoreApplication.translate. For now, it's a placeholder.
        """
        # When QTranslator is fully integrated, this would typically become:
        # from PyQt6.QtCore import QCoreApplication
        # return QCoreApplication.translate(context, text)
        return text # For now, return original text if not using QTranslator directly
