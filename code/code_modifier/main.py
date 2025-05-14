# code/code_modifier/main.py
#!/usr/bin/env python3
"""
Orchestrateur Principal pour le Workflow de Modification de Code.
Ce script pilote le workflow de modification de code basé sur un prompt utilisateur.
Il génère un rapport des différences et un plan d'application avant la finalisation.
"""

import sys
from pathlib import Path
import argparse # Pour type hinting
import traceback
import logging
from typing import Set, Dict, Any, Optional # Ajout de Optional
import asyncio # Pour la politique d'event loop Windows
import datetime # Pour les noms de fichiers de log

# --- Configuration initiale de sys.path ---
try:
    PROJECT_ROOT_DIR = Path(__file__).resolve().parents[1] # code_modifier -> code/
    if str(PROJECT_ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT_DIR))
        print(f"INFO [CodeModifier Main Init]: Ajout de '{PROJECT_ROOT_DIR}' à sys.path.")
except IndexError:
    print(f"ERREUR CRITIQUE [CodeModifier Main Init]: Impossible de déterminer racine projet ('code').", file=sys.stderr); sys.exit(3)
# --- Fin config sys.path ---

# --- Imports modules projet ---
try:
    # Utiliser des imports relatifs pour les modules dans le même package 'code_modifier'
    from . import cli as modifier_cli 
    from .core import workflow_steps
    from .core import execution_loop
    # Imports absolus pour les modules partagés ou d'autres packages
    from manifest import manifest_io
    from lib import utils as shared_utils
    import global_config # Nécessaire pour MAX_BUILD_RETRIES, etc.
except ImportError as e_import:
    print(f"Erreur critique [CodeModifier Main]: Import échoué: {e_import}\nPYTHONPATH: {sys.path}", file=sys.stderr); traceback.print_exc(file=sys.stderr); sys.exit(2)
except Exception as e_init_imports:
    print(f"Erreur init imports [CodeModifier Main]: {e_init_imports}", file=sys.stderr); traceback.print_exc(file=sys.stderr); sys.exit(2)
# -------------------------------------------

logger = logging.getLogger(__name__) # Logger pour cet orchestrateur

def handle_modifier_pipeline_exit(message: str, exit_code: int):
    """Logue, affiche un message utilisateur concis, et termine le programme."""
    if exit_code != 0:
        logger.critical(message)
    else:
        logger.info(message)
    print(f"\nINFO: {message}") # Toujours afficher sur la console pour feedback direct
    print("Arrêt de l'Orchestrateur de Modification de Code.")
    sys.exit(exit_code)

