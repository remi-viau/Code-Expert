# code/code_quality/tasks/docstring_task.py
import logging
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List
import json
import datetime
import sys # Ajouté pour la gestion de sys.path dans le bloc de test

# Imports depuis la racine du projet 'code'
try:
    from agents.qa_docstringenricher.agent import QADocstringEnricherAgent
    from lib import utils as shared_utils
    from .base_quality_task import BaseQualityTask # Import relatif
except ImportError as e_initial_import:
    # Gérer le cas où ce module est exécuté directement pour des tests
    # et que le sys.path n'est pas configuré comme lorsque l'orchestrateur l'appelle.
    if __name__ == '__main__':
        CURRENT_SCRIPT_DIR = Path(__file__).resolve().parent
        # tasks -> code_quality -> code
        PROJECT_ROOT_FOR_TASK = CURRENT_SCRIPT_DIR.parents[1]
        if str(PROJECT_ROOT_FOR_TASK) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT_FOR_TASK))
        
        # Réessayer les imports après avoir ajusté sys.path
        from agents.qa_docstringenricher.agent import QADocstringEnricherAgent
        from lib import utils as shared_utils
        # Pour BaseQualityTask, si __init__.py dans tasks l'exporte, on pourrait faire:
        # from ..base_quality_task import BaseQualityTask # si tasks est un package
        # ou un import absolu si la structure est plate
        from code_quality.tasks.base_quality_task import BaseQualityTask
    else:
        # Si ce n'est pas une exécution directe et que l'import échoue, c'est une vraie erreur.
        print(f"Erreur d'import dans docstring_task.py (non direct): {e_initial_import}", file=sys.stderr)
        raise e_initial_import # Propager l'erreur

logger = logging.getLogger(__name__)

