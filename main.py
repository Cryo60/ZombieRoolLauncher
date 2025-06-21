import sys
import os
import platform
import json
import requests
import shutil # Pour la copie et la suppression de fichiers/dossiers
import zipfile # Pour décompresser les fichiers .zip

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QLabel, QTabWidget,
    QProgressBar, QMessageBox, QFileDialog, QLineEdit
)
from PyQt6.QtCore import Qt, QUrl, QThread, pyqtSignal, QVersionNumber
from PyQt6.QtGui import QDesktopServices # Pour ouvrir des liens externes

# --- GLOBAL CONFIGURATION ---
# Current version of your launcher. This version will be compared by the launcher
# with the one on GitHub to know if it needs to update itself.
__version__ = "1.0.0" 

# Direct URL to the updates.json file on your GitHub repository.
# VERY IMPORTANT: Go to your updates.json file on GitHub, click on "Raw",
# and copy the URL that appears in your browser's address bar. It must start with
# "https://raw.githubusercontent.com/...". Do not use a classic "github.com" URL.
UPDATES_JSON_URL = "https://raw.githubusercontent.com/Cryo60/ZombieRoolLauncher/refs/heads/main/updates.json"

# File name prefix of the mod as it typically appears in the mods folder (for local detection)
# Adapt this name to match the actual format of your mod files.
# Ex: if your mod is named 'ZombieRool-1.3.0.jar', you could use 'ZombieRool-'
MOD_FILE_PREFIX = "ZombieRool-" 

# Path to the local configuration file to save Minecraft paths
CONFIG_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')


# --- UTILITY FUNCTIONS FOR MINECRAFT PATHS ---
def get_default_minecraft_path():
    """
    Tries to find the default .minecraft folder path based on the operating system.
    This function is now mainly used to suggest a starting path
    when the user needs to manually select the folder.
    """
    system = platform.system()
    if system == "Windows":
        appdata = os.getenv('APPDATA')
        if appdata:
            return os.path.join(appdata, '.minecraft')
    elif system == "Darwin":  # macOS
        return os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'minecraft')
    elif system == "Linux":
        return os.path.join(os.path.expanduser('~'), '.minecraft')
    return None

def get_minecraft_sub_paths(mc_path):
    """
    Checks and returns the absolute paths of the mods, saves, and resourcepacks folders
    based on the provided .minecraft folder path.
    Returns None if the base path is invalid or if the subfolders do not exist.
    This function is key to validating the path chosen by the user, even if it is
    a custom instance (like those from CurseForge).
    """
    if mc_path and os.path.isdir(mc_path):
        paths = {
            'mods': os.path.join(mc_path, 'mods'),
            'saves': os.path.join(mc_path, 'saves'),
            'resourcepacks': os.path.join(mc_path, 'resourcepacks')
        }
        # Checks if all necessary subfolders exist.
        # We don't create them here, but we ensure that the base folder exists.
        # Subfolder creation will be handled during installation if necessary.
        # Added `exist_ok=True` to handle cases where folders already exist.
        try:
            os.makedirs(paths['mods'], exist_ok=True)
            os.makedirs(paths['saves'], exist_ok=True)
            os.makedirs(paths['resourcepacks'], exist_ok=True)
            return paths
        except OSError as e:
            print(f"Error creating/checking Minecraft subfolders: {e}")
            return None
    return None

# --- UTILITY FUNCTIONS FOR LOCAL CONFIGURATION ---
def load_config():
    """Loads configuration from a local JSON file."""
    if os.path.exists(CONFIG_FILE_PATH):
        try:
            with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading configuration file: {e}")
    return {}

def save_config(config_data):
    """Saves configuration to a local JSON file."""
    try:
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=4)
    except IOError as e:
        print(f"Error saving configuration file: {e}")

# --- THREAD FOR NON-BLOCKING NETWORK OPERATIONS ---
# It is crucial to perform network requests (downloading updates.json)
# in a separate thread so as not to block the user interface (UI).
# If the UI freezes, the application appears "stuck" and no longer responds.
class UpdateCheckerThread(QThread):
    # Signal emitted when update data is ready (success)
    update_data_ready = pyqtSignal()
    # Signal emitted in case of an error during the request
    error_occurred = pyqtSignal(str)
    
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.update_data = None # Will store JSON data after download

    def run(self):
        """
        Method executed when the thread is started.
        It downloads the JSON file from the URL.
        """
        try:
            response = requests.get(self.url, timeout=10) # Timeout to prevent too long a block
            response.raise_for_status() # Raises an exception for HTTP error codes (4xx or 5xx)
            self.update_data = response.json() # Parses the JSON response
            self.update_data_ready.emit() # Emits the success signal
        except requests.exceptions.RequestException as e:
            # Handles connection, DNS, timeout errors, etc.
            self.error_occurred.emit(f"Connection error while fetching updates: {e}")
        except json.JSONDecodeError as e:
            # Handles errors if the downloaded content is not valid JSON
            self.error_occurred.emit(f"Error reading updates.json file (invalid JSON): {e}. Check the file format on GitHub.")
        except Exception as e:
            # Handles any other unexpected exception
            self.error_occurred.emit(f"An unexpected error occurred: {e}")

