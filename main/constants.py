import os
import platform

# --- GLOBAL CONFIGURATION ---
# Current version of your launcher. This version will be compared by the launcher
# with the one on GitHub to know if it needs to update itself.
__version__ = "4.2.0" # Updated version

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
# Using platform-specific application data directory for persistent config
def get_config_file_base_path():
    """Returns the base application data directory based on OS."""
    if platform.system() == "Windows":
        return os.path.join(os.getenv('APPDATA'), 'ZombieRoolLauncher')
    elif platform.system() == "Darwin": # macOS
        return os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'ZombieRoolLauncher')
    else: # Linux and others
        return os.path.join(os.path.expanduser('~'), '.config', 'ZombieRoolLauncher') # XDG Base Directory Specification

# Full path to the configuration file
CONFIG_FILE_PATH = os.path.join(get_config_file_base_path(), 'config.json')
