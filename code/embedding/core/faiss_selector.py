# code/embedding/core/faiss_selector.py
import json
from pathlib import Path
import sys
import numpy as np # Dépendance: pip install numpy
import faiss       # Dépendance: pip install faiss-cpu (ou faiss-gpu)
import logging
import asyncio # Pour appeler la génération d'embeddings si manquants
from typing import List, Tuple, Dict, Optional, Any # Ajout de Any

logger = logging.getLogger(__name__)

# --- Gestion des Imports et Chemins ---
try:
    SELECTOR_CORE_DIR = Path(__file__).resolve().parent # .../code/embedding/core/
    PROJECT_ROOT = SELECTOR_CORE_DIR.parents[2]      # core -> embedding -> code
    
    # Assurer que PROJECT_ROOT (le dossier 'code') est dans sys.path
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
        # print(f"INFO [{__name__} Init]: Ajout de {PROJECT_ROOT} à sys.path")
    
    # Importer les composants nécessaires du module embedding et global_config
    from embedding.core import embedder_client # Pour générer l'embedding de la requête utilisateur
    # config_loader n'est plus nécessaire ici car embedder_client gère sa propre config
    import global_config # Pour WORKSPACE_PATH
    WORKSPACE_PATH = global_config.WORKSPACE_PATH

except ImportError as e:
    print(f"ERREUR CRITIQUE [FaissSelector Init]: Impossible d'importer un module requis. {e}", file=sys.stderr)
    print(f"  Vérifiez la structure de votre projet, les __init__.py et les dépendances.", file=sys.stderr)
    print(f"  PYTHONPATH actuel: {sys.path}", file=sys.stderr)
    sys.exit(1) # Arrêt critique
except Exception as e_init:
    print(f"Erreur inattendue à l'initialisation [FaissSelector]: {e_init}", file=sys.stderr)
    sys.exit(1)
# --- Fin Gestion des Imports ---

# Chemin vers le fichier contenant les embeddings des fragments (avec code_digest)
# Ce nom doit correspondre à celui utilisé par embedding.core.fragment_processor
EMBEDDINGS_FILE_WITH_DIGEST_PATH = WORKSPACE_PATH / "fragment_embeddings.json"

# --- Cache en mémoire pour l'index FAISS et les mappings ID <=> Index ---
# Ces variables sont globales au module et mises en cache pour la durée de vie de l'application.
_faiss_index_cache: Optional[faiss.Index] = None
_id_to_internal_index_map_cache: Optional[Dict[str, int]] = None # fragment_id -> index dans l'array numpy utilisé par FAISS
_internal_index_to_id_map_cache: Optional[Dict[int, str]] = None # index FAISS -> fragment_id