# --- THREAD FOR FILE DOWNLOAD WITH PROGRESS ---
class FileDownloaderThread(QThread):
    download_progress = pyqtSignal(int) # Signal for progress (0-100)
    download_finished = pyqtSignal(str) # Signal when download is finished (file path)
    download_error = pyqtSignal(str) # Signal in case of download error

    def __init__(self, url, destination_path):
        super().__init__()
        self.url = url
        self.destination_path = destination_path

    def run(self):
        try:
            response = requests.get(self.url, stream=True, timeout=30) # stream=True to download in chunks
            response.raise_for_status() # Raises an exception for HTTP error codes

            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0

            # Ensure that the destination directory exists
            os.makedirs(os.path.dirname(self.destination_path), exist_ok=True)

            with open(self.destination_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192): # Downloads in 8KB blocks
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        if total_size > 0:
                            progress = int((downloaded_size / total_size) * 100)
                            self.download_progress.emit(progress)
            
            self.download_finished.emit(self.destination_path)
        except requests.exceptions.RequestException as e:
            self.download_error.emit(f"Download error: {e}")
        except Exception as e:
            self.download_error.emit(f"Unexpected error during download: {e}")

# --- MAIN LAUNCHER CLASS ---
class ZombieRoolLauncher(QMainWindow):
    def __init__(self):
        super().__init__()
        # Main window initialization
        self.setWindowTitle(f"ZombieRool Launcher - v{__version__}")
        self.setGeometry(100, 100, 800, 600) # x, y, width, height

        # Main container to organize elements
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        # Attributes to store Minecraft paths and update data
        self.minecraft_paths = None 
        self.remote_updates_data = None 

        # --- Launcher Header ---
        self.header_label = QLabel("Welcome to the ZombieRool Launcher!", self)
        self.header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_label.setStyleSheet("font-size: 24px; font-weight: bold; padding: 20px; color: #E74C3C;") # Basic style for a "gamer" look
        self.main_layout.addWidget(self.header_label)

        # --- Tabs ---
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabWidget::pane { border: 1px solid #CCC; background: #EEE; }"
                                "QTabBar::tab { background: #DDD; border: 1px solid #CCC; border-bottom-color: #EEE; "
                                "border-top-left-radius: 4px; border-top-right-radius: 4px; padding: 8px 15px; }"
                                "QTabBar::tab:selected { background: #FFF; border-color: #999; border-bottom-color: #FFF; }"
                                "QTabWidget::tab-bar { alignment: center; }") # Style for tabs
        self.main_layout.addWidget(self.tabs)

        # "Update" tab
        self.update_tab = QWidget()
        self.tabs.addTab(self.update_tab, "Update")
        self.setup_update_tab()

        # "Map Download" tab
        self.download_tab = QWidget()
        self.tabs.addTab(self.download_tab, "Map Download")
        self.setup_download_tab()
        
        # "Settings" tab
        self.settings_tab = QWidget()
        self.tabs.addTab(self.settings_tab, "Settings")
        self.setup_settings_tab()

        # --- Footer (Status Bar / Launcher Version) ---
        self.status_bar = QLabel(f"Launcher Version: {__version__}", self)
        self.status_bar.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status_bar.setStyleSheet("font-size: 10px; padding: 5px; color: #555;")
        self.main_layout.addWidget(self.status_bar)

        # --- Initialization and startup checks ---
        self.load_saved_minecraft_path() # Attempts to load the saved Minecraft path
        self.check_for_updates() # Starts checking for updates from GitHub

    # --- Tab Configuration ---
    def setup_update_tab(self):
        """Configures the 'Update' tab interface."""
        layout = QVBoxLayout(self.update_tab)
        layout.setContentsMargins(20, 20, 20, 20) # Inner margins

        # Section for the Mod
        mod_section_label = QLabel("ZombieRool Mod Updates", self)
        mod_section_label.setStyleSheet("font-size: 18px; font-weight: bold; margin-top: 10px; color: #2C3E50;")
        layout.addWidget(mod_section_label)

        self.mod_status_label = QLabel("Mod Status: Checking...", self)
        layout.addWidget(self.mod_status_label)

        self.mod_progress_bar = QProgressBar(self)
        self.mod_progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mod_progress_bar.hide() # Hidden by default
        layout.addWidget(self.mod_progress_bar)

        self.update_mod_button = QPushButton("Update Mod", self)
        self.update_mod_button.clicked.connect(self.update_mod) 
        self.update_mod_button.setEnabled(False) # Disabled by default, enabled if update is available
        layout.addWidget(self.update_mod_button)
        layout.addSpacing(20) # Spacing

        # Section for the Launcher (for self-update)
        launcher_section_label = QLabel("Launcher Updates", self)
        launcher_section_label.setStyleSheet("font-size: 18px; font-weight: bold; margin-top: 20px; color: #2C3E50;")
        layout.addWidget(launcher_section_label)

        self.launcher_status_label = QLabel("Launcher Status: Checking...", self)
        layout.addWidget(self.launcher_status_label)
        
        self.launcher_progress_bar = QProgressBar(self)
        self.launcher_progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.launcher_progress_bar.hide() # Hidden by default
        layout.addWidget(self.launcher_progress_bar)

        self.update_launcher_button = QPushButton("Update Launcher", self)
        self.update_launcher_button.clicked.connect(self.update_launcher)
        self.update_launcher_button.setEnabled(False) # Disabled by default
        layout.addWidget(self.update_launcher_button)

        layout.addStretch() # Pushes elements to the top

    def setup_download_tab(self):
        """Configures the 'Map Download' tab interface."""
        layout = QVBoxLayout(self.download_tab)
        layout.setContentsMargins(20, 20, 20, 20)

        maps_label = QLabel("Download and Install Maps", self)
        maps_label.setStyleSheet("font-size: 18px; font-weight: bold; margin-top: 10px; color: #2C3E50;")
        layout.addWidget(maps_label)

        self.maps_container_layout = QVBoxLayout() # Container to dynamically add maps
        layout.addLayout(self.maps_container_layout)

        layout.addStretch()

    def setup_settings_tab(self):
        """Configures the 'Settings' tab interface."""
        layout = QVBoxLayout(self.settings_tab)
        layout.setContentsMargins(20, 20, 20, 20)

        settings_label = QLabel("Minecraft Settings and Paths", self)
        settings_label.setStyleSheet("font-size: 18px; font-weight: bold; margin-top: 10px; color: #2C3E50;")
        layout.addWidget(settings_label)

        # Section for the Minecraft instance path
        mc_path_h_layout = QHBoxLayout()
        mc_path_label = QLabel(" .minecraft or Instance Folder :")
        mc_path_h_layout.addWidget(mc_path_label)
        
        self.mc_path_input = QLineEdit()
        self.mc_path_input.setPlaceholderText("Click 'Browse...' to choose your Minecraft folder")
        self.mc_path_input.setReadOnly(True) # Read-only, user uses the button
        mc_path_h_layout.addWidget(self.mc_path_input)
        
        self.browse_mc_path_button = QPushButton("Browse...")
        self.browse_mc_path_button.clicked.connect(self.browse_minecraft_path)
        mc_path_h_layout.addWidget(self.browse_mc_path_button)
        layout.addLayout(mc_path_h_layout)

        self.mods_path_label = QLabel("Mods Folder: Not Detected")
        self.saves_path_label = QLabel("Saves Folder: Not Detected")
        self.resourcepacks_path_label = QLabel("Resourcepacks Folder: Not Detected")
        
        layout.addWidget(self.mods_path_label)
        layout.addWidget(self.saves_path_label)
        layout.addWidget(self.resourcepacks_path_label)

        layout.addStretch()

    # --- Minecraft Path Logic Functions ---
    def load_saved_minecraft_path(self):
        """
        Attempts to load the saved Minecraft path from the configuration file.
        If a path is found and valid, sets it. Otherwise, prompts the user to configure.
        """
        config = load_config()
        saved_path = config.get('minecraft_path')
        if saved_path:
            self.set_minecraft_path(saved_path, show_message=False) # Do not show message on startup
            if not self.minecraft_paths: # If the loaded path is no longer valid
                 QMessageBox.warning(self, "Invalid Minecraft Path",
                                    "The saved Minecraft path is no longer valid or does not exist. Please reconfigure it in the 'Settings' tab.")
                 self.mc_path_input.setText("Path Not Configured")
                 self.mods_path_label.setText("Mods Folder: Not Configured")
                 self.saves_path_label.setText("Saves Folder: Not Configured")
                 self.resourcepacks_path_label.setText("Resourcepacks Folder: Not Configured")
        else:
            # If no path is saved, display the initial invitation message
            QMessageBox.information(self, "Minecraft Path Configuration",
                                    "Welcome! Please select your Minecraft instance folder (containing 'mods', 'saves', 'resourcepacks' folders) in the 'Settings' tab by clicking 'Browse...'.")
            self.mc_path_input.setText("Path Not Configured")
            self.mods_path_label.setText("Mods Folder: Not Configured")
            self.saves_path_label.setText("Saves Folder: Not Configured")
            self.resourcepacks_path_label.setText("Resourcepacks Folder: Not Configured")

    def browse_minecraft_path(self):
        """
        Opens a dialog box to allow the user to manually select
        the folder of their Minecraft instance.
        """
        # Suggest the default path as a starting point for selection (if detected)
        initial_path = self.mc_path_input.text() if self.mc_path_input.text() and os.path.exists(self.mc_path_input.text()) else (get_default_minecraft_path() if get_default_minecraft_path() else os.path.expanduser('~'))
        
        selected_path = QFileDialog.getExistingDirectory(self, "Select Minecraft Instance Folder", initial_path)
        if selected_path:
            self.set_minecraft_path(selected_path, show_message=True)
        else:
            QMessageBox.warning(self, "Selection Canceled", "Minecraft folder selection canceled.")

    def set_minecraft_path(self, path, show_message=True):
        """
        Updates Minecraft paths in the UI and stores validated paths.
        Saves the path to the local configuration.
        """
        self.mc_path_input.setText(path)
        sub_paths = get_minecraft_sub_paths(path)
        if sub_paths:
            self.minecraft_paths = sub_paths
            self.mods_path_label.setText(f"Mods Folder: {self.minecraft_paths['mods']}")
            self.saves_path_label.setText(f"Saves Folder: {self.minecraft_paths['saves']}")
            self.resourcepacks_path_label.setText(f"Resourcepacks Folder: {self.minecraft_paths['resourcepacks']}")
            
            # Saves the validated path to the configuration file
            config = load_config()
            config['minecraft_path'] = path
            save_config(config)

            if show_message:
                QMessageBox.information(self, "Minecraft Path Configured", 
                                        f"The Minecraft folder has been manually configured: {path}")
        else:
            self.minecraft_paths = None
            self.mods_path_label.setText("Mods Folder: Not Found or Invalid Path")
            self.saves_path_label.setText("Saves Folder: Not Found or Invalid Path")
            self.resourcepacks_path_label.setText("Resourcepacks Folder: Not Found or Invalid Path")
            
            # Removes the invalid path from the config if it existed
            config = load_config()
            if 'minecraft_path' in config:
                del config['minecraft_path']
                save_config(config)

            if show_message:
                QMessageBox.warning(self, "Invalid Path", 
                                    "The selected path does not appear to be a valid Minecraft instance (mods, saves, resourcepacks folders not found).")

    # --- Update Check and Processing Functions ---
    def check_for_updates(self):
        """
        Initiates the check for all updates by downloading updates.json
        from GitHub in a separate thread.
        """
        self.header_label.setText("Checking for updates...")
        self.mod_status_label.setText("Mod Status: Checking...")
        self.launcher_status_label.setText("Launcher Status: Checking...")
        
        # Disable buttons during check to prevent multiple clicks
        self.update_mod_button.setEnabled(False)
        self.update_launcher_button.setEnabled(False)

        # Hide progress bars at the start of the check
        self.mod_progress_bar.hide()
        self.launcher_progress_bar.hide()

        # Create and start the update checker thread
        self.update_checker_thread = UpdateCheckerThread(UPDATES_JSON_URL)
        # Connect thread signals to slots (functions) in the main class
        self.update_checker_thread.update_data_ready.connect(self.process_remote_updates)
        self.update_checker_thread.error_occurred.connect(self.handle_update_error)
        self.update_checker_thread.start() # Start thread execution

    def process_remote_updates(self):
        """
        Retrieves JSON data from the thread and initiates version comparison logic
        for the launcher, mod, and maps.
        """
        self.remote_updates_data = self.update_checker_thread.update_data
        
        if self.remote_updates_data:
            self.header_label.setText("ZombieRool Launcher - Updates Checked")
            QMessageBox.information(self, "Updates", "Update information successfully retrieved from GitHub!")
            
            # Call specific update logic functions
            self._check_launcher_update_logic()
            self._check_mod_update_logic()
            self._load_maps_for_download_logic()
        else:
            # This case should normally not be reached if error_occurred is well handled,
            # but it is a safety measure.
            QMessageBox.critical(self, "Error", "Could not retrieve update information. Check the updates.json file URL or JSON structure.")
            self.header_label.setText("ZombieRool Launcher - Update Error")
            # Re-enable buttons even if there's an error if you want to allow another attempt
            self.update_mod_button.setEnabled(True)
            self.update_launcher_button.setEnabled(True)


    def handle_update_error(self, message):
        """
        Handles displaying errors that occurred while retrieving updates.json.
        """
        QMessageBox.critical(self, "Update Error", f"An error occurred while checking for updates: {message}")
        self.header_label.setText("ZombieRool Launcher - Update Error")
        # Re-enable buttons in case of an error so the user can retry manually
        self.update_mod_button.setEnabled(True)
        self.update_launcher_button.setEnabled(True)
    
    # --- Launcher Update Logic (to be developed later) ---
    def _check_launcher_update_logic(self):
        """
        Compares the local launcher version with the remote version in remote_updates_data.
        """
        if not self.remote_updates_data or "launcher" not in self.remote_updates_data:
            self.launcher_status_label.setText("Launcher Status: Remote info not found.")
            return

        remote_version_str = self.remote_updates_data["launcher"]["latest_version"]
        local_version_str = __version__ # Launcher version defined globally
        
        try:
            # Use QVersionNumber for robust version comparison (e.g., 1.0.0 < 1.0.1)
            remote_version = QVersionNumber.fromString(remote_version_str)
            local_version = QVersionNumber.fromString(local_version_str)

            if remote_version > local_version:
                self.launcher_status_label.setText(f"Launcher Status: New version {remote_version_str} available! (Current: {local_version_str})")
                self.update_launcher_button.setEnabled(True)
            else:
                self.launcher_status_label.setText(f"Launcher Status: Up to date (v{local_version_str})")
                self.update_launcher_button.setEnabled(False) # No update needed
        except Exception as e:
            self.launcher_status_label.setText(f"Launcher Status: Version error ({e})")
            print(f"Launcher version comparison error: {e}")

    def update_launcher(self):
        """
        Function called when the "Update Launcher" button is clicked.
        Starts downloading the new launcher.
        """
        if not self.remote_updates_data or "launcher" not in self.remote_updates_data:
            QMessageBox.warning(self, "Launcher Update", "Launcher update information not found.")
            return

        launcher_info = self.remote_updates_data["launcher"]
        download_url = launcher_info.get("download_url")
        if not download_url:
            QMessageBox.warning(self, "Launcher Update", "Launcher download URL not found in update data.")
            return
        
        # Determine temporary path for the new launcher
        temp_dir = os.path.join(os.path.dirname(sys.executable), "temp_launcher_update")
        os.makedirs(temp_dir, exist_ok=True)
        # Use the file name from the URL for the temporary file
        file_name = os.path.basename(QUrl(download_url).path())
        temp_destination_path = os.path.join(temp_dir, file_name)

        QMessageBox.information(self, "Launcher Update", f"Downloading new launcher version ({launcher_info['latest_version']})...")
        self.launcher_progress_bar.setValue(0)
        self.launcher_progress_bar.show()
        self.update_launcher_button.setEnabled(False)
        self.launcher_status_label.setText("Downloading...")

        self.launcher_downloader = FileDownloaderThread(download_url, temp_destination_path)
        self.launcher_downloader.download_progress.connect(self.launcher_progress_bar.setValue)
        self.launcher_downloader.download_finished.connect(self._install_new_launcher)
        self.launcher_downloader.download_error.connect(self._handle_launcher_download_error)
        self.launcher_downloader.start()

    def _install_new_launcher(self, temp_launcher_path):
        """
        After download, launches the new launcher and closes the old one.
        """
        self.launcher_progress_bar.hide()
        self.launcher_status_label.setText("Download complete. Launching update...")
        QMessageBox.information(self, "Launcher Update", "New version downloaded. The launcher will restart.")
        
        try:
            # Build command to launch the new launcher
            # Use 'start' on Windows to launch the process and free the parent
            if platform.system() == "Windows":
                command = f'start "" "{temp_launcher_path}"'
            else: # For macOS/Linux, may require execute permissions
                os.chmod(temp_launcher_path, 0o755) # Make the file executable
                command = f'"{temp_launcher_path}"'
            
            os.system(command) # Execute the new launcher
            QApplication.quit() # Close the current application
        except Exception as e:
            QMessageBox.critical(self, "Launcher Update Error", f"Could not launch new launcher version: {e}. Please download the update manually.")
            self.launcher_status_label.setText(f"Error launching update: {e}")
            self.update_launcher_button.setEnabled(True) # Re-enable button on failure

    def _handle_launcher_download_error(self, message):
        """Handles launcher download errors."""
        self.launcher_progress_bar.hide()
        QMessageBox.critical(self, "Launcher Download Error", message)
        self.launcher_status_label.setText(f"Download failed: {message}")
        self.update_launcher_button.setEnabled(True)

    # --- Mod Update Logic (implementation) ---
    def _check_mod_update_logic(self):
        """
        Compares the local mod version with the remote version.
        """
        if not self.remote_updates_data or "mod" not in self.remote_updates_data:
            self.mod_status_label.setText("Mod Status: Remote info not found.")
            return

        remote_mod_info = self.remote_updates_data["mod"]
        remote_mod_version_str = remote_mod_info["latest_version"]
        
        local_mod_version_str = self._get_local_mod_version() # Get local version
        
        try:
            remote_version = QVersionNumber.fromString(remote_mod_version_str)
            local_version = QVersionNumber.fromString(local_mod_version_str)

            if remote_version > local_version:
                self.mod_status_label.setText(f"Mod Status: New version {remote_mod_version_str} available! (Current: {local_mod_version_str})")
                self.update_mod_button.setEnabled(True)
            else:
                self.mod_status_label.setText(f"Mod Status: Up to date (v{local_version_str})")
                self.update_mod_button.setEnabled(False)
        except Exception as e:
            self.mod_status_label.setText(f"Mod Status: Version error ({e})")
            print(f"Mod version comparison error: {e}")

    def _get_local_mod_version(self):
        """
        Attempts to find the mod version locally in the 'mods' folder.
        This is a simplified implementation that assumes a file name prefix.
        A better approach would be to read a version file if the mod contains one.
        """
        if not self.minecraft_paths or not self.minecraft_paths['mods'] or \
           not os.path.isdir(self.minecraft_paths['mods']):
            return "0.0.0" # Default version if path is invalid

        mod_dir = self.minecraft_paths['mods']
        for filename in os.listdir(mod_dir):
            if filename.startswith(MOD_FILE_PREFIX) and filename.endswith(".jar"):
                # Tries to extract the version from the file name, e.g., "ZombieRool-1.3.0.jar" -> "1.3.0"
                try:
                    version_part = filename[len(MOD_FILE_PREFIX):-len(".jar")]
                    # Remove anything that is not a digit or a dot
                    clean_version = "".join(c for c in version_part if c.isdigit() or c == '.')
                    return clean_version
                except:
                    continue # Ignore if version extraction fails
        return "0.0.0" # Mod not found or version not extractable

    def update_mod(self):
        """
        Function called when the "Update Mod" button is clicked.
        Downloads and installs the mod.
        """
        if not self.minecraft_paths or not self.minecraft_paths['mods']:
            QMessageBox.warning(self, "Installation Error", "The Minecraft 'mods' folder is not configured. Please define it in 'Settings'.")
            return
        
        mod_info = self.remote_updates_data["mod"]
        download_url = mod_info.get("download_url")
        if not download_url:
            QMessageBox.warning(self, "Mod Update", "Mod download URL not found in update data.")
            return

        # Path where the temporary file will be downloaded
        temp_download_dir = os.path.join(os.getcwd(), "temp_downloads")
        os.makedirs(temp_download_dir, exist_ok=True)
        mod_filename = os.path.basename(QUrl(download_url).path())
        temp_mod_path = os.path.join(temp_download_dir, mod_filename)

        QMessageBox.information(self, "Mod Update", f"Downloading mod ({mod_info['latest_version']})...")
        self.mod_progress_bar.setValue(0)
        self.mod_progress_bar.show()
        self.update_mod_button.setEnabled(False)
        self.mod_status_label.setText("Downloading mod...")

        self.mod_downloader = FileDownloaderThread(download_url, temp_mod_path)
        self.mod_downloader.download_progress.connect(self.mod_progress_bar.setValue)
        self.mod_downloader.download_finished.connect(self._install_mod_from_temp)
        self.mod_downloader.download_error.connect(self._handle_mod_download_error)
        self.mod_downloader.start()

    def _install_mod_from_temp(self, temp_mod_path):
        """
        Moves the downloaded mod from the temporary folder to the Minecraft mods folder.
        Deletes old mod versions.
        """
        self.mod_progress_bar.hide()
        self.mod_status_label.setText("Installing mod...")
        
        try:
            mod_dir = self.minecraft_paths['mods']
            # Delete old mod versions
            for filename in os.listdir(mod_dir):
                if filename.startswith(MOD_FILE_PREFIX) and filename.endswith(".jar"):
                    os.remove(os.path.join(mod_dir, filename))
                    print(f"Old mod version deleted: {filename}")

            # Move the new downloaded mod
            shutil.move(temp_mod_path, os.path.join(mod_dir, os.path.basename(temp_mod_path)))
            QMessageBox.information(self, "Mod Update", "Mod updated and installed successfully!")
            self.mod_status_label.setText(f"Mod Status: Up to date (v{self.remote_updates_data['mod']['latest_version']})")
            self.update_mod_button.setEnabled(False) # Disable after update
        except Exception as e:
            QMessageBox.critical(self, "Mod Installation Error", f"An error occurred during mod installation: {e}")
            self.mod_status_label.setText(f"Installation failed: {e}")
            self.update_mod_button.setEnabled(True) # Re-enable on failure
        finally:
            # Clean up temporary file
            if os.path.exists(temp_mod_path):
                os.remove(temp_mod_path)

    def _handle_mod_download_error(self, message):
        """Handles mod download errors."""
        self.mod_progress_bar.hide()
        QMessageBox.critical(self, "Mod Download Error", message)
        self.mod_status_label.setText(f"Download failed: {message}")
        self.update_mod_button.setEnabled(True)

    # --- Map Loading Logic for Download (implementation) ---
    def _load_maps_for_download_logic(self):
        """
        Loads and displays maps available for download
        from remote_updates_data.
        """
        # Clear existing map container before reloading
        while self.maps_container_layout.count():
            child = self.maps_container_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not self.remote_updates_data or "maps" not in self.remote_updates_data:
            map_status = QLabel("No map information available.")
            self.maps_container_layout.addWidget(map_status)
            return

        for map_info in self.remote_updates_data["maps"]:
            self.add_map_to_download_list(map_info)

    def add_map_to_download_list(self, map_info):
        """
        Adds a visual element for each map in the download tab.
        """
        map_widget = QWidget()
        map_widget.setStyleSheet("background-color: #F8F8F8; border: 1px solid #DDD; border-radius: 5px; padding: 10px; margin-bottom: 5px;")
        map_layout = QHBoxLayout(map_widget)
        
        map_details = QVBoxLayout()
        map_details.addWidget(QLabel(f"<b>{map_info['name']}</b> <span style='color:#555;'> (v{map_info['latest_version']})</span>"))
        map_details.addWidget(QLabel(map_info.get('description', 'No description available.'))) # Add description
        map_layout.addLayout(map_details)

        # Progress bar specific to each map
        map_progress_bar = QProgressBar(self)
        map_progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        map_progress_bar.hide()
        map_details.addWidget(map_progress_bar) # Add progress bar below details

        download_button = QPushButton(f"Install Map")
        download_button.setFixedSize(120, 30)
        download_button.setStyleSheet("background-color: #2ECC71; color: white; border-radius: 5px; padding: 5px;")
        # Pass the progress bar in addition to map information
        download_button.clicked.connect(lambda checked, info=map_info, pb=map_progress_bar: self.install_map(info, pb)) 
        map_layout.addWidget(download_button)

        self.maps_container_layout.addWidget(map_widget)

    def install_map(self, map_info, progress_bar):
        """
        Function called when the "Install Map" button is clicked.
        Downloads and installs the map and its associated resource pack.
        """
        if not self.minecraft_paths or not self.minecraft_paths['saves'] or not self.minecraft_paths['resourcepacks']:
            QMessageBox.warning(self, "Installation Error", "Minecraft 'saves' or 'resourcepacks' folders are not configured. Please define them in 'Settings'.")
            return
            
        map_download_url = map_info.get("download_url")
        rp_download_url = map_info.get("resourcepack_url")

        if not map_download_url:
            QMessageBox.warning(self, "Map Installation", "Map download URL not found.")
            return

        # Determine temporary and destination paths
        temp_download_dir = os.path.join(os.getcwd(), "temp_downloads")
        os.makedirs(temp_download_dir, exist_ok=True)
        
        map_filename = os.path.basename(QUrl(map_download_url).path())
        temp_map_path = os.path.join(temp_download_dir, map_filename)

        rp_filename = os.path.basename(QUrl(rp_download_url).path()) if rp_download_url else None
        temp_rp_path = os.path.join(temp_download_dir, rp_filename) if rp_filename else None

        QMessageBox.information(self, f"Installing {map_info['name']}",
                                f"Downloading map '{map_info['name']}'...")
        
        progress_bar.setValue(0)
        progress_bar.show()

        # Start map download
        self.map_downloader = FileDownloaderThread(map_download_url, temp_map_path)
        self.map_downloader.download_progress.connect(progress_bar.setValue)
        # CORRECTION: Pass rp_filename explicitly to _install_map_files
        self.map_downloader.download_finished.connect(
            lambda path=temp_map_path, rp_url=rp_download_url, rp_path=temp_rp_path, map_name=map_info['name'], pb=progress_bar, rp_file_name_val=rp_filename: 
            self._install_map_files(path, rp_url, rp_path, map_name, pb, rp_file_name_val)
        )
        self.map_downloader.download_error.connect(lambda msg: self._handle_map_download_error(msg, progress_bar))
        self.map_downloader.start()

    # CORRECTION: Added rp_filename_val as a parameter
    def _install_map_files(self, temp_map_path, rp_download_url, temp_rp_path, map_name, progress_bar, rp_filename_val):
        """
        Decompresses the map, downloads and decompresses the resource pack if present.
        """
        progress_bar.hide()
        QMessageBox.information(self, "Map Installation", f"Map '{map_name}' downloaded. Installing...")

        try:
            # 1. Decompress the map
            saves_dir = self.minecraft_paths['saves']
            # The map folder is usually the first folder inside the zip
            with zipfile.ZipFile(temp_map_path, 'r') as zip_ref:
                # Extract only the first root directory or all files
                # We assume the zip contains a single root map folder
                map_folder_name = os.path.commonprefix(zip_ref.namelist())
                # CORRECTION: os.grav_path does not exist, use os.path.join
                zip_ref.extractall(saves_dir)
                extracted_path = os.path.join(saves_dir, map_folder_name.split('/')[0]) # Get the name of the root folder
                if os.path.exists(extracted_path) and os.path.basename(extracted_path) != map_name:
                    # Do not rename if there is a conflict, just inform
                    if not os.path.exists(os.path.join(saves_dir, map_name)):
                        os.rename(extracted_path, os.path.join(saves_dir, map_name))
                        print(f"Map folder renamed from {extracted_path} to {os.path.join(saves_dir, map_name)}")
                    else:
                        print(f"The folder {map_name} already exists, not renaming {extracted_path}")


            # 2. Download and install the resource pack if necessary
            if rp_download_url and temp_rp_path:
                QMessageBox.information(self, "Resource Pack Installation", "Downloading associated resource pack...")
                progress_bar.setValue(0)
                progress_bar.show()

                self.rp_downloader = FileDownloaderThread(rp_download_url, temp_rp_path)
                self.rp_downloader.download_progress.connect(progress_bar.setValue)
                self.rp_downloader.download_finished.connect(
                    # CORRECTION: Use rp_filename_val here
                    lambda path=temp_rp_path, rp_name_for_install=rp_filename_val: self._install_resource_pack_from_temp(path, rp_name_for_install, progress_bar)
                )
                self.rp_downloader.download_error.connect(lambda msg: self._handle_map_download_error(msg, progress_bar, is_rp=True))
                self.rp_downloader.start()
            else:
                QMessageBox.information(self, "Installation Complete", f"Map '{map_name}' installed successfully! (No associated Resource Pack)")
                # Mod update check and status update is moved
                # after full RP installation, or here if no RP.
                self.mod_status_label.setText("Mod Status: Checking...") # Update after installation
                self._check_mod_update_logic() # To force update check after install
                progress_bar.hide() # Ensure bar is hidden
        except zipfile.BadZipFile:
            QMessageBox.critical(self, "Decompression Error", "The map ZIP file is corrupted or invalid. The 'zipfile' module only supports ZIP format (not RAR).")
        except Exception as e:
            QMessageBox.critical(self, "Map Installation Error", f"An error occurred during map installation: {e}")
        finally:
            # Clean up temporary map file
            if os.path.exists(temp_map_path):
                os.remove(temp_map_path)
            # Clean up temporary folder if empty
            if os.path.exists(os.path.dirname(temp_map_path)) and not os.listdir(os.path.dirname(temp_map_path)):
                shutil.rmtree(os.path.dirname(temp_map_path))


    def _install_resource_pack_from_temp(self, temp_rp_path, rp_name, progress_bar):
        """
        Moves the downloaded resource pack to the Minecraft resourcepacks folder.
        """
        progress_bar.hide()
        QMessageBox.information(self, "Resource Pack Installation", "Resource pack downloaded. Installing...")
        try:
            rp_dir = self.minecraft_paths['resourcepacks']
            # Resource packs are often just copied as .zip or decompressed if they contain a single folder
            # We will copy the zip file directly
            destination_rp_path = os.path.join(rp_dir, rp_name)
            
            # If an old RP with the same name exists, delete it
            if os.path.exists(destination_rp_path):
                os.remove(destination_rp_path)

            shutil.move(temp_rp_path, destination_rp_path)
            QMessageBox.information(self, "Installation Complete", "Resource Pack installed successfully! Map and Resource Pack are ready.")
        except Exception as e:
            QMessageBox.critical(self, "RP Installation Error", f"An error occurred during resource pack installation: {e}")
        finally:
            # Clean up temporary RP file
            if os.path.exists(temp_rp_path):
                os.remove(temp_rp_path)
            # Clean up temporary folder if empty after processing both files
            if os.path.exists(os.path.dirname(temp_rp_path)) and not os.listdir(os.path.dirname(temp_rp_path)):
                shutil.rmtree(os.path.dirname(temp_rp_path))
        
        self.mod_status_label.setText("Mod Status: Checking...") # Update after installation
        self._check_mod_update_logic() # To force update check after install


    def _handle_map_download_error(self, message, progress_bar, is_rp=False):
        """Handles map or resource pack download errors."""
        progress_bar.hide()
        component_name = "Resource Pack" if is_rp else "Map"
        QMessageBox.critical(self, f"{component_name} Download Error", message)
        # Here, you could re-enable the specific map installation button if you had a reference

# --- APPLICATION START ---
if __name__ == "__main__":
    # Create the PyQt application instance
    app = QApplication(sys.argv)
    
    # Create and display the main launcher window
    launcher = ZombieRoolLauncher()
    launcher.show()
    
    # Start the application event loop. The program remains active as long as this loop runs.
    sys.exit(app.exec())