class DocstringTask(BaseQualityTask):
    """
    Tâche de qualité pour analyser et proposer des améliorations
    aux docstrings des fragments de code Go et Templ.
    """
    task_name: str = "DocstringEnrichment" # Surcharge de l'attribut de la classe de base

    def __init__(self, 
                 target_project_path: Path, 
                 workspace_path: Path, 
                 full_manifest_data: Dict[str, Any]): # Manifeste est requis
        super().__init__(target_project_path, workspace_path, full_manifest_data)
        
        if not self.full_manifest_data or "fragments" not in self.full_manifest_data:
            msg = f"[{self.task_name}] Le manifeste de fragments est requis et doit contenir la clé 'fragments'."
            logger.error(msg)
            raise ValueError(msg)
        
        try:
            self.enricher_agent = QADocstringEnricherAgent()
            logger.debug(f"[{self.task_name}] Agent QADocstringEnricherAgent initialisé.")
        except Exception as e_agent_init:
            logger.error(f"[{self.task_name}] Initialisation de QADocstringEnricherAgent échouée: {e_agent_init}", exc_info=True)
            raise RuntimeError(f"Échec initialisation agent pour {self.task_name}: {e_agent_init}") from e_agent_init
            
        if not self.enricher_agent._config: # Vérifier si la config de l'agent est chargée
            msg = f"[{self.task_name}] Configuration non chargée pour l'agent {self.enricher_agent.agent_name}."
            logger.error(msg)
            raise RuntimeError(f"Config agent non chargée pour {self.task_name}")


    def analyze(self, args: Optional[Dict[str, Any]] = None) -> Tuple[bool, Optional[Path]]:
        """
        Exécute l'analyse des docstrings pour les fragments éligibles.
        Génère des propositions et les sauvegarde dans un rapport JSON.

        Args:
            args: Dictionnaire d'arguments. Peut contenir :
                  'target_fragment_id_filter': Pour cibler un fragment spécifique pour l'analyse.
        """
        shared_utils.print_stage_header(f"Analyse Qualité: {self.task_name}")
        target_fragment_id_filter = args.get("target_fragment_id_filter") if args else None

        if target_fragment_id_filter:
            logger.info(f"Ciblage du fragment spécifique pour analyse de docstring: '{target_fragment_id_filter}'")
        else:
            logger.info(f"Analyse de tous les fragments éligibles pour enrichissement des docstrings...")

        report_file_path: Optional[Path] = None # Chemin du rapport qui sera généré
        fragments_from_manifest = self.full_manifest_data.get("fragments", {})
        
        if not fragments_from_manifest:
            logger.info("Aucun fragment trouvé dans le manifeste. Tâche d'enrichissement des docstrings ignorée.")
            return True, None # Tâche considérée comme réussie car il n'y a rien à faire.

        all_agent_responses: List[Dict[str, Any]] = []
        successful_proposals_count = 0
        failed_proposals_count = 0
        fragments_actually_processed_count = 0 # Compte les fragments après application du filtre

        for frag_id, frag_info in fragments_from_manifest.items():
            if target_fragment_id_filter and frag_id != target_fragment_id_filter:
                continue # Appliquer le filtre
            
            fragments_actually_processed_count += 1
            
            # Filtre sur les types de fragments pertinents pour les docstrings
            # "component" est un type possible pour les éléments Templ si votre manifeste le supporte.
            if frag_info.get("fragment_type") not in ["function", "method", "type", "component"]:
                logger.debug(f"Fragment '{frag_id}' (type: {frag_info.get('fragment_type')}) non pertinent pour docstring. Ignoré.")
                continue

            # Utiliser actual_source_path et is_templ_source directement du manifeste (remplis par ast_parser.go)
            actual_src_rel_path = frag_info.get("actual_source_path")
            is_templ_src = frag_info.get("is_templ_source", False)

            if not actual_src_rel_path:
                logger.warning(f"Champ 'actual_source_path' manquant pour fragment '{frag_id}' dans le manifeste. Ignoré.")
                all_agent_responses.append({"fragment_id_context": frag_id, "agent_response": {"status": "error", "error_message": "actual_source_path manquant dans manifeste."}})
                failed_proposals_count += 1; continue
            
            logger.info(f"  Traitement fragment '{frag_id}' (Fichier source: '{actual_src_rel_path}', Templ: {is_templ_src})...")
            abs_code_path = self.target_project_path / actual_src_rel_path
            code_to_analyze: Optional[str] = None
            
            try:
                if not abs_code_path.is_file():
                    logger.warning(f"  Fichier source '{abs_code_path}' (de actual_source_path) non trouvé. Ignoré.")
                    raise FileNotFoundError(f"Fichier source {abs_code_path} non trouvé.")

                if is_templ_src: # Lire le contenu entier du fichier .templ
                    code_to_analyze = abs_code_path.read_text(encoding='utf-8')
                    logger.debug(f"    Contenu du fichier .templ '{actual_src_rel_path}' lu pour analyse.")
                else: # Fichier .go, extraire le fragment spécifique
                    code_to_analyze = shared_utils.extract_function_body(
                        abs_code_path, 
                        frag_info["start_line"], 
                        frag_info["end_line"]
                    )
            except Exception as e_read_code: # Attrape FileNotFoundError et autres erreurs de lecture
                logger.error(f"  Erreur lecture/extraction code pour '{frag_id}' depuis '{abs_code_path}': {e_read_code}", exc_info=False)
            
            if not code_to_analyze: # Si l'extraction ou la lecture a échoué
                logger.warning(f"  Impossible d'obtenir le code à analyser pour '{frag_id}' depuis '{abs_code_path}'. Ignoré.")
                all_agent_responses.append({"fragment_id_context": frag_id, "original_path_context": actual_src_rel_path, "agent_response": {"status": "error", "error_message": "Code non obtenu/lu."}})
                failed_proposals_count += 1; continue

            # Préparer le contexte pour l'agent enrichisseur
            agent_context = {
                "fragment_id": frag_id,
                "original_path": actual_src_rel_path, # C'est actual_source_path ici
                "is_templ_source": is_templ_src,
                "code_block": code_to_analyze, # Contenu du .templ entier ou du fragment .go
                "current_docstring": frag_info.get("docstring"), # Docstring de l'AST du .go (peut être non pertinent pour .templ)
                "fragment_type": frag_info.get("fragment_type"),
                "identifier": frag_info.get("identifier"),
                "package_name": frag_info.get("package_name"),
                "signature": frag_info.get("signature"),
                "definition": frag_info.get("definition")
            }
            
            try:
                result_from_agent = self.enricher_agent.run(agent_context)
            except Exception as e_agent_run:
                logger.error(f"  Erreur lors de l'exécution de l'agent enrichisseur pour '{frag_id}': {e_agent_run}", exc_info=True)
                result_from_agent = {"status": "error", "fragment_id": frag_id, "error_message": f"Exception pendant agent.run: {e_agent_run}"}

            # Enregistrer la réponse de l'agent
            response_entry = {
                "fragment_id_context": frag_id,
                "original_path_context": actual_src_rel_path, # Le chemin du fichier source analysé
                "is_templ_source_analyzed": is_templ_src, # Pour information dans le rapport
                "agent_response": result_from_agent if isinstance(result_from_agent, dict) else 
                                  {"status": "error", "error_message": "Réponse de l'agent de type invalide ou None"}
            }
            all_agent_responses.append(response_entry)

            # Compter les succès/échecs basés sur le statut retourné par l'agent
            if result_from_agent and result_from_agent.get("status") == "success":
                successful_proposals_count +=1
            elif not result_from_agent or result_from_agent.get("status") == "error":
                failed_proposals_count += 1
            # "no_change_needed" n'est ni un succès de proposition, ni un échec.
        
        if target_fragment_id_filter and fragments_actually_processed_count == 0:
            logger.warning(f"Le fragment cible '{target_fragment_id_filter}' pour l'enrichissement de docstring n'a pas été trouvé ou n'était pas éligible.")
        elif not target_fragment_id_filter and fragments_actually_processed_count == 0 and len(fragments_from_manifest) > 0 :
             logger.warning(f"Aucun fragment n'a été traité pour l'enrichissement de docstring (vérifiez les filtres de type de fragment).")


        # Sauvegarde du rapport JSON
        report_dir = self.workspace_path / "quality_proposals"
        try:
            report_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            report_file_path = report_dir / f"docstring_proposals_{timestamp}.json"
            with open(report_file_path, 'w', encoding='utf-8') as f:
                json.dump(all_agent_responses, f, indent=2, ensure_ascii=False)
            logger.info(f"Rapport des propositions d'enrichissement de docstrings sauvegardé dans: {report_file_path}")
        except Exception as e_save_report:
            logger.error(f"Impossible de sauvegarder le rapport des propositions de docstrings: {e_save_report}", exc_info=True)
            report_file_path = None # Indiquer que le rapport n'a pas pu être sauvegardé
        
        logger.info(f"Analyse {self.task_name} terminée. Fragments éligibles traités: {fragments_actually_processed_count}, "
                    f"Propositions d'agent avec statut 'success': {successful_proposals_count}, "
                    f"Échecs/Erreurs d'agent: {failed_proposals_count}")
        
        # La tâche d'analyse est considérée comme "réussie" si elle a pu s'exécuter et (tenter de) générer un rapport.
        # Les échecs d'agents individuels sont notés dans le rapport.
        # Un échec critique de la tâche serait si l'agent ne s'initialise pas ou si l'écriture du rapport plante.
        return True, report_file_path

    def apply_proposals(self, report_path: Path, force_apply: bool, args: Optional[Dict[str, Any]] = None) -> bool:
        """
        Applique les propositions d'amélioration de docstrings à partir d'un rapport.
        NOTE: L'application réelle des changements de code est un TODO complexe.
              Cette méthode simule actuellement l'application.
        """
        shared_utils.print_stage_header(f"Application Propositions: {self.task_name} depuis {report_path.name}")
        logger.warning(f"APPLICATION DES DOCSTRINGS (MODIFICATION FICHIERS) DEPUIS '{report_path.name}' NON IMPLÉMENTÉE. Mode Simulation.")
        
        if not report_path.is_file():
            logger.error(f"Fichier rapport '{report_path}' introuvable. Application annulée.")
            return False

        try:
            with open(report_path, 'r', encoding='utf-8') as f:
                proposals_report_data = json.load(f) # C'est une liste de all_agent_responses
        except Exception as e_read_report:
            logger.error(f"Impossible de lire ou parser le rapport '{report_path}': {e_read_report}", exc_info=True)
            return False

        if not isinstance(proposals_report_data, list):
            logger.error(f"Format de rapport invalide dans '{report_path}'. Attendu: une liste.")
            return False

        applicable_changes_count = sum(1 for item in proposals_report_data 
                                       if item.get("agent_response", {}).get("status") == "success" and \
                                          item.get("agent_response", {}).get("proposed_docstring") is not None)

        if applicable_changes_count == 0:
            logger.info("Aucune proposition de docstring applicable ('status: success' avec 'proposed_docstring') trouvée dans le rapport.")
            return True # Pas d'erreur, juste rien à faire.

        if not force_apply:
            logger.info("Revue manuelle des propositions est fortement recommandée avant d'appliquer avec --force.")
            confirm = input(f"Voulez-vous simuler l'application de {applicable_changes_count} proposition(s) de docstrings depuis '{report_path.name}'? (oui/Non): ")
            if confirm.lower() != "oui":
                logger.info("Application des changements de docstrings annulée par l'utilisateur.")
                return True 
        
        logger.info(f"Début de la (simulation d')application de {applicable_changes_count} propositions de docstrings...")
        applied_count = 0
        skipped_or_failed_count = 0

        # TODO: Implémenter la logique de Workspace QA ici si application réelle
        # qa_workspace_dir = self._create_qa_workspace("docstrings")
        # if not qa_workspace_dir: return False

        for proposal_item in proposals_report_data:
            agent_response = proposal_item.get("agent_response", {})
            target_file_rel = proposal_item.get("original_path_context")
            fragment_id = proposal_item.get("fragment_id_context")
            is_templ = proposal_item.get("is_templ_source_analyzed", False)

            if agent_response.get("status") == "success" and agent_response.get("proposed_docstring") is not None:
                # Ici, la logique de modification de fichier serait appelée.
                # Elle aurait besoin de:
                # - self.target_project_path (ou qa_workspace_dir / target_file_rel)
                # - target_file_rel
                # - agent_response.get("proposed_docstring")
                # - Les infos de ligne du fragment original (start_line, end_line du docstring existant)
                #   depuis self.full_manifest_data.fragments[fragment_id] pour localiser l'endroit.
                #   Pour les fichiers .templ, la localisation serait plus complexe (trouver `templ Identifier(...){`).
                
                logger.info(f"  (SIMULATION) Appliquerait docstring pour fragment '{fragment_id}' "
                            f"dans le fichier '{target_file_rel}' (Templ: {is_templ}).")
                # logger.debug(f"    Nouveau Docstring: {agent_response.get('proposed_docstring')[:100]}...") # Optionnel
                applied_count +=1
            else:
                skipped_or_failed_count +=1
        
        # TODO: Si application réelle:
        # 1. Lancer build/tests sur le workspace QA.
        # 2. Si OK, self._finalize_qa_application(qa_workspace_dir, set_of_modified_files_relative)
        # 3. Nettoyer le workspace QA.
        
        logger.info(f"(SIMULATION) Application des docstrings terminée. {applied_count} seraient tentées, {skipped_or_failed_count} ignorées/non-applicables.")
        logger.warning("Rappel: La logique de modification réelle des fichiers sources n'est PAS implémentée.")
        return True # Simuler succès pour l'instant

