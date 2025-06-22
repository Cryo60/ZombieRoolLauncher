import os
import json
import requests
import time

from PyQt6.QtCore import QThread, pyqtSignal

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
        self.is_running = True # Control flag

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
                    if not self.is_running: # Allow stopping the download
                        print(f"Download for {self.url} interrupted.")
                        break
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        if total_size > 0:
                            progress = int((downloaded_size / total_size) * 100)
                            self.download_progress.emit(progress)
            
            if self.is_running: # Only emit finished if not interrupted
                self.download_finished.emit(self.destination_path)
            else:
                # If interrupted, clean up partially downloaded file
                if os.path.exists(self.destination_path):
                    os.remove(self.destination_path)

        except requests.exceptions.RequestException as e:
            self.download_error.emit(f"Download error: {e}")
        except Exception as e:
            self.download_error.emit(f"Unexpected error during download: {e}")

    def stop(self):
        """Method to stop the thread gracefully."""
        self.is_running = False
