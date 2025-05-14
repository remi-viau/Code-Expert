# code/agents/planner/agent.py

import sys
import json
import traceback # Utilisé pour le logging d'erreurs détaillées si besoin
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import logging
import datetime # Pour le timestamp des fichiers de debug
import hashlib  # Pour le hash dans les noms de fichiers de debug

# --- Logger ---
logger = logging.getLogger(__name__) # Logger spécifique à ce module

# --- Gestion Imports et Chemins ---
try:
    CURRENT_DIR = Path(__file__).resolve().parent # agents/planner/
    # Remonter de 1 niveau pour 'agents', puis encore 1 pour 'code'
    PROJECT_ROOT = CURRENT_DIR.parents[1] # code/

    # Le point d'entrée (ex: orchestrator.main) devrait s'assurer que PROJECT_ROOT est dans sys.path.
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
        # Utiliser logger.debug au lieu de print pour les messages d'initialisation de path
        logger.debug(f"[{__name__} Init]: Ajout de '{PROJECT_ROOT}' à sys.path par planner/agent.py.")

    from agents.base_agent import BaseAgent # Import absolu depuis le package 'agents'
    import global_config # Pour WORKSPACE_PATH dans la sauvegarde de debug
    # from lib import utils as shared_utils # Décommentez si vous avez besoin de shared_utils directement ici

