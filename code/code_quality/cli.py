# code/code_quality/cli.py
"""
Gestion des arguments de la ligne de commande pour l'Orchestrateur de Qualité de Code.
"""

import argparse
from pathlib import Path
import sys
import traceback
import json # Pour valider le JSON de --update-data si cette option est réintroduite

# Tentative de chargement de global_config pour les chemins par défaut.
global_config_module = None
DEFAULT_WORKSPACE_PATH_STR_QUALITY = "./workspace" # Fallback pur

try:
    CURRENT_SCRIPT_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT_FOR_CLI = CURRENT_SCRIPT_DIR.parent
    if str(PROJECT_ROOT_FOR_CLI) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT_FOR_CLI))
    import global_config as gc_module
    global_config_module = gc_module
    DEFAULT_WORKSPACE_PATH_STR_QUALITY = str(global_config_module.WORKSPACE_PATH)
except (ImportError, AttributeError, Exception) as e:
     print(f"AVERTISSEMENT [CodeQuality CLI Init]: Impossible de charger global_config ({type(e).__name__}: {e}). "
           "Utilisation de chemins par défaut.", file=sys.stderr)

QUALITY_PIPELINE_DESCRIPTION = """
Pipeline d'Agents de Qualité de Code:
  Ce pipeline exécute des agents spécialisés pour analyser le code source
  et proposer des améliorations de qualité. Il fonctionne en plusieurs actions :

  `analyze`: Analyse le code et génère des rapports JSON contenant des propositions.
     Ex: ... quality analyze --tasks docstrings filesplit

  `retry_analysis`: Ré-exécute l'analyse pour un item spécifique OU pour tous les items
     en erreur d'un rapport existant. Met à jour ou crée un rapport.
     Ex (ciblé): ... quality retry_analysis --task-type docstrings --target-fragment "id"
     Ex (toutes erreurs): ... quality retry_analysis --task-type docstrings --input-report <path>
  
  `apply`: Lit un rapport JSON (original ou mis à jour) et tente d'appliquer
     les changements suggérés au code source (actuellement en mode simulation).
     Ex: ... quality apply --report <rapport.json> --force
"""

