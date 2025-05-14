# code/code_quality/tasks/utils_quality.py
import logging
from pathlib import Path
import os # Pour os.path.getmtime
import sys
from typing import Optional, Dict # Ajout de Dict pour type hinting

logger = logging.getLogger(__name__)

# Map pour les préfixes de fichiers de rapport et les types de tâches
# Peut être étendu si d'autres types de tâches de qualité sont ajoutés.
REPORT_PREFIX_MAP: Dict[str, str] = {
    "docstrings": "docstring_proposals_",
    "filesplit": "filesplit_plans_"
    # "another_task_type": "another_prefix_"
}

def find_latest_quality_report(workspace_path: Path, task_type: str) -> Optional[Path]:
    """
    Trouve le dernier rapport JSON généré pour un type de tâche de qualité donné
    dans le sous-dossier 'quality_proposals' du workspace.

    Args:
        workspace_path: Chemin vers le répertoire de workspace principal.
        task_type: Le type de tâche de qualité (ex: "docstrings", "filesplit").
                   Doit correspondre à une clé dans REPORT_PREFIX_MAP.

    Returns:
        Path vers le dernier fichier de rapport trouvé, ou None si aucun rapport
        n'est trouvé pour ce type de tâche ou si une erreur survient.
    """
    report_dir = workspace_path / "quality_proposals"
    if not report_dir.is_dir():
        logger.warning(f"Le dossier des rapports de qualité '{report_dir}' n'existe pas. Impossible de trouver le dernier rapport.")
        return None
    
    report_prefix = REPORT_PREFIX_MAP.get(task_type)
    if not report_prefix:
        logger.error(f"Type de tâche de qualité inconnu: '{task_type}'. Aucun préfixe de rapport correspondant trouvé.")
        logger.debug(f"Types de tâches supportés pour trouver rapports: {list(REPORT_PREFIX_MAP.keys())}")
        return None

    try:
        # Lister tous les fichiers JSON correspondant au préfixe
        # et les trier par date de modification (le plus récent en premier)
        candidate_reports = sorted(
            [file_path for file_path in report_dir.glob(f"{report_prefix}*.json") if file_path.is_file()],
            key=os.path.getmtime, # Utiliser os.path.getmtime pour la date de modification
            reverse=True # Le plus récent en premier
        )
    except Exception as e_glob:
        logger.error(f"Erreur lors de la recherche des fichiers de rapport pour '{task_type}' dans '{report_dir}': {e_glob}", exc_info=True)
        return None

    if candidate_reports:
        latest_report = candidate_reports[0]
        logger.info(f"Dernier rapport trouvé pour le type de tâche '{task_type}': {latest_report.name}")
        return latest_report.resolve() # Retourner le chemin absolu résolu
    else:
        logger.warning(f"Aucun rapport JSON trouvé pour le type de tâche '{task_type}' (préfixe: '{report_prefix}') dans {report_dir}.")
        return None

