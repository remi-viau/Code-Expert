# code/embedding/core/embedder_client.py
import sys
from pathlib import Path
import litellm # Maintenant on utilisera aussi potentiellement aembedding
import logging
from typing import List, Optional, Dict, Any
import asyncio # Pour la version async

logger = logging.getLogger(__name__)

try:
    EMBEDDER_CORE_DIR = Path(__file__).resolve().parent 
    PROJECT_ROOT = EMBEDDER_CORE_DIR.parents[2]      
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    
    from lib import utils as shared_utils 
    from embedding.core.config_loader import get_embedding_config
except ImportError as e:
    print(f"ERREUR CRITIQUE [EmbedderClient Init]: {e}", file=sys.stderr)
    sys.exit(1)

_embedding_call_kwargs_cache: Optional[Dict[str, Any]] = None

def _get_call_kwargs() -> Dict[str, Any]:
    global _embedding_call_kwargs_cache
    if _embedding_call_kwargs_cache is not None:
        return _embedding_call_kwargs_cache
    
    config = get_embedding_config()
    try:
        _embedding_call_kwargs_cache = shared_utils.prepare_embedding_call_kwargs(config) # Définit model, api_key, api_base, timeout
        return _embedding_call_kwargs_cache
    except Exception as e: # Capturer plus largement
        logger.error(f"Erreur de configuration pour l'appel d'embedding: {e}", exc_info=True)
        raise 

def generate_embedding_for_text_sync(text_to_embed: str) -> Optional[List[float]]:
    """Génère un embedding pour UN SEUL texte (version synchrone).
    Utilisé par ex. pour la requête utilisateur dans faiss_selector."""
    if not text_to_embed or not text_to_embed.strip():
        logger.warning("Tentative d'embedder un texte vide.")
        return None
    try:
        call_kwargs = _get_call_kwargs()
        current_call_kwargs = {**call_kwargs, "input": [text_to_embed]}
        logger.debug(f"Appel sync à litellm.embedding avec model: {current_call_kwargs.get('model')}, input (début): '{text_to_embed[:70]}...'")
        response = litellm.embedding(**current_call_kwargs)
        if response.data and response.data[0] and "embedding" in response.data[0] and response.data[0]["embedding"]:
            return response.data[0]['embedding']
        else:
            logger.error(f"Réponse API embedding sync inattendue. Réponse: {str(response)[:500]}")
            return None
    except Exception as e:
        logger.error(f"Erreur embedding sync pour texte: '{text_to_embed[:70]}...': {type(e).__name__} - {e}", exc_info=False)
        return None

async def generate_embedding_for_text_async(text_to_embed: str) -> Optional[List[float]]:
    """Génère un embedding pour UN SEUL texte (version asynchrone).
    Utilisé dans la boucle de fragment_processor."""
    if not text_to_embed or not text_to_embed.strip():
        logger.warning("Tentative d'embedder un texte vide (async).")
        return None
    try:
        call_kwargs = _get_call_kwargs() # Récupère model, api_key, api_base, timeout
        current_call_kwargs = {**call_kwargs, "input": [text_to_embed]}
        logger.debug(f"Appel async à litellm.aembedding avec model: {current_call_kwargs.get('model')}, input (début): '{text_to_embed[:70]}...'")
        
        # Utiliser litellm.aembedding pour un appel asynchrone
        response = await litellm.aembedding(**current_call_kwargs)
        
        if response.data and response.data[0] and "embedding" in response.data[0] and response.data[0]["embedding"]:
            return response.data[0]['embedding']
        else:
            logger.error(f"Réponse API aembedding inattendue. Réponse: {str(response)[:500]}")
            return None
    except Exception as e:
        logger.error(f"Erreur aembedding pour texte: '{text_to_embed[:70]}...': {type(e).__name__} - {e}", exc_info=False)
        return None