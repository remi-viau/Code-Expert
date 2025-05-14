# code/code_modifier/core/context_builder.py
from pathlib import Path
import sys
from typing import Tuple, Optional, Set, Dict, Any, List
import logging

logger = logging.getLogger(__name__)

# --- Gestion des Imports ---
# S'assurer que la racine du projet 'code' est dans sys.path
# pour l'import de 'lib.utils'.
try:
    CURRENT_CONTEXT_BUILDER_DIR = Path(__file__).resolve().parent # .../code_modifier/core/
    # Remonter de deux niveaux : core -> code_modifier -> code/
    PROJECT_ROOT_FOR_CONTEXT_BUILDER = CURRENT_CONTEXT_BUILDER_DIR.parents[1] 
    if str(PROJECT_ROOT_FOR_CONTEXT_BUILDER) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT_FOR_CONTEXT_BUILDER))
    
    from lib import utils as shared_utils # Pour extract_function_body
except ImportError as e_import:
    # Erreur critique si les dépendances ne peuvent pas être importées.
    _err_msg = (
        f"Erreur CRITIQUE [ContextBuilder Init]: Impossible d'importer un module essentiel: {e_import}\n"
        f"  PROJECT_ROOT_FOR_CONTEXT_BUILDER calculé: '{PROJECT_ROOT_FOR_CONTEXT_BUILDER}'\n"
        f"  Vérifiez que ce chemin est correct et que le module 'lib' existe.\n"
        f"  PYTHONPATH actuel: {sys.path}"
    )
    print(_err_msg, file=sys.stderr) # Utiliser print car le logger pourrait ne pas être pleinement fonctionnel
    import traceback
    traceback.print_exc(file=sys.stderr)
    sys.exit(2) # Arrêt critique
except Exception as e_init_unexpected:
    _err_msg_init = f"Erreur inattendue lors de l'initialisation de ContextBuilder (imports/paths): {e_init_unexpected}"
    print(_err_msg_init, file=sys.stderr)
    import traceback
    traceback.print_exc(file=sys.stderr)
    sys.exit(2)
# --- Fin Gestion des Imports ---

