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

# --- CONFIGURATION GLOBALE ---
# Version actuelle de ton launcher. C'est cette version que le launcher comparera
# avec celle sur GitHub pour savoir s'il doit se mettre à jour lui-même.
__version__ = "1.0.0" 

# URL directe vers le fichier updates.json sur ton dépôt GitHub.
# TRÈS IMPORTANT : Va sur ton fichier updates.json sur GitHub, clique sur "Raw",
# et copie l'URL qui s'affiche dans ta barre d'adresse du navigateur. Elle doit commencer par
# "https://raw.githubusercontent.com/...". Ne laisse pas une URL "github.com" classique.
UPDATES_JSON_URL = "https://raw.githubusercontent.com/Cryo60/ZombieRoolLauncher/refs/heads/main/updates.json"

# Nom de fichier du mod tel qu'il est typiquement dans le dossier mods (pour la détection locale)
# Adapte ce nom pour correspondre au format réel de tes fichiers de mod.
# Ex: si ton mod s'appelle 'ZombieRool-1.3.0.jar', tu pourrais utiliser 'ZombieRool-'
MOD_FILE_PREFIX = "ZombieRool-" 

# Chemin du fichier de configuration local pour sauvegarder les chemins Minecraft
CONFIG_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')


# --- FONCTIONS UTILITAIRES POUR LES CHEMINS MINECRAFT ---
def get_default_minecraft_path():
    """
    Tente de trouver le chemin par défaut du dossier .minecraft en fonction du système d'exploitation.
    Cette fonction est maintenant principalement utilisée pour suggérer un chemin de départ
    lorsque l'utilisateur doit sélectionner manuellement le dossier.
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
    Vérifie et retourne les chemins absolus des dossiers mods, saves et resourcepacks
    basés sur le chemin du dossier .minecraft fourni.
    Retourne None si le chemin de base n'est pas valide ou si les sous-dossiers n'existent pas.
    Cette fonction est clé pour valider le chemin choisi par l'utilisateur, même s'il s'agit
    d'une instance personnalisée (comme celles de CurseForge).
    """
    if mc_path and os.path.isdir(mc_path):
        paths = {
            'mods': os.path.join(mc_path, 'mods'),
            'saves': os.path.join(mc_path, 'saves'),
            'resourcepacks': os.path.join(mc_path, 'resourcepacks')
        }
        # Vérifie si tous les sous-dossiers nécessaires existent.
        # On ne les crée pas ici, mais on s'assure que le dossier de base existe.
        # La création des sous-dossiers sera gérée lors de l'installation si nécessaire.
        # Ajout de `exist_ok=True` pour gérer les cas où les dossiers sont déjà là.
        try:
            os.makedirs(paths['mods'], exist_ok=True)
            os.makedirs(paths['saves'], exist_ok=True)
            os.makedirs(paths['resourcepacks'], exist_ok=True)
            return paths
        except OSError as e:
            print(f"Erreur lors de la création/vérification des sous-dossiers Minecraft : {e}")
            return None
    return None

# --- FONCTIONS UTILITAIRES POUR LA CONFIGURATION LOCALE ---
def load_config():
    """Charge la configuration depuis un fichier JSON local."""
    if os.path.exists(CONFIG_FILE_PATH):
        try:
            with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Erreur lors du chargement du fichier de configuration : {e}")
    return {}

def save_config(config_data):
    """Sauvegarde la configuration dans un fichier JSON local."""
    try:
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=4)
    except IOError as e:
        print(f"Erreur lors de la sauvegarde du fichier de configuration : {e}")

# --- THREAD POUR LES OPÉRATIONS RÉSEAU NON BLOQUANTES ---
# Il est crucial d'effectuer les requêtes réseau (téléchargement du updates.json)
# dans un thread séparé pour ne pas bloquer l'interface utilisateur (UI).
# Si l'UI se bloque, l'application paraît "gelée" et ne répond plus.
class UpdateCheckerThread(QThread):
    # Signal émis lorsque les données de mise à jour sont prêtes (succès)
    update_data_ready = pyqtSignal()
    # Signal émis en cas d'erreur pendant la requête
    error_occurred = pyqtSignal(str)
    
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.update_data = None # Stockera les données JSON après téléchargement

    def run(self):
        """
        Méthode exécutée lorsque le thread est démarré.
        Elle télécharge le fichier JSON depuis l'URL.
        """
        try:
            response = requests.get(self.url, timeout=10) # Timeout pour éviter un blocage trop long
            response.raise_for_status() # Lève une exception pour les codes d'erreur HTTP (4xx ou 5xx)
            self.update_data = response.json() # Parse la réponse JSON
            self.update_data_ready.emit() # Émet le signal de succès
        except requests.exceptions.RequestException as e:
            # Gère les erreurs de connexion, DNS, timeout, etc.
            self.error_occurred.emit(f"Erreur de connexion lors de la récupération des mises à jour : {e}")
        except json.JSONDecodeError as e:
            # Gère les erreurs si le contenu téléchargé n'est pas un JSON valide
            self.error_occurred.emit(f"Erreur de lecture du fichier updates.json (JSON invalide) : {e}. Vérifiez le format du fichier sur GitHub.")
        except Exception as e:
            # Gère toute autre exception inattendue
            self.error_occurred.emit(f"Une erreur inattendue est survenue : {e}")

