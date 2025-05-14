# code/manifest/main.py
#!/usr/bin/env python3
"""
Point d'entrée principal pour l'outil de génération du Manifeste de Code.
Ce script orchestre l'analyse statique du code (AST) pour extraire les
fragments de code, leurs métadonnées et leurs docstrings.
Il ne génère plus de résumés par LLM ; cette tâche est remplacée par
la génération d'embeddings à partir des docstrings via un script séparé.
"""
import sys
import os
from pathlib import Path
import argparse
# asyncio n'est plus nécessaire si process_summaries est supprimé
import json # Pour le debug potentiel, bien que manifest_io le gère
import traceback
import logging

# --- Gestion des imports des modules du projet ---
try:
    # PROJECT_ROOT_DIR est le dossier 'code'
    # manifest/main.py -> manifest -> code
    PROJECT_ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(PROJECT_ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT_DIR))
        # print(f"INFO [Manifest Main Init]: Ajout de {PROJECT_ROOT_DIR} à sys.path")

    from . import cli as manifest_cli # Renommé pour éviter confusion avec orchestrator_cli
    from . import ast_interface
    from . import manifest_io # Pour load/save manifest
    # 'summaries' n'est plus importé
    import global_config # Pour les chemins par défaut, etc.
    from lib import utils as shared_utils # Pour setup_logging, print_stage_header
