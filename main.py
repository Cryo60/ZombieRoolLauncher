import sys
import os
import platform
import json
import requests
import shutil # For copying and deleting files/folders
import zipfile # For decompressing .zip files
import subprocess # For launching external processes (needed for updates)
import time # For pausing in the update script

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QLabel, QTabWidget,
    QProgressBar, QMessageBox, QFileDialog, QLineEdit, QTextEdit, QCheckBox, QScrollArea, QComboBox # Import QComboBox
)
from PyQt6.QtCore import Qt, QUrl, QThread, pyqtSignal, QVersionNumber
from PyQt6.QtGui import QDesktopServices # For opening external links

# Import PyGithub
try:
    from github import Github, GithubException
except ImportError:
    QMessageBox.critical(None, "Error", "PyGithub library not found. Please install it: pip install PyGithub")
    sys.exit(1)


# --- GLOBAL CONFIGURATION ---
# Current version of your launcher. This version will be compared by the launcher
# with the one on GitHub to know if it needs to update itself.
__version__ = "4.0.1" # Updated version

# Direct URL to the updates.json file on your GitHub repository.
# IMPORTANT: Go to your updates.json file on GitHub, click on "Raw",
# and copy the URL that appears in your browser's address bar. It must start with
# "https://raw.githubusercontent.com/...". Do not use a classic "github.com" URL.
UPDATES_JSON_URL = "https://raw.githubusercontent.com/Cryo60/ZombieRoolLauncher/refs/heads/main/updates.json"

# File name prefix of the mod as it typically appears in the mods folder (for local detection)
# Adapt this name to match the actual format of your mod files.
# Ex: if your mod is named 'ZombieRool-1.3.0.jar', you could use 'ZombieRool-'
MOD_FILE_PREFIX = "ZombieRool-" 

# GitHub Repository Information for Map Uploads (YOU MUST CONFIGURE THESE!)
# Replace with your GitHub organization/user name and repository name
# Example: "Cryo60" and "ZombieRoolLauncher"
GITHUB_REPO_OWNER = "Cryo60"  # Your GitHub username
GITHUB_REPO_NAME = "ZombieRoolLauncher" # The name of your repository

# Path to the local configuration file to save Minecraft paths
# MODIFICATION: Use platform-specific application data directory for persistent config
def get_config_file_path():
    if platform.system() == "Windows":
        # Use APPDATA for roaming profiles (config that follows user)
        # or LOCALAPPDATA for local machine config
        app_data_dir = os.path.join(os.getenv('APPDATA'), 'ZombieRoolLauncher')
    elif platform.system() == "Darwin": # macOS
        app_data_dir = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'ZombieRoolLauncher')
    else: # Linux and others
        app_data_dir = os.path.join(os.path.expanduser('~'), '.config', 'ZombieRoolLauncher') # XDG Base Directory Specification
    
    os.makedirs(app_data_dir, exist_ok=True) # Ensure the directory exists
    return os.path.join(app_data_dir, 'config.json')

