# code/code_quality/main.py
#!/usr/bin/env python3
"""
Orchestrateur Principal pour le Pipeline de Qualité de Code.
Ce script gère l'exécution des tâches d'analyse de qualité (comme
l'enrichissement des docstrings, le découpage de fichiers longs),
la relance ciblée d'analyses, et potentiellement l'application des
propositions générées (actuellement en mode simulation pour l'application).
"""

import sys
import os
from pathlib import Path
import argparse # Pour type hinting et accès aux args parsés
import re
import traceback
import logging
from typing import Set, Dict, Any, List, Tuple, Optional
import json
import datetime
import asyncio
import shutil # Pour le backup des rapports

# --- Configuration initiale de sys.path ---
try:
    PROJECT_ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(PROJECT_ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT_DIR))
        # Utiliser print car le logger n'est pas encore configuré.
        print(f"INFO [CodeQuality Main Init]: Ajout de '{PROJECT_ROOT_DIR}' à sys.path.")
except IndexError:
    print(f"ERREUR CRITIQUE [CodeQuality Main Init]: Impossible de déterminer racine projet ('code').", file=sys.stderr); sys.exit(3)
# --- Fin config sys.path ---

# --- Imports modules projet ---
try:
    from code_quality import cli as quality_cli
    from code_quality.tasks.docstring_task import DocstringTask
    from code_quality.tasks.filesplit_task import FileSplitTask
    from code_quality.tasks.base_quality_task import BaseQualityTask # Pour type hinting
    from code_quality.tasks import utils_quality
    
    from lib import utils as shared_utils
    from manifest import manifest_io
    # global_config est implicitement utilisé via args.validated_target_path et args.workspace_path
    # qui sont remplis par quality_cli.py en lisant global_config.
except ImportError as e_import:
    print(f"Erreur critique [CodeQuality Main]: Import échoué: {e_import}\nPYTHONPATH: {sys.path}", file=sys.stderr); traceback.print_exc(file=sys.stderr); sys.exit(2)
except Exception as e_init_imports:
    print(f"Erreur init imports [CodeQuality Main]: {e_init_imports}", file=sys.stderr); traceback.print_exc(file=sys.stderr); sys.exit(2)
# -------------------------------------------

logger = logging.getLogger(__name__) # Logger pour cet orchestrateur

def handle_quality_pipeline_exit(message: str, exit_code: int):
    """Logue, affiche un message utilisateur concis, et termine le programme."""
    if exit_code != 0:
        logger.critical(message)
    else:
        logger.info(message)
    print(f"\nINFO: {message}")
    print("Arrêt de l'Orchestrateur de Qualité de Code.")
    sys.exit(exit_code)

# --- Fonctions d'Orchestration des Tâches de Qualité ---