except ImportError as e:
    print(f"Erreur critique [Manifest Main Init]: Impossible d'importer un module requis: {e}", file=sys.stderr)
    print(f"  PYTHONPATH actuel: {sys.path}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(2)
except Exception as e:
    print(f"Erreur inattendue à l'initialisation [Manifest Main]: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(2)
# -------------------------------------------

# --- Logger pour ce module ---
logger = logging.getLogger(__name__)
# -----------------------------


# --- Fonctions du Workflow ---

def run_manifest_generation_workflow(args: argparse.Namespace) -> bool:
    """
    Workflow principal pour la génération du manifeste.
    1. Détermine le chemin du projet cible.
    2. Exécute l'analyse AST pour obtenir les fragments bruts (avec docstrings).
    3. Gère la fusion incrémentale si activée (pour conserver les digestes de code, pas les résumés).
    4. Sauvegarde le manifeste final.
    """
    logger.info(f"--- Début du Workflow de Génération du Manifeste (Incremental: {not args.no_incremental}) ---")

    # 1. Déterminer le chemin du projet cible à analyser
    target_project_path = None
    if args.target_project_path:
        target_project_path_arg = Path(args.target_project_path).resolve()
        if not target_project_path_arg.is_dir():
            logger.error(f"Le chemin du projet cible fourni via CLI '{args.target_project_path}' est invalide ou n'est pas un répertoire.")
            return False
        target_project_path = target_project_path_arg
        # Log si le chemin CLI diffère de global_config (si global_config est chargé et le chemin est défini)
        if hasattr(global_config, 'TARGET_PROJECT_PATH') and global_config.TARGET_PROJECT_PATH and \
           str(target_project_path) != str(global_config.TARGET_PROJECT_PATH.resolve()):
             logger.warning(f"Utilisation du chemin cible CLI '{target_project_path}', qui diffère du chemin dans global_config.py ('{global_config.TARGET_PROJECT_PATH.resolve()}').")
    else:
        target_project_path = global_config.get_validated_target_path()
        if not target_project_path:
            logger.error("Chemin du projet cible non défini ou invalide dans global_config.py et non fourni via CLI.")
            return False
        logger.info(f"Utilisation du chemin cible défini dans global_config: {target_project_path}")

    # 2. Exécuter l'analyse statique du code (AST)
    shared_utils.print_stage_header("Étape 1: Analyse Statique du Code (AST)")
    # ast_interface.run_ast_parser logue déjà ses propres informations
    new_manifest_data_from_ast = ast_interface.run_ast_parser(target_project_path)
    
    if not new_manifest_data_from_ast or "fragments" not in new_manifest_data_from_ast:
        logger.critical("La génération du manifeste a échoué: L'analyse AST n'a pas retourné de données valides ou aucun fragment.")
        return False
    
    num_raw_fragments = len(new_manifest_data_from_ast.get("fragments", {}))
    logger.info(f"Analyse AST terminée. {num_raw_fragments} fragment(s) brut(s) trouvé(s) (incluant docstrings).")

    # 3. Fusion Incrémentale (SI --no-incremental n'est PAS activé)
    #    Le but principal de la fusion ici est de conserver les `code_digest` et autres métadonnées
    #    qui ne changent pas si le code du fragment n'a pas changé.
    #    Le champ `summary` n'est plus géré ici car il n'est plus généré par un LLM.
    #    Le champ `docstring` vient directement de l'AST à chaque fois.
    output_manifest_path = Path(args.output).resolve()
    final_manifest_to_save = new_manifest_data_from_ast # Par défaut, on utilise le nouveau manifeste

    if not args.no_incremental:
         logger.info(f"Mode incrémental activé. Tentative de fusion avec le manifeste existant (s'il existe) à: {output_manifest_path}")
         existing_manifest = manifest_io.load_manifest(output_manifest_path) # Utilise son propre logger
         
         if existing_manifest and isinstance(existing_manifest.get("fragments"), dict):
             logger.info(f"Manifeste existant chargé. Fusion des informations (ex: code_digest) basée sur l'ID du fragment.")
             merged_count = 0
             newly_added_count = 0
             
             new_fragments_from_ast = new_manifest_data_from_ast["fragments"]
             existing_fragments_map = existing_manifest["fragments"]
             
             processed_new_fragments = {}

             for frag_id, new_info_from_ast in new_fragments_from_ast.items():
                 old_info = existing_fragments_map.get(frag_id)
                 current_fragment_data = new_info_from_ast # Commence avec les dernières infos de l'AST

                 if old_info: # Le fragment existait déjà
                     # Conserver l'ancien code_digest si le nouveau (de l'AST) est manquant,
                     # bien que l'AST parser devrait toujours générer un digest.
                     # La principale utilité ici serait si d'autres champs étaient conservés de l'ancien manifeste.
                     # Pour l'instant, le `new_info_from_ast` est assez complet.
                     # On pourrait ajouter une logique plus fine si on veut conserver des champs de l'ancien manifeste
                     # même si le code a changé (ex: un flag manuel, des notes, etc.).
                     # Pour cet exemple, on se contente de loguer.
                     merged_count += 1
                     # Potentiellement, si le code_digest est identique, on pourrait copier certains champs de old_info
                     # vers current_fragment_data si on voulait préserver des métadonnées non générées par l'AST.
                     # Exemple (à adapter si nécessaire) :
                     # if old_info.get("code_digest") and new_info_from_ast.get("code_digest") and \
                     #    old_info["code_digest"] == new_info_from_ast["code_digest"]:
                     #      # Le code n'a pas changé, on pourrait vouloir garder des métadonnées de l'ancien manifeste
                     #      # current_fragment_data["some_preserved_field"] = old_info.get("some_preserved_field")
                     #      pass # Pour l'instant, on prend toutes les infos fraîches de l'AST
                 else: # Nouveau fragment non présent dans l'ancien manifeste
                      newly_added_count +=1
                 
                 processed_new_fragments[frag_id] = current_fragment_data
            
             final_manifest_to_save["fragments"] = processed_new_fragments # Mettre à jour avec les fragments traités
             deleted_fragments_count = len(existing_fragments_map) - (merged_count - newly_added_count + newly_added_count) # Plus complexe
             
             logger.info(f"Fusion incrémentale terminée: {merged_count - newly_added_count} fragments mis à jour (infos AST fraîches), {newly_added_count} nouveaux fragments ajoutés.")
             # Pour calculer les supprimés :
             deleted_ids = set(existing_fragments_map.keys()) - set(new_fragments_from_ast.keys())
             if deleted_ids:
                 logger.info(f"{len(deleted_ids)} fragment(s) présents dans l'ancien manifeste n'existent plus et ont été retirés.")
         else:
              logger.info(f"Aucun manifeste existant trouvé ou valide à {output_manifest_path} pour la fusion incrémentale. Le nouveau manifeste sera utilisé tel quel.")
    else: # --no-incremental
         logger.info("Mode non incrémental (--no-incremental). Le nouveau manifeste généré par l'AST sera utilisé directement.")
         # final_manifest_to_save est déjà new_manifest_data_from_ast

    # Le champ 'summary' n'est plus rempli par un LLM dans ce workflow.
    # Il sera `null` (ou omis si la struct Go le permet et qu'il est vide) en sortie de l'AST parser.
    # Les embeddings seront générés à partir des `docstrings` et autres métadonnées par un script séparé.

    # 4. Sauvegarder le manifeste final
    shared_utils.print_stage_header("Étape Finale: Sauvegarde du Manifeste")
    save_success = manifest_io.save_manifest(final_manifest_to_save, output_manifest_path)
    if not save_success:
        logger.critical("Échec critique lors de la sauvegarde du manifeste final.")
    
    return save_success


# --- Fonction Principale ---
def manifest_tool_main():
    """Fonction principale de l'outil de génération de manifeste."""
    args = manifest_cli.parse_arguments() # Renommé pour clarté
    if not args:
        sys.exit(1) # Erreur déjà gérée par argparse

    # --- Configurer le logging TRES TOT ---
    # Le logger racine est configuré. Les loggers de module l'utiliseront.
    shared_utils.setup_logging(debug_mode=args.debug) # Pas de fichier de log par défaut pour cet outil
    # ----------------------------------

    logger.info(f"Lancement de l'outil de génération de Manifeste (Mode: {args.mode}, Debug: {args.debug})")
    logger.info(f"Chemin de sortie du manifeste: {args.output}")
    if args.target_project_path:
        logger.info(f"Chemin du projet cible (CLI): {args.target_project_path}")
    if hasattr(args, 'no_incremental'): # Vérifier si l'attribut existe (ajouté dans cli.py)
        logger.info(f"Mode incrémental désactivé par CLI: {args.no_incremental}")


    workflow_successful = False
    # Les modes "retry", "target", "reprocess_docstrings" ont été supprimés de la CLI
    # car ils étaient liés à la génération de résumés par LLM.
    # Si vous avez d'autres cas d'usage pour un mode "mise à jour", il faudrait le redéfinir.
    if args.mode == "normal":
        workflow_successful = run_manifest_generation_workflow(args)
    else:
        logger.critical(f"Mode d'exécution interne non géré: '{args.mode}'. Seul le mode 'normal' est supporté actuellement.")
        sys.exit(2) # Code d'erreur pour mode non supporté

    # --- Analyse Finale et Code de Sortie ---
    exit_code = 0
    final_message = "Opération de génération du Manifeste terminée."

    if not workflow_successful:
        final_message = "Opération de génération du Manifeste A ÉCHOUÉ (voir logs pour détails)."
        exit_code = 1
    else:
        final_message += " SUCCÈS COMPLET."
    
    logger.info(final_message)
    if not workflow_successful: # Afficher aussi sur stderr si échec
        print(f"\nERREUR: {final_message}", file=sys.stderr)
    else:
        print(f"\nINFO: {final_message}")
        
    sys.exit(exit_code)

# --- Exécution ---
if __name__ == "__main__":
    # Pas besoin de asyncio.set_event_loop_policy si asyncio n'est plus utilisé
    manifest_tool_main()