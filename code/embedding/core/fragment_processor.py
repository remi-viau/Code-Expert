# code/embedding/core/fragment_processor.py
import json
from pathlib import Path
import sys
import logging
import asyncio # Pour la génération asynchrone des embeddings
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Gestion des Imports et Dépendances ---
try:
    PROCESSOR_DIR = Path(__file__).resolve().parent # .../code/embedding/core/
    PROJECT_ROOT = PROCESSOR_DIR.parents[2]      # core -> embedding -> code
    
    # Assurer que PROJECT_ROOT (le dossier 'code') est dans sys.path
    # pour les imports de modules frères ou parents.
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
        # print(f"INFO [FragmentProcessor Init]: Ajout de {PROJECT_ROOT} à sys.path")
    
    from embedding.core import embedder_client # Client pour appeler l'API d'embedding (maintenant avec fonction async)
    from embedding.core.config_loader import get_embedding_config # Pour les paramètres spécifiques à l'embedding
    # global_config est utilisé pour WORKSPACE_PATH, qui est un point central.
    # Si cette dépendance est trop forte, WORKSPACE_PATH pourrait être passé en argument.
    import global_config 
    WORKSPACE_PATH = global_config.WORKSPACE_PATH

except ImportError as e:
    print(f"ERREUR CRITIQUE [FragmentProcessor Init]: Impossible d'importer un module requis. {e}", file=sys.stderr)
    print(f"  Vérifiez la structure de votre projet et les installations.", file=sys.stderr)
    print(f"  PYTHONPATH actuel: {sys.path}", file=sys.stderr)
    sys.exit(1) # Arrêt critique si les dépendances de base manquent
except Exception as e: # Autres erreurs d'initialisation
    print(f"Erreur inattendue à l'initialisation [FragmentProcessor]: {e}", file=sys.stderr)
    sys.exit(1)
# --- Fin Gestion des Imports ---

# Chemins des fichiers de manifeste et d'embeddings
MANIFEST_FILE_PATH = WORKSPACE_PATH / "fragments_manifest.json"
# Fichier où les embeddings (avec leur code_digest associé) seront stockés
EMBEDDINGS_WITH_DIGEST_FILE_PATH = WORKSPACE_PATH / "fragment_embeddings.json" 

# Charger la configuration d'embedding une fois
try:
    _embedding_service_config = get_embedding_config()
    MAX_TEXT_LENGTH_FOR_EMBEDDING = _embedding_service_config.get('max_text_length_for_embedding', 512)
    # Nombre de tâches d'embedding à exécuter en parallèle avec asyncio.gather
    CONCURRENT_EMBEDDING_TASKS = _embedding_service_config.get('embedding_batch_size', 10) # Réutilisation de embedding_batch_size pour la concurrence
except Exception as e_conf:
    logger.error(f"Erreur lors de la récupération de la configuration d'embedding pour FragmentProcessor: {e_conf}. Utilisation de valeurs par défaut.")
    MAX_TEXT_LENGTH_FOR_EMBEDDING = 512
    CONCURRENT_EMBEDDING_TASKS = 10


