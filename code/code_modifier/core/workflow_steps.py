# code/code_modifier/core/workflow_steps.py
"""
Implémentation des étapes majeures du workflow de modification de code:
Sélection sémantique, Planification, Préparation Workspace, Exécution,
Génération de Rapport de Diff/Plan d'Application, et Finalisation.
"""

import sys
import os
import json
import importlib
import shutil
import time
import datetime
import traceback
import inspect
import logging
from pathlib import Path
from typing import Tuple, Optional, Set, Dict, Any, Type, List
import difflib  # Pour générer les diffs

logger = logging.getLogger(__name__)

# --- Gestion des Imports et Chemins ---
try:
    CURRENT_WORKFLOW_STEPS_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT_FOR_WORKFLOW_STEPS = CURRENT_WORKFLOW_STEPS_DIR.parents[1]
    if str(PROJECT_ROOT_FOR_WORKFLOW_STEPS) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT_FOR_WORKFLOW_STEPS))

    import global_config
    from lib import utils as shared_utils
    from agents.base_agent import BaseAgent
    from . import context_builder
    from embedding.core import faiss_selector
except ImportError as e_import:
    _err_msg = (
        f"Erreur CRITIQUE [WorkflowSteps Init]: Imports échoués: {e_import}\n"
        f"  PROJECT_ROOT_FOR_WORKFLOW_STEPS: '{PROJECT_ROOT_FOR_WORKFLOW_STEPS}'\n"
        f"  PYTHONPATH: {sys.path}")
    print(_err_msg, file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(2)
except Exception as e_init_ws:
    _err_msg_init = f"Erreur inattendue init WorkflowSteps: {e_init_ws}"
    print(_err_msg_init, file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(2)
# ------------------------------------

PLANNER_AGENT_NAME = "planner"


def load_agent_class(agent_name: str) -> Optional[Type[BaseAgent]]:
    """Charge dynamiquement la classe Agent."""
    try:
        module_path = f"agents.{agent_name}.agent"
        logger.debug(f"Chargement module agent: {module_path}")
        agent_module = importlib.import_module(module_path)
        agent_classes = inspect.getmembers(
            agent_module,
            lambda m: inspect.isclass(m) and m != BaseAgent and issubclass(
                m, BaseAgent))
        if not agent_classes:
            logger.error(
                f"Aucune classe BaseAgent trouvée dans {module_path}.")
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"Membres de {module_path}: {[m[0] for m in inspect.getmembers(agent_module)]}"
                )
            return None
        if len(agent_classes) > 1:
            logger.warning(
                f"Plusieurs classes Agent trouvées dans {module_path}. Utilisation de '{agent_classes[0][0]}'."
            )
        agent_class = agent_classes[0][1]
        logger.debug(
            f"Classe agent '{agent_class.__name__}' chargée depuis {module_path}."
        )
        return agent_class
    except ModuleNotFoundError:
        logger.error(
            f"Module agent introuvable: {module_path}. Vérifiez nom et structure agents/"
        )
        return None
    except Exception as e:
        logger.error(
            f"Chargement classe agent '{agent_name}' depuis '{module_path}' échoué: {type(e).__name__} - {e}",
            exc_info=True)
        return None


def run_semantic_fragment_selection(
    validated_request: str,
    top_k_selection: int = 10,
    similarity_threshold_selection: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    """Sélectionne les fragments pertinents via embeddings."""
    shared_utils.print_stage_header(
        "Phase 2: Sélection Sémantique des Fragments")
    effective_top_k = getattr(global_config, 'EMBEDDING_TOP_K_SELECTION',
                              top_k_selection)
    effective_threshold = getattr(global_config,
                                  'EMBEDDING_SIMILARITY_THRESHOLD',
                                  similarity_threshold_selection)
    logger.info(
        f"Paramètres sélection: top_k={effective_top_k}, similarity_threshold={effective_threshold or 'N/D'}"
    )
    try:
        ids, scores, err_msg = faiss_selector.find_relevant_fragments(
            validated_request, effective_top_k, effective_threshold)
    except Exception as e:
        logger.critical(f"Erreur inattendue appel faiss_selector: {e}",
                        exc_info=True)
        return None
    if err_msg:
        logger.error(f"Erreur sélection sémantique: {err_msg}")
        return None
    if not ids:
        logger.warning(
            "Aucun fragment pertinent trouvé par sélection sémantique.")
        return {
            "status": "success_no_fragments_found",
            "relevant_fragment_ids": [],
            "reasoning": "Aucun fragment sémantiquement similaire.",
            "similarity_scores": []
        }
    logger.info(
        f"Sélection sémantique: {len(ids)} ID(s) pertinent(s) sélectionné(s).")
    reasoning = ". ".join([
        f"Fragment '{fid}' (distance L2²: {s:.4f})"
        for fid, s in zip(ids, scores)
    ])
    logger.info(
        f"Raisonnement sélection (scores): {reasoning[:250]}{'...' if len(reasoning) > 250 else ''}"
    )
    return {
        "status": "success",
        "relevant_fragment_ids": ids,
        "reasoning": reasoning,
        "similarity_scores": scores
    }


def run_planning(validated_request: str, relevant_fragment_ids: List[str],
                 selection_reasoning: Optional[str],
                 full_manifest_data: Dict[str,
                                          Any], target_project_root_path: Path,
                 workspace_path: Path) -> Optional[Dict[str, Any]]:
    """Appelle l'agent Planner pour générer le plan d'exécution."""
    shared_utils.print_stage_header("Phase 3: Planification")
    PlannerClass = load_agent_class(PLANNER_AGENT_NAME)
    if not PlannerClass:
        logger.critical(
            f"Agent Planner ('{PLANNER_AGENT_NAME}') introuvable. Planification impossible."
        )
        return None

    generated_plan: Optional[Dict[str, Any]] = None
    planner_agent_response: Optional[Dict[str, Any]] = None
    try:
        planner = PlannerClass()
        logger.info(
            f"Construction contexte Planner ({len(relevant_fragment_ids)} fragment(s) pertinent(s))."
        )
        planner_ctx = context_builder.build_planner_context(
            relevant_fragment_ids, full_manifest_data,
            target_project_root_path, validated_request, selection_reasoning)
        if not planner_ctx:
            logger.error(
                f"Échec critique construction contexte Planner. Planification annulée."
            )
            return None
        if not planner_ctx.get(
                "relevant_code_fragments") and relevant_fragment_ids:
            logger.warning(
                f"Aucun code extrait pour Planner malgré {len(relevant_fragment_ids)} ID(s) sélectionnés."
            )

        logger.info(
            f"Appel agent '{PLANNER_AGENT_NAME}' pour planification...")
        planner_agent_response = planner.run(planner_ctx)

        if planner_agent_response and planner_agent_response.get(
                "status") == "success" and planner_agent_response.get(
                    "plan_status") == "success":
            if isinstance(planner_agent_response.get("steps"), list):
                logger.info(
                    f"Planification réussie par Planner ({len(planner_agent_response['steps'])} étape(s))."
                )
                generated_plan = planner_agent_response
            else:
                logger.error(
                    f"Planner a retourné plan_status 'success' mais 'steps' invalide/manquant. Réponse: {str(planner_agent_response)[:500]}"
                )
        else:
            err_msg = "Erreur Planner non spécifiée."
            if isinstance(planner_agent_response, dict):
                err_msg = planner_agent_response.get(
                    "message",
                    planner_agent_response.get(
                        "error_message",
                        f"Réponse invalide/échec Planner. Aperçu: {str(planner_agent_response)[:200]}"
                    ))
                if "estimated_tokens" in planner_agent_response:
                    err_msg += f" (Tokens estimés: {planner_agent_response['estimated_tokens']})"
            else:
                err_msg = f"Réponse non-dict/invalide Planner: {str(planner_agent_response)[:200]}"
            logger.error(
                f"Planification échouée (Agent: {PLANNER_AGENT_NAME}): {err_msg}"
            )

        if isinstance(planner_agent_response,
                      dict):  # Toujours sauvegarder la réponse du planner
            plan_file = workspace_path / "workflow_plan.json"
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text(json.dumps(planner_agent_response,
                                            indent=2,
                                            ensure_ascii=False),
                                 encoding='utf-8')
            status_log = "réussi" if generated_plan else "échoué (réponse sauvegardée)"
            logger.info(
                f"Plan/Réponse du Planner sauvegardé: {plan_file} (Statut plan: {status_log})"
            )
    except Exception as e:
        logger.critical(
            f"Erreur critique instanciation/exécution Agent {PLANNER_AGENT_NAME}: {e}",
            exc_info=True)
    return generated_plan


def prepare_execution_workspace(target_project_path: Path,
                                workspace_path: Path) -> Optional[Path]:
    """Prépare le workspace d'exécution en copiant le projet cible."""
    shared_utils.print_stage_header(
        "Phase 4a: Préparation Workspace d'Exécution")
    ws_project_dir = workspace_path / "current_project_state"
    try:
        if ws_project_dir.exists():
            logger.info(
                f"Nettoyage workspace exécution précédent: {ws_project_dir}")
            # ... (logique de rmtree avec retries comme dans la version précédente) ...
            shutil.rmtree(
                ws_project_dir
            )  # Simplifié pour cet exemple, ajouter retries si problèmes de lock fréquents
            if ws_project_dir.exists():
                logger.critical(
                    f"Impossible supprimer workspace précédent '{ws_project_dir}'."
                )
                return None

        workspace_path.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"Copie projet cible '{target_project_path}' vers workspace '{ws_project_dir}'..."
        )
        ignore = shutil.ignore_patterns('.git*', 'venv', '__pycache__',
                                        '*.pyc', 'node_modules', 'vendor',
                                        'tmp*', 'build', 'dist', 'target',
                                        '*.log', '*.bak', 'workspace',
                                        'debug_outputs', '*.exe', '*_test.go')
        shutil.copytree(str(target_project_path),
                        str(ws_project_dir),
                        ignore=ignore,
                        dirs_exist_ok=True)
        logger.info("Copie vers workspace d'exécution terminée.")
        return ws_project_dir.resolve()
    except Exception as e:
        logger.critical(
            f"ERREUR préparation workspace '{target_project_path}' -> '{ws_project_dir}': {e}",
            exc_info=True)
        if ws_project_dir.exists():
            try:
                shutil.rmtree(ws_project_dir)
            except Exception as e_clean:
                logger.error(f"Échec nettoyage workspace partiel: {e_clean}")
        return None


# --- NOUVELLE ÉTAPE: Génération Rapport de Diff et Plan d'Application ---
def generate_diff_report_text(
        target_project_path: Path, workspace_project_dir: Path,
        relative_paths_of_modified_files: Set[str]) -> str:
    """Génère le contenu textuel du rapport de diff."""
    all_diffs_lines: List[str] = []
    if not relative_paths_of_modified_files:
        all_diffs_lines.append(
            "Aucun fichier marqué comme modifié dans le workspace.\n")
        return "".join(all_diffs_lines)

    for rel_path_str in sorted(list(relative_paths_of_modified_files)):
        original_file = target_project_path / rel_path_str
        modified_file = workspace_project_dir / rel_path_str
        all_diffs_lines.append(f"--- Diff for {rel_path_str} ---\n")

        if not modified_file.is_file():
            all_diffs_lines.append(
                f"ERREUR: Fichier modifié '{modified_file}' non trouvé dans workspace.\n\n"
            )
            continue

        mod_lines = modified_file.read_text(
            encoding='utf-8', errors='ignore').splitlines(keepends=True)
        orig_lines: List[str] = []
        from_label, to_label = f"a/{rel_path_str}", f"b/{rel_path_str}"

        if original_file.is_file():
            orig_lines = original_file.read_text(
                encoding='utf-8', errors='ignore').splitlines(keepends=True)
        else:  # Nouveau fichier
            from_label = "/dev/null"
            all_diffs_lines.append(f"+++ Nouveau fichier: {rel_path_str}\n")

        diff_gen = difflib.unified_diff(orig_lines,
                                        mod_lines,
                                        fromfile=from_label,
                                        tofile=to_label,
                                        lineterm='\n')
        diff_output = list(diff_gen)
        if diff_output: all_diffs_lines.extend(diff_output)
        elif original_file.is_file():
            all_diffs_lines.append("Aucune différence textuelle trouvée.\n")
        all_diffs_lines.append("\n\n")
    return "".join(all_diffs_lines)


def generate_apply_plan_and_diff_report(
        target_project_path: Path,
        workspace_project_dir: Path,  # C'est current_project_state
        relative_paths_of_modified_files: Set[str],
        workspace_path_for_reports: Path
) -> Tuple[Optional[Path], Optional[Path]]:
    """Génère le rapport de diff (texte) et un plan d'application JSON."""
    reports_dir = workspace_path_for_reports / "modification_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    logger.info(
        f"Génération rapport de diff et plan d'application (timestamp: {timestamp})..."
    )

    diff_report_content_str = generate_diff_report_text(
        target_project_path, workspace_project_dir,
        relative_paths_of_modified_files)
    diff_report_path: Optional[Path] = None
    if diff_report_content_str.strip(
    ):  # Sauvegarder seulement s'il y a du contenu
        diff_file = reports_dir / f"diff_report_{timestamp}.txt"
        try:
            diff_file.write_text(diff_report_content_str, encoding='utf-8')
            diff_report_path = diff_file
        except Exception as e:
            logger.error(f"Erreur sauvegarde rapport diff '{diff_file}': {e}",
                         exc_info=True)
    else:
        logger.info("Contenu du rapport de diff vide, fichier non créé.")

    apply_plan_data = {
        "report_timestamp": timestamp,
        "source_workspace_subpath":
        workspace_project_dir.name,  # ex: "current_project_state"
        "target_project_path_at_generation":
        str(target_project_path),  # Pour info
        "files_to_apply": sorted(list(relative_paths_of_modified_files)),
        "diff_report_filename":
        diff_report_path.name if diff_report_path else None
    }
    apply_plan_path = reports_dir / f"apply_plan_{timestamp}.json"
    try:
        with open(apply_plan_path, 'w', encoding='utf-8') as f:
            json.dump(apply_plan_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Plan d'application sauvegardé: {apply_plan_path}")
    except Exception as e:
        logger.error(
            f"Erreur sauvegarde plan d'application '{apply_plan_path}': {e}",
            exc_info=True)
        apply_plan_path = None

    return diff_report_path, apply_plan_path


# --- Étape de Finalisation (modifiée pour utiliser le plan d'application) ---
def finalize_execution(
    apply_plan_path: Path,
    workspace_path: Path,  # Racine du workspace
    target_project_path: Path
) -> bool:  # Retourne True si l'application a réussi
    shared_utils.print_stage_header(
        "Phase 5: Finalisation (Application du Plan)")
    if not apply_plan_path.is_file():
        logger.critical(
            f"Fichier plan d'application '{apply_plan_path}' introuvable. Application annulée."
        )
        return False
    try:
        with open(apply_plan_path, 'r', encoding='utf-8') as f:
            plan_data = json.load(f)
    except Exception as e:
        logger.critical(f"Erreur lecture plan '{apply_plan_path}': {e}",
                        exc_info=True)
        return False

    rel_paths_to_apply = plan_data.get("files_to_apply")
    ws_subpath = plan_data.get("source_workspace_subpath",
                               "current_project_state")
    ws_project_dir_final = workspace_path / ws_subpath

    if not isinstance(rel_paths_to_apply, list) or not rel_paths_to_apply:
        logger.info(
            "Plan d'application vide. Aucune modification à appliquer.")
        return True

    logger.info(
        f"Application de {len(rel_paths_to_apply)} fichier(s) depuis plan '{apply_plan_path.name}'..."
    )
    ts_backup = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = workspace_path / "backups" / f"backup_modifier_apply_{ts_backup}"
    try:
        files_to_backup = {
            p
            for p in rel_paths_to_apply if (target_project_path / p).is_file()
        }
        if files_to_backup:
            shared_utils.backup_files(list(files_to_backup), backup_dir,
                                      target_project_path)
            logger.info(f"Backup originaux créé: {backup_dir.name}")
        else:
            logger.info(
                "Aucun fichier existant à sauvegarder dans projet cible.")
    except Exception as e:
        logger.critical(f"ERREUR CRITIQUE backup: {e}", exc_info=True)
        logger.critical("CHANGEMENTS NON APPLIQUÉS!")
        return False

    copied = 0
    errors = False
    for rel_path in rel_paths_to_apply:
        src = ws_project_dir_final / rel_path
        dst = target_project_path / rel_path
        if not src.is_file():
            logger.error(
                f"Fichier source '{src}' du plan non trouvé dans '{ws_project_dir_final.name}'. Ignoré."
            )
            errors = True
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            logger.debug(f"  - Appliqué: {rel_path}")
            copied += 1
        except Exception as e:
            logger.error(f"  ERREUR copie finale '{rel_path}' -> '{dst}': {e}",
                         exc_info=True)
            errors = True

    if errors:
        logger.error(
            f"Erreur(s) application finale. Backup: {backup_dir.name}. VÉRIFICATION MANUELLE."
        )
        return False
    logger.info(
        f"{copied} fichier(s) du plan appliqués. Backup originaux: {backup_dir.name}"
    )
    return True


# ... (if __name__ == "__main__") ...
