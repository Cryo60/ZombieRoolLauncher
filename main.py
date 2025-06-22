import sys
import os # Importation de os pour les opérations de chemin

# Déterminer le répertoire de base de l'application.
# Si l'application est exécutée par PyInstaller en mode 'onefile', sys._MEIPASS pointe
# vers le répertoire temporaire où les fichiers sont extraits.
# Sinon, c'est le répertoire du script lui-même.
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # Exécuté dans un bundle PyInstaller
    base_dir = sys._MEIPASS
else:
    # Exécuté en tant que script Python normal
    base_dir = os.path.dirname(os.path.abspath(__file__))

# Ajouter le répertoire 'main' (qui est votre paquet Python) au chemin de recherche des modules.
# Cela garantit que Python peut trouver 'main.launcher' et les autres modules.
main_package_path = os.path.join(base_dir, 'main')
if main_package_path not in sys.path:
    sys.path.insert(0, main_package_path) # Insérer au début pour lui donner la priorité

# Imports PyQt6
from PyQt6.QtWidgets import QApplication

# Import de la classe principale du lanceur depuis le sous-module 'main.launcher'
# Maintenant, il devrait pouvoir trouver 'launcher' à l'intérieur du paquet 'main'
from main.launcher import ZombieRoolLauncher

# --- DÉBUT DE L'APPLICATION ---
if __name__ == "__main__":
    # Créer l'instance de l'application PyQt
    app = QApplication(sys.argv)
    
    # Créer et afficher la fenêtre principale du lanceur
    launcher = ZombieRoolLauncher()
    launcher.show()
    
    # Démarrer la boucle d'événements de l'application. Le programme reste actif tant que cette boucle s'exécute.
    sys.exit(app.exec())