def _get_text_for_fragment_embedding(fragment_info: Dict[str, Any], fragment_id_for_log: str) -> str:
    """
    Construit la chaîne de texte représentative pour un fragment donné,
    destinée à être utilisée pour générer son embedding.
    Combine les métadonnées clés et le docstring (tronqué si nécessaire).
    """
    parts: List[str] = []
    
    identifier = fragment_info.get("identifier")
    frag_type = fragment_info.get("fragment_type")
    package_name = fragment_info.get("package_name")

    # Utiliser MAX_TEXT_LENGTH_FOR_EMBEDDING pour les troncatures
    max_len_for_field = MAX_TEXT_LENGTH_FOR_EMBEDDING # Longueur max pour le docstring
    # Pour signature/definition, on peut être plus court pour laisser place au docstring
    max_len_meta = MAX_TEXT_LENGTH_FOR_EMBEDDING // 3 

    if frag_type and identifier:
        parts.append(f"{frag_type.capitalize()} Name: {identifier}")
    elif identifier:
        parts.append(f"Identifier: {identifier}")
    else:
        parts.append(f"Fragment Type: {frag_type or 'UnknownType'}") # S'assurer qu'il y a toujours quelque chose

    if package_name:
        parts.append(f"Package: {package_name}")

    signature = fragment_info.get("signature")
    if signature:
        sig_text = signature[:max_len_meta] + "..." if len(signature) > max_len_meta else signature
        parts.append(f"Signature: {sig_text}")
    
    definition = fragment_info.get("definition")
    if definition:
        def_text = definition[:max_len_meta] + "..." if len(definition) > max_len_meta else definition
        parts.append(f"Definition: {def_text}")

    docstring_text = fragment_info.get("docstring")
    if docstring_text and docstring_text.strip():
        doc_to_embed = docstring_text.strip()
        if len(doc_to_embed) > max_len_for_field:
             doc_to_embed = doc_to_embed[:max_len_for_field] + "..."
             logger.debug(f"Docstring pour '{fragment_id_for_log}' tronqué à {max_len_for_field} caractères pour l'embedding.")
        parts.append(f"Documentation: {doc_to_embed}")
    else:
        logger.debug(f"Fragment '{fragment_id_for_log}' n'a pas de docstring. L'embedding sera basé sur les métadonnées structurelles.")

    # Joindre les parties avec un séparateur clair.
    final_text = ". ".join(filter(None, parts)) # filter(None, ...) enlève les chaînes vides

    if not final_text.strip(): # Si après toutes ces étapes, le texte est vide
        fallback_text = f"{frag_type or 'Fragment'}: {identifier or fragment_id_for_log}"
        logger.warning(f"Texte d'embedding vide construit pour '{fragment_id_for_log}'. Utilisation d'un fallback minimal: '{fallback_text}'")
        return fallback_text
    
    return final_text


async def _process_single_fragment_for_embedding(
    frag_id: str, 
    text_to_embed: str, 
    current_code_digest: Optional[str]
) -> Tuple[str, Optional[Dict[str, Any]]]: # Retourne (frag_id, entry_pour_json_ou_None)
    """
    Tâche Coroutine pour générer l'embedding pour un seul fragment.
    Appelle la version asynchrone du client d'embedding.
    """
    logger.debug(f"Coroutine: Début de l'embedding pour {frag_id}...")
    embedding_vector = await embedder_client.generate_embedding_for_text_async(text_to_embed)
    
    if embedding_vector:
        logger.debug(f"Coroutine: Embedding généré avec succès pour {frag_id}.")
        return frag_id, {"embedding": embedding_vector, "code_digest": current_code_digest}
    else:
        logger.error(f"Coroutine: Échec de la génération de l'embedding (async) pour le fragment {frag_id}.")
        return frag_id, None # Indiquer l'échec pour ce fragment