except ImportError as e_import:
    # Utiliser print pour les erreurs critiques d'import car le logger pourrait ne pas être prêt
    _err_msg_import = (
        f"Erreur critique [Planner Agent Init]: Impossible d'importer les modules requis: {e_import}\n"
        f"  PROJECT_ROOT calculé: '{PROJECT_ROOT}'\n"
        f"  PYTHONPATH actuel: {sys.path}"
    )
    print(_err_msg_import, file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(2) # Arrêt critique si les imports de base échouent
except Exception as e_init_planner:
    _err_msg_init = f"Erreur inattendue à l'initialisation [Planner Agent]: {e_init_planner}"
    print(_err_msg_init, file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(2)
# ------------------------------------

# --- Fonction Helper pour sauvegarder le contexte du Planner ---
def _save_planner_llm_input_for_debug(agent_name: str, llm_input_data: dict, user_request_for_filename: str):
    """Sauvegarde le dictionnaire complet qui sera envoyé au LLM du Planner pour débogage."""
    try:
        ws_path = getattr(global_config, 'WORKSPACE_PATH', None)
        if not ws_path or not isinstance(ws_path, Path):
             logger.warning(f"[{agent_name}] WORKSPACE_PATH invalide ou absent. Sauvegarde debug pour le Planner impossible.")
             return

        debug_dir = ws_path / "debug_outputs" / agent_name # ex: workspace/debug_outputs/planner/
        debug_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        # Rendre le nom de fichier plus sûr
        safe_req_part = "".join(c for c in user_request_for_filename[:30] if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')
        req_hash = hashlib.sha1(user_request_for_filename.encode()).hexdigest()[:6] # Hash court pour unicité
        
        debug_file_name = f"planner_llm_input_{safe_req_part}_{req_hash}_{timestamp}.json"
        debug_file_path = debug_dir / debug_file_name

        with open(debug_file_path, 'w', encoding='utf-8') as f:
            json.dump(llm_input_data, f, indent=2, ensure_ascii=False)
        logger.debug(f"[{agent_name}] Contexte complet du Planner pour LLM sauvegardé pour debug dans: {debug_file_path.name}")

    except Exception as e_save_debug:
        # Ne pas planter l'agent si la sauvegarde debug échoue
        logger.warning(f"[{agent_name}] Échec de la sauvegarde du contexte debug pour le LLM du Planner: {e_save_debug}", exc_info=True)
# --- Fin Fonction Helper ---


class PlannerAgent(BaseAgent):
    """
    Agent spécialisé dans la génération d'un plan d'action structuré (liste d'étapes)
    basé sur une requête utilisateur et un contexte de code pertinent (incluant le code source).
    Il utilise également les connaissances additionnelles chargées depuis son dossier 'docs/'.
    """
    expects_json_response: bool = True # Indique à BaseAgent de configurer l'appel LLM pour une réponse JSON

    def __init__(self):
        """Initialise l'agent Planner."""
        super().__init__() # BaseAgent gère le chargement de config, instructions, docs
        logger.debug(f"[{self.agent_name}] Initialisation spécifique du PlannerAgent terminée.")

    # @override
    def _preprocess_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Valide la structure minimale du contexte attendu par le Planner.
        Le contexte est censé être construit par `context_builder.build_planner_context`.
        """
        logger.debug(f"[{self.agent_name}] Prétraitement du contexte pour le Planner...")
        if not isinstance(context.get("user_request"), str) or not context["user_request"].strip():
            # Lever une ValueError pour être capturée par BaseAgent.run()
            raise ValueError("Contexte invalide pour Planner: 'user_request' manquant ou vide.")
        if not isinstance(context.get("relevant_code_fragments"), list):
            # Peut être une liste vide si aucun fragment pertinent/extractible.
            logger.warning(f"[{self.agent_name}] 'relevant_code_fragments' n'est pas une liste dans le contexte. Sera traité comme vide.")
            context["relevant_code_fragments"] = [] 
        return context

    # @override - Implémentation obligatoire de la méthode abstraite
    def _prepare_llm_prompt(self, processed_context: Dict[str, Any]) -> Tuple[Optional[str], Optional[List[Dict[str, str]]], Optional[str]]:
        """
        Prépare le prompt JSON pour l'appel au LLM Planner.
        `processed_context` a déjà été validé par `_preprocess_context`.
        """
        logger.debug(f"[{self.agent_name}] Préparation du prompt LLM pour le Planner...")
        
        user_request = processed_context["user_request"]
        relevant_code_fragments = processed_context["relevant_code_fragments"]
        optimizer_reasoning = processed_context.get("optimizer_selection_reasoning")

        prompt_data_for_llm = {
            "user_request": user_request,
            "optimizer_selection_reasoning": optimizer_reasoning,
            "code_context": { 
                "fragments": relevant_code_fragments
            },
            "additional_planning_guidelines": self._additional_knowledge if self._additional_knowledge else "Aucune directive de planification additionnelle spécifique n'est fournie à cet agent."
        }

        if hasattr(global_config, 'WORKSPACE_PATH') and global_config.WORKSPACE_PATH:
            _save_planner_llm_input_for_debug(self.agent_name, prompt_data_for_llm, user_request)
        
        try:
            prompt_content_json_str = json.dumps(prompt_data_for_llm, ensure_ascii=False, indent=None)
            prompt_size_kb = len(prompt_content_json_str) / 1024
            logger.info(f"[{self.agent_name}] Taille du prompt Planner (avec code source) envoyé au LLM (approx): {prompt_size_kb:.2f} KB")
            if prompt_size_kb > 2000: # Seuil d'alerte
                logger.warning(f"[{self.agent_name}] La taille du prompt pour le Planner est très importante ({prompt_size_kb:.2f} KB).")
        except TypeError as e_json_dump:
             logger.error(f"[{self.agent_name}] Erreur lors de la sérialisation du prompt JSON pour le Planner: {e_json_dump}")
             raise ValueError(f"Erreur lors de la sérialisation du prompt JSON pour le Planner: {e_json_dump}")

        return None, None, prompt_content_json_str

    # @override - Surcharge pour parser et valider la réponse JSON spécifique du Planner
    def _postprocess_response(self, response_text: Optional[str], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse la réponse JSON du LLM Planner et valide sa structure.
        Retourne un dictionnaire contenant le plan complet et OBLIGATOIREMENT une clé 'status'.
        """
        logger.debug(f"[{self.agent_name}] Post-traitement de la réponse du Planner...")

        # Cas où l'appel LLM a échoué (géré par BaseAgent avant d'appeler _postprocess_response,
        # mais response_text peut être None si l'appel a échoué après tous les retries dans shared_utils.call_llm)
        if response_text is None or not response_text.strip():
            logger.error(f"[{self.agent_name}] Aucune réponse texte reçue du LLM ou réponse vide.")
            return {
                "status": "error", # Statut pour BaseAgent
                "plan_status": "error", # Statut interne du plan
                "reasoning": "Aucune réponse du LLM pour la planification.",
                "error_message": f"Aucune réponse texte n'a été reçue du LLM pour l'agent {self.agent_name}.",
                "steps": []
            }

        # Logguer la réponse brute reçue par cet agent (tronquée pour ne pas polluer les logs)
        logger.debug(f"[{self.agent_name}] Réponse brute du LLM par Planner (avant nettoyage):\n---\n{response_text[:500]}...\n---")

        # Nettoyage de la réponse (enlever les blocs de code Markdown si présents)
        cleaned_response_text = response_text.strip()
        original_length = len(cleaned_response_text)
        
        if cleaned_response_text.startswith("```json"):
            cleaned_response_text = cleaned_response_text[len("```json"):].strip()
        elif cleaned_response_text.startswith("```"):
            cleaned_response_text = cleaned_response_text[len("```"):].strip()
        
        if cleaned_response_text.endswith("```"):
            cleaned_response_text = cleaned_response_text[:-len("```")].strip()
        
        if len(cleaned_response_text) != original_length: # Log seulement si un nettoyage a eu lieu
            logger.debug(f"[{self.agent_name}] Réponse après nettoyage des blocs Markdown:\n---\n{cleaned_response_text[:500]}...\n---")

        try:
            parsed_data_from_llm = json.loads(cleaned_response_text)

            if not isinstance(parsed_data_from_llm, dict):
                logger.error(f"[{self.agent_name}] Réponse JSON du Planner n'est pas un dictionnaire. Type: {type(parsed_data_from_llm)}")
                return {
                    "status": "error", "plan_status": "error",
                    "reasoning": "Réponse JSON malformée du LLM (n'est pas un dictionnaire).",
                    "error_message": f"La réponse JSON du LLM n'est pas un objet dictionnaire (type: {type(parsed_data_from_llm).__name__}).",
                    "steps": []
                }

            # Vérifier la présence de 'plan_status', clé attendue du format de plan du LLM
            if "plan_status" not in parsed_data_from_llm:
                logger.error(f"[{self.agent_name}] Clé 'plan_status' manquante dans la réponse JSON du Planner. Contenu (début): {str(parsed_data_from_llm)[:200]}...")
                return {
                    "status": "error", "plan_status": "error",
                    "reasoning": "Structure de plan invalide du LLM (plan_status manquant).",
                    "error_message": "Clé 'plan_status' manquante dans la réponse JSON du LLM.",
                    "steps": [], "raw_llm_response_if_parsed": parsed_data_from_llm
                }

            # Le LLM a retourné une structure de plan (bonne ou mauvaise, selon son plan_status interne)
            # Le 'status' de BaseAgent sera basé sur le succès du parsing et la validité structurelle de base.
            # Le 'plan_status' interne du LLM sera propagé.

            # Cas 1: Le plan du LLM est marqué 'success' par le LLM lui-même
            if parsed_data_from_llm.get("plan_status") == "success":
                if not isinstance(parsed_data_from_llm.get("steps"), list):
                    logger.error(f"[{self.agent_name}] Plan JSON avec 'plan_status: success' mais 'steps' est invalide ou manquant.")
                    # Le plan est malformé même si le LLM dit success.
                    return {
                        "status": "error", # Erreur pour BaseAgent
                        "plan_status": "error", # Marquer le plan comme erroné
                        "reasoning": parsed_data_from_llm.get("reasoning", "Structure de plan 'success' invalide du LLM (steps manquants/invalides)."),
                        "error_message": "La clé 'steps' est manquante ou n'est pas une liste dans un plan marqué 'success' par le LLM.",
                        "steps": [], "original_llm_plan": parsed_data_from_llm
                    }
                else:
                    # Le plan est structurellement valide et marqué success par le LLM.
                    logger.info(f"[{self.agent_name}] Plan JSON valide du LLM reçu et parsé avec succès ({len(parsed_data_from_llm['steps'])} étapes).")
                    final_result = {"status": "success"} # Pour BaseAgent
                    final_result.update(parsed_data_from_llm) # Fusionne tout le plan du LLM
                    return final_result

            # Cas 2: Le plan du LLM est marqué 'error' par le LLM lui-même
            elif parsed_data_from_llm.get("plan_status") == "error":
                error_msg_from_llm = parsed_data_from_llm.get("error_message", "Erreur de planification non spécifiée par l'IA.")
                logger.error(f"[{self.agent_name}] L'IA Planner a retourné un statut d'erreur explicite: {error_msg_from_llm}")
                final_result = {"status": "error"} # Erreur pour BaseAgent
                final_result.update(parsed_data_from_llm)
                if "steps" not in final_result: final_result["steps"] = [] # Assurer la présence de 'steps'
                return final_result
            
            # Cas 3: 'plan_status' a une valeur inattendue
            else:
                unknown_plan_status = parsed_data_from_llm.get('plan_status')
                logger.error(f"[{self.agent_name}] Réponse JSON du Planner avec 'plan_status' inconnu: '{unknown_plan_status}'.")
                return {
                    "status": "error", "plan_status": "error",
                    "reasoning": parsed_data_from_llm.get("reasoning", f"Statut de plan inconnu '{unknown_plan_status}' du LLM."),
                    "error_message": f"Statut de plan inconnu reçu du LLM: {unknown_plan_status}",
                    "steps": parsed_data_from_llm.get("steps", []), "original_llm_plan": parsed_data_from_llm
                }

        except json.JSONDecodeError as e_json_decode:
            logger.error(f"[{self.agent_name}] Réponse du Planner non-JSON après nettoyage: {e_json_decode}")
            # Ne pas logguer la réponse complète en production si elle est très longue et non-JSON.
            logger.debug(f"  Réponse nettoyée (Planner, non-JSON, extrait pour debug):\n---\n{cleaned_response_text[:500]}...\n---")
            return {
                "status": "error", "plan_status": "error",
                "reasoning": "Réponse non-JSON du LLM.",
                "error_message": f"La réponse du LLM n'est pas un JSON valide: {e_json_decode}",
                "steps": []
            }
        except Exception as e_general_postprocess:
             logger.error(f"[{self.agent_name}] ERREUR inattendue pendant le post-traitement de la réponse du Planner: {type(e_general_postprocess).__name__} - {e_general_postprocess}", exc_info=True)
             return {
                 "status": "error", "plan_status": "error",
                 "reasoning": "Erreur interne lors du post-traitement de la réponse.",
                 "error_message": f"Erreur interne lors du post-traitement de la réponse: {e_general_postprocess}",
                 "steps": []
             }

# --- Point d'entrée pour test (optionnel) ---
if __name__ == "__main__":
     # Configuration du logging pour les tests directs
     _log_level_test = logging.DEBUG
     _log_format_test = '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
     logging.basicConfig(level=_log_level_test, format=_log_format_test, stream=sys.stderr)
     
     # S'assurer que la racine du projet (dossier 'code') est dans sys.path pour les tests
     _test_project_root_for_planner = Path(__file__).resolve().parents[2] # planner -> agents -> code
     if str(_test_project_root_for_planner) not in sys.path:
         sys.path.insert(0, str(_test_project_root_for_planner))
         logger.info(f"[Test {PlannerAgent.__name__}]: Ajout de {_test_project_root_for_planner} à sys.path.")

     # Charger .env pour les tests si OLLAMA_API_BASE est dedans
     try:
         from dotenv import load_dotenv
         dotenv_path = _test_project_root_for_planner.parent / ".env" # Un niveau au-dessus de 'code'
         if dotenv_path.exists():
            load_dotenv(dotenv_path=dotenv_path, override=False)
            logger.info(f"Variables d'environnement de {dotenv_path} chargées pour le test.")
         else:
            logger.info(f"Fichier .env non trouvé à {dotenv_path}, utilisation des variables d'environnement système.")
     except ImportError:
         logger.info("python-dotenv non installé, .env non chargé pour le test.")


     agent_display_name_test = PlannerAgent.__name__
     logger.info(f"--- Test de l'agent {agent_display_name_test} ---")

     mock_planner_context_test = {
         "user_request": "Ajouter un champ 'email' à la structure User et mettre à jour la fonction GetUser pour retourner cet email.",
         "optimizer_selection_reasoning": "Les fragments User et GetUser sont directement impactés par la demande.",
         "relevant_code_fragments": [
             {
                 "fragment_id": "models_user_type_User", 
                 "path_for_llm": "internal/models/user.go", 
                 "is_templ_source_file": False,
                 "fragment_type": "type", 
                 "identifier": "User", 
                 "package_name": "models",
                 "definition": "type User struct {\n\tID   int    `json:\"id\"`\n\tName string `json:\"name\"`\n}",
                 "docstring" : "Represents a user in the system.",
                 "code_block": "type User struct {\n\tID   int    `json:\"id\"`\n\tName string `json:\"name\"`\n}"
             },
             {
                 "fragment_id": "services_user_func_GetUser", 
                 "path_for_llm": "internal/services/user_service.go", 
                 "is_templ_source_file": False,
                 "fragment_type": "function", 
                 "identifier": "GetUser", 
                 "package_name": "services",
                 "signature": "func GetUser(id int) (*models.User, error)",
                 "docstring" : "Retrieves a user by their ID.",
                 "code_block": "package services\n\nimport \"example.com/project/internal/models\"\n\n// GetUser retrieves a user by their ID.\nfunc GetUser(id int) (*models.User, error) {\n\t// Placeholder: find user in DB\n\tif id == 1 {\n\t\treturn &models.User{ID: 1, Name: \"Test User\"}, nil\n\t}\n\treturn nil, errors.New(\"user not found\")\n}"
             }
         ]
     }
     logger.info(f"Contexte de test pour le Planner:\n{json.dumps(mock_planner_context_test, indent=2, ensure_ascii=False)}")

     try:
         agent_instance_test = PlannerAgent()
         if agent_instance_test._config: # Vérifier si la config a été chargée
             logger.info(f"Config chargée pour {agent_instance_test.agent_name}: OK (Modèle: {agent_instance_test._config.get('model_name')})")
             logger.info(f"Instructions de base chargées: {'Oui' if agent_instance_test._base_instructions else 'Non'}")
             logger.info(f"Connaissances additionnelles chargées: {'Oui (' + str(len(agent_instance_test._additional_knowledge)) + ' chars)' if agent_instance_test._additional_knowledge else 'Non'}")
             logger.info(f"L'agent attend du JSON du LLM: {agent_instance_test.expects_json_response}")
             
             logger.info("\nAppel de agent_instance_test.run(mock_planner_context_test)...")
             result_test = agent_instance_test.run(mock_planner_context_test)

             logger.info("\nRésultat retourné par run():")
             logger.info(json.dumps(result_test, indent=2, ensure_ascii=False))

             # Le statut global de l'agent (de BaseAgent)
             agent_run_status = result_test.get("status") 
             # Le statut interne du plan (du LLM, si le parsing a réussi)
             internal_plan_status = result_test.get("plan_status") 

             if agent_run_status == "success" and internal_plan_status == "success":
                 logger.info("Test du Planner réussi (agent et plan internes OK).")
                 sys.exit(0)
             else:
                 logger.error(f"Test du Planner échoué. Statut agent: {agent_run_status}, Statut plan interne: {internal_plan_status}")
                 sys.exit(1)
         else:
              logger.critical("Impossible de charger la configuration de l'agent pour le test.")
              sys.exit(1)

     except FileNotFoundError as e_fnf: # Si config.yaml ou instructions.md est manquant
         logger.critical(f"ERREUR lors du test: Fichier manquant pour l'agent {agent_display_name_test}: {e_fnf}", exc_info=True)
         sys.exit(1)
     except ValueError as e_val: # Erreurs de config ou de contexte
          logger.critical(f"ERREUR lors du test: Problème de valeur/config pour {agent_display_name_test}: {e_val}", exc_info=True)
          sys.exit(1)
     except Exception as e_test_generic: # Autres erreurs
          logger.critical(f"ERREUR inattendue lors du test de l'agent {agent_display_name_test}: {e_test_generic}", exc_info=True)
          sys.exit(1)