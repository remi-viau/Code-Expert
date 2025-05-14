# code/orchestrator/core/execution_loop.py
import sys
from pathlib import Path
import traceback 
from typing import Tuple, Optional, Set, Dict, Any, Type 
import logging

logger = logging.getLogger(__name__)

# --- Gestion des Imports et Chemins ---
try:
    CURRENT_LOOP_DIR = Path(__file__).resolve().parent # .../orchestrator/core/
    PROJECT_ROOT = CURRENT_LOOP_DIR.parents[2]       # core -> orchestrator -> code
    
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    
    import global_config # Pour BUILD_COMMAND, MAX_BUILD_RETRIES, TEMPL_GENERATE_COMMAND
    from lib import utils as shared_utils # Pour run_build_command, format_go_code, print_stage_header
    from . import context_builder # Pour assemble_expert_context
    # BaseAgent est utilisé par load_agent_class qui est importée de workflow_steps
    from .workflow_steps import load_agent_class 

except ImportError as e:
    print(f"Erreur critique [Execution Loop Init]: Imports échoués: {e}", file=sys.stderr)
    print(f"  PYTHONPATH actuel: {sys.path}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(2) 
except Exception as e_init: 
    print(f"Erreur inattendue à l'initialisation [Execution Loop]: {e_init}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(2)
# --- Fin Gestion des Imports ---


def execute_single_agent_step(
        step_data: Dict[str, Any],
        full_manifest_data: Dict[str, Any], 
        current_project_state_dir: Path,    
        previous_build_error: Optional[str]
) -> Tuple[bool, Set[str]]: # (succès_étape, ensemble_chemins_relatifs_modifiés_dans_workspace)
    """
    Exécute une seule étape du plan en appelant l'agent expert approprié.
    Applique les modifications proposées par l'agent au workspace.
    Si des fichiers .templ sont modifiés, déclenche 'templ generate'.
    """
    agent_name_from_plan = step_data.get("agent") or step_data.get("expert") 
    step_id_for_log = step_data.get("step_id", "ID d'étape inconnu")
    
    shared_utils.print_stage_header(f"Exécution Étape {step_id_for_log} - Agent: '{agent_name_from_plan}'")

    if not agent_name_from_plan:
        logger.error(f"Nom de l'agent manquant pour l'étape {step_id_for_log}. L'étape est annulée.")
        return False, set()

    AgentClass = load_agent_class(agent_name_from_plan)
    if not AgentClass:
        logger.error(f"Impossible de charger l'agent '{agent_name_from_plan}' pour l'étape {step_id_for_log}. L'étape est annulée.")
        return False, set()

    try:
        agent_instance = AgentClass() 
        logger.debug(f"Instance de l'agent '{agent_name_from_plan}' créée avec succès.")
    except Exception as e_init_agent:
        logger.error(f"L'instanciation de l'Agent '{agent_name_from_plan}' a échoué: {e_init_agent}", exc_info=True)
        return False, set()

    logger.info(f"Assemblage du contexte pour l'Agent '{agent_name_from_plan}' (Étape: {step_id_for_log})...")
    
    # Initialiser files_targeted_by_this_step à un ensemble vide avant d'appeler assemble_expert_context.
    # assemble_expert_context retournera l'ensemble des fichiers que le contexte cible,
    # ce qui est utile même si l'agent échoue plus tard, pour savoir ce qui était visé.
    files_targeted_by_this_step: Set[str] = set()
    expert_context_for_agent, files_targeted_by_this_step = context_builder.assemble_expert_context(
        step_data=step_data,
        full_manifest_data=full_manifest_data, 
        current_project_state_dir=current_project_state_dir,
        previous_build_error=previous_build_error
    )
    
    if expert_context_for_agent is None: 
        logger.error(f"Échec de l'assemblage du contexte pour l'étape {step_id_for_log}. L'étape est annulée.")
        return False, files_targeted_by_this_step # files_targeted_by_this_step peut être vide si erreur très tôt dans le builder

    logger.info(f"Appel de {agent_name_from_plan}.run() pour l'étape {step_id_for_log}...")
    agent_response: Optional[Dict[str, Any]] = None
    try:
        agent_response = agent_instance.run(expert_context_for_agent) 
        if not isinstance(agent_response, dict):
            logger.error(f"Type de retour invalide ({type(agent_response).__name__}) de {agent_name_from_plan}.run(). Attendu: dict.")
            return False, files_targeted_by_this_step 
    except Exception as e_run_agent:
        logger.error(f"Exception lors de l'appel à {agent_name_from_plan}.run(): {e_run_agent}", exc_info=True)
        return False, files_targeted_by_this_step

    # Traiter la réponse de l'agent
    if agent_response.get("status") == "success":
        modified_fragments_output = agent_response.get("modified_fragments", [])
        
        if not modified_fragments_output: 
            logger.info(f"L'agent '{agent_name_from_plan}' a terminé avec succès mais n'a retourné aucune modification de code pour cette étape.")
            return True, set() 

        if not isinstance(modified_fragments_output, list):
            logger.error(f"La clé 'modified_fragments' retournée par {agent_name_from_plan} n'est pas une liste. Type reçu: {type(modified_fragments_output)}.")
            return False, files_targeted_by_this_step 

        logger.info(f"Application de {len(modified_fragments_output)} modification(s) de code proposée(s) par l'agent '{agent_name_from_plan}' au workspace...")
        
        modifications_applied_successfully_to_workspace = True
        applied_files_in_workspace_relative_paths: Set[str] = set() 
        any_templ_file_modified_in_this_step = False

        for mod_frag_details in modified_fragments_output:
            if not isinstance(mod_frag_details, dict):
                logger.error(f"Élément invalide dans 'modified_fragments': Attendu dict, reçu {type(mod_frag_details)}. Contenu (début): {str(mod_frag_details)[:100]}")
                modifications_applied_successfully_to_workspace = False; break 
            
            relative_path_of_file_to_modify = mod_frag_details.get("path_to_modify")
            new_code_content_from_agent = mod_frag_details.get("new_content") # Renommé pour clarté
            is_templ_file_type = mod_frag_details.get("is_templ_source", False)

            if not relative_path_of_file_to_modify or \
               not isinstance(relative_path_of_file_to_modify, str) or \
               new_code_content_from_agent is None: # Peut être une chaîne vide, mais pas None
                logger.warning(f"Modification invalide ou incomplète reçue de l'agent: "
                               f"path_to_modify='{relative_path_of_file_to_modify}', new_content fourni={new_code_content_from_agent is not None}. "
                               "Cette modification sera ignorée.")
                continue 

            absolute_path_to_write_in_workspace = current_project_state_dir / relative_path_of_file_to_modify
            
            content_to_write_to_file = new_code_content_from_agent
            if not is_templ_file_type and relative_path_of_file_to_modify.endswith(".go"): 
                logger.debug(f"Formatage du code Go pour le fichier: {relative_path_of_file_to_modify}...")
                formatted_go_code, format_error_msg = shared_utils.format_go_code(new_code_content_from_agent)
                if format_error_msg:
                    logger.warning(f"Le formatage Go a échoué pour '{relative_path_of_file_to_modify}': {format_error_msg}. "
                                   "Utilisation du code brut généré par l'agent.")
                content_to_write_to_file = formatted_go_code
            
            try:
                logger.debug(f"Écriture du fichier modifié dans le workspace: {absolute_path_to_write_in_workspace}")
                absolute_path_to_write_in_workspace.parent.mkdir(parents=True, exist_ok=True)
                absolute_path_to_write_in_workspace.write_text(content_to_write_to_file, encoding='utf-8')
                applied_files_in_workspace_relative_paths.add(relative_path_of_file_to_modify)
                if is_templ_file_type:
                    any_templ_file_modified_in_this_step = True
            except Exception as e_write_file:
                logger.error(f"Échec de l'écriture du fichier '{absolute_path_to_write_in_workspace}' dans le workspace: {e_write_file}", exc_info=True)
                modifications_applied_successfully_to_workspace = False; break 

        if not modifications_applied_successfully_to_workspace:
            logger.error(f"Échec de l'application d'une ou plusieurs modifications pour l'étape {step_id_for_log}.")
            return False, set() 

        if any_templ_file_modified_in_this_step:
            logger.info("Des fichiers .templ ont été modifiés dans cette étape. Exécution de 'templ generate' dans le workspace...")
            templ_generate_cmd = getattr(global_config, 'TEMPL_GENERATE_COMMAND', 'templ generate') 
            
            templ_gen_ok, templ_gen_output = shared_utils.run_build_command(
                command=templ_generate_cmd, 
                project_dir=current_project_state_dir 
            )
            if templ_gen_ok:
                logger.info(f"'{templ_generate_cmd}' exécuté avec succès dans le workspace.")
                logger.debug(f"Sortie de '{templ_generate_cmd}':\n{templ_gen_output}")
            else:
                logger.error(f"ÉCHEC de '{templ_generate_cmd}' dans le workspace après modification de fichiers .templ.")
                logger.error(f"Sortie d'erreur de '{templ_generate_cmd}':\n{templ_gen_output}")
                return False, applied_files_in_workspace_relative_paths 
        
        logger.info(f"Toutes les modifications de l'agent '{agent_name_from_plan}' pour l'étape {step_id_for_log} ont été appliquées au workspace.")
        return True, applied_files_in_workspace_relative_paths

    else: 
        error_msg_from_agent = agent_response.get("message", f"Erreur inconnue ou non spécifiée retournée par l'agent {agent_name_from_plan}")
        logger.error(f"L'agent '{agent_name_from_plan}' a explicitement échoué pour l'étape {step_id_for_log}: {error_msg_from_agent}")
        # Retourner les fichiers que le plan ciblait, car l'agent était censé agir dessus.
        # Cela aide à la logique de restauration ou de rapport.
        return False, files_targeted_by_this_step 


def run_execution_loop(
        workflow_plan: Dict[str, Any],      
        full_manifest_data: Dict[str, Any], 
        current_project_state_dir: Path,    
        max_retries: int                    
) -> Tuple[bool, Optional[str], Set[str]]:
    """
    Exécute la boucle principale: étapes du plan -> build -> correction si échec du build.
    """
    shared_utils.print_stage_header("Phase 4b: Exécution du Workflow et Cycle de Corrections")
    
    build_attempt_number = 0
    last_build_error_output_for_agents: Optional[str] = None 
    overall_execution_successful: bool = False
    all_files_modified_in_workspace_during_loop: Set[str] = set() 

    plan_steps_list = workflow_plan.get("steps", [])
    if not plan_steps_list:
        logger.warning("Le plan d'exécution est vide. Aucune étape à exécuter. Workflow considéré comme réussi.")
        return True, None, set() 

    while build_attempt_number < max_retries:
        build_attempt_number += 1
        logger.info(f"--- Début de la Tentative d'Exécution et Build #{build_attempt_number}/{max_retries} ---")
        
        if last_build_error_output_for_agents:
            log_err_preview = last_build_error_output_for_agents[:1000] + \
                              ('...' if len(last_build_error_output_for_agents) > 1000 else '')
            logger.info(f"Erreur du build précédent fournie aux agents pour cette tentative (tronquée):\n---\n{log_err_preview}\n---")
        
        current_attempt_all_steps_succeeded_flawlessly: bool = True
        modified_files_in_current_attempt: Set[str] = set() 

        for step_data_from_plan in plan_steps_list:
            step_execution_successful, files_modified_by_this_step = execute_single_agent_step(
                step_data=step_data_from_plan,
                full_manifest_data=full_manifest_data,
                current_project_state_dir=current_project_state_dir,
                previous_build_error=last_build_error_output_for_agents 
            )
            modified_files_in_current_attempt.update(files_modified_by_this_step)

            if not step_execution_successful: 
                step_id_that_failed = step_data_from_plan.get('step_id', 'ID Inconnu')
                agent_that_failed = step_data_from_plan.get('agent') or step_data_from_plan.get('expert', 'Agent Inconnu')
                logger.error(
                    f"Échec FATAL de l'étape {step_id_that_failed} (Agent: {agent_that_failed}). "
                    f"Arrêt de la tentative d'exécution #{build_attempt_number} du plan."
                )
                current_attempt_all_steps_succeeded_flawlessly = False
                if not last_build_error_output_for_agents: 
                    last_build_error_output_for_agents = (
                        f"Échec critique de l'étape {step_id_that_failed} par l'agent '{agent_that_failed}'. "
                        "Consultez les logs de l'agent pour les détails."
                    )
                break 

        all_files_modified_in_workspace_during_loop.update(modified_files_in_current_attempt)

        if not current_attempt_all_steps_succeeded_flawlessly: 
            if build_attempt_number < max_retries: 
                logger.info(f"La tentative #{build_attempt_number} a échoué lors de l'exécution des étapes. Préparation de la prochaine tentative...")
                continue 
            else: 
                logger.error(f"Échec de la dernière tentative ({build_attempt_number}/{max_retries}) lors de l'exécution des étapes.")
                overall_execution_successful = False
                break 

        logger.info("Toutes les étapes de la tentative actuelle ont été exécutées avec succès. "
                    "Lancement de la commande de build/run sur le workspace...")
        
        build_command_to_run = getattr(global_config, 'BUILD_COMMAND', 'make build') 
        build_is_successful, build_command_output = shared_utils.run_build_command(
            command=build_command_to_run,
            project_dir=current_project_state_dir
        )

        if build_is_successful:
            logger.info(f"COMMANDE BUILD/RUN RÉUSSIE après la tentative #{build_attempt_number} !")
            overall_execution_successful = True
            last_build_error_output_for_agents = None 
            break 
        else: 
            logger.warning(f"Échec de la commande Build/Run (Tentative #{build_attempt_number}). Stockage de la sortie d'erreur...")
            last_build_error_output_for_agents = build_command_output 
            
            if build_attempt_number >= max_retries: 
                logger.error(f"Nombre maximum de tentatives de build/correction ({max_retries}) atteint après échec du build.")
                overall_execution_successful = False
                break 
            else: 
                logger.info("Préparation de la prochaine tentative de correction basée sur l'erreur de build...")
                
    if overall_execution_successful:
        logger.info("La boucle d'exécution s'est terminée avec un build réussi.")
    else:
        logger.error(f"La boucle d'exécution s'est terminée après {build_attempt_number} tentative(s) sans build réussi.")
        if last_build_error_output_for_agents:
             logger.error(f"Dernière erreur consignée (build ou agent): {last_build_error_output_for_agents[:1500]}{'...' if len(last_build_error_output_for_agents) > 1500 else ''}")

    return overall_execution_successful, last_build_error_output_for_agents, all_files_modified_in_workspace_during_loop

# --- Point d'entrée pour test (optionnel) ---
if __name__ == "__main__":
     logging.basicConfig(
         level=logging.DEBUG, 
         format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', 
         stream=sys.stderr
     )
     logger.info(f"Module {Path(__file__).name} exécuté directement (mode test).")