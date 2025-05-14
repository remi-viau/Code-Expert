# code/code_quality/tasks/filesplit_task.py
import logging
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List
import json
import os # Pour os.getenv
import re # Pour extraire package name (fallback pour .go)
import datetime
import sys # Pour la gestion de sys.path dans le bloc de test

# Imports depuis la racine du projet 'code'
try:
    from agents.qa_filesplitter.agent import QAFileSplitterAgent
    from lib import utils as shared_utils # Pour print_stage_header
    from .base_quality_task import BaseQualityTask # Import relatif
except ImportError as e_initial_import:
    if __name__ == '__main__':
        CURRENT_SCRIPT_DIR = Path(__file__).resolve().parent
        PROJECT_ROOT_FOR_TASK = CURRENT_SCRIPT_DIR.parents[1]
        if str(PROJECT_ROOT_FOR_TASK) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT_FOR_TASK))
        from agents.qa_filesplitter.agent import QAFileSplitterAgent
        from lib import utils as shared_utils
        from code_quality.tasks.base_quality_task import BaseQualityTask
    else:
        print(f"Erreur d'import dans filesplit_task.py (non direct): {e_initial_import}", file=sys.stderr)
        raise e_initial_import

logger = logging.getLogger(__name__)

class FileSplitTask(BaseQualityTask):
    """
    Tâche de qualité pour analyser les fichiers longs (Go ou Templ) et proposer
    des plans de découpage en plusieurs fichiers plus petits.
    Utilise les informations `actual_source_path` et `is_templ_source` du manifeste.
    """
    task_name: str = "FileSplittingAnalysis"

    def __init__(self, 
                 target_project_path: Path, 
                 workspace_path: Path, 
                 full_manifest_data: Dict[str, Any]): # Manifeste requis
        super().__init__(target_project_path, workspace_path, full_manifest_data)
        
        if not self.full_manifest_data or "fragments" not in self.full_manifest_data:
            msg = f"[{self.task_name}] Le manifeste de fragments est requis et doit contenir la clé 'fragments'."
            logger.error(msg)
            raise ValueError(msg)
        
        try:
            self.splitter_agent = QAFileSplitterAgent()
            logger.debug(f"[{self.task_name}] Agent QAFileSplitterAgent initialisé.")
        except Exception as e_agent_init:
            logger.error(f"[{self.task_name}] Initialisation de QAFileSplitterAgent échouée: {e_agent_init}", exc_info=True)
            raise RuntimeError(f"Échec initialisation agent pour {self.task_name}: {e_agent_init}") from e_agent_init
            
        if not self.splitter_agent._config:
            msg = f"[{self.task_name}] Configuration non chargée pour l'agent {self.splitter_agent.agent_name}."
            logger.error(msg)
            raise RuntimeError(f"Config agent non chargée pour {self.task_name}")

    def analyze(self, args: Optional[Dict[str, Any]] = None) -> Tuple[bool, Optional[Path]]:
        """
        Exécute l'analyse des fichiers (basée sur le manifeste) pour proposer des découpages.

        Args:
            args: Dictionnaire d'arguments. Peut contenir :
                  'target_file_path_filter': Pour cibler un chemin de fichier relatif spécifique.
        """
        shared_utils.print_stage_header(f"Analyse Qualité: {self.task_name}")
        target_file_path_filter = args.get("target_file_path_filter") if args else None

        if target_file_path_filter:
            logger.info(f"Ciblage du fichier spécifique pour analyse de découpage: '{target_file_path_filter}'")
        else:
            logger.info(f"Analyse de tous les fichiers uniques du manifeste pour découpage potentiel...")

        report_file_path: Optional[Path] = None
        fragments_from_manifest = self.full_manifest_data.get("fragments", {})
        if not fragments_from_manifest:
            logger.info("Aucun fragment dans le manifeste. Tâche de découpage de fichiers ignorée.")
            return True, None

        # Construire une map des fichiers uniques à analyser à partir du manifeste
        unique_files_map: Dict[str, Dict[str, Any]] = {}
        for _, frag_info in fragments_from_manifest.items():
            actual_rel_path = frag_info.get("actual_source_path")
            if not actual_rel_path: continue # Ignorer fragments sans chemin source
            if actual_rel_path not in unique_files_map:
                unique_files_map[actual_rel_path] = {
                    "is_templ_source": frag_info.get("is_templ_source", False),
                    "package_name": frag_info.get("package_name", "unknown_package")
                }
        
        if not unique_files_map:
            logger.info("Aucun fichier unique à analyser trouvé à partir des fragments du manifeste.")
            return True, None

        MAX_LINES_THRESHOLD_SPLIT = int(os.getenv("QA_FILE_SPLIT_MAX_LINES", "500"))
        all_file_split_plans: List[Dict[str, Any]] = []
        task_had_critical_errors = False # Pour les erreurs de la tâche elle-même (lecture fichier, etc.)
        files_actually_analyzed_count = 0 # Fichiers qui passent le filtre et le seuil de lignes

        for rel_path_str, file_meta_from_manifest in unique_files_map.items():
            if target_file_path_filter and rel_path_str != target_file_path_filter:
                continue # Appliquer le filtre si un fichier spécifique est ciblé
            
            abs_path_to_analyze = self.target_project_path / rel_path_str
            is_templ_file_type = file_meta_from_manifest["is_templ_source"]
            package_name_for_agent = file_meta_from_manifest["package_name"]

            try:
                if not abs_path_to_analyze.is_file():
                    logger.warning(f"  Fichier source '{abs_path_to_analyze}' (de 'actual_source_path') non trouvé. Ignoré pour analyse de découpage.")
                    all_file_split_plans.append({"analyzed_file_path": rel_path_str, "agent_response": {"status": "error", "error_message": "Fichier source non trouvé sur le disque."}})
                    task_had_critical_errors = True; continue

                file_content = abs_path_to_analyze.read_text(encoding='utf-8')
                num_lines = len(file_content.splitlines())

                if num_lines > MAX_LINES_THRESHOLD_SPLIT:
                    files_actually_analyzed_count +=1
                    logger.info(f"  Analyse fichier long '{rel_path_str}' ({num_lines} lignes, type: {'templ' if is_templ_file_type else 'go'})...")
                    
                    # Pour les fichiers .templ, le package_name du manifeste (issu du _templ.go) est généralement correct.
                    # Si package_name est "unknown_package" ou vide, utiliser un fallback.
                    if is_templ_file_type and (package_name_for_agent == "unknown_package" or not package_name_for_agent):
                        package_name_for_agent = Path(abs_path_to_analyze.parent.name).name 
                        logger.debug(f"    Package name pour fichier templ '{rel_path_str}' estimé à '{package_name_for_agent}' (fallback sur nom dossier).")
                    elif not is_templ_file_type and (package_name_for_agent == "unknown_package" or not package_name_for_agent):
                        # Tentative d'extraction pour .go si le manifeste ne l'a pas fourni
                        match_pkg = re.search(r"^\s*package\s+(\w+)", file_content, re.MULTILINE)
                        if match_pkg: package_name_for_agent = match_pkg.group(1)
                        logger.debug(f"    Package name pour fichier Go '{rel_path_str}' extrait à '{package_name_for_agent}'.")


                    agent_context = {
                        "original_file_path": rel_path_str, # Chemin relatif du fichier .templ ou .go
                        "original_file_content": file_content, # Contenu entier du fichier
                        "package_name": package_name_for_agent,
                        "is_templ_source": is_templ_file_type, # Important pour l'agent LLM
                        "max_lines_per_file_target": MAX_LINES_THRESHOLD_SPLIT - 50 
                    }
                    
                    plan_result_from_agent = self.splitter_agent.run(agent_context)
                    
                    entry_for_report = {
                        "analyzed_file_path": rel_path_str, 
                        "lines_in_original": num_lines, 
                        "is_templ_source_analyzed": is_templ_file_type, 
                        "agent_response": plan_result_from_agent if isinstance(plan_result_from_agent, dict) else 
                                          {"status":"error", "error_message":"Réponse de l'agent de type invalide ou None"}
                    }
                    all_file_split_plans.append(entry_for_report)

                    if not plan_result_from_agent or plan_result_from_agent.get("status") not in ["success_plan_generated", "no_action_needed"]:
                        logger.warning(f"    L'agent splitter a retourné un statut inattendu ou une erreur pour '{rel_path_str}'.")
                        # task_had_critical_errors est plus pour les erreurs de la tâche, pas les "erreurs" de plan de l'agent.
                else:
                     logger.debug(f"  Fichier '{rel_path_str}' dans les limites de lignes ({num_lines} <= {MAX_LINES_THRESHOLD_SPLIT}). Non analysé pour découpage.")
            except Exception as e_file_processing:
                logger.error(f"  Erreur lors du traitement du fichier '{abs_path_to_analyze}' pour découpage: {e_file_processing}", exc_info=True)
                all_file_split_plans.append({
                    "analyzed_file_path": rel_path_str, 
                    "is_templ_source_analyzed": is_templ_file_type, 
                    "agent_response": {"status": "error", "error_message": f"Erreur de traitement local du fichier: {e_file_processing}"}
                })
                task_had_critical_errors = True # Ceci est une erreur de la tâche.
        
        if target_file_path_filter and files_actually_analyzed_count == 0:
            logger.warning(f"Le fichier cible '{target_file_path_filter}' pour l'analyse de découpage n'a pas été trouvé ou ne dépassait pas le seuil de lignes.")

        # Sauvegarde du rapport JSON
        report_dir = self.workspace_path / "quality_proposals"
        try:
            report_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            report_file_path = report_dir / f"filesplit_plans_{timestamp}.json"
            with open(report_file_path, 'w', encoding='utf-8') as f:
                json.dump(all_file_split_plans, f, indent=2, ensure_ascii=False)
            logger.info(f"Rapport des plans de découpage de fichiers sauvegardé dans: {report_file_path}")
        except Exception as e_save_report:
            logger.error(f"Impossible de sauvegarder le rapport des plans de découpage: {e_save_report}", exc_info=True)
            report_file_path = None
        
        logger.info(f"Analyse {self.task_name} terminée. Fichiers candidats analysés (si > seuil ou ciblés): {files_actually_analyzed_count}. "
                    f"Entrées dans le rapport (plans ou erreurs): {len(all_file_split_plans)}.")
        
        return not task_had_critical_errors, report_file_path


    def apply_proposals(self, report_path: Path, force_apply: bool, args: Optional[Dict[str, Any]] = None) -> bool:
        """
        Applique un plan de découpage de fichiers à partir d'un rapport.
        NOTE: L'application réelle est un TODO majeur et complexe.
        """
        shared_utils.print_stage_header(f"Application Plan Découpage: {self.task_name} depuis {report_path.name}")
        logger.warning(f"APPLICATION DES PLANS DE DÉCOUPAGE (MODIFICATION FICHIERS) DEPUIS '{report_path.name}' NON IMPLÉMENTÉE. Mode Simulation.")
        
        if not report_path.is_file():
            logger.error(f"Fichier rapport '{report_path}' introuvable. Application annulée.")
            return False
        
        try:
            with open(report_path, 'r', encoding='utf-8') as f:
                split_plans_report = json.load(f) # C'est une liste d'objets "plan_entry"
        except Exception as e_read_report:
            logger.error(f"Impossible de lire ou parser le rapport de plans de découpage '{report_path}': {e_read_report}", exc_info=True)
            return False

        if not isinstance(split_plans_report, list):
            logger.error(f"Format de rapport invalide dans '{report_path}'. Attendu: une liste de plans.")
            return False

        applicable_plans_count = sum(1 for entry in split_plans_report 
                                     if entry.get("agent_response", {}).get("status") == "success_plan_generated")

        if applicable_plans_count == 0:
            logger.info("Aucun plan de découpage applicable ('status: success_plan_generated') trouvé dans le rapport.")
            return True

        if not force_apply:
            logger.info("Revue manuelle du plan de découpage est fortement recommandée avant d'appliquer avec --force.")
            confirm = input(f"Voulez-vous simuler l'application de {applicable_plans_count} plan(s) de découpage depuis '{report_path.name}'? (oui/Non): ")
            if confirm.lower() != "oui":
                logger.info("Application du plan de découpage annulée par l'utilisateur.")
                return True 
        
        logger.info(f"Début de la (simulation d')application de {applicable_plans_count} plan(s) de découpage...")
        applied_count = 0
        
        for plan_entry in split_plans_report:
            agent_response = plan_entry.get("agent_response", {})
            if agent_response.get("status") == "success_plan_generated":
                original_file = plan_entry.get("analyzed_file_path")
                logger.info(f"  (SIMULATION) Appliquerait le plan de découpage pour le fichier '{original_file}'.")
                logger.debug(f"    Plan détaillé: {json.dumps(agent_response, indent=2)}")
                # TODO: Implémenter la logique de refactoring complexe ici.
                # Voir les commentaires dans la version précédente de ce fichier pour les étapes.
                applied_count +=1
        
        logger.info(f"(SIMULATION) Application des plans de découpage terminée. {applied_count} plans seraient traités.")
        logger.warning("Rappel: La logique de refactoring réelle pour le découpage de fichiers est très complexe et n'est pas implémentée.")
        return True # Simuler succès pour l'instant