def run_quality_analysis_orchestrator(args: argparse.Namespace) -> bool:
    """Orchestre l'exécution des tâches d'analyse de qualité et sauvegarde les rapports."""
    shared_utils.print_stage_header("DÉBUT DU PIPELINE D'ANALYSE DE QUALITÉ")
    logger.info(f"Tâches d'analyse demandées: {args.tasks}")
    if args.target_fragment: logger.info(f"Filtre --target-fragment actif: {args.target_fragment}")
    if args.target_file: logger.info(f"Filtre --target-file actif: {args.target_file}")

    if not args.validated_target_path or not args.validated_target_path.is_dir():
        logger.error("Chemin du projet cible est invalide ou non fourni à run_quality_analysis_orchestrator.")
        return False

    overall_pipeline_success = True
    full_manifest_data: Optional[Dict[str, Any]] = None
    reports_generated_map: Dict[str, Optional[Path]] = {}

    needs_manifest = any(task in args.tasks or "all" in args.tasks for task in ["docstrings", "filesplit"])
    if needs_manifest:
        logger.info(f"Chargement du manifeste depuis: {args.manifest_read_path}")
        if not args.manifest_read_path.is_file():
             logger.error(f"Fichier manifeste '{args.manifest_read_path}' requis mais non trouvé.")
             return False # Échec critique si le manifeste est essentiel
        full_manifest_data = manifest_io.load_manifest(args.manifest_read_path)
        if not full_manifest_data:
            logger.error(f"Impossible de charger ou parser le manifeste depuis '{args.manifest_read_path}'.")
            return False
        logger.info("Manifeste chargé avec succès pour les tâches de qualité.")

    task_common_args = vars(args) # Transmettre tous les args CLI aux méthodes des tâches

    # Exécution de la tâche d'enrichissement des Docstrings
    if "docstrings" in args.tasks or "all" in args.tasks:
        if full_manifest_data: # Nécessaire pour cette tâche
            logger.info(f"--- {DocstringTask.task_name}: Initialisation et exécution ---")
            try:
                doc_task = DocstringTask(args.validated_target_path, args.workspace_path, full_manifest_data)
                task_success, report_file = doc_task.analyze(args=task_common_args)
                reports_generated_map["docstrings"] = report_file
                if not task_success: overall_pipeline_success = False
            except Exception as e:
                logger.error(f"Erreur critique lors de l'initialisation ou de l'exécution de DocstringTask: {e}", exc_info=True)
                overall_pipeline_success = False
        else:
            logger.warning("Tâche 'docstrings' ignorée car le manifeste n'a pas pu être chargé ou n'était pas disponible.")
            if "docstrings" in args.tasks: overall_pipeline_success = False # Si explicitement demandée

    # Exécution de la tâche de Découpage de Fichiers Longs
    if "filesplit" in args.tasks or "all" in args.tasks:
        if full_manifest_data: # Nécessaire pour cette tâche
            logger.info(f"--- {FileSplitTask.task_name}: Initialisation et exécution ---")
            try:
                split_task = FileSplitTask(args.validated_target_path, args.workspace_path, full_manifest_data)
                task_success, report_file = split_task.analyze(args=task_common_args)
                reports_generated_map["filesplit"] = report_file
                if not task_success: overall_pipeline_success = False
            except Exception as e:
                logger.error(f"Erreur critique lors de l'initialisation ou de l'exécution de FileSplitTask: {e}", exc_info=True)
                overall_pipeline_success = False
        else:
            logger.warning("Tâche 'filesplit' ignorée car le manifeste n'a pas pu être chargé ou n'était pas disponible.")
            if "filesplit" in args.tasks: overall_pipeline_success = False
            
    shared_utils.print_stage_header("FIN DU PIPELINE D'ANALYSE DE QUALITÉ")
    if reports_generated_map:
        logger.info("Rapports d'analyse générés (ou tentatives de génération) :")
        for task_name, report_p in reports_generated_map.items():
            logger.info(f"  - Tâche '{task_name}': {report_p if report_p else 'Non généré ou erreur lors de la sauvegarde.'}")
    else:
        logger.info("Aucune tâche d'analyse n'a été configurée pour produire un rapport, ou toutes ont été ignorées.")
        
    return overall_pipeline_success