def build_planner_context(
    relevant_fragment_ids: List[str],
    full_manifest_data: Dict[str, Any],
    target_project_root_path: Path, 
    user_request: str,
    selection_reasoning: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Construit le contexte spécifique pour l'agent Planner.
    Utilise 'actual_source_path' et 'is_templ_source' du manifeste pour déterminer
    quel contenu de fichier lire (fichier .templ entier ou fragment de fichier .go).
    """
    logger.info(f"Construction du contexte pour l'agent Planner, basé sur {len(relevant_fragment_ids)} fragment(s) pertinent(s).")
    
    planner_context_fragments: List[Dict[str, Any]] = []
    all_manifest_fragments = full_manifest_data.get("fragments", {})

    if not relevant_fragment_ids:
        logger.warning("Aucun ID de fragment pertinent fourni pour le contexte du Planner. "
                       "Le Planner recevra une liste de fragments de code vide.")

    for frag_id in relevant_fragment_ids:
        fragment_info_from_manifest = all_manifest_fragments.get(frag_id)
        if not fragment_info_from_manifest:
            logger.warning(f"Fragment ID '{frag_id}' (marqué comme pertinent) non trouvé dans le manifeste complet. "
                           "Il sera ignoré pour le contexte du Planner.")
            continue

        # Lire les informations de chemin et de type de source depuis le manifeste
        actual_source_rel_path = fragment_info_from_manifest.get("actual_source_path")
        is_templ_source_file = fragment_info_from_manifest.get("is_templ_source", False)
        # original_path est le chemin du fichier .go (ou _templ.go) parsé par l'AST
        original_go_file_rel_path = fragment_info_from_manifest.get("original_path") 

        if not actual_source_rel_path:
            logger.error(f"Informations de chemin 'actual_source_path' manquantes pour le fragment '{frag_id}'. Ignoré pour le Planner.")
            continue
        if not original_go_file_rel_path and not is_templ_source_file: # original_path est nécessaire pour les lignes si c'est un .go
             logger.error(f"Informations de chemin 'original_path' manquantes pour le fragment Go '{frag_id}'. Ignoré pour le Planner.")
             continue


        absolute_path_to_read = target_project_root_path / actual_source_rel_path
        code_block_content: Optional[str] = None

        try:
            if not absolute_path_to_read.is_file():
                logger.error(f"Fichier source '{absolute_path_to_read}' (de 'actual_source_path') "
                               f"non trouvé pour le fragment '{frag_id}'. Ignoré pour le Planner.")
                continue

            if is_templ_source_file:
                # Si c'est un fichier source .templ, lire le contenu entier du fichier .templ
                code_block_content = absolute_path_to_read.read_text(encoding='utf-8')
                logger.debug(f"  Contenu du fichier .templ source '{actual_source_rel_path}' lu pour le Planner (fragment '{frag_id}').")
            else:
                # Si c'est un fichier .go normal, extraire le fragment spécifique
                # en utilisant start_line/end_line du manifeste, qui se réfèrent à original_go_file_rel_path
                start_line = fragment_info_from_manifest.get("start_line")
                end_line = fragment_info_from_manifest.get("end_line")

                if not (isinstance(start_line, int) and isinstance(end_line, int)):
                    logger.error(f"Lignes de début/fin invalides ou manquantes pour le fragment Go '{frag_id}'. Ignoré pour le Planner.")
                    continue
                
                # Le chemin pour extract_function_body doit être le fichier .go original parsé par l'AST
                # Dans ce cas (non-templ), original_go_file_rel_path et actual_source_rel_path pointent vers le même fichier .go
                absolute_go_file_for_extraction = target_project_root_path / original_go_file_rel_path
                if not absolute_go_file_for_extraction.is_file(): # Double vérification, devrait être le même que absolute_path_to_read
                     logger.error(f"Fichier .go original '{absolute_go_file_for_extraction}' non trouvé pour extraction du fragment '{frag_id}'. Ignoré.")
                     continue

                code_block_content = shared_utils.extract_function_body(
                    absolute_go_file_for_extraction, start_line, end_line
                )
                logger.debug(f"  Fragment Go extrait de '{original_go_file_rel_path}' (lignes {start_line}-{end_line}) "
                               f"pour le Planner (fragment '{frag_id}').")

        except Exception as e_read_code:
            logger.error(f"Erreur lors de la lecture ou de l'extraction du code pour le fragment '{frag_id}' "
                           f"depuis '{absolute_path_to_read}': {e_read_code}", exc_info=True)
            continue # Passer au fragment suivant

        if code_block_content is None: # Si la lecture ou l'extraction a échoué
            logger.error(f"Échec final de l'obtention du code_block pour le fragment '{frag_id}'. Ignoré pour le Planner.")
            continue
            
        # Construire les données du fragment pour le contexte du Planner
        # Le 'path_for_llm' sera actual_source_rel_path car c'est le chemin du code source pertinent
        planner_fragment_data = {
            "fragment_id": frag_id,
            "path_for_llm": actual_source_rel_path, 
            "is_templ_source_file": is_templ_source_file,
            "code_block": code_block_content,
            # Inclure les autres métadonnées du manifeste qui pourraient être utiles au Planner
            "fragment_type": fragment_info_from_manifest.get("fragment_type"),
            "identifier": fragment_info_from_manifest.get("identifier"),
            "package_name": fragment_info_from_manifest.get("package_name"),
            "signature": fragment_info_from_manifest.get("signature"),
            "receiver_type": fragment_info_from_manifest.get("receiver_type"),
            "definition": fragment_info_from_manifest.get("definition"),
            "docstring": fragment_info_from_manifest.get("docstring") # Docstring de l'AST du .go
        }
        planner_context_fragments.append(planner_fragment_data)

    if not planner_context_fragments and relevant_fragment_ids: # Si on avait des IDs mais aucun code n'a pu être extrait
        logger.error("Aucun code de fragment n'a pu être extrait pour les IDs pertinents. "
                       "Le contexte du Planner sera sévèrement limité ou potentiellement vide.")
        # On pourrait retourner None ici pour indiquer un échec critique si le code est indispensable.
        # Pour l'instant, on laisse le Planner recevoir une liste vide de fragments.
    
    final_planner_context = {
        "user_request": user_request, 
        "selection_reasoning": selection_reasoning,
        "relevant_code_fragments": planner_context_fragments
    }
    
    num_frags_in_ctx = len(planner_context_fragments)
    logger.info(f"Contexte pour le Planner construit: {num_frags_in_ctx} fragment(s) inclus avec leur code source.")
    if selection_reasoning: logger.info(f"Raisonnement de sélection des fragments transmis au Planner.")
    
    # Log de debug pour un aperçu du contexte (peut être volumineux)
    if logger.isEnabledFor(logging.DEBUG):
        debug_context_display = {
            "user_request": final_planner_context["user_request"],
            "selection_reasoning_length": len(final_planner_context["selection_reasoning"]) if final_planner_context["selection_reasoning"] else 0,
            "relevant_code_fragments_count": len(final_planner_context["relevant_code_fragments"]),
        }
        if final_planner_context["relevant_code_fragments"]:
            first_frag = final_planner_context["relevant_code_fragments"][0]
            debug_context_display["first_fragment_details_if_any"] = {
                "id": first_frag.get("fragment_id"),
                "path_for_llm": first_frag.get("path_for_llm"),
                "is_templ": first_frag.get("is_templ_source_file"),
                "code_block_length": len(first_frag.get("code_block",""))
            }
        logger.debug(f"Contexte Planner final (résumé pour log): {debug_context_display}")

    return final_planner_context


def assemble_expert_context(
    step_data: Dict[str, Any], # Une étape du plan généré par le Planner
    full_manifest_data: Dict[str, Any],
    current_project_state_dir: Path, # Le workspace où les modifications sont appliquées
    previous_build_error: Optional[str]
) -> Tuple[Optional[Dict[str, Any]], Set[str]]:
    """
    Assemble le contexte spécifique pour un agent exécuteur (ex: TemplFrontendAgent, GoServiceAgent).
    Utilise 'actual_source_path' et 'is_templ_source' du manifeste pour lire le code
    depuis le `current_project_state_dir`.
    """
    step_id_log = step_data.get('step_id', 'ID d_étape inconnu')
    agent_name_log = step_data.get('agent', 'Agent inconnu') # 'expert' est aussi une clé possible
    logger.info(f"Assemblage du contexte pour l'agent exécuteur (Étape: '{step_id_log}', Agent: '{agent_name_log}')...")
    
    target_fragment_ids: List[str] = step_data.get("target_fragment_ids", []) 
    context_fragment_ids: List[str] = step_data.get("context_fragment_ids", []) 
    step_specific_instructions: str = step_data.get("instructions", "")

    if not isinstance(target_fragment_ids, list):
        logger.error(f"Contexte exécuteur: 'target_fragment_ids' doit être une liste. Reçu: {type(target_fragment_ids)}. Échec assemblage.")
        return None, set()
    # Un plan peut légitimement avoir des instructions sans target_fragment_ids (ex: créer un nouveau fichier)
    # if not target_fragment_ids and not step_specific_instructions.strip():
    #      logger.error("Contexte exécuteur: 'target_fragment_ids' et instructions vides. Contexte insuffisant.")
    #      return None, set()
    if not isinstance(context_fragment_ids, list):
        logger.warning(f"Contexte exécuteur: 'context_fragment_ids' n'est pas une liste. Sera traité comme vide.")
        context_fragment_ids = []
    
    # Structure du contexte que l'agent exécuteur recevra
    agent_execution_context: Dict[str, Any] = {
        "step_instructions": step_specific_instructions,
        "previous_build_error": previous_build_error,
        "target_fragments_with_code": [], # Sera rempli avec les infos et le code des cibles
        "context_definitions": {           # Pour les définitions de types/fonctions de contexte
            "types": [], 
            "functions_or_methods": []
        }
    }
    
    all_manifest_fragments = full_manifest_data.get("fragments", {})
    files_potentially_modified_relative: Set[str] = set() # Chemins relatifs au root du projet
    critical_error_occurred = False

    # Traiter les fragments cibles (ceux que l'agent doit modifier/utiliser comme base)
    if target_fragment_ids:
        logger.info(f"Contexte exécuteur: Traitement de {len(target_fragment_ids)} fragment(s) cible(s)...")
        for frag_id in target_fragment_ids:
            fragment_info = all_manifest_fragments.get(frag_id)
            if not fragment_info: 
                logger.error(f"Contexte exécuteur: Fragment cible '{frag_id}' (du plan) non trouvé dans le manifeste. Échec critique assemblage.")
                critical_error_occurred = True; break 
            
            actual_src_rel_path_target = fragment_info.get("actual_source_path")
            is_templ_src_target = fragment_info.get("is_templ_source", False)
            original_go_path_target = fragment_info.get("original_path") # Pour les lignes si c'est un fragment Go

            if not actual_src_rel_path_target:
                logger.error(f"Contexte exécuteur: 'actual_source_path' manquant pour cible '{frag_id}'. Échec.")
                critical_error_occurred = True; break
            if not original_go_path_target and not is_templ_src_target:
                 logger.error(f"Contexte exécuteur: 'original_path' manquant pour cible Go '{frag_id}'. Échec.")
                 critical_error_occurred = True; break

            # Le code est lu depuis le current_project_state_dir (le workspace)
            path_to_read_code_from_ws = current_project_state_dir / actual_src_rel_path_target
            # path_agent_should_modify est le chemin relatif que l'agent utilisera s'il modifie le fichier
            path_agent_should_modify_rel = actual_src_rel_path_target
            
            code_block_for_agent: Optional[str] = None
            try:
                if not path_to_read_code_from_ws.is_file():
                    logger.error(f"Contexte exécuteur: Fichier source '{path_to_read_code_from_ws}' non trouvé dans workspace pour cible '{frag_id}'. Échec.")
                    critical_error_occurred = True; break
                
                if is_templ_src_target: # Fichier .templ, lire en entier
                    code_block_for_agent = path_to_read_code_from_ws.read_text(encoding='utf-8')
                else: # Fichier .go, extraire le fragment
                    start_line = fragment_info.get("start_line"); end_line = fragment_info.get("end_line")
                    if not (isinstance(start_line, int) and isinstance(end_line, int)):
                        logger.error(f"Contexte exécuteur: Lignes invalides pour cible Go '{frag_id}'. Échec.")
                        critical_error_occurred = True; break
                    code_block_for_agent = shared_utils.extract_function_body(path_to_read_code_from_ws, start_line, end_line)
            except Exception as e_read_ws_target:
                logger.error(f"Contexte exécuteur: Erreur lecture/extraction code de '{path_to_read_code_from_ws}' (workspace) pour cible '{frag_id}': {e_read_ws_target}", exc_info=True)
                critical_error_occurred = True; break
            
            if code_block_for_agent is None:
                logger.error(f"Contexte exécuteur: Échec obtention code_block pour cible '{frag_id}'. Échec.")
                critical_error_occurred = True; break
            
            # Récupérer les imports du fichier original (du manifeste)
            file_imports = fragment_info.get("imports") # Les imports sont au niveau du fichier .go parsé

            agent_execution_context["target_fragments_with_code"].append({
                "fragment_id": frag_id, 
                "path_to_modify": path_agent_should_modify_rel, # Chemin relatif au root du projet (pour écriture)
                "is_templ_source": is_templ_src_target, 
                "current_code_block": code_block_for_agent,
                # Ajouter d'autres métadonnées du manifeste
                "package_name": fragment_info.get("package_name"),
                "identifier": fragment_info.get("identifier"), 
                "fragment_type": fragment_info.get("fragment_type"),
                "signature": fragment_info.get("signature"), 
                "receiver_type": fragment_info.get("receiver_type"),
                "docstring": fragment_info.get("docstring"), # Docstring de l'AST du .go
                "imports_in_file": file_imports, # Peut être utile pour les agents Go
            })
            files_potentially_modified_relative.add(path_agent_should_modify_rel) 
        
        if critical_error_occurred: return None, files_potentially_modified_relative # Retourner les fichiers ciblés jusqu'à l'erreur

    # Traiter les fragments de contexte (ceux qui fournissent des définitions, pas à modifier)
    if context_fragment_ids:
        logger.info(f"Contexte exécuteur: Traitement de {len(context_fragment_ids)} fragment(s) de contexte...")
        for frag_id_ctx in context_fragment_ids:
            ctx_info = all_manifest_fragments.get(frag_id_ctx)
            if not ctx_info: 
                logger.warning(f"Contexte exécuteur: Fragment de contexte '{frag_id_ctx}' non trouvé dans manifeste. Ignoré."); continue
            
            frag_type_ctx = ctx_info.get("fragment_type")
            identifier_ctx = ctx_info.get("identifier")
            
            context_entry_details = { 
                "fragment_id": frag_id_ctx, 
                "name": identifier_ctx, 
                "package_name": ctx_info.get("package_name"),
                "original_path": ctx_info.get("original_path"), # Le .go où il est défini
                "docstring": ctx_info.get("docstring")
            }
            if frag_type_ctx == "type" and (definition_ctx := ctx_info.get("definition")):
                context_entry_details["definition"] = definition_ctx
                agent_execution_context["context_definitions"]["types"].append(context_entry_details)
            elif frag_type_ctx in ["function", "method"] and (signature_ctx := ctx_info.get("signature")):
                context_entry_details["signature"] = signature_ctx
                context_entry_details["receiver_type"] = ctx_info.get("receiver_type")
                agent_execution_context["context_definitions"]["functions_or_methods"].append(context_entry_details)
            # On pourrait ajouter CONSTANT, VARIABLE ici si les agents en ont besoin
    
    ctx_summary_log = (f"{len(agent_execution_context['target_fragments_with_code'])} cible(s) avec code, "
                       f"{len(agent_execution_context['context_definitions']['types'])} type(s) de contexte, "
                       f"{len(agent_execution_context['context_definitions']['functions_or_methods'])} func/meth de contexte.")
    logger.info(f"Contexte exécuteur: Assemblage terminé ({ctx_summary_log}).")
    
    if logger.isEnabledFor(logging.DEBUG):
        debug_exec_ctx_display = { "step_instructions_length": len(agent_execution_context["step_instructions"]),
            "previous_build_error_present": agent_execution_context["previous_build_error"] is not None,
            "target_fragments_count": len(agent_execution_context["target_fragments_with_code"]), }
        if agent_execution_context["target_fragments_with_code"]:
            first_target_log = agent_execution_context["target_fragments_with_code"][0]
            debug_exec_ctx_display["first_target_id"] = first_target_log.get("fragment_id")
            debug_exec_ctx_display["first_target_path_to_modify"] = first_target_log.get("path_to_modify")
            debug_exec_ctx_display["first_target_is_templ"] = first_target_log.get("is_templ_source")
        logger.debug(f"Contexte exécuteur final (résumé pour log): {debug_exec_ctx_display}")
    
    return agent_execution_context, files_potentially_modified_relative

# --- Point d'entrée pour test (optionnel) ---
if __name__ == "__main__":
     # Configurer un logger basique pour les tests directs
     logging.basicConfig(
         level=logging.DEBUG, 
         format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', 
         stream=sys.stderr
     )
     logger.info(f"Module {Path(__file__).name} exécuté directement (mode test).")
     # Ici, vous pourriez ajouter des appels de test à build_planner_context et assemble_expert_context
     # en créant des données de manifeste et de plan de mock.
     # Par exemple:
     # mock_manifest = manifest_io.load_manifest(Path("workspace/fragments_manifest.json")) # Charger un vrai manifeste pour test
     # if mock_manifest:
     #     mock_plan_step = { ... } # Définir une étape de plan
     #     mock_workspace = Path("workspace/current_project_state") # Si elle existe
     #     # Testez les fonctions...
     pass