# --- THREAD POUR LE TÉLÉCHARGEMENT DE FICHIERS AVEC PROGRESSION ---
class FileDownloaderThread(QThread):
    download_progress = pyqtSignal(int) # Signal pour la progression (0-100)
    download_finished = pyqtSignal(str) # Signal quand le téléchargement est fini (chemin du fichier)
    download_error = pyqtSignal(str) # Signal en cas d'erreur de téléchargement

    def __init__(self, url, destination_path):
        super().__init__()
        self.url = url
        self.destination_path = destination_path

    def run(self):
        try:
            response = requests.get(self.url, stream=True, timeout=30) # stream=True pour télécharger par morceaux
            response.raise_for_status() # Lève une exception pour les codes d'erreur HTTP

            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0

            # Assurez-vous que le répertoire de destination existe
            os.makedirs(os.path.dirname(self.destination_path), exist_ok=True)

            with open(self.destination_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192): # Télécharge par blocs de 8KB
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        if total_size > 0:
                            progress = int((downloaded_size / total_size) * 100)
                            self.download_progress.emit(progress)
            
            self.download_finished.emit(self.destination_path)
        except requests.exceptions.RequestException as e:
            self.download_error.emit(f"Erreur de téléchargement : {e}")
        except Exception as e:
            self.download_error.emit(f"Erreur inattendue lors du téléchargement : {e}")