def run_quality_retry_analysis_orchestrator(args: argparse.Namespace) -> bool:
    """Relance l'analyse pour un item spécifique OU pour tous les items en erreur d'un rapport."""
    task_type = args.task_type
    target_id = args.target_fragment if task_type == "docstrings" else args.target_file
    target_key_in_report = "fragment_id_context" if task_type == "docstrings" else "analyzed_file_path"

    action_description = f"Item '{target_id}'" if target_id else f"Tous les items en erreur du rapport '{args.input_report.name if args.input_report else 'N/A'}'"
    shared_utils.print_stage_header(f"Relance Analyse Qualité ({task_type}): {action_description}")

    if not args.validated_target_path or not args.validated_target_path.is_dir():
        logger.error("Chemin projet cible invalide pour 'retry_analysis'."); return False
    if not args.manifest_read_path.is_file():
        logger.error(f"Manifeste '{args.manifest_read_path}' requis pour relance mais non trouvé."); return False
    full_manifest_data = manifest_io.load_manifest(args.manifest_read_path)
    if not full_manifest_data or "fragments" not in full_manifest_data:
        logger.error(f"Impossible charger/parser manifeste '{args.manifest_read_path}' pour relance."); return False

    items_to_process_details: List[Dict[str, Any]] = [] # Liste de dicts contenant les infos pour chaque item à relancer

    # CAS 1: Relance ciblée d'un seul item (target_id est défini)
    if target_id:
        item_info: Optional[Dict[str,Any]] = None; actual_src_path: Optional[str] = None; is_templ: bool = False
        if task_type == "docstrings":
            item_info = full_manifest_data["fragments"].get(target_id)
            if item_info: actual_src_path = item_info.get("actual_source_path", item_info.get("original_path")); is_templ = item_info.get("is_templ_source", False)
        elif task_type == "filesplit":
            actual_src_path = target_id
            for _, fi_loop in full_manifest_data["fragments"].items():
                if fi_loop.get("actual_source_path") == target_id: item_info = fi_loop; break
            if not item_info: item_info = {"package_name": Path(target_id).parent.name if target_id.endswith(".templ") else "unknown"}
            is_templ = actual_src_path.endswith(".templ")
        
        if not item_info or not actual_src_path: logger.error(f"Item cible '{target_id}' non trouvé/infos incomplètes. Relance annulée."); return False
        items_to_process_details.append({"target_id": target_id, "item_info": item_info, "actual_src_path": actual_src_path, "is_templ": is_templ})
    
    # CAS 2: Relancer tous les items en erreur d'un rapport d'entrée
    elif args.input_report and args.input_report.is_file(): # input_report est un Path résolu
        try:
            with open(args.input_report, 'r', encoding='utf-8') as f: report_in_data = json.load(f)
            if not isinstance(report_in_data, list): logger.error(f"Rapport entrée '{args.input_report.name}' non une liste JSON."); return False
            
            for entry in report_in_data:
                if isinstance(entry, dict) and isinstance(entry.get("agent_response"), dict) and entry["agent_response"].get("status") == "error":
                    current_target_id_from_report = entry.get(target_key_in_report)
                    if not current_target_id_from_report: continue
                    
                    item_info_m: Optional[Dict[str,Any]] = None; actual_s_p: Optional[str] = None; is_t_s = False
                    if task_type == "docstrings":
                        item_info_m = full_manifest_data["fragments"].get(current_target_id_from_report)
                        if item_info_m: actual_s_p = item_info_m.get("actual_source_path",item_info_m.get("original_path")); is_t_s = item_info_m.get("is_templ_source",False)
                    elif task_type == "filesplit":
                        actual_s_p = current_target_id_from_report
                        for _, fil in full_manifest_data["fragments"].items():
                            if fil.get("actual_source_path") == current_target_id_from_report: item_info_m = fil; break
                        if not item_info_m: item_info_m = {"package_name":Path(current_target_id_from_report).parent.name if current_target_id_from_report.endswith(".templ") else "unknown"}
                        is_t_s = actual_s_p.endswith(".templ")
                    
                    if item_info_m and actual_s_p:
                        items_to_process_details.append({"target_id": current_target_id_from_report, "item_info": item_info_m, "actual_src_path": actual_s_p, "is_templ": is_t_s})
                    else: logger.warning(f"Infos manifestes incomplètes pour item en erreur '{current_target_id_from_report}'. Non relancé.")
            if not items_to_process_details: logger.info(f"Aucun item en erreur trouvé dans '{args.input_report.name}'."); return True
        except Exception as e: logger.error(f"Erreur lecture/traitement rapport entrée '{args.input_report.name}': {e}", exc_info=True); return False
    else: logger.error("Mode relance invalide."); return False

    # Boucle de traitement des items à relancer
    new_agent_responses_map: Dict[str, Dict[str, Any]] = {} # target_id -> new_agent_response
    overall_retry_run_success = True

    for item_detail in items_to_process_details:
        current_target_id = item_detail["target_id"]
        item_info = item_detail["item_info"]
        actual_src_rel_path = item_detail["actual_src_path"]
        is_templ = item_detail["is_templ"]
        abs_code_path = args.validated_target_path / actual_src_rel_path

        if not abs_code_path.is_file():
            logger.error(f"Fichier source '{abs_code_path}' pour '{current_target_id}' non trouvé. Relance item annulée.")
            new_agent_responses_map[current_target_id] = {"status":"error", "error_message":"Fichier source non trouvé pour relance."}
            overall_retry_run_success = False; continue
        try: code_content = abs_code_path.read_text(encoding='utf-8')
        except Exception as e: logger.error(f"Erreur lecture '{abs_code_path}': {e}"); overall_retry_run_success = False; continue
        
        agent_instance_retry: Any = None; agent_ctx_retry: Dict[str,Any] = {}
        try:
            if task_type == "docstrings":
                agent_instance_retry = DocstringTask(args.validated_target_path, args.workspace_path, full_manifest_data).enricher_agent
                code_for_agent = code_content if is_templ else shared_utils.extract_function_body(abs_code_path, item_info["start_line"], item_info["end_line"])
                if not code_for_agent: raise ValueError("Extraction code pour docstring échouée.")
                agent_ctx_retry = {"fragment_id": current_target_id, "original_path": actual_src_rel_path, "is_templ_source": is_templ, "code_block": code_for_agent, **{k:item_info.get(k) for k in ["current_docstring","fragment_type","identifier","package_name","signature","definition"]}}
            elif task_type == "filesplit":
                agent_instance_retry = FileSplitTask(args.validated_target_path, args.workspace_path, full_manifest_data).splitter_agent
                MAX_LINES_RETRY_FS = int(os.getenv("QA_FILE_SPLIT_MAX_LINES", "500"))
                agent_ctx_retry = {"original_file_path": actual_src_rel_path, "original_file_content": code_content, "package_name": item_info.get("package_name", Path(actual_src_rel_path).parent.name if is_templ else "unknown"), "is_templ_source": is_templ, "max_lines_per_file_target": MAX_LINES_RETRY_FS - 50}
            
            if agent_instance_retry and agent_ctx_retry:
                logger.info(f"  Relance agent '{agent_instance_retry.agent_name}' pour item '{current_target_id}'...")
                new_resp = agent_instance_retry.run(agent_ctx_retry)
                new_agent_responses_map[current_target_id] = new_resp if isinstance(new_resp, dict) else {"status":"error", "error_message": "Réponse agent invalide relance"}
                if not new_resp or new_resp.get("status") == "error": overall_retry_run_success = False
            else: raise RuntimeError("Échec préparation agent/contexte pour relance.")
        except Exception as e_agent_call:
            logger.error(f"Erreur appel agent pour '{current_target_id}': {e_agent_call}", exc_info=True)
            new_agent_responses_map[current_target_id] = {"status":"error", "error_message": f"Erreur appel agent: {e_agent_call}"}
            overall_retry_run_success = False
            
    # Mise à jour ou création du rapport final
    report_data_to_save: List[Dict[str, Any]] = []
    if args.input_report and args.input_report.is_file(): # Partir du rapport d'entrée
        with open(args.input_report, 'r', encoding='utf-8') as f_in_upd: report_data_to_save = json.load(f_in_upd)
        for i, entry in enumerate(report_data_to_save):
            if isinstance(entry, dict) and (item_id_in_entry := entry.get(target_key_in_report)) in new_agent_responses_map:
                logger.info(f"  Mise à jour entrée pour '{item_id_in_entry}' dans rapport avec nouveau résultat agent.")
                entry["agent_response"] = new_agent_responses_map.pop(item_id_in_entry) # pop pour marquer comme traité
    # Ajouter les items relancés qui n'étaient pas dans le rapport d'entrée (ou si pas de rapport d'entrée)
    for item_id_new, new_resp_new in new_agent_responses_map.items(): # Ceux qui restent sont nouveaux
        logger.info(f"  Ajout nouvelle entrée pour item relancé '{item_id_new}' au rapport.")
        # Reconstruire l'entrée complète du rapport
        is_t_src_new = False; lines_new = -1
        if task_type == "docstrings": is_t_src_new = full_manifest_data["fragments"].get(item_id_new,{}).get("is_templ_source",False)
        elif task_type == "filesplit": 
            is_t_src_new = item_id_new.endswith(".templ")
            abs_p_new = args.validated_target_path / item_id_new
            if abs_p_new.is_file(): lines_new = len(abs_p_new.read_text(encoding='utf-8').splitlines())
        new_entry_dict = {target_key_in_report: item_id_new, "is_templ_source_analyzed": is_t_src_new, "agent_response": new_resp_new}
        if task_type == "docstrings": new_entry_dict["fragment_id_context"]=item_id_new; new_entry_dict["original_path_context"]=item_id_new # Approximation
        elif task_type == "filesplit": new_entry_dict["analyzed_file_path"]=item_id_new; new_entry_dict["lines_in_original"]=lines_new
        report_data_to_save.append(new_entry_dict)

    output_p_final = args.output_report or args.input_report # Écraser input si output non fourni
    if not output_p_final:
        report_dir = args.workspace_path / "quality_proposals"; report_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S'); prefix = utils_quality.REPORT_PREFIX_MAP.get(task_type, f"{task_type}_")
        id_suffix = target_id.replace("/","_").replace("\\","_") if (args.target_fragment or args.target_file) else "all_retried"
        output_p_final = report_dir / f"{prefix}{id_suffix}_{ts}.json"
    if output_p_final == args.input_report and args.input_report and args.input_report.exists():
        try: shutil.copy2(args.input_report, args.input_report.with_suffix(f".{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.bak_retry_all"))
        except Exception as e: logger.error(f"Erreur backup rapport '{args.input_report.name}': {e}. MAJ annulée."); return False
    try:
        output_p_final.parent.mkdir(parents=True, exist_ok=True)
        with open(output_p_final, 'w', encoding='utf-8') as f: json.dump(report_data_to_save, f, indent=2, ensure_ascii=False)
        logger.info(f"Rapport final avec items relancés sauvegardé: {output_p_final}"); return overall_retry_run_success
    except Exception as e: logger.error(f"Erreur sauvegarde rapport final '{output_p_final}': {e}", exc_info=True); return False

