import os
import platform
import json
import shutil # For copying and deleting files/folders
import zipfile # For decompressing .zip files
import stat # For chmod on Unix-like systems

from PyQt6.QtWidgets import QMessageBox # For utility-level error messages
from PyQt6.QtCore import QUrl

from main.constants import CONFIG_FILE_PATH

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
            # Optionally, delete corrupted config file to start fresh
            # os.remove(CONFIG_FILE_PATH)
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

# --- MAP VALIDATION UTILITY ---
def is_valid_map_zip(zip_path):
    """
    Checks if a given ZIP file contains the basic structure of a Minecraft world save.
    A valid map ZIP should typically contain:
    - level.dat at the root
    - a 'region' folder (and optionally 'DIM-1', 'DIM1' for Nether/End)
    """
    if not zip_path or not os.path.exists(zip_path):
        return False

    temp_extract_dir = None
    is_valid = False
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Get the list of all files/directories in the zip
            namelist = zip_ref.namelist()

            # Strategy 1: Check for level.dat and region/ directly at the root of the zip
            has_level_dat_at_root = 'level.dat' in namelist
            has_region_folder_at_root = any(name.startswith('region/') for name in namelist)

            if has_level_dat_at_root and has_region_folder_at_root:
                is_valid = True
                print("DEBUG: Valid map ZIP (level.dat and region/ at root).")
                return True

            # Strategy 2: If not at root, extract to a temp dir and check the first level
            # This handles zips where the map is inside a single root folder
            temp_extract_dir = os.path.join(os.path.dirname(zip_path), f"temp_map_check_{os.urandom(8).hex()}")
            os.makedirs(temp_extract_dir, exist_ok=True)
            zip_ref.extractall(temp_extract_dir)

            extracted_contents = os.listdir(temp_extract_dir)
            if len(extracted_contents) == 1 and os.path.isdir(os.path.join(temp_extract_dir, extracted_contents[0])):
                # It's a single root folder, check inside that folder
                map_root_folder = os.path.join(temp_extract_dir, extracted_contents[0])
                if os.path.exists(os.path.join(map_root_folder, 'level.dat')) and \
                   os.path.exists(os.path.join(map_root_folder, 'region')):
                    is_valid = True
                    print("DEBUG: Valid map ZIP (level.dat and region/ inside single root folder).")
                    return True
            
            print(f"DEBUG: Map ZIP validation failed for '{zip_path}'. Contents: {namelist}")
            return False

    except zipfile.BadZipFile:
        print(f"ERROR: '{zip_path}' is not a valid ZIP file.")
        return False
    except Exception as e:
        print(f"ERROR: An unexpected error occurred during map ZIP validation: {e}")
        return False
    finally:
        if temp_extract_dir and os.path.exists(temp_extract_dir):
            shutil.rmtree(temp_extract_dir, ignore_errors=True)
            print(f"DEBUG: Cleaned up temporary map validation directory: {temp_extract_dir}")

# --- Helper to make script executable for launcher update ---
def make_executable(path):
    """Makes a file executable on Unix-like systems."""
    if platform.system() != "Windows":
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

# --- Path cleaning ---
def clean_temp_dir(path):
    """Cleans up a temporary directory if it becomes empty."""
    if os.path.exists(path) and not os.listdir(path):
        shutil.rmtree(path)
