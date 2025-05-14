# code/embedding/main.py
import sys
from pathlib import Path
import logging
import asyncio # Pour exécuter la fonction principale asynchrone
import argparse

# --- Gestion des Imports et Chemins ---
# Assurer que la racine du projet 'code' est dans sys.path
# pour que les imports relatifs dans les sous-modules (core) fonctionnent
# et pour importer lib.utils et global_config.
try:
    # embedding/main.py -> embedding -> code
    PROJECT_ROOT = Path(__file__).resolve().parents[1] 
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
        # print(f"INFO [Embedding Main Init]: Ajout de '{PROJECT_ROOT}' à sys.path.")
    
    from embedding import cli as embedding_cli # Arguments CLI spécifiques à ce module
    from embedding.core import fragment_processor # Contient update_fragment_embeddings_async
    from lib import utils as shared_utils # Pour setup_logging
    
    # Charger les variables d'environnement depuis .env (ex: OLLAMA_API_BASE, clés API)
    # Cela doit être fait tôt pour que LiteLLM puisse les utiliser.
    from dotenv import load_dotenv
    # Supposer que .env est à la racine du projet global (un niveau au-dessus de 'code')
    dotenv_file_path = PROJECT_ROOT.parent / ".env" 
    if dotenv_file_path.is_file():
        if load_dotenv(dotenv_path=dotenv_file_path, override=False): # Ne pas écraser les vars d'env existantes
            # Le logger n'est pas encore configuré, utiliser print pour ce message initial si besoin.
            print(f"INFO [Embedding Main Init]: Variables .env (potentielles) chargées depuis {dotenv_file_path}.")
        # else:
            # print(f"DEBUG [Embedding Main Init]: Aucune nouvelle variable chargée depuis {dotenv_file_path} (elles existent peut-être déjà).")
    # else:
        # print(f"DEBUG [Embedding Main Init]: Fichier .env non trouvé à {dotenv_file_path}.")

except ImportError as e_imp:
    print(f"Erreur d'import critique dans embedding.main: {e_imp}", file=sys.stderr)
    print(f"  Vérifiez la structure de votre projet et les dépendances installées.", file=sys.stderr)
    print(f"  PYTHONPATH actuel: {sys.path}", file=sys.stderr)
    sys.exit(1)
except Exception as e_init: # Autres erreurs à l'initialisation
    print(f"Erreur inattendue à l'initialisation de embedding.main: {e_init}", file=sys.stderr)
    sys.exit(1)
# --- Fin Gestion des Imports ---

# Logger pour ce module principal d'embedding
logger = logging.getLogger(__name__) # Sera configuré par setup_logging

async def run_embedding_operations_async(args: argparse.Namespace) -> bool:
    """
    Fonction wrapper asynchrone qui exécute les opérations d'embedding
    basées sur les arguments CLI.
    """
    if args.action == "generate":
        if args.force_rebuild:
            logger.info("Forçage de la regénération complète de tous les embeddings...")
            # La logique de suppression de l'ancien fichier pour forcer la regénération
            # est gérée par update_fragment_embeddings_async si elle détecte
            # qu'aucun ancien embedding n'est réutilisable à cause du flag ou si le fichier est corrompu.
            # Pour un vrai "force rebuild", on pourrait supprimer le fichier ici.
            embeddings_file_to_check = fragment_processor.EMBEDDINGS_WITH_DIGEST_FILE_PATH
            if embeddings_file_to_check.exists():
                try:
                    embeddings_file_to_check.unlink()
                    logger.info(f"Ancien fichier d'embeddings '{embeddings_file_to_check}' supprimé pour forcer la regénération complète.")
                except OSError as e_del:
                    logger.error(f"Impossible de supprimer l'ancien fichier d'embeddings '{embeddings_file_to_check}': {e_del}. La regénération pourrait ne pas être complète ou échouer si le fichier est verrouillé.")
                    # On pourrait décider d'arrêter ici si la suppression est critique pour le "force_rebuild".
        
        # Appeler la fonction principale (asynchrone) du fragment_processor
        success = await fragment_processor.update_fragment_embeddings_async()
        
        if success:
            logger.info("L'opération d'embedding 'generate' (asynchrone) s'est terminée avec succès.")
        else:
            logger.error("L'opération d'embedding 'generate' (asynchrone) a rencontré des erreurs ou n'a pas pu compléter toutes les tâches.")
        return success # Retourner le statut de succès de l'opération
    else:
        logger.error(f"Action non reconnue ou non implémentée: {args.action}")
        return False # Indiquer échec pour action non reconnue

def main():
    """
    Point d'entrée principal pour le module d'embedding.
    Parse les arguments CLI, configure le logging, et lance les opérations d'embedding.
    """
    args = embedding_cli.parse_arguments() # Obtenir les arguments CLI
    
    # Configurer le logging pour ce script, en utilisant le flag --debug de la CLI
    # Pas de fichier de log par défaut pour ce script utilitaire, sauf si spécifié.
    shared_utils.setup_logging(debug_mode=args.debug) 

    logger.info(f"--- Module Embedding ---")
    logger.info(f"Action demandée: {args.action}")
    logger.info(f"Forcer la regénération: {args.force_rebuild}")
    logger.info(f"Mode Debug: {args.debug}")

    # Gérer la politique d'event loop pour Windows si Python >= 3.8
    # Nécessaire car litellm.aembedding (et potentiellement d'autres appels async)
    # peuvent en avoir besoin sur Windows.
    if sys.platform == "win32" and sys.version_info >= (3, 8):
        # logger.debug("Application de la politique WindowsSelectorEventLoopPolicy pour asyncio sur Windows.")
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Exécuter la logique principale asynchrone
    try:
        operation_successful = asyncio.run(run_embedding_operations_async(args))
    except Exception as e_async_run:
        logger.critical(f"Erreur critique lors de l'exécution des opérations d'embedding asynchrones: {e_async_run}", exc_info=True)
        operation_successful = False

    if not operation_successful:
        logger.error("Le script d'embedding s'est terminé avec des erreurs.")
        sys.exit(1) # Quitter avec un code d'erreur si l'opération principale a échoué
    else:
        logger.info("Le script d'embedding s'est terminé avec succès.")
        sys.exit(0) # Succès

if __name__ == "__main__":
    # Le logging est configuré dans main() après le parsing des arguments CLI (pour le flag --debug).
    # Les imports et la configuration de sys.path sont en haut du fichier.
    main()