# code/manifest/manifest_io.py
import json
from pathlib import Path
import sys
import logging

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler()) # Evite msg si non configuré

def load_manifest(manifest_path: Path) -> dict | None:
    logger.info(f"Lecture manifeste: {manifest_path}")
    if not isinstance(manifest_path, Path):
        try: manifest_path = Path(manifest_path)
        except Exception as e_path: logger.error(f"Chemin manifeste invalide: {manifest_path} ({e_path})"); return None
    if not manifest_path.is_file(): logger.error(f"Fichier manifeste introuvable: {manifest_path}"); return None
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f: data = json.load(f)
        if not isinstance(data, dict) or "fragments" not in data or not isinstance(data["fragments"], dict):
             logger.error(f"Structure manifeste invalide: {manifest_path}"); return None
        frag_count = len(data.get('fragments', {})); logger.info(f"Manifeste chargé ({frag_count} fragments).")
        logger.debug(f"Manifeste chargé (tronqué): {str(data)[:500]}...")
        return data
    except json.JSONDecodeError as e: logger.error(f"Parsing JSON manifeste échoué {manifest_path}: {e}", exc_info=True); return None
    except Exception as e: logger.error(f"Erreur lecture manifeste {manifest_path}: {e}", exc_info=True); return None

def save_manifest(manifest_data: dict, output_path: Path) -> bool:
    logger.info(f"Sauvegarde manifeste vers: {output_path}")
    if not isinstance(output_path, Path):
        try: output_path = Path(output_path)
        except Exception as e_path: logger.error(f"Chemin sortie invalide: {output_path} ({e_path})"); return False
    if not isinstance(manifest_data, dict) or "fragments" not in manifest_data or not isinstance(manifest_data.get("fragments"), dict):
         logger.error("Tentative sauvegarde structure manifeste invalide."); return False
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True); logger.debug(f"Écriture dans {output_path}...")
        with open(output_path, 'w', encoding='utf-8') as f: json.dump(manifest_data, f, indent=2, ensure_ascii=False)
        logger.info("Manifeste sauvegardé avec succès.")
        return True
    except TypeError as e: logger.critical(f"Données manifeste non sérialisables JSON: {e}", exc_info=True); logger.critical("Vérifiez structure données."); return False
    except Exception as e: logger.critical(f"Erreur critique sauvegarde manifeste vers {output_path}: {e}", exc_info=True); return False

if __name__ == "__main__":
     logging.basicConfig(level=logging.DEBUG, format='%(asctime)s-%(levelname)s-[%(name)s]-%(message)s')
     logger.info("Module manifest_io.py exécuté.")