# --- Point d'entrée pour test direct ---
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', stream=sys.stderr)
    logger.info(f"--- Test direct de {DocstringTask.task_name} ---")

    # Créer des mocks pour les dépendances
    # S'assurer que les chemins sont relatifs à l'emplacement d'exécution du test
    current_test_dir = Path(__file__).parent
    mock_target_project_path = current_test_dir / "test_project_data_ds"
    mock_workspace_path = current_test_dir / "test_workspace_data_ds"
    mock_target_project_path.mkdir(parents=True, exist_ok=True)
    mock_workspace_path.mkdir(parents=True, exist_ok=True)

    # Créer un fichier Go de test
    test_go_file_content = """package main
import "fmt"
// Old doc for Greet
func Greet(name string) string {
	return fmt.Sprintf("Hello, %s!", name)
}
type MyStruct struct { FieldA int }
// No doc here
func Process(data MyStruct) { fmt.Println(data.FieldA) }"""
    test_go_file_rel_path = "main.go"
    (mock_target_project_path / test_go_file_rel_path).write_text(test_go_file_content)

    # Créer un fichier Templ de test
    test_templ_file_content = """package main
// Templ doc for Hello
templ Hello(name string) {
	<h1>Hello, {name}!</h1>
}"""
    test_templ_file_rel_path = "ui/hello.templ"
    (mock_target_project_path / "ui").mkdir(parents=True, exist_ok=True)
    (mock_target_project_path / test_templ_file_rel_path).write_text(test_templ_file_content)

    mock_manifest = {
        "fragments": {
            "main_main_func_Greet": {
                "original_path": test_go_file_rel_path, "actual_source_path": test_go_file_rel_path,
                "is_templ_source": False, "fragment_type": "function", "identifier": "Greet", "package_name": "main",
                "signature": "func Greet(name string) string", "start_line": 3, "end_line": 5, # Lignes du code, pas du docstring
                "docstring": "// Old doc for Greet"
            },
            "main_main_type_MyStruct": {
                "original_path": test_go_file_rel_path, "actual_source_path": test_go_file_rel_path,
                "is_templ_source": False, "fragment_type": "type", "identifier": "MyStruct", "package_name": "main",
                "definition": "type MyStruct struct {\n\tFieldA int\n}", "start_line": 7, "end_line": 7,
                "docstring": ""
            },
            "main_main_func_Process": {
                "original_path": test_go_file_rel_path, "actual_source_path": test_go_file_rel_path,
                "is_templ_source": False, "fragment_type": "function", "identifier": "Process", "package_name": "main",
                "signature": "func Process(data MyStruct)", "start_line": 9, "end_line": 11,
                "docstring": ""
            },
            "main_ui_hello_templ_Hello": {
                "original_path": "ui/hello_templ.go", "actual_source_path": test_templ_file_rel_path,
                "is_templ_source": True, "fragment_type": "component", "identifier": "Hello", "package_name": "main",
                "signature": "templ Hello(name string)", "start_line": 2, "end_line": 4, # Lignes dans le _templ.go (fictif)
                "docstring": "// Doc from _templ.go" 
            }
        }
    }

    try:
        # Charger .env pour les tests
        from dotenv import load_dotenv
        # Remonter de plusieurs niveaux pour trouver le .env à la racine du projet global
        dotenv_path = Path(__file__).resolve().parents[3] / ".env" 
        if dotenv_path.exists():
            load_dotenv(dotenv_path=dotenv_path, override=False)
            logger.info(f"Variables .env de {dotenv_path} chargées pour le test DocstringTask.")
        else:
            logger.warning(f".env non trouvé à {dotenv_path}. S'assurer que les clés API/bases sont dans l'env système si besoin.")

        # Initialiser global_config si ce test est exécuté isolément
        # (normalement fait par l'orchestrateur principal)
        if 'global_config' not in sys.modules:
            # Créer un mock ou importer si la structure le permet
            class MockGlobalConfig: WORKSPACE_PATH = mock_workspace_path; TARGET_PROJECT_PATH = mock_target_project_path
            sys.modules['global_config'] = MockGlobalConfig()
            logger.info("global_config mocké pour le test direct de DocstringTask.")


        task_instance_test = DocstringTask(mock_target_project_path, mock_workspace_path, mock_manifest)
        
        logger.info("\n--- Test d'analyse de tous les fragments ---")
        success_all_test, report_path_all_test = task_instance_test.analyze()
        logger.info(f"Analyse tous fragments: Succès tâche={success_all_test}, Rapport={report_path_all_test}")
        if report_path_all_test and report_path_all_test.exists():
            logger.info(f"Contenu du rapport (tous) (premières 500 car.):\n{report_path_all_test.read_text()[:500]}...")

        if report_path_all_test:
            logger.info(f"\n--- Test d'application (simulation) du rapport: {report_path_all_test.name} ---")
            apply_success_test = task_instance_test.apply_proposals(report_path_all_test, force_apply=False)
            logger.info(f"Application (simulation): Succès tâche={apply_success_test}")

    except Exception as e_main_test:
        logger.error(f"Erreur durant le test direct de DocstringTask: {e_main_test}", exc_info=True)
    finally:
        # Nettoyage optionnel des dossiers de mock
        # import shutil
        # if mock_target_project_path.exists(): shutil.rmtree(mock_target_project_path)
        # if mock_workspace_path.exists(): shutil.rmtree(mock_workspace_path)
        logger.info("Nettoyage des dossiers de mock (si décommenté).")