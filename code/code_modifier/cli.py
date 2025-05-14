# code/code_modifier/cli.py
"""
Gestion des arguments de la ligne de commande pour l'Orchestrateur de Modification de Code.
"""

import argparse
from pathlib import Path
import sys
import traceback

# Tentative de chargement de global_config pour les chemins par défaut.
global_config_module = None
DEFAULT_WORKSPACE_PATH_STR_MODIFIER = "./workspace" # Fallback

try:
    CURRENT_SCRIPT_DIR = Path(__file__).resolve().parent # code_modifier/
    PROJECT_ROOT_FOR_CLI = CURRENT_SCRIPT_DIR.parent     # code/
    if str(PROJECT_ROOT_FOR_CLI) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT_FOR_CLI))
    import global_config as gc_module
    global_config_module = gc_module
    DEFAULT_WORKSPACE_PATH_STR_MODIFIER = str(global_config_module.WORKSPACE_PATH)
except (ImportError, AttributeError, Exception) as e:
     print(f"AVERTISSEMENT [CodeModifier CLI Init]: Impossible de charger global_config ({type(e).__name__}: {e}). "
           "Utilisation de chemins par défaut.", file=sys.stderr)


MODIFY_WORKFLOW_DESCRIPTION = """
Workflow de Modification de Code:
  Ce workflow prend une requête utilisateur en langage naturel et tente de
  modifier le code source cible en conséquence. Il comprend plusieurs étapes :
  1. Optimization: Sélection des fragments de code pertinents.
  2. Planning: Génération d'un plan d'action par un LLM.
  3. Workspace Prep: Création d'une copie isolée du projet cible.
  4. Execution: Application du plan par des agents LLM spécialisés, avec
     des cycles de build/test et de correction.
  5. Finalization: Si succès, application des changements au projet original.
"""

def parse_arguments() -> argparse.Namespace | None:
    """
    Parse et valide les arguments de la ligne de commande pour l'Orchestrateur de Modification.
    """
    parser = argparse.ArgumentParser(
        prog="python -m code_modifier.main", # Mis à jour pour le nouveau point d'entrée
        description="Orchestrateur IA multi-agent pour la modification de code basée sur un prompt.",
        epilog=MODIFY_WORKFLOW_DESCRIPTION,
        formatter_class=argparse.RawTextHelpFormatter
    )

    # --- Arguments spécifiques au workflow de modification ---
    parser.add_argument(
        "user_request",
        help="Description CLAIRE et DÉTAILLÉE (langage naturel) de la tâche de modification de code à effectuer."
    )
    parser.add_argument(
        "-w", "--workspace",
        default=DEFAULT_WORKSPACE_PATH_STR_MODIFIER,
        metavar="WORKSPACE_DIR",
        help=f"Répertoire de travail principal pour les artefacts (manifeste, plan, etc.).\n(Défaut: {DEFAULT_WORKSPACE_PATH_STR_MODIFIER})"
    )
    parser.add_argument(
        "--manifest-file",
        default="fragments_manifest.json",
        metavar="FILENAME",
        help="Nom du fichier manifeste (relatif au workspace) à LIRE.\n(Défaut: fragments_manifest.json)"
    )
    parser.add_argument(
        "--stop-after",
        choices=['optimization', 'planning', 'workspace_prep', 'execution'],
        default=None,
        metavar="STAGE",
        help="Optionnel: Arrêter le workflow de modification après l'étape STAGE spécifiée."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Active les logs de débogage détaillés."
    )

    try:
        args = parser.parse_args()

        # --- Validation et Normalisation des Chemins ---
        args.workspace_path = Path(args.workspace).resolve()
        try:
            args.workspace_path.mkdir(parents=True, exist_ok=True)
        except OSError as e_mkdir:
            parser.error(f"Le répertoire workspace '{args.workspace_path}' est invalide ou inaccessible: {e_mkdir}")

        args.manifest_read_path = args.workspace_path / args.manifest_file
        
        target_proj_path_conf = None
        if global_config_module and hasattr(global_config_module, 'TARGET_PROJECT_PATH'):
            target_proj_path_conf = global_config_module.TARGET_PROJECT_PATH
        else:
            parser.error("Erreur critique: global_config non chargé ou TARGET_PROJECT_PATH non défini.")

        if not target_proj_path_conf or not isinstance(target_proj_path_conf, Path) or not target_proj_path_conf.is_dir():
             parser.error(f"TARGET_PROJECT_PATH ('{target_proj_path_conf}') est invalide ou non répertoire.")
        args.validated_target_path = target_proj_path_conf.resolve()

    except SystemExit:
        return None 
    except Exception as e_cli_unexpected:
        print(f"\nErreur inattendue lors de l'analyse des arguments CLI pour CodeModifier: {e_cli_unexpected}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return None

    return args

if __name__ == '__main__':
    print("--- Test du Parsing des Arguments CLI pour CodeModifier ---")
    test_argv_list = [
        ["Ajouter une nouvelle fonctionnalité X"],
        ["Modifier la fonction Y pour retourner Z", "--stop-after", "planning"],
        ["--debug", "Réparer le bug dans le module A"],
        ["--help"],
    ]
    original_argv = sys.argv
    for i, test_args_sim in enumerate(test_argv_list):
        print(f"\n--- Test CLI CodeModifier Scenario #{i+1} ---")
        sys.argv = [original_argv[0]] + test_args_sim
        print(f"Simulating: python -m code_modifier.main {' '.join(test_args_sim)}")
        parsed_args_test = parse_arguments()
        if parsed_args_test:
            print("Arguments parsés:"); [print(f"  {k:<22}: {v} (Type: {type(v).__name__})") for k,v in vars(parsed_args_test).items()]
        else:
            print("Parsing échoué ou --help.")
    sys.argv = original_argv
    print("\n--- Fin des Tests CLI CodeModifier ---")