# ZombieRoolLauncher/main/github_threads.py
import os
import json
import time

from github import GithubException
from PyQt6.QtCore import QVersionNumber, pyqtSignal # Add pyqtSignal here
from PyQt6.QtCore import QThread # QThread est déjà importé via GitHubWorkerBase mais on le laisse pour la clarté si besoin direct

from main.github_worker_base import GitHubWorkerBase
from main.constants import GITHUB_REPO_OWNER, GITHUB_REPO_NAME, UPDATES_JSON_URL # Import UPDATES_JSON_URL for fetching updates.json within worker

class GitHubUploaderThread(GitHubWorkerBase):
    upload_finished = pyqtSignal(dict) # Contains map_info and uploaded asset URLs
    # Inherits upload_progress (renamed from progress) and upload_error from GitHubWorkerBase

    def __init__(self, github_token, map_info, map_zip_path, rp_zip_path=None, remote_updates_data=None):
        super().__init__(github_token)
        self.map_info = map_info
        self.map_zip_path = map_zip_path
        self.rp_zip_path = rp_zip_path
        self.uploaded_assets = {} # To store {asset_name: download_url}
        self.remote_updates_data = remote_updates_data # Pass existing remote data for conflict check

    def run(self):
        if not self._authenticate_github():
            return

        try:
            # Add author to map_info before publication
            self.map_info['author'] = self.authenticated_user_login

            # --- Version Conflict Resolution ---
            existing_map = None
            if self.remote_updates_data and "maps" in self.remote_updates_data:
                for m in self.remote_updates_data["maps"]:
                    if m.get('id') == self.map_info['id']:
                        existing_map = m
                        break

            if existing_map:
                existing_version = QVersionNumber.fromString(existing_map['latest_version'])
                new_version = QVersionNumber.fromString(self.map_info['latest_version'])

                if new_version <= existing_version:
                    # Conflict: new version is not strictly greater than existing
                    self.error_occurred.emit(
                        f"A map with ID '{self.map_info['id']}' and version {existing_map['latest_version']} already exists. "
                        f"Your version is {self.map_info['latest_version']}. "
                        "Please increment the version or explicitly confirm overwrite (not yet implemented for this flow)."
                    )
                    return
                else:
                    self.progress_update.emit(f"Map ID '{self.map_info['id']}' already exists. Updating from v{existing_version.toString()} to v{new_version.toString()}.")


            # Define release details
            release_tag = f"map-{self.map_info['id']}-v{self.map_info['latest_version']}"
            release_title = f"Map: {self.map_info['name']} v{self.map_info['latest_version']}"
            release_message = self.map_info.get('description', 'New map release.')

            # Check if tag already exists (more robust check, as a release might not be linked to ref)
            try:
                self.repo.get_git_ref(f"tags/{release_tag}")
                self.error_occurred.emit(f"Error: A release with tag '{release_tag}' already exists. Please increment the map version.")
                return
            except GithubException as e:
                if e.status != 404: # If not 404 (not found), it's another error
                    raise e # Re-raise if it's a real error, otherwise continue

            self.progress_update.emit(f"Creating GitHub release: {release_title}...")
            release = self.repo.create_git_release(
                tag=release_tag,
                name=release_title,
                message=release_message,
                prerelease=False, 
                draft=False 
            )
            self.progress_update.emit(f"Release created: {release.html_url}")

            # Upload map file
            self.progress_update.emit(f"Uploading map file: {os.path.basename(self.map_zip_path)}...")
            uploaded_map_asset = release.upload_asset(self.map_zip_path, name=os.path.basename(self.map_zip_path))
            self.uploaded_assets[os.path.basename(self.map_zip_path)] = uploaded_map_asset.browser_download_url
            self.progress_update.emit(f"Map file uploaded.")

            # Upload resource pack file if applicable
            if self.rp_zip_path and os.path.exists(self.rp_zip_path):
                self.progress_update.emit(f"Uploading resource pack file: {os.path.basename(self.rp_zip_path)}...")
                uploaded_rp_asset = release.upload_asset(self.rp_zip_path, name=os.path.basename(self.rp_zip_path))
                self.uploaded_assets[os.path.basename(self.rp_zip_path)] = uploaded_rp_asset.browser_download_url
                self.progress_update.emit(f"Resource pack file uploaded.")

            # Update updates.json
            self.progress_update.emit("Updating updates.json...")
            self._update_remote_updates_json(release)

            self.upload_finished.emit(self.map_info)

        except GithubException as e:
            self.error_occurred.emit(f"GitHub operation failed: {e.data.get('message', str(e))}. Please check your token permissions (should include 'Contents' read/write and 'Releases' for this repository).")
        except Exception as e:
            self.error_occurred.emit(f"An unexpected error occurred during upload: {e}")

    def _update_remote_updates_json(self, release_info):
        """
        Reads updates.json from GitHub, modifies it with new map data,
        and pushes it back to GitHub.
        """
        updates_json_path = "updates.json" # Relative path on GitHub
        current_json_content = None
        updates_data = {}
        updates_file_sha = None

        try:
            contents = self.repo.get_contents(updates_json_path, ref="main") # Assuming main branch
            current_json_content = contents.decoded_content.decode('utf-8')
            updates_data = json.loads(current_json_content)
            updates_file_sha = contents.sha # Keep SHA for file update
            self.progress_update.emit(f"'{updates_json_path}' loaded from GitHub.")
        except GithubException as e:
            if e.status == 404:
                self.progress_update.emit(f"WARNING: '{updates_json_path}' not found on GitHub. Creating a new base file.")
                updates_data = {
                    "launcher": {"latest_version": "0.0.0", "download_url": ""},
                    "mod": {"name": "NomDuMod", "latest_version": "0.0.0", "download_url": "", "changelog_url": ""},
                    "maps": [],
                    "content_packs": [],
                    "admins": [self.authenticated_user_login] # Initialize with uploader as admin
                }
            else:
                raise # Re-raise other GitHub exceptions
        except json.JSONDecodeError as e:
            raise Exception(f"ERROR: '{updates_json_path}' on GitHub is invalid (malformed JSON): {e}")

        # Construct the new map entry, including author
        new_map_entry = {
            "id": self.map_info['id'],
            "name": self.map_info['name'],
            "latest_version": self.map_info['latest_version'],
            "download_url": self.uploaded_assets.get(os.path.basename(self.map_zip_path), ""),
            "description": self.map_info['description'],
            "author": self.map_info['author'] # Add the author field
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
                self.progress_update.emit(f"Map '{self.map_info['id']}' updated in updates.json.")
                found_map = True
                break
        if not found_map:
            updates_data["maps"].append(new_map_entry)
            self.progress_update.emit(f"New map '{self.map_info['id']}' added to updates.json.")
        
        # Commit and push the updated JSON
        new_json_content = json.dumps(updates_data, indent=4, ensure_ascii=False) # ensure_ascii=False for UTF-8 chars
        
        commit_message = f"feat: Add/Update map {self.map_info['name']} (v{self.map_info['latest_version']}) via launcher"
        if updates_file_sha: # File existed, update it
            self.repo.update_file(
                path=updates_json_path,
                message=commit_message,
                content=new_json_content,
                sha=updates_file_sha,
                branch="main" 
            )
        else: # File did not exist, create it
            self.repo.create_file(
                path=updates_json_path,
                message=commit_message,
                content=new_json_content,
                branch="main"
            )
        self.progress_update.emit(f"'{updates_json_path}' updated and pushed to GitHub successfully!")


class GitHubDeleterThread(GitHubWorkerBase):
    deletion_finished = pyqtSignal(str) # Emits the ID of the deleted map
    # Inherits deletion_progress (renamed from progress) and deletion_error from GitHubWorkerBase

    def __init__(self, github_token, map_id_to_delete):
        super().__init__(github_token)
        self.map_id_to_delete = map_id_to_delete

    def run(self):
        if not self._authenticate_github():
            return

        try:
            # Fetch updates.json to get map info and admin list
            updates_json_path = "updates.json"
            updates_data = {}
            try:
                contents = self.repo.get_contents(updates_json_path, ref="main")
                updates_data = json.loads(contents.decoded_content.decode('utf-8'))
            except GithubException as e:
                if e.status == 404:
                    self.error_occurred.emit(f"Error: '{updates_json_path}' not found on GitHub. Cannot perform deletion checks.")
                    return
                else:
                    raise # Re-raise other GitHub exceptions
            except json.JSONDecodeError as e:
                self.error_occurred.emit(f"ERROR: '{updates_json_path}' on GitHub is invalid (malformed JSON): {e}. Cannot perform deletion checks.")
                return

            # Check authorization for deletion
            map_to_delete_info = None
            if "maps" in updates_data:
                for map_obj in updates_data["maps"]:
                    if map_obj.get("id") == self.map_id_to_delete:
                        map_to_delete_info = map_obj
                        break
            
            is_admin = self.authenticated_user_login in updates_data.get("admins", [])
            is_author = map_to_delete_info and map_to_delete_info.get("author") == self.authenticated_user_login

            if not is_admin and not is_author:
                self.error_occurred.emit(f"Deletion failed: You are not authorized to delete map '{self.map_id_to_delete}'. Only the author ('{map_to_delete_info.get('author', 'N/A')}') or an admin can delete this map.")
                return

            self.progress_update.emit(f"Searching for releases for map ID '{self.map_id_to_delete}'...")
            
            releases_deleted_count = 0
            all_releases_found = list(self.repo.get_releases())
            for release in all_releases_found: 
                if release.tag_name and release.tag_name.startswith(f"map-{self.map_id_to_delete}-"):
                    try:
                        self.progress_update.emit(f"Attempting to delete release '{release.title}' (tag: {release.tag_name})...")
                        release.delete_release()
                        releases_deleted_count += 1
                        self.progress_update.emit(f"Successfully deleted release: {release.title}")
                    except GithubException as e:
                        error_msg = e.data.get('message', str(e))
                        self.progress_update.emit(f"Warning: Could not delete release '{release.title}'. Status: {e.status}, Message: {error_msg}. "
                                                    f"Please ensure your token has 'Releases' (write) permission for this repository.")
                        print(f"DEBUG: GitHubException during release deletion (Status: {e.status}, Data: {e.data})")
            
            self.progress_update.emit(f"Deleted {releases_deleted_count} associated GitHub releases. Waiting 2 seconds for GitHub propagation...")
            time.sleep(2) # Give GitHub some time to process deletions

            # 2. Delete associated Git tag references (for robustness, in case some tags were orphaned or not part of a release)
            self.progress_update.emit(f"Searching for and deleting associated Git tags for map ID '{self.map_id_to_delete}'...")
            tags_deleted_count = 0
            all_tags_found = list(self.repo.get_tags())
            for tag in all_tags_found: 
                if tag.name.startswith(f"map-{self.map_id_to_delete}-"):
                    try:
                        git_ref_path = f"tags/{tag.name}"
                        git_ref = self.repo.get_git_ref(git_ref_path)
                        self.progress_update.emit(f"Attempting to delete Git tag reference '{tag.name}'...")
                        git_ref.delete()
                        tags_deleted_count += 1
                        self.progress_update.emit(f"Successfully deleted tag: {tag.name}")
                    except GithubException as e:
                        error_msg = e.data.get('message', str(e))
                        if e.status == 404:
                            self.progress_update.emit(f"Tag '{tag.name}' not found, possibly already deleted by release deletion or previously missing.")
                        else:
                            self.progress_update.emit(f"Warning: Could not delete Git tag reference '{tag.name}'. Status: {e.status}, Message: {error_msg}. "
                                                        f"Please ensure your token has 'Git tags' (write) permission for this repository.")
                            print(f"DEBUG: GitHubException during tag deletion (Status: {e.status}, Data: {e.data})")
            self.progress_update.emit(f"Deleted {tags_deleted_count} associated Git tags. Waiting 2 seconds for GitHub propagation...")
            time.sleep(2) # Give GitHub some time to process deletions

            # 3. Update updates.json to remove the map entry
            self.progress_update.emit("Updating updates.json...")
            updates_file_sha = contents.sha # Use the SHA from initial fetch

            # Filter out the map to be deleted
            if "maps" in updates_data:
                initial_map_count = len(updates_data["maps"])
                updates_data["maps"] = [
                    map_obj for map_obj in updates_data["maps"]
                    if map_obj.get("id") != self.map_id_to_delete
                ]
                if len(updates_data["maps"]) < initial_map_count:
                    self.progress_update.emit(f"Map ID '{self.map_id_to_delete}' removed from updates.json.")
                else:
                    self.progress_update.emit(f"Map ID '{self.map_id_to_delete}' was not found in updates.json (it might have been deleted manually or previously).")

            # Commit and push the updated JSON
            new_json_content = json.dumps(updates_data, indent=4, ensure_ascii=False)
            commit_message = f"chore: Remove map {self.map_id_to_delete} via launcher"
            
            self.repo.update_file(
                path=updates_json_path,
                message=commit_message,
                content=new_json_content,
                sha=updates_file_sha,
                branch="main" 
            )
            self.progress_update.emit(f"'{updates_json_path}' updated and pushed to GitHub successfully!")

            self.deletion_finished.emit(self.map_id_to_delete)

        except GithubException as e:
            self.error_occurred.emit(f"Échec de l'opération GitHub lors de la suppression : {e.data.get('message', str(e))}. "
                                    "Veuillez vous assurer que votre Personal Access Token GitHub dispose des permissions suffisantes "
                                    "(par exemple, le scope 'repo' complet ou spécifiquement 'contents:write', 'releases:write' pour ce dépôt).")
            print(f"DEBUG: Critical GitHubException during deletion (Status: {e.status}, Data: {e.data})")
        except Exception as e:
            self.error_occurred.emit(f"Une erreur inattendue est survenue lors de la suppression : {e}")
            print(f"DEBUG: Unexpected error during deletion: {e}")