def _load_embeddings_and_build_index(
    force_reload: bool = False, 
    attempt_generation_if_missing: bool = True
) -> bool:
    """
    Charge les embeddings de fragments depuis le fichier JSON, construit un index FAISS,
    et met en cache l'index ainsi que les mappings ID <=> index interne FAISS.
    Si `attempt_generation_if_missing` est True et que le fichier d'embeddings est manquant,
    tente d'appeler le script de génération d'embeddings (de manière asynchrone).
    Retourne True si l'index est prêt (chargé ou construit), False sinon.
    """
    global _faiss_index_cache, _id_to_internal_index_map_cache, _internal_index_to_id_map_cache

    # Utiliser le cache si disponible et que le rechargement n'est pas forcé
    if _faiss_index_cache is not None and \
       _id_to_internal_index_map_cache is not None and \
       _internal_index_to_id_map_cache is not None and \
       not force_reload:
        logger.debug("Index FAISS et mappings déjà présents en cache mémoire. Utilisation de la version en cache.")
        return True

    # Vérifier si le fichier d'embeddings existe
    if not EMBEDDINGS_FILE_WITH_DIGEST_PATH.is_file():
        logger.warning(f"Fichier d'embeddings avec digest '{EMBEDDINGS_FILE_WITH_DIGEST_PATH}' non trouvé.")
        if attempt_generation_if_missing:
            # Tenter d'importer et d'appeler la fonction de génération d'embeddings du module embedding
            try:
                # Import local pour éviter une dépendance circulaire stricte au moment du chargement initial du module
                from embedding.core import fragment_processor 
                logger.info(f"Tentative de génération des embeddings (via fonction asynchrone) car le fichier est manquant...")
                
                # Le fragment_processor a besoin du fragments_manifest.json
                manifest_path_for_generator = WORKSPACE_PATH / "fragments_manifest.json"
                if not manifest_path_for_generator.is_file():
                    logger.error(f"Le fichier manifeste de fragments '{manifest_path_for_generator}' est requis pour "
                                 "générer les embeddings, mais il est introuvable. Impossible de continuer.")
                    return False # Ne peut pas générer sans le manifeste de base

                # Exécuter la fonction asynchrone de fragment_processor
                # Gérer la politique d'event loop pour Windows si Python >= 3.8
                if sys.platform == "win32" and sys.version_info >= (3,8):
                    # logger.debug("Application de WindowsSelectorEventLoopPolicy pour l'appel async de génération d'embeddings.")
                    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
                
                generation_successful = asyncio.run(fragment_processor.update_fragment_embeddings_async())
                
                if not generation_successful:
                    logger.error("La génération automatique des embeddings (asynchrone) a échoué. Voir logs précédents.")
                    return False
                if not EMBEDDINGS_FILE_WITH_DIGEST_PATH.is_file(): # Revérifier après tentative de génération
                    logger.error(f"La génération des embeddings a été tentée, mais le fichier "
                                 f"'{EMBEDDINGS_FILE_WITH_DIGEST_PATH}' est toujours introuvable.")
                    return False
                logger.info(f"Embeddings (avec digest) générés avec succès. Tentative de chargement pour l'index FAISS...")
            except ImportError:
                logger.error("Impossible d'importer 'embedding.core.fragment_processor' pour la génération automatique des embeddings. "
                             "Assurez-vous que le module existe et est accessible.")
                return False
            except Exception as e_gen:
                logger.error(f"Erreur inattendue lors de la tentative de génération des embeddings (avec digest): {e_gen}", exc_info=True)
                return False
        else: # Le fichier n'existe pas et on ne tente pas de le générer
            logger.error(f"Fichier d'embeddings '{EMBEDDINGS_FILE_WITH_DIGEST_PATH}' non trouvé et la génération automatique n'est pas activée. "
                         "L'index FAISS ne peut pas être construit.")
            return False 
    
    # Si on arrive ici, le fichier d'embeddings (devrait) exister.
    try:
        logger.info(f"Chargement des embeddings depuis '{EMBEDDINGS_FILE_WITH_DIGEST_PATH}' pour la construction de l'index FAISS...")
        with open(EMBEDDINGS_FILE_WITH_DIGEST_PATH, 'r', encoding='utf-8') as f:
            embeddings_with_digest_data = json.load(f) # Format: {frag_id: {"embedding": [...], "code_digest": "..."}}
        
        if not embeddings_with_digest_data:
            logger.error("Aucune donnée trouvée dans le fichier d'embeddings avec digest. L'index FAISS sera vide.")
            # Construire un index vide pour éviter des erreurs None plus tard, mais il sera inutile.
            _faiss_index_cache = faiss.IndexFlatL2(1) # Dimension factice, ne sera pas utilisé si vide
            _id_to_internal_index_map_cache = {}
            _internal_index_to_id_map_cache = {}
            return True # Techniquement, "chargé" mais vide.

        valid_fragment_ids_for_index: List[str] = []
        valid_embedding_vectors_list: List[np.ndarray] = []

        for frag_id, entry_data in embeddings_with_digest_data.items():
            if isinstance(entry_data, dict) and "embedding" in entry_data:
                embedding_vector = entry_data["embedding"]
                if isinstance(embedding_vector, list) and all(isinstance(x, (int, float)) for x in embedding_vector):
                    valid_embedding_vectors_list.append(np.array(embedding_vector, dtype='float32'))
                    valid_fragment_ids_for_index.append(frag_id)
                else:
                    logger.warning(f"Vecteur d'embedding invalide (type ou contenu) pour le fragment '{frag_id}'. Ce fragment sera ignoré de l'index FAISS.")
            else:
                logger.warning(f"Entrée d'embedding malformée pour le fragment '{frag_id}' (manque la clé 'embedding' ou l'entrée n'est pas un dictionnaire). Ce fragment sera ignoré.")
        
        if not valid_embedding_vectors_list:
            logger.error("La liste des embeddings valides est vide après le filtrage des données du fichier. L'index FAISS sera vide.")
            _faiss_index_cache = faiss.IndexFlatL2(1) # Index vide mais initialisé
            _id_to_internal_index_map_cache = {}
            _internal_index_to_id_map_cache = {}
            return True

        # Convertir la liste d'arrays 1D en un array NumPy 2D
        embeddings_np_array = np.vstack(valid_embedding_vectors_list)
        
        if embeddings_np_array.shape[0] == 0: # Double vérification après vstack
            logger.error("Aucun vecteur d'embedding valide à indexer après l'opération vstack. L'index FAISS sera vide.")
            _faiss_index_cache = faiss.IndexFlatL2(1)
            _id_to_internal_index_map_cache = {}
            _internal_index_to_id_map_cache = {}
            return True

        embedding_dimension = embeddings_np_array.shape[1]
        num_vectors_to_index = embeddings_np_array.shape[0]
        logger.info(f"Construction de l'index FAISS avec {num_vectors_to_index} vecteur(s) valide(s) de dimension {embedding_dimension}.")
        
        index = faiss.IndexFlatL2(embedding_dimension) 
        index.add(embeddings_np_array)
        
        # Mettre en cache l'index et les mappings
        _faiss_index_cache = index
        _id_to_internal_index_map_cache = {frag_id: i for i, frag_id in enumerate(valid_fragment_ids_for_index)}
        _internal_index_to_id_map_cache = {i: frag_id for i, frag_id in enumerate(valid_fragment_ids_for_index)}
        
        logger.info("Index FAISS construit et embeddings (avec digest) chargés avec succès en mémoire.")
        return True
    except FileNotFoundError: # Spécifique au cas où le fichier disparaîtrait entre le check et l'ouverture
        logger.error(f"Le fichier d'embeddings '{EMBEDDINGS_FILE_WITH_DIGEST_PATH}' a disparu pendant le chargement.")
        return False
    except Exception as e:
        logger.error(f"Erreur inattendue lors du chargement des embeddings ou de la construction de l'index FAISS: {e}", exc_info=True)
        _faiss_index_cache = None # Réinitialiser le cache en cas d'erreur
        _id_to_internal_index_map_cache = None
        _internal_index_to_id_map_cache = None
        return False