CONFIG_FILE_PATH = get_config_file_path()


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
    IMPORTANT: This function also *creates* the subfolders if they don't exist.
    """
    if mc_path and os.path.isdir(mc_path):
        paths = {
            'mods': os.path.join(mc_path, 'mods'),
            'saves': os.path.join(mc_path, 'saves'),
            'resourcepacks': os.path.join(mc_path, 'resourcepacks')
        }
        try:
            # Ensure all necessary subfolders exist, creating them if necessary.
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
        # Ensure the directory for the config file exists
        os.makedirs(os.path.dirname(CONFIG_FILE_PATH), exist_ok=True)
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
    
    def __init__(self, url, cache_bust=False): # Add cache_bust parameter
        super().__init__()
        self.url = url
        self.update_data = None # Will store JSON data after download
        self.cache_bust = cache_bust

    def run(self):
        """
        Method executed when the thread is started.
        It downloads the JSON file from the URL.
        """
        try:
            fetch_url = self.url
            if self.cache_bust:
                # Add a unique timestamp to the URL to bypass caching
                fetch_url = f"{self.url}?_={int(time.time() * 1000)}"
                print(f"DEBUG: Fetching updates.json with cache bust: {fetch_url}") # For debug/visibility

            response = requests.get(fetch_url, timeout=10) # Timeout to prevent too long a block
            response.raise_for_status() # Raises an exception for HTTP error codes (4xx or 5xx)
            self.update_data = response.json() # Parses the JSON response
            
            # Debugging: Print information about the received data
            print(f"DEBUG: UpdateCheckerThread received data. Maps count: {len(self.update_data.get('maps', []))}")
            if self.update_data.get('maps'):
                # Print ID and name of the first map to confirm data structure
                if len(self.update_data['maps']) > 0:
                    print(f"DEBUG: First map entry received: ID='{self.update_data['maps'][0].get('id')}', Name='{self.update_data['maps'][0].get('name')}'")
                else:
                    print(f"DEBUG: No maps found in updates.json (empty 'maps' array).")


            self.update_data_ready.emit() # Emits the success signal
        except requests.exceptions.RequestException as e:
            # Handles connection, DNS, timeout errors, etc.
            self.error_occurred.emit(f"Connection error while fetching updates: {e}")
            print(f"DEBUG: UpdateCheckerThread connection error: {e}")
        except json.JSONDecodeError as e:
            # Handles errors if the downloaded content is not valid JSON
            self.error_occurred.emit(f"Error reading updates.json file (invalid JSON): {e}. Check the file format on GitHub.")
            print(f"DEBUG: UpdateCheckerThread JSON decode error: {e}")
        except Exception as e:
            # Handles any other unexpected exception
            self.error_occurred.emit(f"An unexpected error occurred: {e}")
            print(f"DEBUG: UpdateCheckerThread unexpected error: {e}")

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

# --- THREAD FOR GITHUB UPLOAD OPERATIONS ---
class GitHubUploaderThread(QThread):
    upload_progress = pyqtSignal(str) # For status updates
    upload_finished = pyqtSignal(dict) # Contains map_info and uploaded asset URLs
    upload_error = pyqtSignal(str)

    def __init__(self, github_token, map_info, map_zip_path, rp_zip_path=None):
        super().__init__()
        self.github_token = github_token
        self.map_info = map_info
        self.map_zip_path = map_zip_path
        self.rp_zip_path = rp_zip_path
        self.uploaded_assets = {} # To store {asset_name: download_url}

    def run(self):
        try:
            self.upload_progress.emit("Connecting to GitHub...")
            g = Github(self.github_token)
            
            # Get user to validate token
            try:
                g.get_user().login
            except GithubException as e:
                self.upload_error.emit(f"GitHub authentication error: {e.data.get('message', 'Invalid token or insufficient permissions')}")
                return

            repo = g.get_user(GITHUB_REPO_OWNER).get_repo(GITHUB_REPO_NAME)
            self.upload_progress.emit(f"Connected to repository: {repo.full_name}")

            # Define release details
            release_tag = f"map-{self.map_info['id']}-v{self.map_info['latest_version']}"
            release_title = f"Map: {self.map_info['name']} v{self.map_info['latest_version']}"
            release_message = self.map_info.get('description', 'New map release.')

            # Check if tag already exists
            try:
                repo.get_git_ref(f"tags/{release_tag}")
                self.upload_error.emit(f"Error: A release with tag '{release_tag}' already exists. Please increment the map version.")
                return
            except GithubException as e:
                if e.status != 404: # If not 404 (not found), it's another error
                    raise e # Re-raise if it's a real error, otherwise continue

            self.upload_progress.emit(f"Creating GitHub release: {release_title}...")
            release = repo.create_git_release(
                tag=release_tag,
                name=release_title,
                message=release_message,
                prerelease=False, 
                draft=False 
            )
            self.upload_progress.emit(f"Release created: {release.html_url}")

            # Upload map file
            self.upload_progress.emit(f"Uploading map file: {os.path.basename(self.map_zip_path)}...")
            uploaded_map_asset = release.upload_asset(self.map_zip_path, name=os.path.basename(self.map_zip_path))
            self.uploaded_assets[os.path.basename(self.map_zip_path)] = uploaded_map_asset.browser_download_url
            self.upload_progress.emit(f"Map file uploaded.")

            # Upload resource pack file if applicable
            if self.rp_zip_path and os.path.exists(self.rp_zip_path):
                self.upload_progress.emit(f"Uploading resource pack file: {os.path.basename(self.rp_zip_path)}...")
                uploaded_rp_asset = release.upload_asset(self.rp_zip_path, name=os.path.basename(self.rp_zip_path))
                self.uploaded_assets[os.path.basename(self.rp_zip_path)] = uploaded_rp_asset.browser_download_url
                self.upload_progress.emit(f"Resource pack file uploaded.")

            # Update updates.json
            self.upload_progress.emit("Updating updates.json...")
            self._update_remote_updates_json(repo, release)

            self.upload_finished.emit(self.map_info)

        except GithubException as e:
            self.upload_error.emit(f"GitHub operation failed: {e.data.get('message', str(e))}. Please check your token permissions (should include 'Contents' read/write for this repository).")
        except Exception as e:
            self.upload_error.emit(f"An unexpected error occurred during upload: {e}")

    def _update_remote_updates_json(self, repo, release_info):
        """
        Reads updates.json from GitHub, modifies it with new map data,
        and pushes it back to GitHub.
        """
        updates_json_path = "updates.json" # Relative path on GitHub
        current_json_content = None
        updates_data = {}
        updates_file_sha = None

        try:
            contents = repo.get_contents(updates_json_path, ref="main") # Assuming main branch
            current_json_content = contents.decoded_content.decode('utf-8')
            updates_data = json.loads(current_json_content)
            updates_file_sha = contents.sha # Keep SHA for file update
            self.upload_progress.emit(f"'{updates_json_path}' loaded from GitHub.")
        except GithubException as e:
            if e.status == 404:
                self.upload_progress.emit(f"WARNING: '{updates_json_path}' not found on GitHub. Creating a new base file.")
                updates_data = {
                    "launcher": {"latest_version": "0.0.0", "download_url": ""},
                    "mod": {"name": "NomDuMod", "latest_version": "0.0.0", "download_url": "", "changelog_url": ""},
                    "maps": [],
                    "content_packs": [] # Changed from "modpacks" to "content_packs"
                }
            else:
                raise # Re-raise other GitHub exceptions
        except json.JSONDecodeError as e:
            raise Exception(f"ERROR: '{updates_json_path}' on GitHub is invalid (malformed JSON): {e}")

        # Construct the new map entry
        new_map_entry = {
            "id": self.map_info['id'],
            "name": self.map_info['name'],
            "latest_version": self.map_info['latest_version'],
            "download_url": self.uploaded_assets.get(os.path.basename(self.map_zip_path), ""),
            "description": self.map_info['description']
        }
        if self.rp_zip_path and os.path.exists(self.rp_zip_path):
            new_map_entry["resourcepack_url"] = self.uploaded_assets.get(os.path.basename(self.rp_zip_path), "")

        # Check if the map already exists (by ID) and update it, otherwise add it
        found_map = False
        if "maps" not in updates_data:
            updates_data["maps"] = []
        for i, map_obj in enumerate(updates_data["maps"]):
            if map_obj.get("id") == self.map_info['id']:
                updates_data["maps"][i] = new_map_entry
                self.upload_progress.emit(f"Map '{self.map_info['id']}' updated in updates.json.")
                found_map = True
                break
        if not found_map:
            updates_data["maps"].append(new_map_entry)
            self.upload_progress.emit(f"New map '{self.map_info['id']}' added to updates.json.")
        
        # Commit and push the updated JSON
        new_json_content = json.dumps(updates_data, indent=4, ensure_ascii=False) # ensure_ascii=False for UTF-8 chars
        
        commit_message = f"feat: Add/Update map {self.map_info['name']} (v{self.map_info['latest_version']}) via launcher"
        if updates_file_sha: # File existed, update it
            repo.update_file(
                path=updates_json_path,
                message=commit_message,
                content=new_json_content,
                sha=updates_file_sha,
                branch="main" 
            )
        else: # File did not exist, create it
            repo.create_file(
                path=updates_json_path,
                message=commit_message,
                content=new_json_content,
                branch="main"
            )
        self.upload_progress.emit(f"'{updates_json_path}' updated and pushed to GitHub successfully!")

# --- THREAD FOR GITHUB DELETION OPERATIONS ---
class GitHubDeleterThread(QThread):
    deletion_progress = pyqtSignal(str)
    deletion_finished = pyqtSignal(str) # Emits the ID of the deleted map
    deletion_error = pyqtSignal(str)

    def __init__(self, github_token, map_id_to_delete):
        super().__init__()
        self.github_token = github_token
        self.map_id_to_delete = map_id_to_delete

    def run(self):
        try:
            self.deletion_progress.emit("Connecting to GitHub for deletion...")
            g = Github(self.github_token)
            
            try:
                g.get_user().login
            except GithubException as e:
                self.deletion_error.emit(f"GitHub authentication error: {e.data.get('message', 'Invalid token or insufficient permissions')}")
                return

            repo = g.get_user(GITHUB_REPO_OWNER).get_repo(GITHUB_REPO_NAME)
            self.deletion_progress.emit(f"Connected to repository: {repo.full_name}")

            # 1. Delete associated GitHub releases
            self.deletion_progress.emit(f"Searching for releases for map ID '{self.map_id_to_delete}'...")
            
            releases_deleted_count = 0
            # Get all releases. Convert to list to avoid issues with modifying collection during iteration.
            # Make a copy to iterate because we are deleting elements
            all_releases_found = list(repo.get_releases())
            for release in all_releases_found: 
                # Check if the release's tag name starts with our map ID pattern
                if release.tag_name and release.tag_name.startswith(f"map-{self.map_id_to_delete}-"):
                    try:
                        self.deletion_progress.emit(f"Attempting to delete release '{release.title}' (tag: {release.tag_name})...")
                        release.delete_release()
                        releases_deleted_count += 1
                        self.deletion_progress.emit(f"Successfully deleted release: {release.title}")
                    except GithubException as e:
                        error_msg = e.data.get('message', str(e))
                        self.deletion_progress.emit(f"Warning: Could not delete release '{release.title}'. Status: {e.status}, Message: {error_msg}. "
                                                    f"Please ensure your token has 'Releases' (write) permission for this repository.")
                        print(f"DEBUG: GitHubException during release deletion (Status: {e.status}, Data: {e.data})")
            
            self.deletion_progress.emit(f"Deleted {releases_deleted_count} associated GitHub releases. Waiting 2 seconds for GitHub propagation...")
            time.sleep(2) # Give GitHub some time to process deletions

            # 2. Delete associated Git tag references (for robustness, in case some tags were orphaned or not part of a release)
            self.deletion_progress.emit(f"Searching for and deleting associated Git tags for map ID '{self.map_id_to_delete}'...")
            tags_deleted_count = 0
            # Get all tags. Convert to list to avoid issues with modifying collection during iteration.
            all_tags_found = list(repo.get_tags())
            for tag in all_tags_found: 
                if tag.name.startswith(f"map-{self.map_id_to_delete}-"):
                    try:
                        # Get the GitRef object for the tag
                        git_ref_path = f"tags/{tag.name}"
                        git_ref = repo.get_git_ref(git_ref_path)
                        self.deletion_progress.emit(f"Attempting to delete Git tag reference '{tag.name}'...")
                        git_ref.delete()
                        tags_deleted_count += 1
                        self.deletion_progress.emit(f"Successfully deleted tag: {tag.name}")
                    except GithubException as e:
                        error_msg = e.data.get('message', str(e))
                        if e.status == 404:
                            self.deletion_progress.emit(f"Tag '{tag.name}' not found, possibly already deleted by release deletion or previously missing.")
                        else:
                            self.deletion_progress.emit(f"Warning: Could not delete Git tag reference '{tag.name}'. Status: {e.status}, Message: {error_msg}. "
                                                        f"Please ensure your token has 'Git tags' (write) permission for this repository.")
                            print(f"DEBUG: GitHubException during tag deletion (Status: {e.status}, Data: {e.data})")
            self.deletion_progress.emit(f"Deleted {tags_deleted_count} associated Git tags. Waiting 2 seconds for GitHub propagation...")
            time.sleep(2) # Give GitHub some time to process deletions

            # 3. Update updates.json to remove the map entry
            self.deletion_progress.emit("Updating updates.json...")
            updates_json_path = "updates.json"
            updates_data = {}
            updates_file_sha = None

            try:
                contents = repo.get_contents(updates_json_path, ref="main")
                current_json_content = contents.decoded_content.decode('utf-8')
                updates_data = json.loads(current_json_content)
                updates_file_sha = contents.sha
                self.deletion_progress.emit(f"'{updates_json_path}' loaded from GitHub.")
            except GithubException as e:
                if e.status == 404:
                    self.deletion_error.emit(f"Error: '{updates_json_path}' not found on GitHub. Cannot delete entry. Please ensure this file exists in your repository.")
                    return
                else:
                    raise # Re-raise other GitHub exceptions
            except json.JSONDecodeError as e:
                self.deletion_error.emit(f"ERROR: '{updates_json_path}' on GitHub is invalid (malformed JSON): {e}. Cannot delete entry. Please check the file's content on GitHub.")
                return


            # Filter out the map to be deleted
            if "maps" in updates_data:
                initial_map_count = len(updates_data["maps"])
                updates_data["maps"] = [
                    map_obj for map_obj in updates_data["maps"]
                    if map_obj.get("id") != self.map_id_to_delete
                ]
                if len(updates_data["maps"]) < initial_map_count:
                    self.deletion_progress.emit(f"Map ID '{self.map_id_to_delete}' removed from updates.json.")
                else:
                    self.deletion_progress.emit(f"Map ID '{self.map_id_to_delete}' was not found in updates.json (it might have been deleted manually or previously).")

            # Commit and push the updated JSON
            new_json_content = json.dumps(updates_data, indent=4, ensure_ascii=False)
            commit_message = f"chore: Remove map {self.map_id_to_delete} via launcher"
            
            repo.update_file(
                path=updates_json_path,
                message=commit_message,
                content=new_json_content,
                sha=updates_file_sha,
                branch="main" 
            )
            self.deletion_progress.emit(f"'{updates_json_path}' updated and pushed to GitHub successfully!")

            self.deletion_finished.emit(self.map_id_to_delete)

        except GithubException as e:
            # General GitHub error for deletion
            self.deletion_error.emit(f"Échec de l'opération GitHub lors de la suppression : {e.data.get('message', str(e))}. "
                                    "Veuillez vous assurer que votre Personal Access Token GitHub dispose des permissions suffisantes "
                                    "(par exemple, le scope 'repo' complet ou spécifiquement 'contents:write', 'releases:write', 'delete_repo' pour ce dépôt, et 'write:discussion' si vous avez l'option).")
            print(f"DEBUG: Critical GitHubException during deletion (Status: {e.status}, Data: {e.data})")
        except Exception as e:
            self.deletion_error.emit(f"Une erreur inattendue est survenue lors de la suppression : {e}")
            print(f"DEBUG: Unexpected error during deletion: {e}")


# --- MAIN LAUNCHER CLASS ---
class ZombieRoolLauncher(QMainWindow):
    def __init__(self):
        super().__init__()

        # --- Language and Theme Dictionaries ---
        self.translations = {
            "Welcome to the ZombieRool Launcher!": {
                "en": "Welcome to the ZombieRool Launcher!",
                "fr": "Bienvenue sur le Lanceur ZombieRool !"
            },
            "Launcher Version:": {
                "en": "Launcher Version:",
                "fr": "Version du Lanceur :"
            },
            "Update": {"en": "Update", "fr": "Mise à Jour"},
            "Map Download": {"en": "Map Download", "fr": "Télécharger Cartes"},
            "Access Code": {"en": "Access Code", "fr": "Code d'Accès"}, # Changed tab title translation
            "Upload Map": {"en": "Upload Map", "fr": "Publier Carte"},
            "Settings": {"en": "Settings", "fr": "Paramètres"},
            "ZombieRool Mod Updates": {
                "en": "ZombieRool Mod Updates",
                "fr": "Mises à jour du Mod ZombieRool"
            },
            "Mod Status: Checking...": {
                "en": "Mod Status: Checking...",
                "fr": "Statut du Mod : Vérification..."
            },
            "Update Mod": {"en": "Update Mod", "fr": "Mettre à Jour le Mod"},
            "Launcher Updates": {
                "en": "Launcher Updates",
                "fr": "Mises à jour du Lanceur"
            },
            "Launcher Status: Checking...": {
                "en": "Launcher Status: Checking...",
                "fr": "Statut du Lanceur : Vérification..."
            },
            "Update Launcher": {"en": "Update Launcher", "fr": "Mettre à Jour le Lanceur"},
            "Download and Install Maps": {
                "en": "Download and Install Maps",
                "fr": "Télécharger et Installer des Cartes"
            },
            "Search maps by name or description...": {
                "en": "Search maps by name or description...",
                "fr": "Rechercher des cartes par nom ou description..."
            },
            "Refresh Map Catalog": {
                "en": "Refresh Map Catalog",
                "fr": "Actualiser le Catalogue de Cartes"
            },
            "Publish Map to GitHub": {
                "en": "Publish Map to GitHub",
                "fr": "Publier une Carte sur GitHub"
            },
            "GitHub Personal Access Token:": {
                "en": "GitHub Personal Access Token:",
                "fr": "Jeton d'Accès Personnel GitHub :"
            },
            "Enter your GitHub token (not saved!)": {
                "en": "Enter your GitHub token (not saved!)",
                "fr": "Entrez votre jeton GitHub (non sauvegardé !)"
            },
            "Map ID (unique, e.g., 'winter'):": {
                "en": "Map ID (unique, e.g., 'winter'):",
                "fr": "ID de la Carte (unique, ex: 'winter') :"
            },
            "Enter a unique ID for the map (e.g., 'my-awesome-map')": {
                "en": "Enter a unique ID for the map (e.g., 'my-awesome-map')",
                "fr": "Entrez un ID unique pour la carte (ex: 'ma-super-carte')"
            },
            "Map Name (displayed):": {
                "en": "Map Name (displayed):",
                "fr": "Nom de la Carte (affiché) :"
            },
            "Enter the map's display name (e.g., 'The Asylum Map')": {
                "en": "Enter the map's display name (e.g., 'The Asylum Map')",
                "fr": "Entrez le nom d'affichage de la carte (ex: 'La Carte de l'Asile')"
            },
            "Map Version:": {"en": "Map Version:", "fr": "Version de la Carte :"},
            "Enter the map's version (e.g., '1.0.0')": {
                "en": "Enter the map's version (e.g., '1.0.0')",
                "fr": "Entrez la version de la carte (ex: '1.0.0')"
            },
            "Map Description:": {"en": "Map Description:", "fr": "Description de la Carte :"},
            "Enter a brief description for the map.": {
                "en": "Enter a brief description for the map.",
                "fr": "Entrez une brève description de la carte."
            },
            "Select Map ZIP file:": {
                "en": "Select Map ZIP file:",
                "fr": "Sélectionner le fichier ZIP de la Carte :"
            },
            "Browse...": {"en": "Browse...", "fr": "Parcourir..."},
            "Associated Resource Pack?": {
                "en": "Associated Resource Pack?",
                "fr": "Pack de Ressources Associé ?"
            },
            "Select Resource Pack ZIP file:": {
                "en": "Select Resource Pack ZIP file:",
                "fr": "Sélectionner le fichier ZIP du Pack de Ressources :"
            },
            "Publish Map to GitHub": {
                "en": "Publish Map to GitHub",
                "fr": "Publier la Carte sur GitHub"
            },
            "Delete Map from GitHub": {
                "en": "Delete Map from GitHub",
                "fr": "Supprimer la Carte de GitHub"
            },
            "Map ID to Delete:": {
                "en": "Map ID to Delete:",
                "fr": "ID de la Carte à Supprimer :"
            },
            "Enter the ID of the map to delete (e.g., 'old-map-id')": {
                "en": "Enter the ID of the map to delete (e.g., 'old-map-id')",
                "fr": "Entrez l'ID de la carte à supprimer (ex: 'ancienne-carte-id')"
            },
            "Delete Map from Catalog": {
                "en": "Delete Map from Catalog",
                "fr": "Supprimer la Carte du Catalogue"
            },
            "Minecraft Settings and Paths": {
                "en": "Minecraft Settings and Paths",
                "fr": "Paramètres et Chemins Minecraft"
            },
            " .minecraft or Instance Folder :": {
                "en": " .minecraft or Instance Folder :",
                "fr": " Dossier .minecraft ou d'Instance :"
            },
            "Click 'Browse...' to choose your Minecraft folder": {
                "en": "Click 'Browse...' to choose your Minecraft folder",
                "fr": "Cliquez sur 'Parcourir...' pour choisir votre dossier Minecraft"
            },
            "Mods Folder: Not Detected": {
                "en": "Mods Folder: Not Detected",
                "fr": "Dossier Mods : Non Détecté"
            },
            "Saves Folder: Not Detected": {
                "en": "Saves Folder: Not Detected",
                "fr": "Dossier Sauvegardes : Non Détecté"
            },
            "Resourcepacks Folder: Not Detected": {
                "en": "Resourcepacks Folder: Not Detected",
                "fr": "Dossier Packs de Ressources : Non Détecté"
            },
            "Path Not Configured": {
                "en": "Path Not Configured",
                "fr": "Chemin Non Configuré"
            },
            "Mods Folder: Not Configured": {
                "en": "Mods Folder: Not Configured",
                "fr": "Dossier Mods : Non Configuré"
            },
            "Saves Folder: Not Configured": {
                "en": "Saves Folder: Not Configured",
                "fr": "Dossier Sauvegardes : Non Configuré"
            },
            "Resourcepacks Folder: Not Configured": {
                "en": "Resourcepacks Folder: Non Configuré"
            },
            "Minecraft Path Configuration": {
                "en": "Minecraft Path Configuration",
                "fr": "Configuration du Chemin Minecraft"
            },
            "Welcome! Please select your Minecraft instance folder (containing 'mods', 'saves', 'resourcepacks' folders) in 'Settings' by clicking 'Browse...'.": {
                "en": "Welcome! Please select your Minecraft instance folder (containing 'mods', 'saves', 'resourcepacks' folders) in 'Settings' by clicking 'Browse...'.",
                "fr": "Bienvenue ! Veuillez sélectionner votre dossier d'instance Minecraft (contenant les dossiers 'mods', 'saves', 'resourcepacks') dans 'Paramètres' en cliquant sur 'Parcourir...'."
            },
            "Selection Canceled": {"en": "Selection Canceled", "fr": "Sélection Annulée"},
            "Minecraft Path Configured": {
                "en": "Minecraft Path Configured",
                "fr": "Chemin Minecraft Configuré"
            },
            "The Minecraft folder has been manually configured:": {
                "en": "The Minecraft folder has been manually configured:",
                "fr": "Le dossier Minecraft a été configuré manuellement :"
            },
            "Invalid Path": {"en": "Invalid Path", "fr": "Chemin Invalide"},
            "The selected path does not appear to be a valid Minecraft instance (mods, saves, resourcepacks folders not found).": {
                "en": "The selected path does not appear to be a valid Minecraft instance (mods, saves, resourcepacks folders not found).",
                "fr": "Le chemin sélectionné ne semble pas être une instance Minecraft valide (dossiers mods, saves, resourcepacks introuvables)."
            },
            "Checking for updates...": {
                "en": "Checking for updates...",
                "fr": "Vérification des mises à jour..."
            },
            "ZombieRool Launcher - Updates Checked": {
                "en": "ZombieRool Launcher - Updates Checked",
                "fr": "Lanceur ZombieRool - Mises à jour Vérifiées"
            },
            "Updates": {"en": "Updates", "fr": "Mises à jour"},
            "Update information successfully retrieved from GitHub!": {
                "en": "Update information successfully retrieved from GitHub!",
                "fr": "Informations de mise à jour récupérées avec succès depuis GitHub !"
            },
            "Error": {"en": "Error", "fr": "Erreur"},
            "Could not retrieve update information. Check the updates.json file URL or JSON structure.": {
                "en": "Could not retrieve update information. Check the updates.json file URL or JSON structure.",
                "fr": "Impossible de récupérer les informations de mise à jour. Vérifiez l'URL ou la structure JSON du fichier updates.json."
            },
            "ZombieRool Launcher - Update Error": {
                "en": "ZombieRool Launcher - Update Error",
                "fr": "Lanceur ZombieRool - Erreur de Mise à Jour"
            },
            "Update Error": {"en": "Update Error", "fr": "Erreur de Mise à Jour"},
            "An error occurred while checking for updates:": {
                "en": "An error occurred while checking for updates:",
                "fr": "Une erreur est survenue lors de la vérification des mises à jour :"
            },
            "Mod Status: Remote info not found.": {
                "en": "Mod Status: Remote info not found.",
                "fr": "Statut du Mod : Informations distantes introuvables."
            },
            "Mod Status: New version {remote_version_str} available! (Current: {local_version_str})": {
                "en": "Mod Status: New version {remote_version_str} available! (Current: {local_version_str})",
                "fr": "Statut du Mod : Nouvelle version {remote_version_str} disponible ! (Actuelle : {local_version_str})"
            },
            "Mod Status: Up to date (v{local_version_str})": {
                "en": "Mod Status: Up to date (v{local_version_str})",
                "fr": "Statut du Mod : À jour (v{local_version_str})"
            },
            "Mod Status: Version error ({e})": {
                "en": "Mod Status: Version error ({e})",
                "fr": "Statut du Mod : Erreur de version ({e})"
            },
            "Launcher Update": {"en": "Launcher Update", "fr": "Mise à Jour du Lanceur"},
            "Launcher update information not found.": {
                "en": "Launcher update information not found.",
                "fr": "Informations de mise à jour du lanceur introuvables."
            },
            "Launcher download URL not found in update data.": {
                "en": "Launcher download URL not found in update data.",
                "fr": "URL de téléchargement du lanceur introuvable dans les données de mise à jour."
            },
            "Downloading new launcher version ({latest_version})...": { 
                "en": "Downloading new launcher version ({latest_version})...",
                "fr": "Téléchargement de la nouvelle version du lanceur ({latest_version})..."
            },
            "Downloading...": {"en": "Downloading...", "fr": "Téléchargement en cours..."},
            "Téléchargement terminé. Préparation à la mise à jour...": {
                "en": "Download complete. Preparing for update...",
                "fr": "Téléchargement terminé. Préparation à la mise à jour..."
            },
            "Nouvelle version téléchargée. Le launcher va se relancer.": {
                "en": "New version downloaded. The launcher will restart.",
                "fr": "Nouvelle version téléchargée. Le lanceur va se relancer."
            },
            "Erreur Mise à Jour Launcher": {
                "en": "Launcher Update Error",
                "fr": "Erreur Mise à Jour Lanceur"
            },
            "Impossible de lancer la procédure de mise à jour : {e}. Veuillez redémarrer le launcher manuellement.": {
                "en": "Could not start update procedure: {e}. Please restart the launcher manually.",
                "fr": "Impossible de lancer la procédure de mise à jour : {e}. Veuillez redémarrer le lanceur manuellement."
            },
            "Erreur lors du lancement de la mise à jour : {e}": {
                "en": "Error launching update: {e}",
                "fr": "Erreur lors du lancement de la mise à jour : {e}"
            },
            "Launcher Download Error": {
                "en": "Launcher Download Error",
                "fr": "Erreur de Téléchargement du Lanceur"
            },
            "Download failed: {message}": {
                "en": "Download failed: {message}",
                "fr": "Échec du téléchargement : {message}"
            },
            "Installation Error": {"en": "Installation Error", "fr": "Erreur d'Installation"},
            "The Minecraft 'mods' folder is not configured. Please define it in 'Settings'.": {
                "en": "The Minecraft 'mods' folder is not configured. Please define it in 'Settings'.",
                "fr": "Le dossier 'mods' de Minecraft n'est pas configuré. Veuillez le définir dans 'Paramètres'."
            },
            "Mod Update": {"en": "Mod Update", "fr": "Mise à Jour du Mod"},
            "Mod download URL not found in update data.": {
                "en": "Mod download URL not found in update data.",
                "fr": "URL de téléchargement du mod introuvable dans les données de mise à jour."
            },
            "Downloading mod ({latest_version})...": {
                "en": "Downloading mod ({latest_version})...",
                "fr": "Téléchargement du mod ({latest_version})..."
            },
            "Downloading mod...": {"en": "Downloading mod...", "fr": "Téléchargement du mod..."},
            "Installing mod...": {"en": "Installing mod...", "fr": "Installation du mod..."},
            "Mod updated and installed successfully!": {
                "en": "Mod updated and installed successfully!",
                "fr": "Mod mis à jour et installé avec succès !"
            },
            "Mod Installation Error": {
                "en": "Mod Installation Error",
                "fr": "Erreur d'Installation du Mod"
            },
            "An error occurred during mod installation: {e}": {
                "en": "An error occurred during mod installation: {e}",
                "fr": "Une erreur est survenue lors de l'installation du mod : {e}"
            },
            "Installation failed: {e}": {
                "en": "Installation failed: {e}",
                "fr": "Échec de l'installation : {e}"
            },
            "Mod Download Error": {
                "en": "Mod Download Error",
                "fr": "Erreur de Téléchargement du Mod"
            },
            "No map information available.": {
                "en": "No map information available.",
                "fr": "Aucune information de carte disponible."
            },
            "No maps found matching your search criteria.": {
                "en": "No maps found matching your search criteria.",
                "fr": "Aucune carte trouvée correspondant à vos critères de recherche."
            },
            "Install Map": {"en": "Install Map", "fr": "Installer Carte"},
            "Minecraft 'saves' or 'resourcepacks' folders are not configured. Please define them in 'Settings'.": {
                "en": "Minecraft 'saves' or 'resourcepacks' folders are not configured. Please define them in 'Settings'.",
                "fr": "Les dossiers 'saves' ou 'resourcepacks' de Minecraft ne sont pas configurés. Veuillez les définir dans 'Paramètres'."
            },
             "Minecraft 'mods' folder is not configured. Please define it in 'Settings'.": {
                "en": "Minecraft 'mods' folder is not configured. Please define it in 'Settings'.",
                "fr": "Le dossier 'mods' de Minecraft n'est pas configuré. Veuillez le définir dans 'Paramètres'."
            },
            "Map Installation": {"en": "Map Installation", "fr": "Installation de Carte"},
            "Map download URL not found.": {
                "en": "Map download URL not found.",
                "fr": "URL de téléchargement de la carte introuvable."
            },
            "Downloading map '{map_name}'...": { 
                "en": "Downloading map '{map_name}'...",
                "fr": "Téléchargement de la carte '{map_name}'..."
            },
            "Map '{map_name}' downloaded. Installing...": {
                "en": "Map '{map_name}' downloaded. Installing...",
                "fr": "Carte '{map_name}' téléchargée. Installation..."
            },
            "Decompression Error": {"en": "Decompression Error", "fr": "Erreur de Décompression"},
            "The map ZIP file is corrupted or invalid. The 'zipfile' module only supports ZIP format (not RAR).": {
                "en": "The map ZIP file is corrupted or invalid. The 'zipfile' module only supports ZIP format (not RAR).",
                "fr": "Le fichier ZIP de la carte est corrompu ou invalide. Le module 'zipfile' ne prend en charge que le format ZIP (pas RAR)."
            },
            "Map Installation Error": {
                "en": "Map Installation Error",
                "fr": "Erreur d'Installation de la Carte"
            },
            "An error occurred during map installation: {e}": {
                "en": "An error occurred during map installation: {e}",
                "fr": "Une erreur est survenue lors de l'installation de la carte : {e}"
            },
            "Resource Pack Installation": {
                "en": "Resource Pack Installation",
                "fr": "Installation du Pack de Ressources"
            },
            "Downloading associated resource pack...": {
                "en": "Downloading associated resource pack...",
                "fr": "Téléchargement du pack de ressources associé..."
            },
            "Resource pack downloaded. Installing...": {
                "en": "Resource pack downloaded. Installing...",
                "fr": "Pack de ressources téléchargé. Installation..."
            },
            "Resource Pack installed successfully! Map and Resource Pack are ready.": {
                "en": "Resource Pack installed successfully! Map and Resource Pack are ready.",
                "fr": "Pack de ressources installé avec succès ! La carte et le pack de ressources sont prêts."
            },
            "RP Installation Error": {
                "en": "RP Installation Error",
                "fr": "Erreur d'Installation du Pack de Ressources"
            },
            "An error occurred during resource pack installation: {e}": {
                "en": "An error occurred during resource pack installation: {e}",
                "fr": "Une erreur est survenue lors de l'installation du pack de ressources : {e}"
            },
            "Installation Complete": {"en": "Installation Complete", "fr": "Installation Terminée"},
            "Map '{map_name}' installed successfully! (No associated Resource Pack)": {
                "en": "Map '{map_name}' installed successfully! (No associated Resource Pack)",
                "fr": "Carte '{map_name}' installée avec succès ! (Pas de Pack de Ressources associé)"
            },
            "Resource Pack Download Error": {
                "en": "Resource Pack Download Error",
                "fr": "Erreur de Téléchargement du Pack de Ressources"
            },
            "Confirm Deletion": {"en": "Confirm Deletion", "fr": "Confirmer la Suppression"},
            "Are you sure you want to delete ALL releases and the entry for map ID '{map_id_to_delete}' from GitHub?\nThis action cannot be undone!": {
                "en": "Are you sure you want to delete ALL releases and the entry for map ID '{map_id_to_delete}' from GitHub?\nThis action cannot be undone!",
                "fr": "Êtes-vous sûr de vouloir supprimer TOUTES les versions et l'entrée de la carte ID '{map_id_to_delete}' de GitHub ?\nCette action est irréversible !"
            },
            "Deletion canceled.": {"en": "Deletion canceled.", "fr": "Suppression annulée."},
            "Initiating deletion for map ID '{map_id_to_delete}'...": {
                "en": "Initiating deletion for map ID '{map_id_to_delete}'...",
                "fr": "Initialisation de la suppression pour la carte ID '{map_id_to_delete}'..."
            },
            "Deletion complete for map ID '{map_id}'.": {
                "en": "Deletion complete for map ID '{map_id}'.",
                "fr": "Suppression terminée pour la carte ID '{map_id}'."
            },
            "Deletion Success": {"en": "Deletion Success", "fr": "Suppression Réussie"},
            "Map ID '{map_id}' and its associated GitHub releases have been successfully deleted, and updates.json has been updated!": {
                "en": "Map ID '{map_id}' and its associated GitHub releases have been successfully deleted, and updates.json has been updated!",
                "fr": "La carte ID '{map_id}' et ses versions GitHub associées ont été supprimées avec succès, et updates.json a été mis à jour !"
            },
            "Deletion Error": {"en": "Deletion Error", "fr": "Erreur de Suppression"},
            "Échec de l'opération GitHub lors de la suppression : {message}. Veuillez vous assurer que votre Personal Access Token GitHub dispose des permissions suffisantes (par exemple, le scope 'repo' complet ou spécifiquement 'contents:write', 'releases:write', 'delete_repo' pour ce dépôt, et 'write:discussion' si vous avez l'option).": {
                "en": "GitHub operation failed during deletion: {message}. Please ensure your GitHub Personal Access Token has sufficient permissions (e.g., full 'repo' scope or specifically 'contents:write', 'releases:write', 'delete_repo' for this repository, and 'write:discussion' if you have the option).",
                "fr": "Échec de l'opération GitHub lors de la suppression : {message}. Veuillez vous assurer que votre Jeton d'Accès Personnel GitHub dispose des permissions suffisantes (par exemple, le scope 'repo' complet ou spécifiquement 'contents:write', 'releases:write', 'delete_repo' pour ce dépôt, et 'write:discussion' si vous avez l'option)."
            },
            "Une erreur inattendue est survenue lors de la suppression : {e}": {
                "en": "An unexpected error occurred during deletion: {e}",
                "fr": "Une erreur inattendue est survenue lors de la suppression : {e}"
            },
            "Missing Information": {"en": "Missing Information", "fr": "Informations Manquantes"},
            "Please enter your GitHub Personal Access Token.": {
                "en": "Please enter your GitHub Personal Access Token.",
                "fr": "Veuillez entrer votre Jeton d'Accès Personnel GitHub."
            },
            "Publication failed: Missing GitHub token.": {
                "en": "Publication failed: Missing GitHub token.",
                "fr": "Publication échouée : Jeton GitHub manquant."
            },
            "Please fill in Map ID, Map Name, Map Version, and select the Map ZIP file.": {
                "en": "Please fill in Map ID, Map Name, Map Version, and select the Map ZIP file.",
                "fr": "Veuillez remplir l'ID de la carte, le Nom de la carte, la Version de la carte et sélectionner le fichier ZIP de la carte."
            },
            "Publication failed: Missing map information.": {
                "en": "Publication failed: Missing map information.",
                "fr": "Publication échouée : Informations de carte manquantes."
            },
            "File Not Found": {"en": "File Not Found", "fr": "Fichier Introuvable"},
            "Map ZIP file not found at: {map_zip_path}": {
                "en": "Map ZIP file not found at: {map_zip_path}",
                "fr": "Fichier ZIP de la carte introuvable à : {map_zip_path}"
            },
            "Publication failed: Map ZIP not found.": {
                "en": "Publication failed: Map ZIP not found.",
                "fr": "Publication échouée : ZIP de la carte introuvable."
            },
            "Missing Resource Pack File": {
                "en": "Missing Resource Pack File",
                "fr": "Fichier de Pack de Ressources Manquant"
            },
            "You checked 'Associated Resource Pack' but did not select a valid RP ZIP file or it does not exist.": {
                "en": "You checked 'Associated Resource Pack' but did not select a valid RP ZIP file or it does not exist.",
                "fr": "Vous avez coché 'Pack de Ressources Associé' mais n'avez pas sélectionné de fichier ZIP de pack de ressources valide ou il n'existe pas."
            },
            "Publication failed: RP ZIP not found.": {
                "en": "Publication failed: RP ZIP not found.",
                "fr": "Publication échouée : ZIP du pack de ressources introuvable."
            },
            "Initiating GitHub upload...": {
                "en": "Initiating GitHub upload...",
                "fr": "Initialisation de l'envoi sur GitHub..."
            },
            "Publication complete! Check GitHub.": {
                "en": "Publication complete! Check GitHub.",
                "fr": "Publication terminée ! Vérifiez GitHub."
            },
            "Publication Success": {"en": "Publication Success", "fr": "Publication Réussie"},
            "Map '{map_name}' (v{map_version}) has been successfully published to GitHub and updates.json has been updated!": {
                "en": "Map '{map_name}' (v{map_version}) has been successfully published to GitHub and updates.json has been updated!",
                "fr": "La carte '{map_name}' (v{map_version}) a été publiée avec succès sur GitHub et updates.json a été mis à jour !"
            },
            "Publication Error": {"en": "Publication Error", "fr": "Erreur de Publication"},
            "Publication failed: {message}": {
                "en": "Publication failed: {message}",
                "fr": "Publication échouée : {message}"
            },
            "Please enter the Map ID to delete.": {
                "en": "Please enter the Map ID to delete.",
                "fr": "Veuillez entrer l'ID de la carte à supprimer."
            },
            "Deletion failed: Missing map ID.": {
                "en": "Deletion failed: Missing map ID.",
                "fr": "Suppression échouée : ID de carte manquant."
            },
            "Select Language:": {"en": "Select Language:", "fr": "Sélectionner la Langue :"},
            "Select Theme:": {"en": "Select Theme:", "fr": "Sélectionner le Thème :"},
            "Default Theme": {"en": "Default Theme", "fr": "Thème par Défaut"},
            "Dark Theme": {"en": "Dark Theme", "fr": "Thème Sombre"},
            # Updated translations for generic content code tab
            "Enter Content Code:": {"en": "Enter Content Code:", "fr": "Entrez le Code d'Accès :"}, # Changed text
            "Enter the secret code for the content pack": {"en": "Enter the secret code for the content pack", "fr": "Entrez le code secret pour le pack de contenu"}, # Changed text
            "Download Content Pack": {"en": "Download Content Pack", "fr": "Télécharger le Pack de Contenu"}, # Changed text
            "Content Status: Waiting for code...": {"en": "Content Status: Waiting for code...", "fr": "Statut du Contenu : En attente de code..."}, # Changed text
            "Invalid Code.": {"en": "Invalid Code.", "fr": "Code Invalide."}, # Changed text
            "Content information not found for code '{code}'.": {"en": "Content information not found for code '{code}'.", "fr": "Informations de contenu introuvables pour le code '{code}'."}, # Changed text
            "Downloading Content Pack '{name}' (v{version})...": {"en": "Downloading Content Pack '{name}' (v{version})...", "fr": "Téléchargement du Pack de Contenu '{name}' (v{version})..."}, # Changed text
            "Content Download Error": {"en": "Content Download Error", "fr": "Erreur de Téléchargement du Contenu"}, # Changed text
            "Content Pack '{name}' downloaded. Installing...": {"en": "Content Pack '{name}' downloaded. Installing...", "fr": "Pack de Contenu '{name}' téléchargé. Installation..."}, # Changed text
            "Content Pack installed successfully!": {"en": "Content Pack installed successfully!", "fr": "Pack de Contenu installé avec succès !"}, # Changed text
            "Content Installation Error": {"en": "Content Installation Error", "fr": "Erreur d'Installation du Contenu"}, # Changed text
            "An error occurred during content installation: {e}": {"en": "An error occurred during content installation: {e}", "fr": "Une erreur est survenue lors de l'installation du contenu : {e}"}, # Changed text
            "The Minecraft 'mods' folder is not configured. Please define it in 'Settings' to install the content pack.": { # Changed text
                "en": "The Minecraft 'mods' folder is not configured. Please define it in 'Settings' to install the content pack.",
                "fr": "Le dossier 'mods' de Minecraft n'est pas configuré. Veuillez le définir dans 'Paramètres' pour installer le pack de contenu."
            }
        }

        self.current_language = "en" # Default language
        self.current_theme = "Default" # Default theme

        self.themes = {
            "Default": {
                "main_window": "QMainWindow { background-color: #ECF0F1; }",
                "tabs": "QTabWidget::pane { border: 1px solid #CCC; background: #EEE; }"
                        "QTabBar::tab { background: #DDD; border: 1px solid #CCC; border-bottom-color: #EEE; "
                        "border-top-left-radius: 4px; border-top-right-radius: 4px; padding: 8px 15px; }"
                        "QTabBar::tab:selected { background: #FFF; border-color: #999; border-bottom-color: #FFF; }"
                        "QTabWidget::tab-bar { alignment: center; }",
                "header_label": "font-size: 24px; font-weight: bold; padding: 20px; color: #E74C3C;",
                "section_label": "font-size: 18px; font-weight: bold; margin-top: 10px; color: #2C3E50;",
                "status_bar": "font-size: 10px; padding: 5px; color: #555;",
                "map_widget": "background-color: #F8F8F8; border: 1px solid #DDD; border-radius: 5px; padding: 10px; margin-bottom: 5px;",
                "download_button": "background-color: #2ECC71; color: white; border-radius: 5px; padding: 5px;",
                "publish_button": "background-color: #3498DB; color: white; border-radius: 5px; padding: 10px;",
                "delete_button": "background-color: #C0392B; color: white; border-radius: 5px; padding: 10px;",
                "refresh_button": "background-color: #5DADE2; color: white; border-radius: 5px; padding: 10px;",
                "label_text_color": "color: #333;" # General text color for labels
            },
            "Dark": {
                "main_window": "QMainWindow { background-color: #2C3E50; color: #ECF0F1; }",
                "tabs": "QTabWidget::pane { border: 1px solid #555; background: #34495E; }"
                        "QTabBar::tab { background: #4F627A; border: 1px solid #555; border-bottom-color: #34495E; "
                        "border-top-left-radius: 4px; border-top-right-radius: 4px; padding: 8px 15px; color: #ECF0F1; }"
                        "QTabBar::tab:selected { background: #2C3E50; border-color: #777; border-bottom-color: #2C3E50; color: #ECF0F1; }"
                        "QTabWidget::tab-bar { alignment: center; }",
                "header_label": "font-size: 24px; font-weight: bold; padding: 20px; color: #E74C3C;", # Red stays
                "section_label": "font-size: 18px; font-weight: bold; margin-top: 10px; color: #ECF0F1;",
                "status_bar": "font-size: 10px; padding: 5px; color: #BDC3C7;",
                "map_widget": "background-color: #34495E; border: 1px solid #555; border-radius: 5px; padding: 10px; margin-bottom: 5px; color: #ECF0F1;",
                "download_button": "background-color: #27AE60; color: white; border-radius: 5px; padding: 5px;",
                "publish_button": "background-color: #2980B9; color: white; border-radius: 5px; padding: 10px;",
                "delete_button": "background-color: #A03422; color: white; border-radius: 5px; padding: 10px;",
                "refresh_button": "background-color: #4A90D9; color: white; border-radius: 5px; padding: 10px;",
                "label_text_color": "color: #ECF0F1;" # General text color for labels
            }
        }
        
        # Mapping of widget attributes to their text keys for dynamic language updates
        self.translatable_widgets = {}

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
        self.header_label = QLabel("", self) # Text will be set by set_language
        self.header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.main_layout.addWidget(self.header_label)
        self.translatable_widgets[self.header_label] = "Welcome to the ZombieRool Launcher!"

        # --- Tabs ---
        self.tabs = QTabWidget()
        self.main_layout.addWidget(self.tabs)

        # "Update" tab
        self.update_tab = QWidget()
        self.tabs.addTab(self.update_tab, "") # Text will be set by set_language
        self.translatable_widgets[self.tabs] = {0: "Update"} # Special handling for tab titles
        self.setup_update_tab()

        # "Map Download" tab
        self.download_tab = QWidget()
        self.tabs.addTab(self.download_tab, "") # Text will be set by set_language
        self.translatable_widgets[self.tabs][1] = "Map Download"
        self.setup_download_tab()

        # "Access Code" tab - NEW TAB
        self.code_download_tab = QWidget() # Renamed from modpack_tab
        self.tabs.addTab(self.code_download_tab, "") # Text will be set by set_language
        self.translatable_widgets[self.tabs][2] = "Access Code" # Updated tab title
        self.setup_code_download_tab() # Renamed setup function
        
        # "Upload Map" tab
        self.upload_map_tab = QWidget()
        self.tabs.addTab(self.upload_map_tab, "") # Text will be set by set_language
        self.translatable_widgets[self.tabs][3] = "Upload Map"
        self.setup_upload_map_tab()

        # "Settings" tab
        self.settings_tab = QWidget()
        self.tabs.addTab(self.settings_tab, "") # Text will be set by set_language
        self.translatable_widgets[self.tabs][4] = "Settings"
        self.setup_settings_tab()

        # --- Footer (Status Bar / Launcher Version) ---
        self.status_bar = QLabel(f"", self) # Text will be set by set_language
        self.status_bar.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.main_layout.addWidget(self.status_bar)
        self.translatable_widgets[self.status_bar] = "Launcher Version:"

        # --- Initialization and startup checks ---
        self.load_settings() # Load saved language and theme first
        self.load_saved_minecraft_path() # Attempts to load the saved Minecraft path
        
        # Start checking for updates from GitHub immediately on launch.
        # This will trigger auto-update logic if a new launcher version is available.
        self.check_for_updates() 

        # Apply initial language and theme based on loaded settings
        self.apply_language(self.current_language)
        self.apply_theme(self.current_theme)


    def _(self, key, *args, **kwargs):
        """Helper to get translated text with optional formatting."""
        translation_dict = self.translations.get(key, {})
        text = translation_dict.get(self.current_language, key) # Fallback to key if no translation
        
        # Format string if args/kwargs are provided
        if args or kwargs:
            try:
                # Ensure all kwargs are passed directly for formatting
                text = text.format(*args, **kwargs)
            except KeyError as e:
                print(f"Translation formatting error: Missing key {e} in string '{text}' for language '{self.current_language}'")
            except IndexError as e:
                print(f"Translation formatting error: Index error in string '{text}' for language '{self.current_language}' with args {args}")
        return text

    def apply_language(self, lang_code):
        """Applies the selected language to all translatable UI elements."""
        self.current_language = lang_code
        # Update main window title
        self.setWindowTitle(f"{self._('ZombieRool Launcher - v')}{__version__}") # Special handling for title

        # Update all tracked widgets
        for widget, text_key in self.translatable_widgets.items():
            if isinstance(widget, QTabWidget):
                # Special handling for tab titles
                # The text_key for QTabWidget is a dictionary {index: "Text Key"}
                for index, tab_key in text_key.items():
                    widget.setTabText(index, self._(tab_key))
            elif isinstance(widget, QLabel):
                if text_key == "Launcher Version:": # Special case for status bar
                    widget.setText(f"{self._(text_key)} {__version__}")
                else:
                    widget.setText(self._(text_key))
            elif isinstance(widget, QPushButton):
                widget.setText(self._(text_key))
            # QLineEdit placeholders are updated separately below as they are not directly in translatable_widgets
        
        # Re-apply placeholder texts as they are not directly in translatable_widgets
        self.map_search_input.setPlaceholderText(self._("Search maps by name or description..."))
        self.github_token_input.setPlaceholderText(self._("Enter your GitHub token (not saved!)"))
        self.upload_map_id_input.setPlaceholderText(self._("Enter a unique ID for the map (e.g., 'my-awesome-map')"))
        self.upload_map_name_input.setPlaceholderText(self._("Enter the map's display name (e.g., 'The Asylum Map')"))
        self.upload_map_version_input.setPlaceholderText(self._("Enter the map's version (e.g., '1.0.0')"))
        self.upload_map_description_input.setPlaceholderText(self._("Enter a brief description for the map."))
        self.mc_path_input.setPlaceholderText(self._("Click 'Browse...' to choose your Minecraft folder"))
        self.delete_map_id_input.setPlaceholderText(self._("Enter the ID of the map to delete (e.g., 'old-map-id')"))
        self.content_code_input.setPlaceholderText(self._("Enter the secret code for the content pack")) # Updated placeholder text

        # Reload maps to show translated "Install Map" button if needed (or if filter changed text)
        self._load_maps_for_download_logic()
        
        # Save the new language preference
        config = load_config()
        config['language'] = lang_code
        save_config(config)

    def apply_theme(self, theme_name):
        """Applies the selected theme's stylesheets to UI elements."""
        self.current_theme = theme_name
        theme_styles = self.themes.get(theme_name, self.themes["Default"])

        # Apply main window style
        self.setStyleSheet(theme_styles["main_window"])

        # Apply tab style
        self.tabs.setStyleSheet(theme_styles["tabs"])

        # Apply specific widget styles
        self.header_label.setStyleSheet(theme_styles["header_label"])

        # Apply to section labels
        for label in [self.mod_section_label, self.launcher_section_label, 
                      self.maps_label, self.upload_label, self.delete_label, 
                      self.settings_label, self.code_download_label]: # Updated label name
            label.setStyleSheet(theme_styles["section_label"])
            
        self.status_bar.setStyleSheet(theme_styles["status_bar"])

        # Apply to map widgets (requires re-creating them or iterating through existing)
        # For simplicity, we'll just re-load maps which rebuilds the widgets with new style
        self._load_maps_for_download_logic() # Rebuilds map widgets with current theme

        # Apply to specific buttons
        self.update_mod_button.setStyleSheet(theme_styles["download_button"]) # Reusing download button style for mod update
        self.update_launcher_button.setStyleSheet(theme_styles["download_button"]) # Reusing download button style for launcher update
        self.publish_map_button.setStyleSheet(theme_styles["publish_button"])
        self.delete_map_button.setStyleSheet(theme_styles["delete_button"])
        self.refresh_maps_button.setStyleSheet(theme_styles["refresh_button"])
        self.download_content_button.setStyleSheet(theme_styles["download_button"]) # Updated button name

        # Update general label text color for dynamic labels (like status)
        # This needs to be applied to labels not covered by specific styles
        for label in [self.mod_status_label, self.launcher_status_label, 
                      self.upload_status_label, self.delete_status_label,
                      self.mc_path_label, self.mods_path_label, self.saves_path_label, 
                      self.resourcepacks_path_label, self.language_label, 
                      self.theme_label, self.code_download_status_label]: # Updated status label name
            label.setStyleSheet(theme_styles["label_text_color"])

        # Save the new theme preference
        config = load_config()
        config['theme'] = theme_name
        save_config(config)

    def load_settings(self):
        """Loads saved language and theme from configuration."""
        config = load_config()
        self.current_language = config.get('language', 'en')
        self.current_theme = config.get('theme', 'Default')


    # --- Tab Configuration ---
    def setup_update_tab(self):
        """Configures the 'Update' tab interface."""
        layout = QVBoxLayout(self.update_tab)
        layout.setContentsMargins(20, 20, 20, 20) # Inner margins

        # Section for the Mod
        self.mod_section_label = QLabel("", self) # Text set by apply_language
        layout.addWidget(self.mod_section_label)
        self.translatable_widgets[self.mod_section_label] = "ZombieRool Mod Updates"

        self.mod_status_label = QLabel("", self) # Text set by apply_language
        layout.addWidget(self.mod_status_label)
        self.translatable_widgets[self.mod_status_label] = "Mod Status: Checking..."

        self.mod_progress_bar = QProgressBar(self)
        self.mod_progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mod_progress_bar.hide() # Hidden by default
        layout.addWidget(self.mod_progress_bar)

        self.update_mod_button = QPushButton("", self) # Text set by apply_language
        self.update_mod_button.clicked.connect(self.update_mod) 
        self.update_mod_button.setEnabled(False) # Disabled by default, enabled if update is available
        layout.addWidget(self.update_mod_button)
        self.translatable_widgets[self.update_mod_button] = "Update Mod"
        layout.addSpacing(20) # Spacing

        # Section for the Launcher (for self-update)
        self.launcher_section_label = QLabel("", self) # Text set by apply_language
        layout.addWidget(self.launcher_section_label)
        self.translatable_widgets[self.launcher_section_label] = "Launcher Updates"

        self.launcher_status_label = QLabel("", self) # Text set by apply_language
        layout.addWidget(self.launcher_status_label)
        self.translatable_widgets[self.launcher_status_label] = "Launcher Status: Checking..."
        
        self.launcher_progress_bar = QProgressBar(self)
        self.launcher_progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.launcher_progress_bar.hide() # Hidden by default
        layout.addWidget(self.launcher_progress_bar)

        self.update_launcher_button = QPushButton("", self) # Text set by apply_language
        self.update_launcher_button.clicked.connect(self.update_launcher)
        self.update_launcher_button.setEnabled(False) # Disabled by default
        layout.addWidget(self.update_launcher_button)
        self.translatable_widgets[self.update_launcher_button] = "Update Launcher"
        layout.addStretch() # Pushes elements to the top

    def setup_download_tab(self):
        """Configures the 'Map Download' tab interface."""
        layout = QVBoxLayout(self.download_tab)
        layout.setContentsMargins(20, 20, 20, 20)

        self.maps_label = QLabel("", self) # Text set by apply_language
        layout.addWidget(self.maps_label)
        self.translatable_widgets[self.maps_label] = "Download and Install Maps"

        # Search Bar for Maps
        self.map_search_input = QLineEdit()
        # Placeholder text set by apply_language initially
        self.map_search_input.textChanged.connect(self._filter_maps_display)
        layout.addWidget(self.map_search_input)

        # Scroll Area for Maps
        self.maps_scroll_area = QScrollArea()
        self.maps_scroll_area.setWidgetResizable(True)
        self.maps_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff) # Disable horizontal scrollbar

        self.maps_container_widget = QWidget()
        self.maps_container_layout = QVBoxLayout(self.maps_container_widget)
        self.maps_container_layout.setAlignment(Qt.AlignmentFlag.AlignTop) # Align content to top

        self.maps_scroll_area.setWidget(self.maps_container_widget)
        layout.addWidget(self.maps_scroll_area)

        # Refresh button for maps catalog
        self.refresh_maps_button = QPushButton("") # Text set by apply_language
        self.refresh_maps_button.clicked.connect(lambda: self.check_for_updates(cache_bust=True))
        layout.addWidget(self.refresh_maps_button)
        self.translatable_widgets[self.refresh_maps_button] = "Refresh Map Catalog"


        layout.addStretch()

    def setup_code_download_tab(self): # Renamed from setup_modpack_tab
        """Configures the 'Access Code' tab interface.""" # Updated description
        layout = QVBoxLayout(self.code_download_tab) # Updated tab variable
        layout.setContentsMargins(20, 20, 20, 20)

        self.code_download_label = QLabel("", self) # Updated label name
        layout.addWidget(self.code_download_label)
        self.translatable_widgets[self.code_download_label] = "Enter Content Code:" # Updated text

        # Content Code Input
        code_layout = QHBoxLayout()
        code_label = QLabel("") # Text set by apply_language
        code_layout.addWidget(code_label)
        self.translatable_widgets[code_label] = "Enter Content Code:" # Updated text

        self.content_code_input = QLineEdit() # Renamed from modpack_code_input
        # Placeholder text set by apply_language initially
        self.content_code_input.setEchoMode(QLineEdit.EchoMode.Password) # Hide input
        code_layout.addWidget(self.content_code_input)
        layout.addLayout(code_layout)

        # Download Content Button
        self.download_content_button = QPushButton("") # Renamed from download_modpack_button
        self.download_content_button.clicked.connect(self.download_content_by_code) # Renamed function
        layout.addWidget(self.download_content_button)
        self.translatable_widgets[self.download_content_button] = "Download Content Pack" # Updated text

        # Status Label for Content Tab
        self.code_download_status_label = QLabel("", self) # Renamed from modpack_status_label
        self.code_download_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.code_download_status_label)
        self.translatable_widgets[self.code_download_status_label] = "Content Status: Waiting for code..." # Updated text

        self.content_progress_bar = QProgressBar(self) # Renamed from modpack_progress_bar
        self.content_progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_progress_bar.hide() # Hidden by default
        layout.addWidget(self.content_progress_bar)

        layout.addStretch()

    def download_content_by_code(self): # Renamed from download_modpack_by_code
        """
        Handles the download of a content pack based on a secret code.
        """
        content_code = self.content_code_input.text().strip() # Renamed variable
        if not content_code:
            self.code_download_status_label.setText(self._("Please enter the secret code for the content pack")) # Updated text
            return

        # Ensure Minecraft mods folder is configured
        if not self.minecraft_paths or not self.minecraft_paths.get('mods'):
            QMessageBox.warning(self, self._("Installation Error"), self._("The Minecraft 'mods' folder is not configured. Please define it in 'Settings' to install the content pack.")) # Updated text
            self.code_download_status_label.setText(self._("Minecraft 'mods' folder is not configured. Please define it in 'Settings'.")) # Updated text
            return

        if not self.remote_updates_data or "content_packs" not in self.remote_updates_data: # Renamed data key
            self.code_download_status_label.setText(self._("Content information not found for code '{code}'.").format(code=content_code)) # Updated text
            QMessageBox.warning(self, self._("Content Download Error"), self._("Content information not found for code '{code}'.").format(code=content_code)) # Updated text
            return

        found_content_pack = None
        for cp_info in self.remote_updates_data["content_packs"]: # Renamed data key
            if cp_info.get("code") == content_code:
                found_content_pack = cp_info
                break

        if not found_content_pack:
            self.code_download_status_label.setText(self._("Invalid Code.")) # Updated text
            QMessageBox.warning(self, self._("Content Download Error"), self._("Invalid Code.")) # Updated text
            return

        content_pack_download_url = found_content_pack.get("download_url")
        if not content_pack_download_url:
            self.code_download_status_label.setText(self._("Content download URL not found for '{name}'.").format(name=found_content_pack.get('name', 'N/A'))) # Updated text
            QMessageBox.warning(self, self._("Content Download Error"), self._("Content download URL not found for '{name}'.").format(name=found_content_pack.get('name', 'N/A'))) # Updated text
            return

        temp_download_dir = os.path.join(os.getcwd(), "temp_downloads")
        os.makedirs(temp_download_dir, exist_ok=True)
        content_pack_filename = os.path.basename(QUrl(content_pack_download_url).path())
        temp_content_pack_path = os.path.join(temp_download_dir, content_pack_filename)

        QMessageBox.information(self, self._("Download Content Pack"), self._("Downloading Content Pack '{name}' (v{version})...").format(name=found_content_pack['name'], version=found_content_pack['version'])) # Updated text and keys
        
        self.content_progress_bar.setValue(0) # Updated progress bar name
        self.content_progress_bar.show() # Updated progress bar name
        self.download_content_button.setEnabled(False) # Updated button name
        self.code_download_status_label.setText(self._("Downloading Content Pack '{name}'...").format(name=found_content_pack['name'])) # Updated text

        self.content_downloader = FileDownloaderThread(content_pack_download_url, temp_content_pack_path) # Renamed downloader
        self.content_downloader.download_progress.connect(self.content_progress_bar.setValue) # Updated progress bar name
        self.content_downloader.download_finished.connect(
            lambda path=temp_content_pack_path, content_pack_name=found_content_pack['name']: self._install_content_from_temp(path, content_pack_name) # Renamed function
        )
        self.content_downloader.download_error.connect(lambda msg: self._handle_content_download_error(msg)) # Renamed function
        self.content_downloader.start()


    def _install_content_from_temp(self, temp_content_pack_path, content_pack_name): # Renamed from _install_modpack_from_temp
        """
        Decompresses and installs the content pack into the Minecraft mods folder.
        """
        self.content_progress_bar.hide() # Updated progress bar name
        self.code_download_status_label.setText(self._("Content Pack '{name}' downloaded. Installing...").format(name=content_pack_name)) # Updated text
        QMessageBox.information(self, self._("Content Installation"), self._("Content Pack '{name}' downloaded. Installing...").format(name=content_pack_name)) # Updated text
        
        try:
            mods_dir = self.minecraft_paths['mods']
            
            with zipfile.ZipFile(temp_content_pack_path, 'r') as zip_ref:
                # Extract all contents of the content pack directly into the mods folder
                zip_ref.extractall(mods_dir)

            QMessageBox.information(self, self._("Installation Complete"), self._("Content Pack installed successfully!")) # Updated text
            self.code_download_status_label.setText(self._("Content Pack installed successfully!")) # Updated text
        except zipfile.BadZipFile:
            QMessageBox.critical(self, self._("Decompression Error"), self._("The content pack ZIP file is corrupted or invalid. The 'zipfile' module only supports ZIP format (not RAR).")) # Updated text
            self.code_download_status_label.setText(self._("Decompression Error: Corrupted ZIP.")) # Updated text
        except Exception as e:
            QMessageBox.critical(self, self._("Content Installation Error"), self._("An error occurred during content installation: {e}").format(e=e)) # Updated text
            self.code_download_status_label.setText(self._("Content Installation Error: {e}").format(e=e)) # Updated text
        finally:
            self.download_content_button.setEnabled(True) # Updated button name
            if os.path.exists(temp_content_pack_path):
                os.remove(temp_content_pack_path)
            if os.path.exists(os.path.dirname(temp_content_pack_path)) and not os.listdir(os.path.dirname(temp_content_pack_path)):
                shutil.rmtree(os.path.dirname(temp_content_pack_path))
        
        # After content pack installation, re-check mod status as mods might have changed
        self.mod_status_label.setText(self._("Mod Status: Checking...")) 
        self._check_mod_update_logic()

    def _handle_content_download_error(self, message): # Renamed from _handle_modpack_download_error
        """Handles content pack download errors."""
        self.content_progress_bar.hide() # Updated progress bar name
        QMessageBox.critical(self, self._("Content Download Error"), message) # Updated text
        self.code_download_status_label.setText(self._("Download failed: {message}").format(message=message)) # Updated text
        self.download_content_button.setEnabled(True) # Updated button name

    def setup_upload_map_tab(self):
        """Configures the 'Upload Map' tab interface."""
        layout = QVBoxLayout(self.upload_map_tab)
        layout.setContentsMargins(20, 20, 20, 20)

        # --- Publish Map Section ---
        self.upload_label = QLabel("", self) # Text set by apply_language
        layout.addWidget(self.upload_label)
        self.translatable_widgets[self.upload_label] = "Publish Map to GitHub"


        # GitHub Token Input
        token_layout = QHBoxLayout()
        token_label = QLabel("") # Text set by apply_language
        token_layout.addWidget(token_label)
        self.translatable_widgets[token_label] = "GitHub Personal Access Token:"

        self.github_token_input = QLineEdit()
        self.github_token_input.setEchoMode(QLineEdit.EchoMode.Password) # Hide token input
        # Placeholder text set by apply_language initially
        token_layout.addWidget(self.github_token_input)
        layout.addLayout(token_layout)

        layout.addSpacing(10)

        # Map ID Input
        map_id_layout = QHBoxLayout()
        map_id_label = QLabel("") # Text set by apply_language
        map_id_layout.addWidget(map_id_label)
        self.translatable_widgets[map_id_label] = "Map ID (unique, e.g., 'winter'):"

        self.upload_map_id_input = QLineEdit()
        # Placeholder text set by apply_language initially
        map_id_layout.addWidget(self.upload_map_id_input)
        layout.addLayout(map_id_layout)

        # Map Name Input
        map_name_layout = QHBoxLayout()
        map_name_label = QLabel("") # Text set by apply_language
        map_name_layout.addWidget(map_name_label)
        self.translatable_widgets[map_name_label] = "Map Name (displayed):"

        self.upload_map_name_input = QLineEdit()
        # Placeholder text set by apply_language initially
        map_name_layout.addWidget(self.upload_map_name_input)
        layout.addLayout(map_name_layout)

        # Map Version Input
        map_version_layout = QHBoxLayout()
        map_version_label = QLabel("") # Text set by apply_language
        map_version_layout.addWidget(map_version_label)
        self.translatable_widgets[map_version_label] = "Map Version:"

        self.upload_map_version_input = QLineEdit()
        # Placeholder text set by apply_language initially
        map_version_layout.addWidget(self.upload_map_version_input)
        layout.addLayout(map_version_layout)

        # Map Description Input
        map_description_layout = QVBoxLayout()
        map_description_label = QLabel("") # Text set by apply_language
        map_description_layout.addWidget(map_description_label)
        self.translatable_widgets[map_description_label] = "Map Description:"

        self.upload_map_description_input = QTextEdit()
        # Placeholder text set by apply_language initially
        self.upload_map_description_input.setFixedSize(self.width() - 80, 80) # Fixed size for description
        map_description_layout.addWidget(self.upload_map_description_input)
        layout.addLayout(map_description_layout)

        layout.addSpacing(10)

        # Map File Selection
        map_file_layout = QHBoxLayout()
        map_file_label = QLabel("") # Text set by apply_language
        map_file_layout.addWidget(map_file_label)
        self.translatable_widgets[map_file_label] = "Select Map ZIP file:"

        self.upload_map_file_path = QLineEdit()
        self.upload_map_file_path.setReadOnly(True)
        map_file_layout.addWidget(self.upload_map_file_path)
        
        self.browse_map_file_button = QPushButton("") # Text set by apply_language
        self.browse_map_file_button.clicked.connect(self.select_map_zip_file)
        map_file_layout.addWidget(self.browse_map_file_button)
        self.translatable_widgets[self.browse_map_file_button] = "Browse..."
        layout.addLayout(map_file_layout)

        # Resource Pack Checkbox
        self.has_rp_checkbox = QCheckBox("") # Text set by apply_language
        self.has_rp_checkbox.stateChanged.connect(self._toggle_rp_selection)
        layout.addWidget(self.has_rp_checkbox)
        self.translatable_widgets[self.has_rp_checkbox] = "Associated Resource Pack?"

        # Resource Pack File Selection (initially hidden)
        self.rp_file_layout = QHBoxLayout()
        rp_file_label = QLabel("") # Text set by apply_language
        self.rp_file_layout.addWidget(rp_file_label)
        self.translatable_widgets[rp_file_label] = "Select Resource Pack ZIP file:"

        self.upload_rp_file_path = QLineEdit()
        self.upload_rp_file_path.setReadOnly(True)
        self.rp_file_layout.addWidget(self.upload_rp_file_path)
        
        self.browse_rp_file_button = QPushButton("") # Text set by apply_language
        self.browse_rp_file_button.clicked.connect(self.select_rp_zip_file)
        self.rp_file_layout.addWidget(self.browse_rp_file_button)
        self.translatable_widgets[self.browse_rp_file_button] = "Browse..."
        
        # Add layout, but hide it initially
        layout.addLayout(self.rp_file_layout)
        self._toggle_rp_selection() # Initial state set

        layout.addSpacing(20)

        # Upload Button
        self.publish_map_button = QPushButton("") # Text set by apply_language
        self.publish_map_button.clicked.connect(self.publish_map_to_github)
        layout.addWidget(self.publish_map_button)
        self.translatable_widgets[self.publish_map_button] = "Publish Map to GitHub"


        # Status Label for Upload Tab
        self.upload_status_label = QLabel("", self)
        self.upload_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.upload_status_label)

        layout.addStretch() # Push publish section to top

        # --- Delete Map Section ---
        layout.addSpacing(30)
        self.delete_label = QLabel("", self) # Text set by apply_language
        layout.addWidget(self.delete_label)
        self.translatable_widgets[self.delete_label] = "Delete Map from GitHub"

        delete_map_id_layout = QHBoxLayout()
        delete_map_id_label = QLabel("") # Text set by apply_language
        delete_map_id_layout.addWidget(delete_map_id_label)
        self.translatable_widgets[delete_map_id_label] = "Map ID to Delete:"

        self.delete_map_id_input = QLineEdit()
        # Placeholder text set by apply_language initially
        delete_map_id_layout.addWidget(self.delete_map_id_input)
        layout.addLayout(delete_map_id_layout)

        self.delete_map_button = QPushButton("") # Text set by apply_language
        self.delete_map_button.clicked.connect(self.delete_selected_map)
        layout.addWidget(self.delete_map_button)
        self.translatable_widgets[self.delete_map_button] = "Delete Map from Catalog"


        self.delete_status_label = QLabel("", self)
        self.delete_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.delete_status_label)
        
        layout.addStretch() # Push delete section to top as well within its own stretch

    def _toggle_rp_selection(self):
        """Toggles the visibility of the resource pack file selection based on checkbox state."""
        is_checked = self.has_rp_checkbox.isChecked()
        for i in range(self.rp_file_layout.count()):
            widget_item = self.rp_file_layout.itemAt(i)
            if widget_item.widget():
                widget_item.widget().setVisible(is_checked)
            elif widget_item.layout(): # Handle nested layouts if any
                # For a QHBoxLayout, it seems setting visibility of widgets is enough,
                # but if there were nested layouts, we'd enable/disable them.
                # For simplicity here, just setting widget visibility should work.
                pass 


    def select_map_zip_file(self):
        """Opens a file dialog to select the map ZIP file."""
        file_path, _ = QFileDialog.getOpenFileName(self, self._("Select Map ZIP File:"), "", self._("ZIP Files (*.zip)"))
        if file_path:
            self.upload_map_file_path.setText(file_path)

    def select_rp_zip_file(self):
        """Opens a file dialog to select the resource pack ZIP file."""
        file_path, _ = QFileDialog.getOpenFileName(self, self._("Select Resource Pack ZIP file:"), "", self._("ZIP Files (*.zip)"))
        if file_path:
            self.upload_rp_file_path.setText(file_path)

    def publish_map_to_github(self):
        """
        Initiates the GitHub upload process for the map and associated resource pack.
        """
        github_token = self.github_token_input.text().strip()
        map_id = self.upload_map_id_input.text().strip()
        map_name = self.upload_map_name_input.text().strip()
        map_version = self.upload_map_version_input.text().strip()
        map_description = self.upload_map_description_input.toPlainText().strip()
        map_zip_path = self.upload_map_file_path.text().strip()
        has_rp = self.has_rp_checkbox.isChecked()
        rp_zip_path = self.upload_rp_file_path.text().strip() if has_rp else ""

        # Basic validation
        if not github_token:
            QMessageBox.warning(self, self._("Missing Information"), self._("Please enter your GitHub Personal Access Token."))
            self.upload_status_label.setText(self._("Publication failed: Missing GitHub token."))
            return
        if not map_id or not map_name or not map_version or not map_zip_path:
            QMessageBox.warning(self, self._("Missing Information"), self._("Please fill in Map ID, Map Name, Map Version, and select the Map ZIP file."))
            self.upload_status_label.setText(self._("Publication failed: Missing map information."))
            return
        if not os.path.exists(map_zip_path):
            QMessageBox.warning(self, self._("File Not Found"), self._("Map ZIP file not found at: {map_zip_path}").format(map_zip_path=map_zip_path))
            self.upload_status_label.setText(self._("Publication failed: Map ZIP not found."))
            return
        if has_rp and (not rp_zip_path or not os.path.exists(rp_zip_path)):
            QMessageBox.warning(self, self._("Missing Resource Pack File"), self._("You checked 'Associated Resource Pack' but did not select a valid RP ZIP file or it does not exist."))
            self.upload_status_label.setText(self._("Publication failed: RP ZIP not found."))
            return

        # Disable button during upload
        self.publish_map_button.setEnabled(False)
        self.upload_status_label.setText(self._("Initiating GitHub upload..."))

        map_info = {
            "id": map_id,
            "name": map_name,
            "latest_version": map_version,
            "description": map_description
        }

        # Start GitHub upload in a separate thread
        self.uploader_thread = GitHubUploaderThread(github_token, map_info, map_zip_path, rp_zip_path)
        self.uploader_thread.upload_progress.connect(self.upload_status_label.setText)
        self.uploader_thread.upload_finished.connect(self._handle_upload_finished)
        self.uploader_thread.upload_error.connect(self._handle_upload_error)
        self.uploader_thread.start()

    def _handle_upload_finished(self, map_info):
        """Handles successful map upload."""
        self.publish_map_button.setEnabled(True)
        self.upload_status_label.setText(self._("Publication complete! Check GitHub."))
        QMessageBox.information(self, self._("Publication Success"), 
                                self._("Map '{map_name}' (v{map_version}) has been successfully published to GitHub and updates.json has been updated!").format(map_name=map_info['name'], map_version=map_info['latest_version']))
        
        # Clear fields after successful upload, but keep token for convenience
        self.upload_map_id_input.clear()
        self.upload_map_name_input.clear()
        self.upload_map_version_input.clear()
        self.upload_map_description_input.clear()
        self.upload_map_file_path.clear()
        self.upload_rp_file_path.clear()
        self.has_rp_checkbox.setChecked(False) # Resets checkbox and hides RP fields

        # Force refresh of map list in download tab with cache bust
        self.check_for_updates(cache_bust=True) 

    def _handle_upload_error(self, message):
        """Handles errors during map upload."""
        self.publish_map_button.setEnabled(True)
        self.upload_status_label.setText(self._("Publication failed: {message}").format(message=message))
        QMessageBox.critical(self, self._("Publication Error"), message)

    def delete_selected_map(self):
        """
        Initiates the GitHub deletion process for a map from the catalog.
        """
        github_token = self.github_token_input.text().strip()
        map_id_to_delete = self.delete_map_id_input.text().strip()

        if not github_token:
            QMessageBox.warning(self, self._("Missing Information"), self._("Please enter your GitHub Personal Access Token."))
            self.delete_status_label.setText(self._("Deletion failed: Missing GitHub token."))
            return
        if not map_id_to_delete:
            QMessageBox.warning(self, self._("Missing Information"), self._("Please enter the Map ID to delete."))
            self.delete_status_label.setText(self._("Deletion failed: Missing map ID."))
            return

        reply = QMessageBox.question(self, self._('Confirm Deletion'), 
                                    self._("Are you sure you want to delete ALL releases and the entry for map ID '{map_id_to_delete}' from GitHub?\\nThis action cannot be undone!").format(map_id_to_delete=map_id_to_delete),
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.No:
            self.delete_status_label.setText(self._("Deletion canceled."))
            return

        self.delete_map_button.setEnabled(False)
        self.delete_status_label.setText(self._("Initiating deletion for map ID '{map_id_to_delete}'...").format(map_id_to_delete=map_id_to_delete))

        # Start GitHub deletion in a separate thread
        self.deleter_thread = GitHubDeleterThread(github_token, map_id_to_delete)
        self.deleter_thread.deletion_progress.connect(self.delete_status_label.setText)
        self.deleter_thread.deletion_finished.connect(self._handle_deletion_finished)
        self.deleter_thread.deletion_error.connect(self._handle_deletion_error)
        self.deleter_thread.start()

    def _handle_deletion_finished(self, map_id):
        """Handles successful map deletion."""
        self.delete_map_button.setEnabled(True)
        self.delete_status_label.setText(self._("Deletion complete for map ID '{map_id}'.").format(map_id=map_id))
        QMessageBox.information(self, self._("Deletion Success"), 
                                self._("Map ID '{map_id}' and its associated GitHub releases have been successfully deleted, and updates.json has been updated!").format(map_id=map_id))
        self.delete_map_id_input.clear()
        # Force refresh of map list in download tab with cache bust
        self.check_for_updates(cache_bust=True) 

    def _handle_deletion_error(self, message):
        """Handles errors during map deletion."""
        self.delete_map_button.setEnabled(True)
        self.delete_status_label.setText(self._("Deletion failed: {message}").format(message=message))
        QMessageBox.critical(self, self._("Deletion Error"), message)

    def setup_settings_tab(self):
        """Configures the 'Settings' tab interface."""
        layout = QVBoxLayout(self.settings_tab)
        layout.setContentsMargins(20, 20, 20, 20)

        self.settings_label = QLabel("", self) # Text set by apply_language
        layout.addWidget(self.settings_label)
        self.translatable_widgets[self.settings_label] = "Minecraft Settings and Paths"

        # Section for the Minecraft instance path
        mc_path_h_layout = QHBoxLayout()
        self.mc_path_label = QLabel("", self) # Text set by apply_language
        mc_path_h_layout.addWidget(self.mc_path_label)
        self.translatable_widgets[self.mc_path_label] = " .minecraft or Instance Folder :"
        
        self.mc_path_input = QLineEdit()
        # Placeholder text set by apply_language initially
        self.mc_path_input.setReadOnly(True) # Read-only, user uses the button
        mc_path_h_layout.addWidget(self.mc_path_input)
        
        self.browse_mc_path_button = QPushButton("") # Text set by apply_language
        self.browse_mc_path_button.clicked.connect(self.browse_minecraft_path)
        mc_path_h_layout.addWidget(self.browse_mc_path_button)
        self.translatable_widgets[self.browse_mc_path_button] = "Browse..."
        layout.addLayout(mc_path_h_layout)

        self.mods_path_label = QLabel("", self) # Text set by apply_language
        self.saves_path_label = QLabel("", self) # Text set by apply_language
        self.resourcepacks_path_label = QLabel("", self) # Text set by apply_language
        
        layout.addWidget(self.mods_path_label)
        layout.addWidget(self.saves_path_label)
        layout.addWidget(self.resourcepacks_path_label)
        self.translatable_widgets[self.mods_path_label] = "Mods Folder: Not Detected"
        self.translatable_widgets[self.saves_path_label] = "Saves Folder: Not Detected"
        self.translatable_widgets[self.resourcepacks_path_label] = "Resourcepacks Folder: Not Detected"

        layout.addSpacing(20)

        # Language Selection
        language_layout = QHBoxLayout()
        self.language_label = QLabel("", self) # Text set by apply_language
        language_layout.addWidget(self.language_label)
        self.translatable_widgets[self.language_label] = "Select Language:"

        self.language_combo = QComboBox(self)
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("Français", "fr")
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        language_layout.addWidget(self.language_combo)
        layout.addLayout(language_layout)

        # Theme Selection
        theme_layout = QHBoxLayout()
        self.theme_label = QLabel("", self) # Text set by apply_language
        theme_layout.addWidget(self.theme_label)
        self.translatable_widgets[self.theme_label] = "Select Theme:"

        self.theme_combo = QComboBox(self)
        self.theme_combo.addItem(self._("Default Theme"), "Default")
        self.theme_combo.addItem(self._("Dark Theme"), "Dark")
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_layout.addWidget(self.theme_combo)
        layout.addLayout(theme_layout)

        layout.addStretch()

    def _on_language_changed(self, index):
        lang_code = self.language_combo.itemData(index)
        self.apply_language(lang_code)

    def _on_theme_changed(self, index):
        theme_name = self.theme_combo.itemData(index)
        self.apply_theme(theme_name)


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
                 QMessageBox.warning(self, self._("Invalid Minecraft Path"),
                                    self._("The saved Minecraft path is no longer valid or does not exist. Please reconfigure it in 'Settings'."))
                 self.mc_path_input.setText(self._("Path Not Configured"))
                 self.mods_path_label.setText(self._("Mods Folder: Not Configured"))
                 self.saves_path_label.setText(self._("Saves Folder: Not Configured"))
                 self.resourcepacks_path_label.setText(self._("Resourcepacks Folder: Not Configured"))
        else:
            # If no path is saved, display the initial invitation message
            QMessageBox.information(self, self._("Minecraft Path Configuration"),
                                    self._("Welcome! Please select your Minecraft instance folder (containing 'mods', 'saves', 'resourcepacks' folders) in 'Settings' by clicking 'Browse...'."))
            self.mc_path_input.setText(self._("Path Not Configured"))
            self.mods_path_label.setText(self._("Mods Folder: Not Configured"))
            self.saves_path_label.setText(self._("Saves Folder: Not Configured"))
            self.resourcepacks_path_label.setText(self._("Resourcepacks Folder: Not Configured"))

    def browse_minecraft_path(self):
        """
        Opens a dialog box to allow the user to manually select
        the folder of their Minecraft instance.
        """
        # Suggest the default path as a starting point for selection (if detected)
        initial_path = self.mc_path_input.text() if self.mc_path_input.text() and os.path.exists(self.mc_path_input.text()) else (get_default_minecraft_path() if get_default_minecraft_path() else os.path.expanduser('~'))
        
        selected_path = QFileDialog.getExistingDirectory(self, self._("Select Minecraft Instance Folder"), initial_path)
        if selected_path:
            self.set_minecraft_path(selected_path, show_message=True)
        else:
            QMessageBox.warning(self, self._("Selection Canceled"), self._("Minecraft folder selection canceled."))

    def set_minecraft_path(self, path, show_message=True):
        """
        Updates Minecraft paths in the UI and stores validated paths.
        Saves the path to the local configuration.
        """
        self.mc_path_input.setText(path)
        # get_minecraft_sub_paths now handles creating directories with exist_ok=True
        sub_paths = get_minecraft_sub_paths(path) 
        if sub_paths:
            self.minecraft_paths = sub_paths
            self.mods_path_label.setText(f"{self._('Mods Folder: ')}{self.minecraft_paths['mods']}")
            self.saves_path_label.setText(f"{self._('Saves Folder: ')}{self.minecraft_paths['saves']}")
            self.resourcepacks_path_label.setText(f"{self._('Resourcepacks Folder: ')}{self.minecraft_paths['resourcepacks']}")
            
            # Saves the validated path to the configuration file
            config = load_config()
            config['minecraft_path'] = path
            save_config(config)

            if show_message:
                QMessageBox.information(self, self._("Minecraft Path Configured"), 
                                        f"{self._('The Minecraft folder has been manually configured:')} {path}")
        else:
            self.minecraft_paths = None
            self.mods_path_label.setText(self._("Mods Folder: Not Detected"))
            self.saves_path_label.setText(self._("Saves Folder: Not Detected"))
            self.resourcepacks_path_label.setText(self._("Resourcepacks Folder: Not Detected"))
            
            # Removes the invalid path from the config if it existed
            config = load_config()
            if 'minecraft_path' in config:
                del config['minecraft_path']
                save_config(config)

            if show_message:
                QMessageBox.warning(self, self._("Invalid Path"), 
                                    self._("The selected path does not appear to be a valid Minecraft instance (mods, saves, resourcepacks folders not found)."))

    # --- Update Check and Processing Functions ---
    def check_for_updates(self, cache_bust=False):
        """
        Initiates the check for all updates by downloading updates.json
        from GitHub in a separate thread.
        """
        self.header_label.setText(self._("Checking for updates..."))
        self.mod_status_label.setText(self._("Mod Status: Checking..."))
        self.launcher_status_label.setText(self._("Launcher Status: Checking..."))
        
        # Disable buttons during check to prevent multiple clicks
        self.update_mod_button.setEnabled(False)
        self.update_launcher_button.setEnabled(False)
        self.download_content_button.setEnabled(False) # Updated button name
        # Disable map management buttons during update check
        self.publish_map_button.setEnabled(False)
        self.delete_map_button.setEnabled(False)
        self.refresh_maps_button.setEnabled(False) # Disable refresh button during check


        # Hide progress bars at the start of the check
        self.mod_progress_bar.hide()
        self.launcher_progress_bar.hide()
        self.content_progress_bar.hide() # Updated progress bar name

        # Create and start the update checker thread
        self.update_checker_thread = UpdateCheckerThread(UPDATES_JSON_URL, cache_bust=cache_bust)
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
            # Check for launcher update first and trigger it if available
            launcher_update_available = self._check_launcher_update_logic()
            
            # If a launcher update is available and triggered, the current instance will close.
            # So, we only proceed with other updates if no launcher update was triggered.
            if not launcher_update_available:
                self.header_label.setText(self._("ZombieRool Launcher - Updates Checked"))
                # Only show this if it's not a background refresh (e.g., from upload/delete)
                # This logic avoids redundant pop-ups during auto-refresh
                if not self.sender() or (isinstance(self.sender(), QThread) and self.sender() not in [getattr(self, 'uploader_thread', None), getattr(self, 'deleter_thread', None)]):
                    # No longer show general success message here for more seamless auto-update
                    pass 
                
                # Call specific update logic functions for mod and maps
                self._check_mod_update_logic()
                self._load_maps_for_download_logic() # This will now filter too
                
                # Re-enable buttons after update check
                self.publish_map_button.setEnabled(True)
                self.delete_map_button.setEnabled(True)
                self.refresh_maps_button.setEnabled(True) # Re-enable refresh button
                self.download_content_button.setEnabled(True) # Updated button name

        else:
            # This case should normally not be reached if error_occurred is well handled,
            # but it is a safety measure.
            QMessageBox.critical(self, self._("Error"), self._("Could not retrieve update information. Check the updates.json file URL or JSON structure."))
            self.header_label.setText(self._("ZombieRool Launcher - Update Error"))
            # Re-enable buttons even if there's an error if you want to allow another attempt
            self.update_mod_button.setEnabled(True)
            self.update_launcher_button.setEnabled(True)
            self.publish_map_button.setEnabled(True)
            self.delete_map_button.setEnabled(True)
            self.refresh_maps_button.setEnabled(True) # Re-enable refresh button
            self.download_content_button.setEnabled(True) # Updated button name


    def handle_update_error(self, message):
        """
        Handles displaying errors that occurred while retrieving updates.json.
        """
        QMessageBox.critical(self, self._("Update Error"), f"{self._('An error occurred while checking for updates:')} {message}")
        self.header_label.setText(self._("ZombieRool Launcher - Update Error"))
        # Re-enable buttons in case of an error so the user can retry manually
        self.update_mod_button.setEnabled(True)
        self.update_launcher_button.setEnabled(True)
        self.publish_map_button.setEnabled(True)
        self.delete_map_button.setEnabled(True)
        self.refresh_maps_button.setEnabled(True) # Re-enable refresh button
        self.download_content_button.setEnabled(True) # Updated button name
    
    # --- Launcher Update Logic (to be developed later) ---
    def _check_launcher_update_logic(self):
        """
        Compares the local launcher version with the remote version in remote_updates_data.
        If a new version is available, it triggers the update process.
        Returns True if an update was triggered, False otherwise.
        """
        if not self.remote_updates_data or "launcher" not in self.remote_updates_data:
            self.launcher_status_label.setText(self._("Launcher Status: Remote info not found."))
            return False

        launcher_info = self.remote_updates_data["launcher"]
        remote_version_str = launcher_info["latest_version"]
        local_version_str = __version__ # Launcher version defined globally
        
        try:
            # Use QVersionNumber for robust version comparison (e.g., 1.0.0 < 1.0.1)
            remote_version = QVersionNumber.fromString(remote_version_str)
            local_version = QVersionNumber.fromString(local_version_str)

            if remote_version > local_version:
                self.launcher_status_label.setText(self._("Launcher Status: New version {remote_version_str} available! (Current: {local_version_str})").format(remote_version_str=remote_version_str, local_version_str=local_version_str))
                self.update_launcher_button.setEnabled(True)
                
                # Trigger auto-update if a new version is found
                print("DEBUG: New launcher version detected. Triggering auto-update.")
                self.update_launcher(auto_trigger=True) # Pass a flag to differentiate auto-trigger
                return True # Indicate that an update was triggered
            else:
                self.launcher_status_label.setText(self._("Launcher Status: Up to date (v{local_version_str})").format(local_version_str=local_version_str))
                self.update_launcher_button.setEnabled(False) # No update needed
                return False
        except Exception as e:
            self.launcher_status_label.setText(self._("Launcher Status: Version error ({e})").format(e=e))
            print(f"Launcher version comparison error: {e}")
            return False

    def update_launcher(self, auto_trigger=False):
        """
        Function called when the "Update Launcher" button is clicked or automatically.
        Starts downloading the new launcher.
        """
        if not self.remote_updates_data or "launcher" not in self.remote_updates_data:
            if not auto_trigger: # Only show warning if manually triggered
                QMessageBox.warning(self, self._("Launcher Update"), self._("Launcher update information not found."))
            return

        launcher_info = self.remote_updates_data["launcher"]
        download_url = launcher_info.get("download_url")
        if not download_url:
            if not auto_trigger: # Only show warning if manually triggered
                QMessageBox.warning(self, self._("Launcher Update"), self._("Launcher download URL not found in update data."))
            return
        
        # Determine temporary path for the new launcher
        temp_dir = os.path.join(os.path.dirname(sys.executable), "temp_launcher_update")
        os.makedirs(temp_dir, exist_ok=True)
        # Use the file name from the URL for the temporary file
        file_name = os.path.basename(QUrl(download_url).path())
        temp_destination_path = os.path.join(temp_dir, file_name)

        if not auto_trigger: # Only show info if manually triggered
            QMessageBox.information(self, self._("Launcher Update"), self._("Downloading new launcher version ({latest_version})...").format(latest_version=launcher_info['latest_version']))
        
        self.launcher_progress_bar.setValue(0)
        self.launcher_progress_bar.show()
        self.update_launcher_button.setEnabled(False)
        self.launcher_status_label.setText(self._("Downloading..."))

        self.launcher_downloader = FileDownloaderThread(download_url, temp_destination_path)
        self.launcher_downloader.download_progress.connect(self.launcher_progress_bar.setValue)
        # MODIFICATION: Changed to a more robust update installation method
        self.launcher_downloader.download_finished.connect(
            lambda: self._trigger_launcher_replacement(temp_destination_path)
        )
        self.launcher_downloader.download_error.connect(self._handle_launcher_download_error)
        self.launcher_downloader.start()

    def _trigger_launcher_replacement(self, new_launcher_path):
        """
        Triggers the replacement of the old launcher with the new one using a helper script.
        """
        self.launcher_progress_bar.hide()
        self.launcher_status_label.setText(self._("Téléchargement terminé. Préparation à la mise à jour..."))
        
        # This QMessageBox is crucial as it informs the user that the app will restart.
        # It's okay to keep it even for auto-update to ensure user awareness.
        QMessageBox.information(self, self._("Launcher Update"), self._("Nouvelle version téléchargée. Le launcher va se relancer."))

        current_launcher_path = os.path.abspath(sys.argv[0]) # Path to the currently running executable
        temp_update_dir = os.path.dirname(new_launcher_path) # Directory where the new launcher was downloaded

        # Define the helper script path
        helper_script_name = "update_helper.bat" if platform.system() == "Windows" else "update_helper.sh"
        helper_script_path = os.path.join(temp_update_dir, helper_script_name)

        if platform.system() == "Windows":
            helper_content = f"""
@echo off
timeout /t 2 /nobreak >nul
rem Delete old launcher
del "{current_launcher_path}" /f /q
rem Move new launcher to old path
move "{new_launcher_path}" "{current_launcher_path}" >nul
rem Remove temporary update directory
rmdir /s /q "{temp_update_dir}" >nul
rem Launch the new launcher
start "" "{current_launcher_path}"
exit
"""
        else: # For macOS/Linux (simplified, may need more robust error handling/permissions)
            helper_content = f"""
#!/bin/bash
sleep 2
rm -f "{current_launcher_path}"
mv "{new_launcher_path}" "{current_launcher_path}"
rm -rf "{temp_update_dir}"
chmod +x "{current_launcher_path}" # Ensure new launcher is executable
"{current_launcher_path}" &
exit
"""
        
        try:
            with open(helper_script_path, 'w') as f:
                f.write(helper_content)
            
            # Make the script executable on Unix-like systems
            if platform.system() != "Windows":
                os.chmod(helper_script_path, 0o755)

            # Launch the helper script in a detached process
            if platform.system() == "Windows":
                subprocess.Popen([helper_script_path], creationflags=subprocess.DETACHED_PROCESS, close_fds=True)
            else:
                subprocess.Popen(['bash', helper_script_path], preexec_fn=os.setsid, close_fds=True)

            QApplication.quit() # Close the current instance of the launcher
        except Exception as e:
            QMessageBox.critical(self, self._("Erreur Mise à Jour Launcher"), self._("Impossible de lancer la procédure de mise à jour : {e}. Veuillez redémarrer le launcher manuellement.").format(e=e))
            self.launcher_status_label.setText(self._("Erreur lors du lancement de la mise à jour : {e}").format(e=e))
            self.update_launcher_button.setEnabled(True) # Réactiver le bouton en cas d'échec

    def _handle_launcher_download_error(self, message):
        """Handles launcher download errors."""
        self.launcher_progress_bar.hide()
        QMessageBox.critical(self, self._("Launcher Download Error"), message)
        self.launcher_status_label.setText(self._("Download failed: {message}").format(message=message))
        self.update_launcher_button.setEnabled(True)

    # --- Mod Update Logic (implementation) ---
    def _check_mod_update_logic(self):
        """
        Compares the local mod version with the remote version.
        """
        if not self.remote_updates_data or "mod" not in self.remote_updates_data:
            self.mod_status_label.setText(self._("Mod Status: Remote info not found."))
            return

        remote_mod_info = self.remote_updates_data["mod"]
        remote_mod_version_str = remote_mod_info["latest_version"]
        
        # Ensure local_version_str is always defined before use
        local_mod_version_str = self._get_local_mod_version() 
        
        try:
            remote_version = QVersionNumber.fromString(remote_mod_version_str)
            local_version = QVersionNumber.fromString(local_mod_version_str)

            if remote_version > local_version:
                # Passing named arguments to .format()
                self.mod_status_label.setText(self._("Mod Status: New version {remote_version_str} available! (Current: {local_version_str})").format(
                    remote_version_str=remote_mod_version_str, local_version_str=local_mod_version_str
                ))
                self.update_mod_button.setEnabled(True)
            else:
                self.mod_status_label.setText(self._("Mod Status: Up to date (v{local_version_str})").format(local_version_str=local_mod_version_str))
                self.update_mod_button.setEnabled(False)
        except Exception as e:
            self.mod_status_label.setText(self._("Mod Status: Version error ({e})").format(e=e))
            print(f"Mod version comparison error: {e}")

    def _get_local_mod_version(self):
        """
        Attempts to find the mod version locally in the 'mods' folder.
        This is a simplified implementation that assumes a file name prefix.
        A better approach would be to read a version file if the mod contains one.
        """
        # Ensure minecraft_paths is not None and mods folder is configured
        if not self.minecraft_paths or not self.minecraft_paths.get('mods') or \
           not os.path.isdir(self.minecraft_paths['mods']):
            # Provide a default version if the path is invalid or not configured
            return "0.0.0" 

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
        # Check for valid mods path before proceeding
        if not self.minecraft_paths or not self.minecraft_paths.get('mods'):
            QMessageBox.warning(self, self._("Installation Error"), self._("The Minecraft 'mods' folder is not configured. Please define it in 'Settings'."))
            return
        
        mod_info = self.remote_updates_data["mod"]
        download_url = mod_info.get("download_url")
        if not download_url:
            QMessageBox.warning(self, self._("Mod Update"), self._("Mod download URL not found in update data."))
            return

        # Path where the temporary file will be downloaded
        temp_download_dir = os.path.join(os.getcwd(), "temp_downloads")
        os.makedirs(temp_download_dir, exist_ok=True)
        mod_filename = os.path.basename(QUrl(download_url).path())
        temp_mod_path = os.path.join(temp_download_dir, mod_filename)

        QMessageBox.information(self, self._("Mod Update"), self._("Downloading mod ({latest_version})...").format(latest_version=mod_info['latest_version']))
        self.mod_progress_bar.setValue(0)
        self.mod_progress_bar.show()
        self.update_mod_button.setEnabled(False)
        self.mod_status_label.setText(self._("Downloading mod..."))

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
        self.mod_status_label.setText(self._("Installing mod..."))
        
        try:
            mod_dir = self.minecraft_paths['mods']
            # Delete old mod versions
            for filename in os.listdir(mod_dir):
                if filename.startswith(MOD_FILE_PREFIX) and filename.endswith(".jar"):
                    os.remove(os.path.join(mod_dir, filename))
                    print(f"Old mod version deleted: {filename}")

            # Move the new downloaded mod
            shutil.move(temp_mod_path, os.path.join(mod_dir, os.path.basename(temp_mod_path)))
            QMessageBox.information(self, self._("Mod Update"), self._("Mod updated and installed successfully!"))
            # Ensure local_version_str is defined for the status message
            local_version_after_update = self._get_local_mod_version() 
            self.mod_status_label.setText(self._("Mod Status: Up to date (v{local_version_str})").format(local_version_str=local_version_after_update))
            self.update_mod_button.setEnabled(False) # Disable after update
        except Exception as e:
            QMessageBox.critical(self, self._("Mod Installation Error"), self._("An error occurred during mod installation: {e}").format(e=e))
            self.mod_status_label.setText(self._("Installation failed: {e}").format(e=e))
            self.update_mod_button.setEnabled(True) # Re-enable on failure
        finally:
            # Clean up temporary file
            if os.path.exists(temp_mod_path):
                os.remove(temp_mod_path)

    def _handle_mod_download_error(self, message):
        """Handles mod download errors."""
        self.mod_progress_bar.hide()
        QMessageBox.critical(self, self._("Mod Download Error"), message)
        self.mod_status_label.setText(self._("Download failed: {message}").format(message=message))
        self.update_mod_button.setEnabled(True)

    # --- Map Loading Logic for Download (implementation) ---
    def _load_maps_for_download_logic(self):
        """
        Loads and displays maps available for download
        from remote_updates_data, applying search filter if any.
        """
        # Clear existing map container before reloading
        while self.maps_container_layout.count():
            child = self.maps_container_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not self.remote_updates_data or "maps" not in self.remote_updates_data:
            map_status = QLabel(self._("No map information available."))
            map_status.setStyleSheet(self.themes.get(self.current_theme, self.themes["Default"])["label_text_color"])
            self.maps_container_layout.addWidget(map_status)
            print(f"DEBUG: _load_maps_for_download_logic: No maps array or remote data.")
            return

        search_query = self.map_search_input.text().lower()
        print(f"DEBUG: _load_maps_for_download_logic called. Remote maps available: {len(self.remote_updates_data.get('maps', []))}")
        if search_query:
            print(f"DEBUG: Current search query: '{search_query}'")
        
        filtered_maps = []
        for map_info in self.remote_updates_data["maps"]:
            map_name = map_info.get('name', '').lower()
            map_description = map_info.get('description', '').lower()
            map_id = map_info.get('id', '').lower()

            if (not search_query or 
                search_query in map_name or 
                search_query in map_description or
                search_query in map_id):
                filtered_maps.append(map_info)

        print(f"DEBUG: Maps after filtering: {len(filtered_maps)}")
        if not filtered_maps:
            no_results_label = QLabel(self._("No maps found matching your search criteria."))
            no_results_label.setStyleSheet(self.themes.get(self.current_theme, self.themes["Default"])["label_text_color"])
            self.maps_container_layout.addWidget(no_results_label)
        else:
            for map_info in filtered_maps:
                self.add_map_to_download_list(map_info)

    def _filter_maps_display(self):
        """
        Triggered by search bar input. Reloads maps based on the current filter.
        """
        self._load_maps_for_download_logic()


    def add_map_to_download_list(self, map_info):
        """
        Adds a visual element for each map in the download tab.
        """
        theme_styles = self.themes.get(self.current_theme, self.themes["Default"])

        map_widget = QWidget()
        map_widget.setStyleSheet(theme_styles["map_widget"])
        map_layout = QHBoxLayout(map_widget)
        
        map_details = QVBoxLayout()
        map_details.addWidget(QLabel(f"<b>{map_info['name']}</b> <span style='color:#555;'> (v{map_info['latest_version']})</span>"))
        map_details.addWidget(QLabel(map_info.get('description', self._('No description available.')))) # Translate description fallback
        map_layout.addLayout(map_details)

        # Progress bar specific to each map
        map_progress_bar = QProgressBar(self)
        map_progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        map_progress_bar.hide()
        map_details.addWidget(map_progress_bar) # Add progress bar below details

        download_button = QPushButton(self._("Install Map")) # Translate button text
        download_button.setFixedSize(120, 30)
        download_button.setStyleSheet(theme_styles["download_button"])
        # Pass the progress bar in addition to map information
        download_button.clicked.connect(lambda checked, info=map_info, pb=map_progress_bar: self.install_map(info, pb)) 
        map_layout.addWidget(download_button)

        self.maps_container_layout.addWidget(map_widget)

    def install_map(self, map_info, progress_bar):
        """
        Function called when the "Install Map" button is clicked.
        Downloads and installs the map and its associated resource pack.
        """
        # Ensure Minecraft saves and resourcepacks folders are configured and exist (get_minecraft_sub_paths creates them)
        if not self.minecraft_paths or not self.minecraft_paths.get('saves') or not self.minecraft_paths.get('resourcepacks'):
            QMessageBox.warning(self, self._("Installation Error"), self._("Minecraft 'saves' or 'resourcepacks' folders are not configured. Please define them in 'Settings'."))
            return
            
        map_download_url = map_info.get("download_url")
        rp_download_url = map_info.get("resourcepack_url")

        if not map_download_url:
            QMessageBox.warning(self, self._("Map Installation"), self._("Map download URL not found."))
            return

        # Determine temporary and destination paths
        temp_download_dir = os.path.join(os.getcwd(), "temp_downloads")
        os.makedirs(temp_download_dir, exist_ok=True)
        
        map_filename = os.path.basename(QUrl(map_download_url).path())
        temp_map_path = os.path.join(temp_download_dir, map_filename)

        rp_filename = os.path.basename(QUrl(rp_download_url).path()) if rp_download_url else None
        temp_rp_path = os.path.join(temp_download_dir, rp_filename) if rp_filename else None

        QMessageBox.information(self, self._("Map Installation"), # Changed key for consistency
                                self._("Downloading map '{map_name}'...").format(map_name=map_info['name']))
        
        progress_bar.setValue(0)
        progress_bar.show()

        # Start map download
        self.map_downloader = FileDownloaderThread(map_download_url, temp_map_path)
        self.map_downloader.download_progress.connect(progress_bar.setValue)
        # Pass rp_filename explicitly to _install_map_files
        self.map_downloader.download_finished.connect(
            lambda path=temp_map_path, rp_url=rp_download_url, rp_path=temp_rp_path, map_name=map_info['name'], pb=progress_bar, rp_file_name_val=rp_filename: 
            self._install_map_files(path, rp_url, rp_path, map_name, pb, rp_file_name_val)
        )
        self.map_downloader.download_error.connect(lambda msg: self._handle_map_download_error(msg, progress_bar))
        self.map_downloader.start()

    def _install_map_files(self, temp_map_path, rp_download_url, temp_rp_path, map_name, progress_bar, rp_filename_val):
        """
        Decompresses the map, downloads and decompresses the resource pack if present.
        """
        progress_bar.hide()
        QMessageBox.information(self, self._("Map Installation"), self._("Map '{map_name}' downloaded. Installing...").format(map_name=map_name))

        try:
            # 1. Decompress the map
            saves_dir = self.minecraft_paths['saves']
            
            # Ensure the saves directory exists (it should, from set_minecraft_path, but as a safeguard)
            os.makedirs(saves_dir, exist_ok=True)

            with zipfile.ZipFile(temp_map_path, 'r') as zip_ref:
                # Extract only the first root directory or all files
                # We assume the zip contains a single root map folder
                map_folder_name = os.path.commonprefix(zip_ref.namelist())
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
                QMessageBox.information(self, self._("Resource Pack Installation"), self._("Downloading associated resource pack..."))
                progress_bar.setValue(0)
                progress_bar.show()

                self.rp_downloader = FileDownloaderThread(rp_download_url, temp_rp_path)
                self.rp_downloader.download_progress.connect(progress_bar.setValue)
                self.rp_downloader.download_finished.connect(
                    # Use rp_filename_val here
                    lambda path=temp_rp_path, rp_name_for_install=rp_filename_val: self._install_resource_pack_from_temp(path, rp_name_for_install, progress_bar)
                )
                self.rp_downloader.download_error.connect(lambda msg: self._handle_map_download_error(msg, progress_bar, is_rp=True))
                self.rp_downloader.start()
            else:
                QMessageBox.information(self, self._("Installation Complete"), self._("Map '{map_name}' installed successfully! (No associated Resource Pack)").format(map_name=map_name))
                # Mod update check and status update is moved
                # after full RP installation, or here if no RP.
                self.mod_status_label.setText(self._("Mod Status: Checking...")) # Update after installation
                self._check_mod_update_logic() # To force update check after install
                progress_bar.hide() # Ensure bar is hidden
        except zipfile.BadZipFile:
            QMessageBox.critical(self, self._("Decompression Error"), self._("The map ZIP file is corrupted or invalid. The 'zipfile' module only supports ZIP format (not RAR)."))
        except Exception as e:
            QMessageBox.critical(self, self._("Map Installation Error"), self._("An error occurred during map installation: {e}").format(e=e))
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
        QMessageBox.information(self, self._("Resource Pack Installation"), self._("Resource pack downloaded. Installing..."))
        try:
            rp_dir = self.minecraft_paths['resourcepacks']
            
            # Ensure the resourcepacks directory exists
            os.makedirs(rp_dir, exist_ok=True)

            # Resource packs are often just copied as .zip or decompressed if they contain a single folder
            # We will copy the zip file directly
            destination_rp_path = os.path.join(rp_dir, rp_name)
            
            # If an old RP with the same name exists, delete it
            if os.path.exists(destination_rp_path):
                os.remove(destination_rp_path)

            shutil.move(temp_rp_path, destination_rp_path)
            QMessageBox.information(self, self._("Installation Complete"), self._("Resource Pack installed successfully! Map and Resource Pack are ready."))
        except Exception as e:
            QMessageBox.critical(self, self._("RP Installation Error"), self._("An error occurred during resource pack installation: {e}").format(e=e))
        finally:
            # Clean up temporary RP file
            if os.path.exists(temp_rp_path):
                os.remove(temp_rp_path)
            # Clean up temporary folder if empty after processing both files
            if os.path.exists(os.path.dirname(temp_rp_path)) and not os.listdir(os.path.dirname(temp_rp_path)):
                shutil.rmtree(os.path.dirname(temp_rp_path))
        
        self.mod_status_label.setText(self._("Mod Status: Checking...")) # Update after installation
        self._check_mod_update_logic() # To force update check after install


    def _handle_map_download_error(self, message, progress_bar, is_rp=False):
        """Handles map or resource pack download errors."""
        progress_bar.hide()
        component_name = self._("Resource Pack") if is_rp else self._("Map")
        QMessageBox.critical(self, f"{component_name} {self._('Download Error')}", message)
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
