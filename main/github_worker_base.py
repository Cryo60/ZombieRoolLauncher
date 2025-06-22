# ZombieRoolLauncher/main/github_worker_base.py
from PyQt6.QtCore import QThread, pyqtSignal # Add pyqtSignal here
from github import Github, GithubException

from main.constants import GITHUB_REPO_OWNER, GITHUB_REPO_NAME

class GitHubWorkerBase(QThread):
    """
    Base class for GitHub API interactions, handling authentication and common errors.
    """
    error_occurred = pyqtSignal(str) # General error signal
    progress_update = pyqtSignal(str) # General progress update

    def __init__(self, github_token):
        super().__init__()
        self.github_token = github_token
        self.g = None # GitHub instance
        self.repo = None # Repository instance
        self.authenticated_user_login = None # Stores the login of the authenticated user

    def _authenticate_github(self):
        """
        Authenticates with GitHub and gets the repository object.
        Returns True on success, False on failure (emits error_occurred).
        """
        self.progress_update.emit("Connecting to GitHub...")
        try:
            self.g = Github(self.github_token)
            # Validate token by trying to get the user's login
            user = self.g.get_user()
            self.authenticated_user_login = user.login
            self.repo = self.g.get_user(GITHUB_REPO_OWNER).get_repo(GITHUB_REPO_NAME)
            self.progress_update.emit(f"Connected to repository: {self.repo.full_name}")
            return True
        except GithubException as e:
            error_message = e.data.get('message', 'Invalid token or insufficient permissions')
            self.error_occurred.emit(f"GitHub authentication error: {error_message}")
            print(f"DEBUG: GitHub authentication error: {e.status} - {e.data}")
            return False
        except Exception as e:
            self.error_occurred.emit(f"An unexpected error occurred during GitHub authentication: {e}")
            print(f"DEBUG: Unexpected authentication error: {e}")
            return False

    def run(self):
        # This method should be overridden by subclasses
        raise NotImplementedError("Subclasses must implement the 'run' method.")