def find_relevant_fragments( # Renommé de find_relevant_fragments_by_embedding pour usage externe plus simple
    user_request: str,
    top_k: int = 10, # Nombre de fragments les plus pertinents à retourner
    similarity_threshold: Optional[float] = None, # Seuil de distance L2 maximale (plus petit = plus similaire)
    force_index_reload: bool = False # Pour forcer le rechargement de l'index FAISS (utile pour tests)
) -> Tuple[List[str], List[float], Optional[str]]: # (IDs, Scores L2^2, Message d'erreur)
    """
    Trouve les fragments les plus pertinents pour une requête utilisateur en utilisant des embeddings et FAISS.
    Gère le chargement/construction de l'index FAISS et l'embedding de la requête.
    Retourne une liste d'IDs de fragments, leurs scores de distance L2 au carré, et un message d'erreur optionnel.
    """
    global _faiss_index_cache, _internal_index_to_id_map_cache # Utiliser les variables globales du cache

    # S'assurer que l'index est chargé (et potentiellement généré si manquant)
    if _faiss_index_cache is None or force_index_reload:
        if not _load_embeddings_and_build_index(force_reload=force_index_reload, attempt_generation_if_missing=True):
            # _load_embeddings_and_build_index logue déjà l'erreur critique
            return [], [], "Erreur critique lors du chargement ou de la construction de l'index d'embeddings."

    # Vérifications post-chargement (au cas où le chargement aurait échoué silencieusement)
    if _faiss_index_cache is None or _internal_index_to_id_map_cache is None:
        logger.critical("L'index FAISS ou le mapping d'ID n'est pas initialisé correctement après la tentative de chargement.")
        return [], [], "L'index FAISS n'est pas initialisé."

    # Générer l'embedding pour la requête utilisateur
    try:
        # Utiliser le module embedder_client pour obtenir l'embedding de la requête
        # Il utilise sa propre config (via config_loader) pour le modèle et les paramètres API.
        query_embedding_vector_list = embedder_client.generate_embedding_for_text_sync(user_request)
        if query_embedding_vector_list is None: # Si l'embedding de la requête échoue
            raise ValueError("L'embedding de la requête utilisateur a échoué (retourné None).")
        query_embedding_np_array = np.array(query_embedding_vector_list, dtype='float32').reshape(1, -1) # FAISS attend un array 2D
    except Exception as e_query_embed:
        logger.error(f"Erreur lors de la génération de l'embedding pour la requête utilisateur: {e_query_embed}", exc_info=True)
        return [], [], f"Erreur lors de l'embedding de la requête: {e_query_embed}"

    # Effectuer la recherche dans l'index FAISS
    if _faiss_index_cache.ntotal == 0: # Vérifier si l'index est vide
        logger.warning("L'index FAISS est vide. Aucune recherche de similarité ne peut être effectuée.")
        return [], [], "Index FAISS vide, impossible de rechercher."
        
    # S'assurer que k (top_k) n'est pas plus grand que le nombre d'éléments dans l'index
    actual_k_for_search = min(top_k, _faiss_index_cache.ntotal)
    if actual_k_for_search == 0 : # Si l'index est vide, ntotal sera 0.
        logger.warning("Le nombre de voisins à rechercher (k) est 0 car l'index est vide ou top_k est 0. Aucune recherche effectuée.")
        return [], [], "Aucun voisin à rechercher (k=0)."

    logger.info(f"Recherche des {actual_k_for_search} plus proches voisins dans l'index FAISS (contenant {_faiss_index_cache.ntotal} vecteurs)...")
    try:
        # search retourne (distances L2 au carré, indices des voisins)
        distances_l2_sq_results, indices_results = _faiss_index_cache.search(query_embedding_np_array, actual_k_for_search)
    except Exception as e_faiss_search:
        logger.error(f"Erreur inattendue lors de la recherche FAISS: {e_faiss_search}", exc_info=True)
        return [], [], f"Erreur de recherche FAISS: {e_faiss_search}"
    
    relevant_ids_found: List[str] = []
    similarity_scores_l2_distances: List[float] = [] 

    # Traiter les résultats de la recherche FAISS
    # indices_results[0] contient les indices des k plus proches voisins pour la première (et unique) requête.
    # distances_l2_sq_results[0] contient les distances L2^2 correspondantes.
    if indices_results.size > 0 and indices_results[0][0] != -1 : # Vérifier si au moins un voisin a été trouvé
        for i in range(indices_results.shape[1]): # Parcourir les 'actual_k_for_search' résultats
            db_internal_index = indices_results[0, i]
            # FAISS retourne -1 pour un index si moins de 'k' voisins sont trouvés et que k > ntotal
            # (ce qui ne devrait pas arriver avec actual_k = min(top_k, ntotal)).
            # Mais une vérification supplémentaire est une bonne pratique.
            if db_internal_index == -1 : 
                break # Plus de voisins valides
            
            distance_value = distances_l2_sq_results[0, i] # C'est la distance L2 au carré
            
            fragment_id = _internal_index_to_id_map_cache.get(db_internal_index)
            if fragment_id:
                # Appliquer le seuil de similarité (distance maximale acceptée)
                if similarity_threshold is not None and distance_value > similarity_threshold: 
                    logger.debug(f"Fragment {fragment_id} (Distance L2^2: {distance_value:.4f}) écarté car supérieur au seuil ({similarity_threshold}).")
                    continue # Passer au voisin suivant
                
                relevant_ids_found.append(fragment_id)
                similarity_scores_l2_distances.append(float(distance_value)) # Stocker la distance L2^2
                logger.debug(f"  Fragment pertinent trouvé: {fragment_id} (Index FAISS interne: {db_internal_index}, Distance L2^2: {distance_value:.4f})")
            else:
                # Cela ne devrait pas arriver si _internal_index_to_id_map_cache est correctement construit et synchronisé
                logger.warning(f"Aucun fragment_id trouvé pour l'index FAISS interne {db_internal_index}. Problème de mapping interne.")
    
    if not relevant_ids_found:
        logger.info("Aucun fragment pertinent trouvé par la recherche d'embedding (ou tous ont été écartés par le seuil de similarité).")

    return relevant_ids_found, similarity_scores_l2_distances, None # (IDs, Scores, Pas d'erreur)