# --- Point d'entrée pour test direct ---
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', stream=sys.stderr)
    logger.info(f"--- Test direct de {FileSplitTask.task_name} ---")

    # Créer des mocks pour les dépendances
    current_test_dir_fs = Path(__file__).parent
    mock_target_project_path_fs = current_test_dir_fs / "test_project_data_fs"
    mock_workspace_path_fs = current_test_dir_fs / "test_workspace_data_fs"
    mock_target_project_path_fs.mkdir(parents=True, exist_ok=True)
    mock_workspace_path_fs.mkdir(parents=True, exist_ok=True)

    # Fichier Go long
    long_go_content_test_fs = "package services\nimport \"fmt\"\n" + "\n".join([f"// Doc for ServiceFunc{i}\nfunc ServiceFunc{i}(id string) string {{\n  return fmt.Sprintf(\"ServiceFunc{i}: %s\", id)\n}}" for i in range(70)]) # ~280 lignes
    long_go_file_rel_path_fs = "services/item_service.go"
    go_file_abs_test_fs = mock_target_project_path_fs / long_go_file_rel_path_fs
    go_file_abs_test_fs.parent.mkdir(parents=True, exist_ok=True)
    go_file_abs_test_fs.write_text(long_go_content_test_fs)
    
    # Fichier Templ long
    long_templ_content_test_fs = "package views\n" + "\n".join([f"// Templ Component{i}\ntempl ItemCard{i}(item string) {{\n\t<div class=\"card\">{{ item }} {i}</div>\n}}" for i in range(50)]) # ~150 lignes
    long_templ_file_rel_path_fs = "views/components/item_cards.templ"
    templ_file_abs_test_fs = mock_target_project_path_fs / long_templ_file_rel_path_fs
    templ_file_abs_test_fs.parent.mkdir(parents=True, exist_ok=True)
    templ_file_abs_test_fs.write_text(long_templ_content_test_fs)

    # Manifeste de mock
    mock_manifest_for_fs_test = {
        "fragments": {
            "services_item_service_func_ServiceFunc0": {
                "original_path": long_go_file_rel_path_fs, 
                "actual_source_path": long_go_file_rel_path_fs,
                "is_templ_source": False, "package_name": "services",
                "fragment_type": "function", "identifier": "ServiceFunc0", "start_line": 3, "end_line": 3
            },
            "views_item_cards_templ_ItemCard0": { 
                "original_path": "views/components/item_cards_templ.go", # Fictif _templ.go
                "actual_source_path": long_templ_file_rel_path_fs, 
                "is_templ_source": True, "package_name": "views", # Package du _templ.go
                "fragment_type": "component", "identifier": "ItemCard0", "start_line": 1, "end_line": 1
            }
        }
    }
    # Définir une variable d'environnement pour le seuil de test
    os.environ["QA_FILE_SPLIT_MAX_LINES"] = "100" # Pour que les deux fichiers de test soient "longs"

    try:
        from dotenv import load_dotenv
        dotenv_path = Path(__file__).resolve().parents[3] / ".env"
        if dotenv_path.exists(): load_dotenv(dotenv_path=dotenv_path, override=False); logger.info(f"Variables .env de {dotenv_path} chargées.")
        else: logger.warning(f".env non trouvé à {dotenv_path}.")

        if 'global_config' not in sys.modules:
            class MockGC: WORKSPACE_PATH = mock_workspace_path_fs; TARGET_PROJECT_PATH = mock_target_project_path_fs
            sys.modules['global_config'] = MockGC; logger.info("global_config mocké.")

        task_instance_fs_test = FileSplitTask(mock_target_project_path_fs, mock_workspace_path_fs, mock_manifest_for_fs_test)
        
        logger.info("\n--- Test d'analyse de tous les fichiers (FileSplitTask) ---")
        success_all, report_path_all = task_instance_fs_test.analyze()
        logger.info(f"Analyse tous fichiers: Succès tâche={success_all}, Rapport={report_path_all}")
        if report_path_all and report_path_all.exists():
            logger.info(f"Contenu du rapport (filesplit all) (premières 1000 car.):\n{report_path_all.read_text()[:1000]}...")

        if report_path_all :
             logger.info(f"\n--- Test d'application (simulation) du rapport filesplit: {report_path_all.name} ---")
             apply_success = task_instance_fs_test.apply_proposals(report_path_all, force_apply=True)
             logger.info(f"Application (simulation) filesplit: Succès tâche={apply_success}")

    except Exception as e:
        logger.error(f"Erreur durant le test direct de FileSplitTask: {e}", exc_info=True)
    finally:
        # import shutil
        # if mock_target_project_path_fs.exists(): shutil.rmtree(mock_target_project_path_fs)
        # if mock_workspace_path_fs.exists(): shutil.rmtree(mock_workspace_path_fs)
        logger.info("Nettoyage des dossiers de mock FileSplitTask (si décommenté).")