def run_quality_report_update_orchestrator(args: argparse.Namespace) -> bool:
    # ... (logique de run_quality_report_update_orchestrator comme fournie précédemment) ...
    # S'assurer qu'elle utilise args.validated_target_path si besoin (peu probable car opère sur JSON)
    return True # Remplacer par logique réelle

def run_quality_application_orchestrator(args: argparse.Namespace) -> bool:
    # ... (logique inchangée, appelle les coquilles apply_..._from_report des modules de tâches) ...
    return True # Remplacer par logique réelle

# --- Fonction Principale ---
def quality_orchestrator_main():
    args = quality_cli.parse_arguments()
    if not args: sys.exit(1)
    
    log_file_name = f"code_quality_run_{args.quality_command}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_file_path = args.workspace_path / log_file_name
    try:
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        shared_utils.setup_logging(debug_mode=args.debug, log_file=log_file_path)
    except Exception as e_log:
        print(f"AVERTISSEMENT: Échec config logging vers '{log_file_path}': {e_log}", file=sys.stderr)
        if not logging.getLogger().hasHandlers():
             logging.basicConfig(level=logging.INFO if not args.debug else logging.DEBUG, stream=sys.stderr)
             logger.warning("Logging fichier a échoué. Utilisation console basique.")

    logger.info(f"--- Lancement Orchestrateur Qualité ---")
    logger.info(f"Commande: {args.quality_command}")
    logger.info(f"Workspace: {args.workspace_path}, Manifeste: {args.manifest_read_path.name}")
    logger.info(f"Projet Cible: {args.validated_target_path if args.validated_target_path else 'NON VALIDE/MANQUANT'}")
    if not args.validated_target_path or not args.validated_target_path.is_dir():
        handle_quality_pipeline_exit(f"Critique: Chemin projet cible ('{args.validated_target_path}') invalide.", 1); return

    if args.quality_command == "analyze":
        logger.info(f"Tâches d'analyse: {args.tasks}")
        try:
            success = run_quality_analysis_orchestrator(args)
            msg = f"Pipeline analyse qualité terminé. Statut: {'OK (vérifiez rapports)' if success else 'AVEC ERREURS'}."
            msg += f"\nConsultez rapports dans '{args.workspace_path / 'quality_proposals'}'."
            handle_quality_pipeline_exit(msg, 0 if success else 1)
        except SystemExit: raise
        except Exception as e: logger.critical(f"ERREUR 'quality analyze': {e}", exc_info=True); handle_quality_pipeline_exit(f"Erreur critique: {e}", 2)
    
    elif args.quality_command == "retry_analysis":
        try:
            success = run_quality_retry_analysis_orchestrator(args)
            msg = f"Relance analyse pour item(s) ciblé(s) terminée. Statut: {'SUCCÈS' if success else 'ÉCHEC'}."
            handle_quality_pipeline_exit(msg, 0 if success else 1)
        except SystemExit: raise
        except Exception as e: logger.critical(f"ERREUR 'quality retry_analysis': {e}", exc_info=True); handle_quality_pipeline_exit(f"Erreur critique: {e}", 2)

    elif args.quality_command == "update_report": # Logique pour update_report (inchangée)
        logger.info(f"Mise à jour rapport: {args.report}, Type: {args.task_type}, Données MAJ: {args.update_data.name if args.update_data else 'N/A'}")
        try:
            update_successful = run_quality_report_update_orchestrator(args) # Assurez-vous que cette fonction est définie
            exit_code_qa_update = 0 if update_successful else 1
            final_msg_qa_update = f"Mise à jour du rapport de qualité terminée. Statut: {'SUCCÈS' if update_successful else 'ÉCHEC'}."
            handle_quality_pipeline_exit(final_msg_qa_update, exit_code_qa_update)
        except SystemExit: raise
        except Exception as e_qa_update:
             logger.critical(f"ERREUR NON CAPTURÉE 'quality update_report': {type(e_qa_update).__name__} - {e_qa_update}", exc_info=True)
             handle_quality_pipeline_exit(f"Erreur critique 'quality update_report': {e_qa_update}", 2)

    elif args.quality_command == "apply":
        # ... (logique d'application comme avant) ...
        pass
    else: 
        logger.error(f"Sous-commande qualité non reconnue: '{args.quality_command}'."); handle_quality_pipeline_exit("Commande invalide.", 1)

if __name__ == "__main__":
    print("Démarrage Orchestrateur Qualité Code...")
    if sys.platform == "win32" and sys.version_info >= (3, 8):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    quality_orchestrator_main()