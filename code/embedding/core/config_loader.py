# code/embedding/core/config_loader.py
import json # Garder pour la structure par défaut initiale, pourrait être retiré
from pathlib import Path
import logging
from typing import Dict, Any, Optional
import yaml # <<< AJOUTER CET IMPORT

logger = logging.getLogger(__name__)

# Chemin vers le fichier de configuration, relatif à ce fichier
CONFIG_FILE_PATH = Path(__file__).resolve().parent.parent / "config.yaml" # <<< CHANGER L'EXTENSION

_config_cache: Optional[Dict[str, Any]] = None

def get_embedding_config() -> Dict[str, Any]:
    """Charge et retourne la configuration du service d'embedding, avec mise en cache."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    # La structure par défaut est toujours utile
    default_config = {
        "api_provider": "ollama",
        "model_name": "ollama/nomic-embed-text",
        "api_key_env_var": None,
        "api_base_env_var": "OLLAMA_API_BASE",
        "api_base": None,
        "max_text_length_for_embedding": 512, # Peut être ajusté
        "embedding_batch_size": 10, # Pour la concurrence asyncio
        "max_retries": 2,
        "retry_delay": 3,
        "timeout": 60
    }

    if not CONFIG_FILE_PATH.is_file():
        logger.warning(
            f"Fichier de configuration YAML du service d'embedding introuvable à '{CONFIG_FILE_PATH}'. "
            f"Utilisation de la configuration par défaut."
        )
        _config_cache = default_config
        return default_config
    try:
        with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
            config_from_file = yaml.safe_load(f) # <<< UTILISER yaml.safe_load
        
        # Fusionner, en donnant la priorité aux valeurs du fichier de configuration
        if isinstance(config_from_file, dict): # S'assurer que le YAML a été parsé en dictionnaire
            _config_cache = {**default_config, **config_from_file}
        else:
            logger.error(
                f"Le contenu du fichier YAML '{CONFIG_FILE_PATH}' n'est pas un dictionnaire valide. "
                "Utilisation de la configuration par défaut."
            )
            _config_cache = default_config

        logger.info(f"Configuration du service d'embedding chargée depuis '{CONFIG_FILE_PATH}'.")
        logger.debug(f"Configuration d'embedding effective: {_config_cache}")
        return _config_cache
    except yaml.YAMLError as e_yaml: # <<< CHANGER L'EXCEPTION POUR YAML
        logger.error(
            f"Erreur lors du parsing YAML de la configuration d'embedding depuis '{CONFIG_FILE_PATH}': {e_yaml}. "
            f"Utilisation de la configuration par défaut.", exc_info=True
        )
        _config_cache = default_config
        return default_config
    except Exception as e: # Autres erreurs (ex: permissions)
        logger.error(
            f"Erreur inattendue lors du chargement de la configuration d'embedding depuis '{CONFIG_FILE_PATH}': {e}. "
            f"Utilisation de la configuration par défaut.", exc_info=True
        )
        _config_cache = default_config
        return default_config