# --- Bloc de test pour exécution directe de ce module (optionnel) ---
if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG, 
        format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', 
        stream=sys.stderr
    )
    logger.info(f"Module {Path(__file__).name} exécuté directement (mode test).")
    
    # S'assurer que la racine du projet 'code' est dans sys.path pour les imports et global_config
    _test_project_root = Path(__file__).resolve().parents[2] 
    if str(_test_project_root) not in sys.path:
        sys.path.insert(0, str(_test_project_root))
        print(f"INFO [Test FaissSelector]: Ajout de '{_test_project_root}' à sys.path pour les tests.")
    
    # Tenter de charger .env pour les variables d'environnement (ex: OLLAMA_API_BASE)
    try:
        from dotenv import load_dotenv
        # Supposer que .env est à la racine du projet global (un niveau au-dessus de 'code')
        dotenv_file_path = _test_project_root.parent / ".env" 
        if dotenv_file_path.is_file():
            if load_dotenv(dotenv_path=dotenv_file_path, override=False):
                logger.info(f"Variables .env (potentielles) chargées depuis {dotenv_file_path} pour le test de faiss_selector.")
            else:
                logger.debug(f"Aucune nouvelle variable d'environnement n'a été chargée depuis {dotenv_file_path}.")
        else:
            logger.debug(f"Fichier .env non trouvé à {dotenv_file_path}.")
    except ImportError:
        logger.info("Le package 'python-dotenv' n'est pas installé. Le chargement de variables .env est ignoré.")
    except Exception as e_dotenv:
        logger.warning(f"Erreur lors de la tentative de chargement du fichier .env : {e_dotenv}")

    # Vérifier l'existence du fichier d'embeddings avant de lancer le test
    # Utiliser le chemin défini au niveau du module
    if not EMBEDDINGS_FILE_WITH_DIGEST_PATH.is_file():
        logger.error(f"ERREUR DE TEST: Le fichier d'embeddings requis '{EMBEDDINGS_FILE_WITH_DIGEST_PATH}' est manquant.")
        logger.error("             Veuillez d'abord exécuter 'python -m code.embedding.main generate'.")
    else:
        test_user_request = "Changer les icones de type corbeille en une icone représentant un avion en papier"
        logger.info(f"Test de recherche sémantique pour la requête: \"{test_user_request}\"")
        
        # Récupérer le nom du modèle utilisé pour la requête depuis le client embedder (qui lit la config)
        try:
            test_model_id_for_query = embedder_client._get_call_kwargs().get("model", "Modèle inconnu (config non chargée)")
            logger.info(f"Utilisation du modèle d'embedding pour la requête: {test_model_id_for_query}")
        except Exception as e_get_model:
            logger.warning(f"Impossible de déterminer le modèle d'embedding pour la requête via embedder_client: {e_get_model}")

        # Forcer le rechargement de l'index pour ce test
        retrieved_ids, retrieved_scores, error_message = find_relevant_fragments(
            user_request=test_user_request, 
            top_k=7, # Demander un peu plus de résultats pour voir
            force_index_reload=True 
        )
        
        if error_message:
            logger.error(f"Erreur lors du test de la recherche sémantique: {error_message}")
        else:
            if retrieved_ids:
                logger.info(f"Top {len(retrieved_ids)} fragments pertinents trouvés par le test:")
                for frag_id, score in zip(retrieved_ids, retrieved_scores):
                    # Rappel: pour IndexFlatL2, score est la distance L2^2, plus petit = plus similaire
                    logger.info(f"  - ID: {frag_id}, Score (Distance L2^2): {score:.4f}")
            else:
                logger.info("Aucun fragment pertinent trouvé pour la requête de test.")