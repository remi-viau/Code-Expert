# code/manifest/cli.py
import argparse
from pathlib import Path
import sys
import traceback # Pour logguer les erreurs de parsing imprévues
import logging # Optionnel: pour logguer des infos du CLI lui-même si besoin

# Logger pour ce module (optionnel, généralement pas nécessaire pour la CLI pure)
# cli_logger = logging.getLogger(__name__)
# cli_logger.addHandler(logging.NullHandler()) # Évite "No handler found"

try:
    # Déterminer la racine du projet (dossier 'code')
    # manifest/cli.py -> manifest -> code
    CURRENT_DIR = Path(__file__).resolve().parent 
    PROJECT_ROOT = CURRENT_DIR.parent.parent # Remonter de deux niveaux pour atteindre 'code'
    
    # S'assurer que PROJECT_ROOT (le dossier 'code') est dans sys.path
    # Cela permet d'importer global_config de manière fiable.
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
        # print(f"INFO [Manifest CLI Init]: Ajout de {PROJECT_ROOT} à sys.path")

    import global_config # Pour le chemin par défaut du manifeste
    DEFAULT_WORKSPACE_MANIFEST_PATH = str(global_config.WORKSPACE_PATH / "fragments_manifest.json")
except (ImportError, AttributeError, IndexError) as e:
    # Fallback si global_config n'est pas chargeable (ex: exécution très isolée)
    # Utiliser print car le logger global n'est pas encore configuré.
    print(f"AVERTISSEMENT [Manifest CLI]: Impossible de charger global_config ({e}). Utilisation de chemins par défaut.", file=sys.stderr)
    DEFAULT_WORKSPACE_MANIFEST_PATH = "workspace/fragments_manifest.json"


def parse_arguments() -> argparse.Namespace | None:
    """
    Parse et valide les arguments de la ligne de commande pour l'outil Manifest.
    Les options liées à la génération de résumés par LLM ont été supprimées.
    """
    parser = argparse.ArgumentParser(
        prog="python -m manifest.main", # Indique comment lancer le programme
        description="Outil pour générer le manifeste de fragments de code à partir de l'analyse statique (AST). "
                    "Ce manifeste inclut les docstrings mais ne génère plus de résumés par LLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples d'utilisation:
  Génération complète (écrase l'existant si --no-incremental):
    python -m manifest.main --target-project-path /chemin/vers/mon/projet-go

  Génération incrémentale (par défaut, fusionne avec l'existant si possible):
    python -m manifest.main

  Spécifier un fichier de sortie différent:
    python -m manifest.main -o custom_manifest.json

  Activer les logs de débogage:
    python -m manifest.main --debug
"""
    )

    # --- Options Générales ---
    parser.add_argument(
        "-o", "--output",
        default=DEFAULT_WORKSPACE_MANIFEST_PATH,
        metavar="FILE_PATH",
        help=f"Chemin du fichier manifeste à générer/mettre à jour. "
             f"(Défaut: {DEFAULT_WORKSPACE_MANIFEST_PATH})"
    )
    parser.add_argument(
        "--target-project-path",
        default=None,
        metavar="DIR_PATH",
        help="Chemin vers le répertoire racine du projet Go à analyser. "
             "Si non fourni, utilise la valeur de TARGET_PROJECT_PATH de global_config.py."
    )
    parser.add_argument(
        "--no-incremental",
        action="store_true", # Crée un booléen, True si le flag est présent
        help="Force une regénération complète du manifeste à partir de l'AST, "
             "sans tenter de fusionner avec un manifeste existant. "
             "L'ancien manifeste sera écrasé."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Active les logs de débogage détaillés pour l'outil Manifest."
    )
    
    # Les modes spécifiques (retry, target_fragment_id, reprocess_docstrings) ont été supprimés
    # car ils étaient liés à la génération de résumés par LLM, qui est retirée.
    # L'outil se concentre maintenant sur la génération 'normale' (complète ou incrémentale)
    # basée sur l'AST.

    try:
        args = parser.parse_args()

        # --- Détermination et Validation Logique ---
        # Le mode est implicitement "normal" maintenant. 
        # L'option --no-incremental contrôle si c'est une full-regen ou une fusion.
        args.mode = "normal" 

        # Valider le chemin de sortie (le dossier parent doit exister ou être créable)
        output_path_obj = Path(args.output).resolve()
        try:
            # Essayer de créer le dossier parent si nécessaire, ne lève pas d'erreur s'il existe.
            output_path_obj.parent.mkdir(parents=True, exist_ok=True)
            args.output = str(output_path_obj) # Stocker le chemin résolu
        except OSError as e:
            parser.error(f"Impossible de créer/accéder au dossier parent pour le fichier de sortie '{args.output}': {e}")

        # La validation de target_project_path sera faite dans manifest.main.py
        # car il peut provenir de global_config.

    except SystemExit: # Gérer --help ou les erreurs de parsing d'argparse (parser.error)
        return None # Indique que le programme doit se terminer
    except Exception as e:
        # Utiliser print ici car le logger global n'est pas encore configuré par le script principal
        print(f"\nErreur imprévue lors du parsing des arguments pour l'outil Manifest: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return None

    return args

# --- Point d'entrée pour test (optionnel) ---
if __name__ == '__main__':
     # Configurer un logger basique pour les tests directs de la CLI
     logging.basicConfig(level=logging.DEBUG, format='%(levelname)s:[%(name)s CLI Test] %(message)s')
     
     print("\n--- Test du parsing des arguments CLI pour l'outil Manifest ---")
     
     # Simuler différents ensembles d'arguments pour le test
     test_scenarios = [
         [], # Test avec les valeurs par défaut
         ["--no-incremental"],
         ["-o", "test_outputs/my_manifest.json", "--debug"],
         ["--target-project-path", "/fake/project/path"],
         ["--help"] # Test de l'aide
     ]

     for i, test_argv in enumerate(test_scenarios):
         print(f"\n--- Scénario de test #{i+1} avec args: {test_argv} ---")
         # Sauvegarder et restaurer sys.argv car argparse.parse_args() le modifie globalement
         original_argv = sys.argv
         sys.argv = ['cli.py'] + test_argv # Simuler l'appel en ligne de commande
         
         parsed_args = parse_arguments()
         
         if parsed_args:
              print("Arguments parsés avec succès:")
              for key, value in vars(parsed_args).items():
                   print(f"  {key:<25}: {value} (Type: {type(value).__name__})")
         else:
              print("Échec du parsing des arguments ou --help demandé (ce qui est normal pour le scénario --help).")
         
         sys.argv = original_argv # Restaurer sys.argv
     
     print("\n--- Fin des tests CLI ---")