async def update_fragment_embeddings_async() -> bool:
    """
    Fonction principale (asynchrone) pour générer ou mettre à jour les embeddings
    pour les fragments du manifeste de manière incrémentale.
    Utilise asyncio.gather pour traiter les fragments en parallèle.
    Retourne True si l'opération globale est considérée comme un succès (même si certains embeddings ont échoué),
    False si une erreur critique empêche le traitement.
    """
    logger.info(f"--- Début de la Mise à Jour Asynchrone et Incrémentale des Embeddings de Fragments ---")
    logger.info(f"Utilisation de {CONCURRENT_EMBEDDING_TASKS} tâches concurrentes max pour l'embedding.")
    
    logger.info(f"Chargement du manifeste de fragments depuis: {MANIFEST_FILE_PATH}")
    if not MANIFEST_FILE_PATH.is_file():
        logger.error(f"Fichier manifeste introuvable à l'emplacement: {MANIFEST_FILE_PATH}. "
                     "Veuillez d'abord générer le manifeste (ex: avec 'python -m code.manifest.main').")
        return False # Échec critique

    try:
        with open(MANIFEST_FILE_PATH, 'r', encoding='utf-8') as f:
            current_manifest_data = json.load(f)
    except Exception as e:
        logger.error(f"Erreur lors du chargement ou du parsing du fichier manifeste {MANIFEST_FILE_PATH}: {e}", exc_info=True)
        return False

    current_fragments = current_manifest_data.get("fragments", {})
    if not current_fragments:
        logger.warning("Aucun fragment trouvé dans le manifeste actuel. Aucune embedding à générer.")
        # Si le fichier d'embeddings existe et que le manifeste est vide, on pourrait le vider.
        if EMBEDDINGS_WITH_DIGEST_FILE_PATH.exists():
            try: 
                EMBEDDINGS_WITH_DIGEST_FILE_PATH.unlink()
                logger.info(f"Fichier d'embeddings existant '{EMBEDDINGS_WITH_DIGEST_FILE_PATH}' supprimé car le manifeste actuel est vide.")
            except OSError as e_del: 
                logger.error(f"Erreur lors de la suppression de l'ancien fichier d'embeddings '{EMBEDDINGS_WITH_DIGEST_FILE_PATH}': {e_del}")
        return True # Considéré comme un succès car il n'y a rien à faire.

    # Charger les anciens embeddings (s'ils existent) pour la comparaison incrémentale
    old_embeddings_data: Dict[str, Dict[str, Any]] = {} # Format: {frag_id: {"embedding": [], "code_digest": ""}}
    if EMBEDDINGS_WITH_DIGEST_FILE_PATH.is_file():
        try:
            with open(EMBEDDINGS_WITH_DIGEST_FILE_PATH, 'r', encoding='utf-8') as f:
                old_embeddings_data = json.load(f)
            logger.info(f"{len(old_embeddings_data)} entrées d'embeddings existantes chargées depuis '{EMBEDDINGS_WITH_DIGEST_FILE_PATH}'.")
        except Exception as e:
            logger.warning(
                f"Erreur lors du chargement de l'ancien fichier d'embeddings '{EMBEDDINGS_WITH_DIGEST_FILE_PATH}': {e}. "
                "Une regénération complète sera tentée pour tous les fragments.", exc_info=True
            )
            old_embeddings_data = {} # Forcer la regénération si le fichier est corrompu
    else:
        logger.info(f"Aucun fichier d'embeddings existant trouvé à '{EMBEDDINGS_WITH_DIGEST_FILE_PATH}'. Une génération complète est nécessaire.")

    updated_embeddings_data: Dict[str, Dict[str, Any]] = {} # Stockera les embeddings finaux
    embedding_tasks: List[asyncio.Task] = [] # Liste des tâches asyncio à exécuter
    
    embeddings_reused_count = 0

    # Identifier les fragments à ré-embedder et ceux dont l'embedding peut être réutilisé
    for frag_id, current_frag_info in current_fragments.items():
        current_code_digest = current_frag_info.get("code_digest")
        old_entry = old_embeddings_data.get(frag_id)

        # Condition pour réutiliser : l'entrée existe, le digest correspond, et un embedding est présent
        if old_entry and \
           isinstance(old_entry, dict) and \
           old_entry.get("code_digest") == current_code_digest and \
           old_entry.get("embedding"):
            updated_embeddings_data[frag_id] = old_entry # Réutiliser l'entrée complète (embedding + digest)
            embeddings_reused_count += 1
        else: # Nouveau fragment, digest modifié, ou embedding/digest manquant dans l'ancienne entrée
            if not current_code_digest:
                logger.warning(f"Fragment '{frag_id}' n'a pas de 'code_digest' dans le manifeste actuel. Il sera (ré)embeddé.")
            
            text_to_embed = _get_text_for_fragment_embedding(current_frag_info, frag_id)
            if text_to_embed.strip():
                # Créer une tâche coroutine pour ce fragment
                # Le troisième argument est current_code_digest, qui sera stocké avec le nouvel embedding
                task = asyncio.create_task(_process_single_fragment_for_embedding(frag_id, text_to_embed, current_code_digest))
                embedding_tasks.append(task)
            else: 
                logger.warning(f"Texte vide généré pour l'embedding du fragment '{frag_id}' (qui nécessitait une mise à jour). Ce fragment sera ignoré.")
    
    logger.info(f"{embeddings_reused_count} embeddings ont été réutilisés car leur code_digest n'a pas changé.")
    logger.info(f"{len(embedding_tasks)} fragments nécessitent une (re)génération d'embedding et seront traités en parallèle.")

    if embedding_tasks:
        # Utiliser un sémaphore pour limiter la concurrence des tâches d'embedding
        # CONCURRENT_EMBEDDING_TASKS est lu depuis la config
        semaphore = asyncio.Semaphore(CONCURRENT_EMBEDDING_TASKS) 
        
        async def run_with_semaphore_wrapper(task_coro: asyncio.Task):
            async with semaphore:
                return await task_coro # Exécuter la tâche (qui est déjà une coroutine)
        
        # Exécuter toutes les tâches d'embedding en concurrence
        # return_exceptions=True permet de récupérer les exceptions au lieu de planter gather
        results_from_gather = await asyncio.gather(*(run_with_semaphore_wrapper(task) for task in embedding_tasks), return_exceptions=True)
        
        successful_new_embeddings_count = 0
        for i, result_or_exception in enumerate(results_from_gather):
            # Tenter de récupérer l'ID du fragment associé à cette tâche (pour un meilleur logging)
            # Note: L'accès direct aux arguments de la coroutine d'une tâche n'est pas simple.
            # On pourrait encapsuler la tâche dans un objet qui conserve l'ID, ou logguer l'index.
            task_identifier_for_log = f"tâche #{i+1}" # Log par index si l'ID n'est pas facilement récupérable

            if isinstance(result_or_exception, Exception):
                logger.error(f"Erreur lors de l'exécution de la {task_identifier_for_log} d'embedding: {result_or_exception}", exc_info=False) # exc_info=False pour éviter de dupliquer les traces si l'erreur vient de litellm
            elif isinstance(result_or_exception, tuple) and len(result_or_exception) == 2:
                frag_id_result, embedding_entry_result = result_or_exception
                if embedding_entry_result: # Si l'embedding a été généré avec succès
                    updated_embeddings_data[frag_id_result] = embedding_entry_result
                    successful_new_embeddings_count += 1
                # Si embedding_entry_result est None, _process_single_fragment_for_embedding a déjà loggué l'erreur
            else: # Résultat inattendu de la coroutine
                logger.error(f"Résultat inattendu reçu de la {task_identifier_for_log} d'embedding: {result_or_exception}")

        logger.info(f"{successful_new_embeddings_count} nouveaux embeddings ont été générés avec succès sur {len(embedding_tasks)} tâches lancées.")

    # --- Sauvegarde finale ---
    if not updated_embeddings_data and current_fragments: 
        logger.error("Aucun embedding n'a pu être généré ou réutilisé, bien qu'il y ait des fragments dans le manifeste. Le fichier d'embeddings ne sera pas mis à jour.")
        return False # Indiquer un échec partiel/total
    
    # S'il n'y a aucun fragment dans le manifeste, updated_embeddings_data sera vide, ce qui est correct.
    # On sauvegarde même si c'est vide pour refléter l'état du manifeste.
    logger.info(f"Sauvegarde de {len(updated_embeddings_data)} entrées d'embeddings (avec code_digest) vers: {EMBEDDINGS_WITH_DIGEST_FILE_PATH}")
    try:
        EMBEDDINGS_WITH_DIGEST_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(EMBEDDINGS_WITH_DIGEST_FILE_PATH, 'w', encoding='utf-8') as f:
            # indent=None pour un fichier plus petit en production, indent=2 pour lisibilité en dev/debug
            json.dump(updated_embeddings_data, f, indent=None) 
        logger.info("Fichier d'embeddings (avec code_digest) sauvegardé avec succès.")
    except Exception as e:
        logger.error(f"Erreur lors de la sauvegarde du fichier d'embeddings '{EMBEDDINGS_WITH_DIGEST_FILE_PATH}': {e}", exc_info=True)
        return False # Indiquer échec
    
    logger.info(f"--- Fin de la Mise à Jour Asynchrone et Incrémentale des Embeddings ---")
    return True # Succès global de l'opération (même si certains embeddings individuels ont pu échouer)

# Ce fichier est un module, il sera importé et sa fonction principale
# update_fragment_embeddings_async() sera appelée par embedding/main.py.
# Pas de bloc if __name__ == "__main__" ici.