# --- CLASSE PRINCIPALE DU LAUNCHER ---
class ZombieRoolLauncher(QMainWindow):
    def __init__(self):
        super().__init__()
        # Initialisation de la fenêtre principale
        self.setWindowTitle(f"ZombieRool Launcher - v{__version__}")
        self.setGeometry(100, 100, 800, 600) # x, y, largeur, hauteur

        # Conteneur central pour organiser les éléments
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        # Attributs pour stocker les chemins de Minecraft et les données de mise à jour
        self.minecraft_paths = None 
        self.remote_updates_data = None 

        # --- En-tête du Launcher ---
        self.header_label = QLabel("Bienvenue sur le Launcher ZombieRool !", self)
        self.header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_label.setStyleSheet("font-size: 24px; font-weight: bold; padding: 20px; color: #E74C3C;") # Style basique pour un look "gamer"
        self.main_layout.addWidget(self.header_label)

        # --- Onglets ---
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabWidget::pane { border: 1px solid #CCC; background: #EEE; }"
                                "QTabBar::tab { background: #DDD; border: 1px solid #CCC; border-bottom-color: #EEE; "
                                "border-top-left-radius: 4px; border-top-right-radius: 4px; padding: 8px 15px; }"
                                "QTabBar::tab:selected { background: #FFF; border-color: #999; border-bottom-color: #FFF; }"
                                "QTabWidget::tab-bar { alignment: center; }") # Style pour les onglets
        self.main_layout.addWidget(self.tabs)

        # Onglet "Mise à Jour"
        self.update_tab = QWidget()
        self.tabs.addTab(self.update_tab, "Mise à Jour")
        self.setup_update_tab()

        # Onglet "Téléchargement de Maps"
        self.download_tab = QWidget()
        self.tabs.addTab(self.download_tab, "Téléchargement de Maps")
        self.setup_download_tab()
        
        # Onglet "Paramètres"
        self.settings_tab = QWidget()
        self.tabs.addTab(self.settings_tab, "Paramètres")
        self.setup_settings_tab()

        # --- Pied de page (Barre de statut / Version du Launcher) ---
        self.status_bar = QLabel(f"Version du Launcher: {__version__}", self)
        self.status_bar.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status_bar.setStyleSheet("font-size: 10px; padding: 5px; color: #555;")
        self.main_layout.addWidget(self.status_bar)

        # --- Initialisation et vérifications au démarrage ---
        self.load_saved_minecraft_path() # Tente de charger le chemin Minecraft sauvegardé
        self.check_for_updates() # Lance la vérification des mises à jour depuis GitHub

    # --- Configuration des onglets ---
    def setup_update_tab(self):
        """Configure l'interface de l'onglet 'Mise à Jour'."""
        layout = QVBoxLayout(self.update_tab)
        layout.setContentsMargins(20, 20, 20, 20) # Marges intérieures

        # Section pour le Mod
        mod_section_label = QLabel("Mises à jour du Mod ZombieRool", self)
        mod_section_label.setStyleSheet("font-size: 18px; font-weight: bold; margin-top: 10px; color: #2C3E50;")
        layout.addWidget(mod_section_label)

        self.mod_status_label = QLabel("Statut du Mod: Vérification en cours...", self)
        layout.addWidget(self.mod_status_label)

        self.mod_progress_bar = QProgressBar(self)
        self.mod_progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mod_progress_bar.hide() # Cachée par défaut
        layout.addWidget(self.mod_progress_bar)

        self.update_mod_button = QPushButton("Mettre à jour le Mod", self)
        self.update_mod_button.clicked.connect(self.update_mod) 
        self.update_mod_button.setEnabled(False) # Désactivé par défaut, activé si une maj est dispo
        layout.addWidget(self.update_mod_button)
        layout.addSpacing(20) # Espacement

        # Section pour le Launcher (pour l'auto-mise à jour)
        launcher_section_label = QLabel("Mises à jour du Launcher", self)
        launcher_section_label.setStyleSheet("font-size: 18px; font-weight: bold; margin-top: 20px; color: #2C3E50;")
        layout.addWidget(launcher_section_label)

        self.launcher_status_label = QLabel("Statut du Launcher: Vérification en cours...", self)
        layout.addWidget(self.launcher_status_label)
        
        self.launcher_progress_bar = QProgressBar(self)
        self.launcher_progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.launcher_progress_bar.hide() # Cachée par défaut
        layout.addWidget(self.launcher_progress_bar)

        self.update_launcher_button = QPushButton("Mettre à jour le Launcher", self)
        self.update_launcher_button.clicked.connect(self.update_launcher)
        self.update_launcher_button.setEnabled(False) # Désactivé par défaut
        layout.addWidget(self.update_launcher_button)

        layout.addStretch() # Pousse les éléments vers le haut

    def setup_download_tab(self):
        """Configure l'interface de l'onglet 'Téléchargement de Maps'."""
        layout = QVBoxLayout(self.download_tab)
        layout.setContentsMargins(20, 20, 20, 20)

        maps_label = QLabel("Télécharger et Installer des Maps", self)
        maps_label.setStyleSheet("font-size: 18px; font-weight: bold; margin-top: 10px; color: #2C3E50;")
        layout.addWidget(maps_label)

        self.maps_container_layout = QVBoxLayout() # Conteneur pour ajouter dynamiquement les maps
        layout.addLayout(self.maps_container_layout)

        layout.addStretch()

    def setup_settings_tab(self):
        """Configure l'interface de l'onglet 'Paramètres'."""
        layout = QVBoxLayout(self.settings_tab)
        layout.setContentsMargins(20, 20, 20, 20)

        settings_label = QLabel("Paramètres et Chemins Minecraft", self)
        settings_label.setStyleSheet("font-size: 18px; font-weight: bold; margin-top: 10px; color: #2C3E50;")
        layout.addWidget(settings_label)

        # Section pour le chemin de l'instance Minecraft
        mc_path_h_layout = QHBoxLayout()
        mc_path_label = QLabel("Dossier .minecraft ou Instance :")
        mc_path_h_layout.addWidget(mc_path_label)
        
        self.mc_path_input = QLineEdit()
        self.mc_path_input.setPlaceholderText("Cliquez sur 'Parcourir...' pour choisir votre dossier Minecraft")
        self.mc_path_input.setReadOnly(True) # Lecture seule, l'utilisateur passe par le bouton
        mc_path_h_layout.addWidget(self.mc_path_input)
        
        self.browse_mc_path_button = QPushButton("Parcourir...")
        self.browse_mc_path_button.clicked.connect(self.browse_minecraft_path)
        mc_path_h_layout.addWidget(self.browse_mc_path_button)
        layout.addLayout(mc_path_h_layout)

        self.mods_path_label = QLabel("Dossier Mods : Non détecté")
        self.saves_path_label = QLabel("Dossier Saves : Non détecté")
        self.resourcepacks_path_label = QLabel("Dossier Resourcepacks : Non détecté")
        
        layout.addWidget(self.mods_path_label)
        layout.addWidget(self.saves_path_label)
        layout.addWidget(self.resourcepacks_path_label)

        layout.addStretch()

    # --- Fonctions de logique des chemins Minecraft ---
    def load_saved_minecraft_path(self):
        """
        Tente de charger le chemin Minecraft sauvegardé depuis le fichier de configuration.
        Si un chemin est trouvé et valide, le définit. Sinon, invite l'utilisateur à configurer.
        """
        config = load_config()
        saved_path = config.get('minecraft_path')
        if saved_path:
            self.set_minecraft_path(saved_path, show_message=False) # Ne pas afficher de message au démarrage
            if not self.minecraft_paths: # Si le chemin chargé n'est plus valide
                 QMessageBox.warning(self, "Chemin Minecraft Invalide",
                                    "Le chemin Minecraft sauvegardé n'est plus valide ou n'existe plus. Veuillez le reconfigurer dans l'onglet 'Paramètres'.")
                 self.mc_path_input.setText("Chemin non configuré")
                 self.mods_path_label.setText("Dossier Mods : Non configuré")
                 self.saves_path_label.setText("Dossier Saves : Non configuré")
                 self.resourcepacks_path_label.setText("Dossier Resourcepacks : Non configuré")
        else:
            # Si aucun chemin n'est sauvegardé, affiche le message d'invitation initial
            QMessageBox.information(self, "Configuration du Chemin Minecraft",
                                    "Bienvenue ! Veuillez sélectionner le dossier de votre instance Minecraft (contenant les dossiers 'mods', 'saves', 'resourcepacks') dans l'onglet 'Paramètres' en cliquant sur 'Parcourir...'.")
            self.mc_path_input.setText("Chemin non configuré")
            self.mods_path_label.setText("Dossier Mods : Non configuré")
            self.saves_path_label.setText("Dossier Saves : Non configuré")
            self.resourcepacks_path_label.setText("Dossier Resourcepacks : Non configuré")

    def browse_minecraft_path(self):
        """
        Ouvre une boîte de dialogue pour permettre à l'utilisateur de sélectionner
        manuellement le dossier de son instance Minecraft.
        """
        # Propose le chemin par défaut comme point de départ pour la sélection (si détecté)
        initial_path = self.mc_path_input.text() if self.mc_path_input.text() and os.path.exists(self.mc_path_input.text()) else (get_default_minecraft_path() if get_default_minecraft_path() else os.path.expanduser('~'))
        
        selected_path = QFileDialog.getExistingDirectory(self, "Sélectionner le dossier de l'instance Minecraft", initial_path)
        if selected_path:
            self.set_minecraft_path(selected_path, show_message=True)
        else:
            QMessageBox.warning(self, "Sélection Annulée", "La sélection du dossier Minecraft a été annulée.")

    def set_minecraft_path(self, path, show_message=True):
        """
        Met à jour les chemins Minecraft dans l'UI et stocke les chemins validés.
        Sauvegarde le chemin dans la configuration locale.
        """
        self.mc_path_input.setText(path)
        sub_paths = get_minecraft_sub_paths(path)
        if sub_paths:
            self.minecraft_paths = sub_paths
            self.mods_path_label.setText(f"Dossier Mods : {self.minecraft_paths['mods']}")
            self.saves_path_label.setText(f"Dossier Saves : {self.minecraft_paths['saves']}")
            self.resourcepacks_path_label.setText(f"Dossier Resourcepacks : {self.minecraft_paths['resourcepacks']}")
            
            # Sauvegarde le chemin validé dans le fichier de configuration
            config = load_config()
            config['minecraft_path'] = path
            save_config(config)

            if show_message:
                QMessageBox.information(self, "Chemin Minecraft Configuré", 
                                        f"Le dossier Minecraft a été configuré manuellement : {path}")
        else:
            self.minecraft_paths = None
            self.mods_path_label.setText("Dossier Mods : Introuvable ou chemin invalide")
            self.saves_path_label.setText("Dossier Saves : Introuvable ou chemin invalide")
            self.resourcepacks_path_label.setText("Dossier Resourcepacks : Introuvable ou chemin invalide")
            
            # Supprime le chemin invalide de la config s'il existait
            config = load_config()
            if 'minecraft_path' in config:
                del config['minecraft_path']
                save_config(config)

            if show_message:
                QMessageBox.warning(self, "Chemin Invalide", 
                                    "Le chemin sélectionné ne semble pas être une instance Minecraft valide (dossiers 'mods', 'saves', 'resourcepacks' introuvables).")

    # --- Fonctions de Vérification et Traitement des Mises à Jour ---
    def check_for_updates(self):
        """
        Lance la vérification de toutes les mises à jour en téléchargeant le updates.json
        depuis GitHub dans un thread séparé.
        """
        self.header_label.setText("Vérification des mises à jour en cours...")
        self.mod_status_label.setText("Statut du Mod: Vérification...")
        self.launcher_status_label.setText("Statut du Launcher: Vérification...")
        
        # Désactive les boutons pendant la vérification pour éviter les clics multiples
        self.update_mod_button.setEnabled(False)
        self.update_launcher_button.setEnabled(False)

        # Cache les barres de progression au début de la vérification
        self.mod_progress_bar.hide()
        self.launcher_progress_bar.hide()

        # Crée et démarre le thread de vérification
        self.update_checker_thread = UpdateCheckerThread(UPDATES_JSON_URL)
        # Connecte les signaux du thread à des slots (fonctions) dans la classe principale
        self.update_checker_thread.update_data_ready.connect(self.process_remote_updates)
        self.update_checker_thread.error_occurred.connect(self.handle_update_error)
        self.update_checker_thread.start() # Démarre l'exécution du thread

    def process_remote_updates(self):
        """
        Récupère les données JSON du thread et lance la logique de comparaison des versions
        pour le launcher, le mod et les maps.
        """
        self.remote_updates_data = self.update_checker_thread.update_data
        
        if self.remote_updates_data:
            self.header_label.setText("ZombieRool Launcher - Mises à jour vérifiées")
            QMessageBox.information(self, "Mises à jour", "Les informations de mise à jour ont été récupérées avec succès depuis GitHub !")
            
            # Appelle les fonctions spécifiques de logique de mise à jour
            self._check_launcher_update_logic()
            self._check_mod_update_logic()
            self._load_maps_for_download_logic()
        else:
            # Ce cas ne devrait normalement pas être atteint si error_occurred est bien géré,
            # mais c'est une sécurité.
            QMessageBox.critical(self, "Erreur", "Impossible de récupérer les informations de mise à jour. Vérifiez l'URL du fichier `updates.json` ou la structure JSON.")
            self.header_label.setText("ZombieRool Launcher - Erreur de mise à jour")
            # Réactive les boutons même en cas d'erreur si tu veux permettre une nouvelle tentative
            self.update_mod_button.setEnabled(True)
            self.update_launcher_button.setEnabled(True)


    def handle_update_error(self, message):
        """
        Gère l'affichage des erreurs survenues lors de la récupération du updates.json.
        """
        QMessageBox.critical(self, "Erreur de mise à jour", f"Une erreur est survenue lors de la vérification des mises à jour : {message}")
        self.header_label.setText("ZombieRool Launcher - Erreur de mise à jour")
        # Réactive les boutons en cas d'erreur pour que l'utilisateur puisse réessayer manuellement
        self.update_mod_button.setEnabled(True)
        self.update_launcher_button.setEnabled(True)
    
    # --- Logique de mise à jour du Launcher (sera développée plus tard) ---
    def _check_launcher_update_logic(self):
        """
        Compare la version locale du launcher avec la version distante dans remote_updates_data.
        """
        if not self.remote_updates_data or "launcher" not in self.remote_updates_data:
            self.launcher_status_label.setText("Statut du Launcher: Info distante introuvable.")
            return

        remote_version_str = self.remote_updates_data["launcher"]["latest_version"]
        local_version_str = __version__ # Version du launcher définie globalement
        
        try:
            # Utilise QVersionNumber pour une comparaison de version robuste (ex: 1.0.0 < 1.0.1)
            remote_version = QVersionNumber.fromString(remote_version_str)
            local_version = QVersionNumber.fromString(local_version_str)

            if remote_version > local_version:
                self.launcher_status_label.setText(f"Statut du Launcher: Nouvelle version {remote_version_str} disponible ! (Actuelle: {local_version_str})")
                self.update_launcher_button.setEnabled(True)
            else:
                self.launcher_status_label.setText(f"Statut du Launcher: À jour (v{local_version_str})")
                self.update_launcher_button.setEnabled(False) # Pas de maj nécessaire
        except Exception as e:
            self.launcher_status_label.setText(f"Statut du Launcher: Erreur de version ({e})")
            print(f"Erreur de comparaison de version du launcher: {e}")

    def update_launcher(self):
        """
        Fonction appelée quand le bouton "Mettre à jour le Launcher" est cliqué.
        Lance le téléchargement du nouveau launcher.
        """
        if not self.remote_updates_data or "launcher" not in self.remote_updates_data:
            QMessageBox.warning(self, "Mise à jour Launcher", "Informations de mise à jour du launcher introuvables.")
            return

        launcher_info = self.remote_updates_data["launcher"]
        download_url = launcher_info.get("download_url")
        if not download_url:
            QMessageBox.warning(self, "Mise à jour Launcher", "URL de téléchargement du launcher introuvable dans les données de mise à jour.")
            return
        
        # Détermine le chemin temporaire pour le nouveau launcher
        temp_dir = os.path.join(os.path.dirname(sys.executable), "temp_launcher_update")
        os.makedirs(temp_dir, exist_ok=True)
        # Utilise le nom de fichier de l'URL pour le fichier temporaire
        file_name = os.path.basename(QUrl(download_url).path())
        temp_destination_path = os.path.join(temp_dir, file_name)

        QMessageBox.information(self, "Mise à jour Launcher", f"Téléchargement de la nouvelle version du launcher ({launcher_info['latest_version']}) en cours...")
        self.launcher_progress_bar.setValue(0)
        self.launcher_progress_bar.show()
        self.update_launcher_button.setEnabled(False)
        self.launcher_status_label.setText("Téléchargement en cours...")

        self.launcher_downloader = FileDownloaderThread(download_url, temp_destination_path)
        self.launcher_downloader.download_progress.connect(self.launcher_progress_bar.setValue)
        self.launcher_downloader.download_finished.connect(self._install_new_launcher)
        self.launcher_downloader.download_error.connect(self._handle_launcher_download_error)
        self.launcher_downloader.start()

    def _install_new_launcher(self, temp_launcher_path):
        """
        Après le téléchargement, lance le nouveau launcher et ferme l'ancien.
        """
        self.launcher_progress_bar.hide()
        self.launcher_status_label.setText("Téléchargement terminé. Lancement de la mise à jour...")
        QMessageBox.information(self, "Mise à jour Launcher", "Nouvelle version téléchargée. Le launcher va se relancer.")
        
        try:
            # Construire la commande pour lancer le nouveau launcher
            # Utilise 'start' sur Windows pour lancer le processus et libérer le parent
            if platform.system() == "Windows":
                command = f'start "" "{temp_launcher_path}"'
            else: # Pour macOS/Linux, peut nécessiter des permissions d'exécution
                os.chmod(temp_launcher_path, 0o755) # Rend le fichier exécutable
                command = f'"{temp_launcher_path}"'
            
            os.system(command) # Exécute le nouveau launcher
            QApplication.quit() # Ferme l'application actuelle
        except Exception as e:
            QMessageBox.critical(self, "Erreur Mise à Jour Launcher", f"Impossible de lancer la nouvelle version du launcher : {e}. Veuillez télécharger la mise à jour manuellement.")
            self.launcher_status_label.setText(f"Erreur lors du lancement de la mise à jour : {e}")
            self.update_launcher_button.setEnabled(True) # Réactiver le bouton en cas d'échec

    def _handle_launcher_download_error(self, message):
        """Gère les erreurs de téléchargement du launcher."""
        self.launcher_progress_bar.hide()
        QMessageBox.critical(self, "Erreur de Téléchargement du Launcher", message)
        self.launcher_status_label.setText(f"Échec du téléchargement: {message}")
        self.update_launcher_button.setEnabled(True)

    # --- Logique de mise à jour du Mod (implémentation) ---
    def _check_mod_update_logic(self):
        """
        Compare la version locale du mod avec la version distante.
        """
        if not self.remote_updates_data or "mod" not in self.remote_updates_data:
            self.mod_status_label.setText("Statut du Mod: Info distante introuvable.")
            return

        remote_mod_info = self.remote_updates_data["mod"]
        remote_mod_version_str = remote_mod_info["latest_version"]
        
        local_mod_version_str = self._get_local_mod_version() # Récupère la version locale
        
        try:
            remote_version = QVersionNumber.fromString(remote_mod_version_str)
            local_version = QVersionNumber.fromString(local_mod_version_str)

            if remote_version > local_version:
                self.mod_status_label.setText(f"Statut du Mod: Nouvelle version {remote_mod_version_str} disponible ! (Actuelle: {local_mod_version_str})")
                self.update_mod_button.setEnabled(True)
            else:
                self.mod_status_label.setText(f"Statut du Mod: À jour (v{local_version_str})")
                self.update_mod_button.setEnabled(False)
        except Exception as e:
            self.mod_status_label.setText(f"Statut du Mod: Erreur de version ({e})")
            print(f"Erreur de comparaison de version du mod: {e}")

    def _get_local_mod_version(self):
        """
        Tente de trouver la version du mod localement dans le dossier 'mods'.
        Ceci est une implémentation simplifiée qui suppose un préfixe de nom de fichier.
        Une meilleure approche serait de lire un fichier de version si le mod en contient un.
        """
        if not self.minecraft_paths or not self.minecraft_paths['mods'] or \
           not os.path.isdir(self.minecraft_paths['mods']):
            return "0.0.0" # Version par défaut si le chemin n'est pas valide

        mod_dir = self.minecraft_paths['mods']
        for filename in os.listdir(mod_dir):
            if filename.startswith(MOD_FILE_PREFIX) and filename.endswith(".jar"):
                # Essaye d'extraire la version du nom de fichier, ex: "ZombieRool-1.3.0.jar" -> "1.3.0"
                try:
                    version_part = filename[len(MOD_FILE_PREFIX):-len(".jar")]
                    # Supprime tout ce qui n'est pas un chiffre ou un point
                    clean_version = "".join(c for c in version_part if c.isdigit() or c == '.')
                    return clean_version
                except:
                    continue # Ignore si l'extraction de version échoue
        return "0.0.0" # Mod non trouvé ou version non extractible

    def update_mod(self):
        """
        Fonction appelée quand le bouton "Mettre à jour le Mod" est cliqué.
        Télécharge et installe le mod.
        """
        if not self.minecraft_paths or not self.minecraft_paths['mods']:
            QMessageBox.warning(self, "Erreur d'Installation", "Le dossier 'mods' de Minecraft n'est pas configuré. Veuillez le définir dans les 'Paramètres'.")
            return
        
        mod_info = self.remote_updates_data["mod"]
        download_url = mod_info.get("download_url")
        if not download_url:
            QMessageBox.warning(self, "Mise à jour Mod", "URL de téléchargement du mod introuvable dans les données de mise à jour.")
            return

        # Chemin où le fichier temporaire sera téléchargé
        temp_download_dir = os.path.join(os.getcwd(), "temp_downloads")
        os.makedirs(temp_download_dir, exist_ok=True)
        mod_filename = os.path.basename(QUrl(download_url).path())
        temp_mod_path = os.path.join(temp_download_dir, mod_filename)

        QMessageBox.information(self, "Mise à jour Mod", f"Téléchargement du mod ({mod_info['latest_version']}) en cours...")
        self.mod_progress_bar.setValue(0)
        self.mod_progress_bar.show()
        self.update_mod_button.setEnabled(False)
        self.mod_status_label.setText("Téléchargement du mod en cours...")

        self.mod_downloader = FileDownloaderThread(download_url, temp_mod_path)
        self.mod_downloader.download_progress.connect(self.mod_progress_bar.setValue)
        self.mod_downloader.download_finished.connect(self._install_mod_from_temp)
        self.mod_downloader.download_error.connect(self._handle_mod_download_error)
        self.mod_downloader.start()

    def _install_mod_from_temp(self, temp_mod_path):
        """
        Déplace le mod téléchargé depuis le dossier temporaire vers le dossier mods de Minecraft.
        Supprime les anciennes versions du mod.
        """
        self.mod_progress_bar.hide()
        self.mod_status_label.setText("Installation du mod en cours...")
        
        try:
            mod_dir = self.minecraft_paths['mods']
            # Supprime les anciennes versions du mod
            for filename in os.listdir(mod_dir):
                if filename.startswith(MOD_FILE_PREFIX) and filename.endswith(".jar"):
                    os.remove(os.path.join(mod_dir, filename))
                    print(f"Ancienne version du mod supprimée : {filename}")

            # Déplace le nouveau mod téléchargé
            shutil.move(temp_mod_path, os.path.join(mod_dir, os.path.basename(temp_mod_path)))
            QMessageBox.information(self, "Mise à jour Mod", "Mod mis à jour et installé avec succès !")
            self.mod_status_label.setText(f"Statut du Mod: À jour (v{self.remote_updates_data['mod']['latest_version']})")
            self.update_mod_button.setEnabled(False) # Désactiver après mise à jour
        except Exception as e:
            QMessageBox.critical(self, "Erreur d'Installation Mod", f"Une erreur est survenue lors de l'installation du mod : {e}")
            self.mod_status_label.setText(f"Échec de l'installation: {e}")
            self.update_mod_button.setEnabled(True) # Réactiver en cas d'échec
        finally:
            # Nettoie le fichier temporaire
            if os.path.exists(temp_mod_path):
                os.remove(temp_mod_path)

    def _handle_mod_download_error(self, message):
        """Gère les erreurs de téléchargement du mod."""
        self.mod_progress_bar.hide()
        QMessageBox.critical(self, "Erreur de Téléchargement Mod", message)
        self.mod_status_label.setText(f"Échec du téléchargement: {message}")
        self.update_mod_button.setEnabled(True)

    # --- Logique de chargement des Maps pour le téléchargement (implémentation) ---
    def _load_maps_for_download_logic(self):
        """
        Charge et affiche les cartes disponibles pour le téléchargement
        à partir des données de remote_updates_data.
        """
        # Nettoie le conteneur des maps existantes avant de recharger
        while self.maps_container_layout.count():
            child = self.maps_container_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not self.remote_updates_data or "maps" not in self.remote_updates_data:
            map_status = QLabel("Aucune information sur les maps disponible.")
            self.maps_container_layout.addWidget(map_status)
            return

        for map_info in self.remote_updates_data["maps"]:
            self.add_map_to_download_list(map_info)

    def add_map_to_download_list(self, map_info):
        """
        Ajoute un élément visuel pour chaque map dans l'onglet de téléchargement.
        """
        map_widget = QWidget()
        map_widget.setStyleSheet("background-color: #F8F8F8; border: 1px solid #DDD; border-radius: 5px; padding: 10px; margin-bottom: 5px;")
        map_layout = QHBoxLayout(map_widget)
        
        map_details = QVBoxLayout()
        map_details.addWidget(QLabel(f"<b>{map_info['name']}</b> <span style='color:#555;'> (v{map_info['latest_version']})</span>"))
        map_details.addWidget(QLabel(map_info.get('description', 'Pas de description disponible.'))) # Ajout d'une description
        map_layout.addLayout(map_details)

        # Barre de progression spécifique à chaque map
        map_progress_bar = QProgressBar(self)
        map_progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        map_progress_bar.hide()
        map_details.addWidget(map_progress_bar) # Ajoute la barre de progression sous les détails

        download_button = QPushButton(f"Installer Map")
        download_button.setFixedSize(120, 30)
        download_button.setStyleSheet("background-color: #2ECC71; color: white; border-radius: 5px; padding: 5px;")
        # Passe la barre de progression en plus des informations de la map
        download_button.clicked.connect(lambda checked, info=map_info, pb=map_progress_bar: self.install_map(info, pb)) 
        map_layout.addWidget(download_button)

        self.maps_container_layout.addWidget(map_widget)

    def install_map(self, map_info, progress_bar):
        """
        Fonction appelée quand le bouton "Installer Map" est cliqué.
        Télécharge et installe la map et son resource pack associé.
        """
        if not self.minecraft_paths or not self.minecraft_paths['saves'] or not self.minecraft_paths['resourcepacks']:
            QMessageBox.warning(self, "Erreur d'Installation", "Les dossiers 'saves' ou 'resourcepacks' de Minecraft ne sont pas configurés. Veuillez les définir dans les 'Paramètres'.")
            return
            
        map_download_url = map_info.get("download_url")
        rp_download_url = map_info.get("resourcepack_url")

        if not map_download_url:
            QMessageBox.warning(self, "Installation Map", "URL de téléchargement de la map introuvable.")
            return

        # Détermine les chemins temporaires et de destination
        temp_download_dir = os.path.join(os.getcwd(), "temp_downloads")
        os.makedirs(temp_download_dir, exist_ok=True)
        
        map_filename = os.path.basename(QUrl(map_download_url).path())
        temp_map_path = os.path.join(temp_download_dir, map_filename)

        rp_filename = os.path.basename(QUrl(rp_download_url).path()) if rp_download_url else None
        temp_rp_path = os.path.join(temp_download_dir, rp_filename) if rp_filename else None

        QMessageBox.information(self, f"Installation de {map_info['name']}",
                                f"Téléchargement de la map '{map_info['name']}' en cours...")
        
        progress_bar.setValue(0)
        progress_bar.show()

        # Démarrer le téléchargement de la map
        self.map_downloader = FileDownloaderThread(map_download_url, temp_map_path)
        self.map_downloader.download_progress.connect(progress_bar.setValue)
        # CORRECTION : Passer rp_filename explicitement à _install_map_files
        self.map_downloader.download_finished.connect(
            lambda path=temp_map_path, rp_url=rp_download_url, rp_path=temp_rp_path, map_name=map_info['name'], pb=progress_bar, rp_file_name_val=rp_filename: 
            self._install_map_files(path, rp_url, rp_path, map_name, pb, rp_file_name_val)
        )
        self.map_downloader.download_error.connect(lambda msg: self._handle_map_download_error(msg, progress_bar))
        self.map_downloader.start()

    # CORRECTION : Ajout de rp_filename_val comme paramètre
    def _install_map_files(self, temp_map_path, rp_download_url, temp_rp_path, map_name, progress_bar, rp_filename_val):
        """
        Décompresse la map, télécharge et décompresse le resource pack si présent.
        """
        progress_bar.hide()
        QMessageBox.information(self, "Installation Map", f"Map '{map_name}' téléchargée. Installation en cours...")

        try:
            # 1. Décompresser la map
            saves_dir = self.minecraft_paths['saves']
            # Le dossier de la map est généralement le premier dossier à l'intérieur du zip
            with zipfile.ZipFile(temp_map_path, 'r') as zip_ref:
                # Extraire uniquement le premier répertoire racine ou tous les fichiers
                # On suppose que le zip contient un seul dossier racine de map
                map_folder_name = os.path.commonprefix(zip_ref.namelist())
                # CORRECTION: os.grav_path n'existe pas, il faut utiliser os.path.join
                zip_ref.extractall(saves_dir)
                extracted_path = os.path.join(saves_dir, map_folder_name.split('/')[0]) # Prends le nom du dossier racine
                if os.path.exists(extracted_path) and os.path.basename(extracted_path) != map_name:
                    # Ne pas renommer s'il y a un conflit, juste informer
                    if not os.path.exists(os.path.join(saves_dir, map_name)):
                        os.rename(extracted_path, os.path.join(saves_dir, map_name))
                        print(f"Dossier de map renommé de {extracted_path} à {os.path.join(saves_dir, map_name)}")
                    else:
                        print(f"Le dossier {map_name} existe déjà, ne renomme pas {extracted_path}")


            # 2. Télécharger et installer le resource pack si nécessaire
            if rp_download_url and temp_rp_path:
                QMessageBox.information(self, "Installation Resource Pack", "Téléchargement du resource pack associé en cours...")
                progress_bar.setValue(0)
                progress_bar.show()

                self.rp_downloader = FileDownloaderThread(rp_download_url, temp_rp_path)
                self.rp_downloader.download_progress.connect(progress_bar.setValue)
                self.rp_downloader.download_finished.connect(
                    # CORRECTION : Utiliser rp_filename_val ici
                    lambda path=temp_rp_path, rp_name_for_install=rp_filename_val: self._install_resource_pack_from_temp(path, rp_name_for_install, progress_bar)
                )
                self.rp_downloader.download_error.connect(lambda msg: self._handle_map_download_error(msg, progress_bar, is_rp=True))
                self.rp_downloader.start()
            else:
                QMessageBox.information(self, "Installation Terminée", f"Map '{map_name}' installée avec succès ! (Pas de Resource Pack associé)")
                # La vérification de maj du mod et la mise à jour des statuts est déplacée
                # après l'installation complète du RP, ou ici si pas de RP.
                self.mod_status_label.setText("Statut du Mod: Vérification en cours...") # Réactualise après installation
                self._check_mod_update_logic() # Pour forcer la vérification de maj après install
                progress_bar.hide() # Assure que la barre est cachée
        except zipfile.BadZipFile:
            QMessageBox.critical(self, "Erreur de décompression", "Le fichier ZIP de la map est corrompu ou invalide. Le module 'zipfile' ne supporte que le format ZIP (pas RAR).")
        except Exception as e:
            QMessageBox.critical(self, "Erreur d'Installation Map", f"Une erreur est survenue lors de l'installation de la map : {e}")
        finally:
            # Nettoie le fichier temporaire de la map
            if os.path.exists(temp_map_path):
                os.remove(temp_map_path)
            # Nettoie le dossier temporaire s'il est vide
            if os.path.exists(os.path.dirname(temp_map_path)) and not os.listdir(os.path.dirname(temp_map_path)):
                shutil.rmtree(os.path.dirname(temp_map_path))


    def _install_resource_pack_from_temp(self, temp_rp_path, rp_name, progress_bar):
        """
        Déplace le resource pack téléchargé vers le dossier resourcepacks de Minecraft.
        """
        progress_bar.hide()
        QMessageBox.information(self, "Installation Resource Pack", "Resource pack téléchargé. Installation en cours...")
        try:
            rp_dir = self.minecraft_paths['resourcepacks']
            # Les resource packs sont souvent juste copiés en tant que .zip ou décompressés s'ils contiennent un seul dossier
            # On va copier le fichier zip directement
            destination_rp_path = os.path.join(rp_dir, rp_name)
            
            # Si un ancien RP avec le même nom existe, le supprimer
            if os.path.exists(destination_rp_path):
                os.remove(destination_rp_path)

            shutil.move(temp_rp_path, destination_rp_path)
            QMessageBox.information(self, "Installation Terminée", "Resource Pack installé avec succès ! Map et Resource Pack sont prêts.")
        except Exception as e:
            QMessageBox.critical(self, "Erreur d'Installation RP", f"Une erreur est survenue lors de l'installation du resource pack : {e}")
        finally:
            # Nettoie le fichier temporaire du RP
            if os.path.exists(temp_rp_path):
                os.remove(temp_rp_path)
            # Nettoie le dossier temporaire s'il est vide après le traitement des deux fichiers
            if os.path.exists(os.path.dirname(temp_rp_path)) and not os.listdir(os.path.dirname(temp_rp_path)):
                shutil.rmtree(os.path.dirname(temp_rp_path))
        
        self.mod_status_label.setText("Statut du Mod: Vérification en cours...") # Réactualise après installation
        self._check_mod_update_logic() # Pour forcer la vérification de maj après install


    def _handle_map_download_error(self, message, progress_bar, is_rp=False):
        """Gère les erreurs de téléchargement de map ou de resource pack."""
        progress_bar.hide()
        component_name = "Resource Pack" if is_rp else "Map"
        QMessageBox.critical(self, f"Erreur de Téléchargement {component_name}", message)
        # Ici, tu pourrais réactiver le bouton d'installation de la map spécifique si tu avais une référence

# --- DÉMARRAGE DE L'APPLICATION ---
if __name__ == "__main__":
    # Crée l'instance de l'application PyQt
    app = QApplication(sys.argv)
    
    # Crée et affiche la fenêtre principale du launcher
    launcher = ZombieRoolLauncher()
    launcher.show()
    
    # Lance la boucle d'événements de l'application. Le programme reste actif tant que cette boucle tourne.
    sys.exit(app.exec())