if __name__ == '__main__':
    # Bloc de test pour les utilitaires de qualité
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', stream=sys.stderr)
    logger.info(f"--- Test direct de {Path(__file__).name} ---")

    # Créer une structure de workspace de mock pour les tests
    mock_ws_path = Path("./mock_workspace_quality_utils")
    mock_proposals_dir = mock_ws_path / "quality_proposals"
    mock_proposals_dir.mkdir(parents=True, exist_ok=True)

    # Créer quelques fichiers de rapport de mock avec des timestamps différents
    # (os.utime peut être utilisé pour modifier les timestamps, ou simplement créer dans l'ordre)
    import time
    import datetime

    logger.info(f"Création de fichiers de rapport de mock dans: {mock_proposals_dir.resolve()}")
    
    # Fichiers Docstring
    (mock_proposals_dir / "docstring_proposals_20230101_100000.json").write_text("{}")
    time.sleep(0.1) # S'assurer que les timestamps sont différents
    report_ds_latest = mock_proposals_dir / "docstring_proposals_20230101_110000.json"
    report_ds_latest.write_text("{\"key\": \"latest_docstring\"}")
    time.sleep(0.1)
    (mock_proposals_dir / "docstring_proposals_20230101_090000.json").write_text("{}")

    # Fichiers Filesplit
    (mock_proposals_dir / "filesplit_plans_20230102_120000.json").write_text("{}")
    report_fs_latest = mock_proposals_dir / "filesplit_plans_20230102_130000.json"
    report_fs_latest.write_text("{\"key\": \"latest_filesplit\"}")

    # Fichier d'un autre type (ne devrait pas être trouvé)
    (mock_proposals_dir / "other_report_20230101_100000.json").write_text("{}")


    logger.info("\n--- Test de find_latest_quality_report ---")
    
    # Test pour docstrings
    latest_ds = find_latest_quality_report(mock_ws_path, "docstrings")
    if latest_ds and latest_ds.name == report_ds_latest.name:
        logger.info(f"Succès: Dernier rapport docstrings trouvé: {latest_ds.name}")
        logger.info(f"Contenu (vérification): {latest_ds.read_text()}")
    else:
        logger.error(f"Échec: Test docstrings. Attendu: {report_ds_latest.name}, Obtenu: {latest_ds.name if latest_ds else 'None'}")

    # Test pour filesplit
    latest_fs = find_latest_quality_report(mock_ws_path, "filesplit")
    if latest_fs and latest_fs.name == report_fs_latest.name:
        logger.info(f"Succès: Dernier rapport filesplit trouvé: {latest_fs.name}")
    else:
        logger.error(f"Échec: Test filesplit. Attendu: {report_fs_latest.name}, Obtenu: {latest_fs.name if latest_fs else 'None'}")

    # Test pour un type de tâche inconnu
    latest_unknown = find_latest_quality_report(mock_ws_path, "unknown_task_type")
    if latest_unknown is None:
        logger.info(f"Succès: Aucun rapport trouvé pour type inconnu (attendu).")
    else:
        logger.error(f"Échec: Test type inconnu. Attendu: None, Obtenu: {latest_unknown.name}")

    # Test avec un dossier de rapports vide
    empty_report_dir = mock_ws_path / "empty_quality_proposals"
    empty_report_dir.mkdir(exist_ok=True)
    latest_empty_ds = find_latest_quality_report(mock_ws_path, "docstrings") # Devrait toujours utiliser mock_proposals_dir
    if latest_empty_ds and latest_empty_ds.name == report_ds_latest.name: # Vérifier qu'il trouve bien dans le bon dossier
        logger.info(f"Succès: find_latest_quality_report ne s'est pas trompé de dossier.")
    
    # Test avec un dossier de workspace qui n'a pas 'quality_proposals'
    non_existent_report_dir_ws = mock_ws_path / "non_existent_reports_test"
    # non_existent_report_dir_ws.mkdir() # Ne pas créer le dossier quality_proposals ici
    latest_non_existent = find_latest_quality_report(non_existent_report_dir_ws, "docstrings")
    if latest_non_existent is None:
        logger.info(f"Succès: Aucun rapport trouvé si quality_proposals/ n'existe pas (attendu).")
    else:
        logger.error(f"Échec: Test dossier quality_proposals non existant. Attendu: None, Obtenu: {latest_non_existent.name}")


    # Nettoyage optionnel des dossiers de mock
    # import shutil
    # if mock_ws_path.exists():
    #     shutil.rmtree(mock_ws_path)
    #     logger.info(f"Dossier de mock '{mock_ws_path}' supprimé.")
    # if non_existent_report_dir_ws.exists(): # S'il avait été créé par erreur
    #    shutil.rmtree(non_existent_report_dir_ws)


    logger.info(f"--- Fin des tests pour {Path(__file__).name} ---")