# --- Fonction Principale de l'Orchestrateur de Modification ---
def modifier_orchestrator_main():
    """Point d'entrée pour le workflow de modification de code."""
    args = modifier_cli.parse_arguments()
    if not args: 
        # Erreur de parsing CLI gérée par modifier_cli.parse_arguments() qui lève SystemExit
        # ou retourne None, ce qui devrait déjà causer une sortie.
        sys.exit(1) 

    # Setup logging (le nom du fichier log inclut maintenant la date)
    log_file_name = f"code_modifier_run_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_file_path = args.workspace_path / log_file_name
    try:
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        shared_utils.setup_logging(debug_mode=args.debug, log_file=log_file_path)
    except Exception as e_log:
        print(f"AVERTISSEMENT CRITIQUE: Échec config logging vers '{log_file_path}': {e_log}", file=sys.stderr)
        if not logging.getLogger().hasHandlers(): # Fallback si aucun handler n'est configuré
             logging.basicConfig(
                 level=logging.INFO if not args.debug else logging.DEBUG,
                 format='%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] - %(message)s',
                 stream=sys.stderr
             )
             logger.warning("Logging fichier a échoué. Utilisation d'un logging console basique.")

    logger.info(f"--- Lancement de l'Orchestrateur de Modification de Code ---")
    logger.info(f"Requête Utilisateur (tronquée): \"{args.user_request[:100]}{'...' if len(args.user_request) > 100 else ''}\"")
    logger.info(f"Workspace: {args.workspace_path}, Manifeste: {args.manifest_read_path.name}")
    logger.info(f"Projet Cible: {args.validated_target_path}") # Doit être un Path résolu et validé par la CLI
    logger.info(f"Arrêt demandé après l'étape: {args.stop_after or 'Fin normale du workflow'}")
    if log_file_path.exists(): logger.info(f"Logs de cette exécution écrits dans: {log_file_path.resolve()}")


    # --- Début du Workflow de Modification de Code ---
    validated_request: str = args.user_request
    workspace_root: Path = args.workspace_path
    manifest_read_path: Path = args.manifest_read_path
    stop_after_stage: Optional[str] = args.stop_after
    target_project_path_validated: Path = args.validated_target_path
    
    # Initialisation des variables pour le workflow
    full_manifest_data: Optional[Dict[str, Any]] = None
    selection_result: Optional[Dict[str, Any]] = None
    workflow_plan: Optional[Dict[str, Any]] = None
    workspace_project_dir: Optional[Path] = None # Sera current_project_state
    execution_successful: bool = False # Statut après la boucle d'exécution ET la génération du plan d'apply
    last_significant_error: Optional[str] = None
    modified_files_in_workspace: Set[str] = set()
    apply_plan_file: Optional[Path] = None # Chemin vers le apply_plan_*.json

    try:
        # === Étape 1: Chargement du Manifeste de Code Complet ===
        shared_utils.print_stage_header("Phase 1: Chargement Manifeste Complet")
        if not manifest_read_path.is_file(): 
            return handle_modifier_pipeline_exit(f"ERREUR: Fichier manifeste '{manifest_read_path}' introuvable.", 1)
        full_manifest_data = manifest_io.load_manifest(manifest_read_path)
        if not full_manifest_data: 
            return handle_modifier_pipeline_exit(f"ERREUR: Impossible de charger ou parser le manifeste depuis: {manifest_read_path}", 1)
        logger.info(f"Manifeste complet chargé ({len(full_manifest_data.get('fragments', {}))} fragments).")

        # === Étape 2: Sélection Sémantique des Fragments ===
        selection_result = workflow_steps.run_semantic_fragment_selection(validated_request=validated_request)
        if not selection_result or selection_result.get("status") not in ["success", "success_no_fragments_found"]:
             return handle_modifier_pipeline_exit("ERREUR CRITIQUE: Échec de l'étape de sélection sémantique des fragments.", 1)
        
        relevant_ids = selection_result.get("relevant_fragment_ids", [])
        if not relevant_ids:
            logger.warning("La sélection sémantique n'a retourné aucun fragment pertinent. Le Planner pourrait être limité.")
        
        if stop_after_stage == 'optimization': 
            return handle_modifier_pipeline_exit(
                f"Arrêt demandé après 'optimization'. {len(relevant_ids)} ID(s) pertinent(s) sélectionné(s).", 0
            )

        # === Étape 3: Planification ===
        workflow_plan = workflow_steps.run_planning(
            validated_request=validated_request,
            relevant_fragment_ids=relevant_ids,
            selection_reasoning=selection_result.get("reasoning"),
            full_manifest_data=full_manifest_data, 
            target_project_root_path=target_project_path_validated,
            workspace_path=workspace_root 
        )
        if not workflow_plan: # run_planning retourne None si échec critique ou plan LLM non-success
             return handle_modifier_pipeline_exit("ERREUR CRITIQUE: Échec de la phase de planification.", 1)
        
        if stop_after_stage == 'planning': 
            return handle_modifier_pipeline_exit("Arrêt demandé après 'planning'. Plan généré et sauvegardé.", 0)

        # === Étape 4a: Préparation du Workspace d'Exécution ===
        workspace_project_dir = workflow_steps.prepare_execution_workspace(
            target_project_path_validated, workspace_root
        )
        if not workspace_project_dir:
             return handle_modifier_pipeline_exit("ERREUR CRITIQUE: Échec de la préparation du workspace d'exécution.", 1)
        if stop_after_stage == 'workspace_prep': 
            return handle_modifier_pipeline_exit("Arrêt demandé après 'workspace_prep'. Workspace d'exécution préparé.", 0)

        # === Étape 4b: Boucle d'Exécution et de Correction des Agents ===
        # Note: execution_loop.run_execution_loop s'occupe du build/test DANS le workspace
        loop_run_successful, last_error_from_loop, files_modified_in_ws_by_loop = execution_loop.run_execution_loop(
            workflow_plan=workflow_plan, 
            full_manifest_data=full_manifest_data, 
            current_project_state_dir=workspace_project_dir, # C'est le current_project_state
            max_retries=global_config.MAX_BUILD_RETRIES
        )
        modified_files_in_workspace.update(files_modified_in_ws_by_loop) 
        last_significant_error = last_error_from_loop

        if not loop_run_successful:
            logger.error("La boucle d'exécution et de correction des agents a échoué (build/test non réussi dans le workspace).")
            execution_successful = False # Marquer l'échec global
            # Pas besoin de générer de plan d'application si la boucle a échoué
        else:
            logger.info("Boucle d'exécution et de correction terminée avec succès (build/test réussi dans le workspace).")
            # === NOUVELLE ÉTAPE 4c: Génération du Rapport de Diff et du Plan d'Application ===
            shared_utils.print_stage_header("Phase 4c: Génération Rapport de Diff et Plan d'Application")
            if not modified_files_in_workspace:
                logger.info("Aucun fichier n'a été modifié dans le workspace par la boucle d'exécution. Pas de diff ou de plan d'application à générer.")
                execution_successful = True # La boucle a réussi, mais n'a rien fait.
            else:
                diff_report_file, plan_file = workflow_steps.generate_apply_plan_and_diff_report(
                    target_project_path=target_project_path_validated, # Original pour comparaison
                    workspace_project_dir=workspace_project_dir,       # Modifié
                    relative_paths_of_modified_files=modified_files_in_workspace,
                    workspace_path_for_reports=workspace_root # Pour y mettre modification_reports/
                )
                apply_plan_file = plan_file # Sauvegarder pour la finalisation
                
                if not diff_report_file: logger.warning("Le rapport de diff n'a pas pu être généré ou est vide.")
                if not apply_plan_file:
                    logger.error("Le plan d'application n'a pas pu être généré. La finalisation sera impossible.")
                    execution_successful = False # Échec critique si pas de plan d'application
                    last_significant_error = last_significant_error or "Échec de la génération du plan d'application."
                else:
                    execution_successful = True # Boucle OK ET plan d'application OK
        
        # Gérer --stop-after execution
        if stop_after_stage == 'execution':
             status_msg_exec_phase = "réussie" if execution_successful else \
                                     f"échouée (erreur: {last_significant_error or 'Non spécifiée'})"
             if execution_successful and apply_plan_file:
                 status_msg_exec_phase += f". Rapport de diff et plan d'application générés (plan: {apply_plan_file.name})."
             elif execution_successful and not modified_files_in_workspace:
                 status_msg_exec_phase += ". Aucune modification détectée dans le workspace."

             return handle_modifier_pipeline_exit(
                 f"Arrêt demandé après la phase d'exécution. Statut: {status_msg_exec_phase}", 
                 0 if execution_successful else 1
             )

        # === Étape 5: Finalisation (Application des changements au projet cible si succès ET plan d'application existe) ===
        if execution_successful and apply_plan_file:
            finalization_successful = workflow_steps.finalize_execution(
                apply_plan_path=apply_plan_file,
                workspace_path=workspace_root, # Pour localiser current_project_state
                target_project_path=target_project_path_validated
            )
            if not finalization_successful:
                execution_successful = False # La finalisation a échoué
                last_significant_error = last_significant_error or "Échec de l'application finale des changements depuis le plan."
        elif not apply_plan_file and execution_successful: 
            # Si la boucle était ok, mais pas de plan d'apply, c'est un échec
            execution_successful = False 
            last_significant_error = last_significant_error or "Plan d'application manquant pour la finalisation."
        # Si execution_successful était déjà False, on ne tente pas la finalisation.

    except SystemExit: # Laisser handle_modifier_pipeline_exit gérer la sortie
        raise 
    except Exception as e_workflow: # Attraper les exceptions non gérées dans le workflow
         logger.critical(f"ERREUR NON CAPTURÉE et inattendue dans l'Orchestrateur de Modification: {type(e_workflow).__name__} - {e_workflow}", exc_info=True)
         if hasattr(args, 'workspace_path') and args.workspace_path: # Vérifier si args.workspace_path existe
            logger.info(f"L'état partiel du workspace (si créé) peut se trouver dans: {args.workspace_path}")
         # Utiliser last_significant_error s'il est déjà défini par une étape précédente
         err_msg_final = last_significant_error or f"Erreur critique inattendue: {e_workflow}"
         handle_modifier_pipeline_exit(err_msg_final, 2) # Code d'erreur interne grave
         return # Pour satisfaire Pylance, bien que sys.exit() soit appelé.

    # --- Bilan Final et Code de Sortie pour le workflow 'modify' ---
    exit_code = 0 if execution_successful else 1
    final_user_message = f"Workflow de modification de code terminé avec {'SUCCÈS' if execution_successful else 'ÉCHEC'}."
    if not execution_successful: 
        final_user_message += f" Dernière erreur/raison: {last_significant_error or 'Non spécifiée par le workflow.'}"
    if execution_successful and apply_plan_file:
        final_user_message += f" Plan d'application utilisé: {apply_plan_file.name}."
    
    handle_modifier_pipeline_exit(final_user_message, exit_code)


if __name__ == "__main__":
    print("Démarrage de l'Orchestrateur de Modification de Code (depuis code_modifier/main.py)...")
    if sys.platform == "win32" and sys.version_info >= (3, 8): # Pour compatibilité LiteLLM async
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    modifier_orchestrator_main()