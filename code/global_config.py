# global_config.py
# Configuration Globale du Projet
import os
import sys
from pathlib import Path
from dotenv import load_dotenv # <<< AJOUTER CET IMPORT

# --- Chemins Essentiels ---
PROJECT_ROOT_DIR = Path(__file__).resolve().parent
WORKSPACE_PATH = PROJECT_ROOT_DIR / "workspace"

# --- Chargement du fichier .env (Fallback) ---
# Cherche un fichier .env à la racine du projet
DOTENV_PATH = PROJECT_ROOT_DIR / ".env"
# load_dotenv va charger les variables du .env dans os.environ
# MAIS ne remplacera PAS les variables déjà existantes dans l'environnement système
# grâce à override=False. C'est exactement le comportement de fallback souhaité.
dotenv_loaded = load_dotenv(dotenv_path=DOTENV_PATH, override=False, verbose=True)

if dotenv_loaded:
    print(f"Configuration chargée depuis : {DOTENV_PATH}")
else:
    print(f"Fichier .env non trouvé ou vide à : {DOTENV_PATH}. Utilisation des variables d'environnement système uniquement.")
# ------------------------------------------------

# --- Configuration du Projet Cible ---
# !! À CONFIGURER ABSOLUMENT (via env var ou .env) !!
DEFAULT_TARGET_PATH = "code" # Le défaut si ni env var ni .env ne le définissent
# os.getenv lira d'abord la variable système, puis celle chargée depuis .env si elle existe
TARGET_PROJECT_PATH_STR = os.getenv("TARGET_PROJECT_PATH", DEFAULT_TARGET_PATH)
TARGET_PROJECT_PATH = PROJECT_ROOT_DIR / TARGET_PROJECT_PATH_STR

# --- Clé API Principale (Exemple - les agents liront la leur) ---
# Juste pour montrer l'exemple, l'agent Summarizer lira sa propre clé via config.json
DEFAULT_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # Sera lu depuis l'env ou .env

# --- Commande de Build ---
BUILD_COMMAND = os.getenv("TARGET_BUILD_COMMAND", "make run")

# --- Configuration pour l'Orchestrateur ---
MAX_BUILD_RETRIES = int(os.getenv("MAX_BUILD_RETRIES", 5)) # Lire comme int

# --- Vérification Initiale et Affichage ---
print("\n--- Global Config Loaded ---")
print(f"Project Root Dir        : {PROJECT_ROOT_DIR}")
print(f"Workspace Path          : {WORKSPACE_PATH}")
print(f"Target Project Path     : {TARGET_PROJECT_PATH}")
target_path_exists = TARGET_PROJECT_PATH.is_dir()
if not target_path_exists:
     print("!! ATTENTION: Le répertoire du projet cible N'EXISTE PAS.")
     if TARGET_PROJECT_PATH_STR == DEFAULT_TARGET_PATH:
         print(f"   Utilisation du défaut '{DEFAULT_TARGET_PATH}'. Créez ce dossier ou configurez TARGET_PROJECT_PATH.")
     else:
         print(f"   Vérifiez le chemin '{TARGET_PROJECT_PATH_STR}' défini via env var ou .env.")
else:
     print(f"   (Répertoire cible trouvé: {target_path_exists})")

# Vérifier si la clé API principale est définie (exemple)
if not DEFAULT_GEMINI_API_KEY:
     print("!! ATTENTION: GEMINI_API_KEY non définie (via env var ou .env).")
else:
     print(f"   GEMINI_API_KEY        : {'*' * (len(DEFAULT_GEMINI_API_KEY) - 4) + DEFAULT_GEMINI_API_KEY[-4:]}") # Masquer la clé

print(f"Build Command           : {BUILD_COMMAND}")
print(f"Max Build Retries       : {MAX_BUILD_RETRIES}")
print("----------------------------\n")


# Fonction utilitaire pour obtenir le chemin cible validé
def get_validated_target_path():
    """Retourne le chemin cible résolu s'il est valide, sinon None."""
    if not TARGET_PROJECT_PATH.is_dir():
        print(f"Erreur critique: Le chemin du projet cible configuré n'est pas un répertoire valide: {TARGET_PROJECT_PATH}", file=sys.stderr)
        return None
    return TARGET_PROJECT_PATH.resolve()