def parse_arguments() -> argparse.Namespace | None:
    """
    Parse et valide les arguments de la ligne de commande pour l'Orchestrateur de Qualité.
    """
    parser = argparse.ArgumentParser(
        prog="python -m code_quality.main",
        description="Orchestrateur pour le pipeline d'analyse et d'application de la qualité du code.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    # --- Arguments Globaux ---
    parser.add_argument( "-w", "--workspace", default=DEFAULT_WORKSPACE_PATH_STR_QUALITY, metavar="WORKSPACE_DIR", help=f"Répertoire de travail principal.\n(Défaut: {DEFAULT_WORKSPACE_PATH_STR_QUALITY})")
    parser.add_argument( "--manifest-file", default="fragments_manifest.json", metavar="FILENAME", help="Nom du fichier manifeste (relatif au workspace).\n(Défaut: fragments_manifest.json)")
    parser.add_argument( "--debug", action="store_true", help="Active les logs de débogage détaillés.")

    quality_subparsers = parser.add_subparsers(dest="quality_command", title="Actions de qualité disponibles", metavar="<action_qualité>")
    quality_subparsers.required = True

    # --- Sous-commande 'analyze' ---
    analyze_parser = quality_subparsers.add_parser("analyze", help="Analyse le code et génère des rapports de propositions.", description="Exécute les agents QA pour générer des propositions d'amélioration.")
    analyze_parser.add_argument("--tasks", nargs='+', choices=['docstrings', 'filesplit', 'all'], default=['all'], help="Tâche(s) d'analyse à exécuter (ex: 'docstrings filesplit').\n'all' exécute toutes les tâches disponibles.")
    analyze_parser.add_argument("--target-fragment", metavar="FRAGMENT_ID", default=None, help="Optionnel (pour debug): Cible un fragment_id spécifique pour la tâche 'docstrings'.")
    analyze_parser.add_argument("--target-file", metavar="REL_FILE_PATH", default=None, help="Optionnel (pour debug): Cible un chemin de fichier relatif au projet pour la tâche 'filesplit'.")

    # --- Sous-commande 'retry_analysis' ---
    retry_parser = quality_subparsers.add_parser("retry_analysis", help="Ré-exécute l'analyse pour des items spécifiques ou tous les items en erreur d'un rapport.", description="Relance un agent QA sur un ou plusieurs items et met à jour/crée un rapport avec les nouveaux résultats.")
    retry_parser.add_argument("--task-type", choices=['docstrings', 'filesplit'], required=True, help="Type de tâche de qualité à relancer.")
    # Le groupe pour cibler un item spécifique est optionnel. Si non fourni, et si --input-report est là, on relance tous les items en erreur.
    retry_target_group = retry_parser.add_mutually_exclusive_group(required=False)
    retry_target_group.add_argument("--target-fragment", metavar="FRAGMENT_ID", help="ID du fragment spécifique à ré-analyser (pour 'docstrings').")
    retry_target_group.add_argument("--target-file", metavar="REL_FILE_PATH", help="Chemin du fichier spécifique à ré-analyser (pour 'filesplit').")
    retry_parser.add_argument("--input-report", metavar="EXISTING_REPORT.JSON", default=None, type=str, help="Rapport JSON existant. Si fourni sans --target-*, tous les items en erreur de ce rapport seront relancés et le rapport sera mis à jour.")
    retry_parser.add_argument("--output-report", metavar="OUTPUT_REPORT.JSON", default=None, type=str, help="Optionnel: Chemin pour sauvegarder le rapport (nouveau ou mis à jour). Si omis et --input-report est fourni, l'original est écrasé (après backup).")

    # --- Sous-commande 'apply' ---
    apply_parser = quality_subparsers.add_parser("apply", help="Applique les propositions d'un rapport qualité JSON (simulation actuelle).", description="Lit un rapport et tente d'appliquer les changements au code source.")
    apply_parser.add_argument("--report", metavar="REPORT.JSON", default=None, type=str, help="Optionnel: Rapport JSON spécifique à appliquer. Si omis, utilise le dernier du --task-type.")
    apply_parser.add_argument("--task-type", choices=['docstrings', 'filesplit'], required=False, help="Type de tâche du rapport. Requis si --report omis.")
    apply_parser.add_argument("--force", action="store_true", help="Appliquer les changements sans confirmation interactive (PRUDENCE!).")

    try:
        args = parser.parse_args()

        # --- Validation et Normalisation des Chemins Globaux ---
        args.workspace_path = Path(args.workspace).resolve()
        try: args.workspace_path.mkdir(parents=True, exist_ok=True)
        except OSError as e: parser.error(f"Workspace '{args.workspace_path}' invalide: {e}")
        args.manifest_read_path = args.workspace_path / args.manifest_file
        
        target_proj_path_conf = None
        if global_config_module and hasattr(global_config_module, 'TARGET_PROJECT_PATH'):
            target_proj_path_conf = global_config_module.TARGET_PROJECT_PATH
        else: parser.error("Critique: global_config n'a pas pu être chargé ou TARGET_PROJECT_PATH n'y est pas défini.")
        if not target_proj_path_conf or not isinstance(target_proj_path_conf, Path) or not target_proj_path_conf.is_dir():
             parser.error(f"TARGET_PROJECT_PATH ('{target_proj_path_conf}') est invalide ou n'est pas un répertoire.")
        args.validated_target_path = target_proj_path_conf.resolve()


        # --- Validations Spécifiques aux Sous-commandes ---
        if args.quality_command == "analyze":
            if args.target_fragment and not ("docstrings" in args.tasks or "all" in args.tasks):
                analyze_parser.error("--target-fragment ne s'applique qu'à la tâche 'docstrings' (ou 'all').")
            if args.target_file and not ("filesplit" in args.tasks or "all" in args.tasks):
                analyze_parser.error("--target-file ne s'applique qu'à la tâche 'filesplit' (ou 'all').")
        
        elif args.quality_command == "retry_analysis":
            # Si ni target-fragment ni target-file n'est spécifié, alors input-report est obligatoire
            if not args.target_fragment and not args.target_file:
                if not args.input_report:
                    retry_parser.error("Pour 'retry_analysis' sans ciblage spécifique (--target-fragment/--target-file), "
                                       "l'option --input-report est requise pour identifier les items en erreur à relancer.")
            # Valider que le target correspond au task-type
            if args.target_fragment and args.task_type != "docstrings":
                retry_parser.error("--target-fragment ne peut être utilisé qu'avec --task-type docstrings pour 'retry_analysis'.")
            if args.target_file and args.task_type != "filesplit":
                retry_parser.error("--target-file ne peut être utilisé qu'avec --task-type filesplit pour 'retry_analysis'.")

            if args.input_report:
                args.input_report = Path(args.input_report).resolve()
                if not args.input_report.is_file():
                    retry_parser.error(f"Fichier rapport d'entrée (--input-report) '{args.input_report}' introuvable.")
            if args.output_report:
                args.output_report = Path(args.output_report).resolve()
                try: args.output_report.parent.mkdir(parents=True, exist_ok=True)
                except OSError as e: retry_parser.error(f"Impossible de créer le dossier parent pour --output-report '{args.output_report.parent}': {e}")
        
        elif args.quality_command == "apply":
            if not args.report and not args.task_type:
                apply_parser.error("L'argument --task-type est requis pour 'quality apply' si --report n'est pas spécifié.")
            if args.report: # Si un rapport est fourni, il doit exister
                report_file_to_apply = Path(args.report)
                if not report_file_to_apply.is_file():
                    apply_parser.error(f"Le fichier de rapport spécifié (--report) '{args.report}' est introuvable.")
                args.report = report_file_to_apply.resolve() # Stocker comme Path résolu

    except SystemExit: # Gère parser.error() ou --help
        return None 
    except Exception as e_cli_unexpected:
        print(f"\nErreur inattendue lors de l'analyse des arguments CLI pour CodeQuality: {e_cli_unexpected}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return None

    return args

# --- Point d'entrée pour test direct de la CLI (optionnel) ---
if __name__ == '__main__':
    print("--- Test du Parsing des Arguments CLI pour l'Orchestrateur de Qualité ---")
    
    test_argv_list = [
        ["analyze", "--tasks", "docstrings"],
        ["analyze", "--tasks", "filesplit", "--target-file", "path/to/somefile.go"],
        ["retry_analysis", "--task-type", "docstrings", "--target-fragment", "some_id"],
        ["retry_analysis", "--task-type", "filesplit", "--input-report", "workspace/quality_proposals/fs_report.json"], # Retry all errors
        ["retry_analysis", "--task-type", "docstrings"], # Devrait échouer (manque target ou input-report)
        ["apply", "--task-type", "docstrings", "--force"],
        ["apply", "--report", "path/to/my_report.json"],
        ["--debug", "analyze", "--tasks", "all"],
        ["--help"],
        ["analyze", "--help"],
        ["retry_analysis", "--help"],
        ["apply", "--help"],
    ]

    original_argv = sys.argv
    for i, test_args_sim in enumerate(test_argv_list):
        print(f"\n--- Test CLI Qualité Scenario #{i+1} ---")
        sys.argv = [original_argv[0]] + test_args_sim 
        print(f"Simulating: python -m code_quality.main {' '.join(test_args_sim)}")
        
        # Pour les tests, il faut s'attendre à des SystemExit pour les cas d'erreur ou --help
        try:
            parsed_args_test = parse_arguments()
            if parsed_args_test:
                print("Arguments parsés avec succès:")
                for key, value in vars(parsed_args_test).items():
                    print(f"  {key:<22}: {value} (Type: {type(value).__name__})")
            else:
                # Si parse_arguments retourne None sans SystemExit (ce qui ne devrait pas arriver
                # avec la logique actuelle qui utilise parser.error()), c'est un cas à investiguer.
                print("parse_arguments() a retourné None sans lever SystemExit.")
        except SystemExit:
            print("Parsing des arguments a terminé avec SystemExit (normal pour --help ou erreur de validation CLI).")
    
    sys.argv = original_argv
    print("\n--- Fin des Tests CLI Qualité ---")