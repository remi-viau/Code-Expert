# code/agents/templ_frontend/agent.py
import sys
from pathlib import Path
import logging # Importer logging
import json
from typing import Dict, Any, Optional, List, Tuple

# --- Logger ---
logger = logging.getLogger(__name__)

# --- Gestion Imports et Chemins ---
try:
    CURRENT_DIR = Path(__file__).resolve().parent 
    PROJECT_ROOT = CURRENT_DIR.parents[1] # templ_frontend -> agents -> code
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    
    from agents.base_agent import BaseAgent
    # from lib import utils as shared_utils # Si vous avez besoin d'utilitaires spécifiques ici
except ImportError as e:
    print(f"Erreur critique [TemplFrontendAgent Init]: {e}", file=sys.stderr)
    sys.exit(1)

class TemplFrontendAgent(BaseAgent):
    """
    Agent spécialisé dans la modification de fichiers de template .templ.
    Il reçoit le code source d'un fichier .templ et des instructions,
    et retourne le code source modifié.
    """
    # Cet agent ne s'attend PAS à ce que le LLM retourne du JSON.
    # Le LLM retourne le code source modifié (texte brut).
    # expects_json_response = False (valeur par défaut de BaseAgent)

    def __init__(self):
        super().__init__()
        logger.debug(f"[{self.agent_name}] Initialisation spécifique TemplFrontendAgent terminée.")

    # _preprocess_context: Peut être surchargé si le contexte brut de l'orchestrateur
    #                      a besoin d'une transformation spécifique avant d'être utilisé
    #                      par _prepare_llm_prompt. Pour l'instant, on suppose que le contexte
    #                      fourni par context_builder.assemble_expert_context est déjà bien structuré.
    # def _preprocess_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
    #     logger.debug(f"[{self.agent_name}] Prétraitement du contexte pour TemplFrontendAgent...")
    #     # Ex: Valider que target_fragments_with_code existe et a au moins un élément
    #     if not context.get("target_fragments_with_code"):
    #         raise ValueError("Contexte invalide: 'target_fragments_with_code' est manquant pour TemplFrontendAgent.")
    #     return context

    def _prepare_llm_prompt(
        self, processed_context: Dict[str, Any]
    ) -> Tuple[Optional[str], Optional[List[Dict[str, str]]], Optional[str]]:
        """
        Prépare le prompt pour le LLM. Le LLM recevra les instructions de l'étape
        et le code source du/des fichier(s) .templ cible(s).
        Pour cet agent, nous envoyons un prompt textuel car nous attendons du code en retour,
        pas une structure JSON du LLM.
        """
        logger.debug(f"[{self.agent_name}] Préparation du prompt LLM...")

        step_instructions = processed_context.get("step_instructions")
        target_fragments = processed_context.get("target_fragments_with_code", [])
        # previous_build_error = processed_context.get("previous_build_error") # Peut être utilisé

        if not step_instructions:
            logger.error(f"[{self.agent_name}] Instructions d'étape manquantes. Impossible de générer un prompt.")
            return None, None, None # Annuler l'appel LLM

        if not target_fragments:
            # Cas où l'agent doit créer un nouveau fichier .templ (pas de code existant)
            logger.info(f"[{self.agent_name}] Aucun fragment cible existant fourni. L'agent doit créer un nouveau fichier .templ basé sur les instructions.")
            # Le prompt sera principalement basé sur step_instructions.
            # Il est crucial que les instructions système (instructions.md) couvrent bien ce cas.
            prompt_lines = [
                "Vous devez créer le contenu d'un nouveau fichier .templ.",
                "Voici les instructions pour ce nouveau fichier :",
                "--- INSTRUCTIONS DE L'ÉTAPE ---",
                step_instructions,
                "--- FIN DES INSTRUCTIONS DE L'ÉTAPE ---",
                "\nRépondez UNIQUEMENT avec le contenu complet du nouveau fichier .templ."
            ]
        elif len(target_fragments) == 1:
            # Cas le plus courant : modifier un seul fichier .templ
            target_fragment = target_fragments[0]
            current_code = target_fragment.get("current_code_block")
            path_to_modify = target_fragment.get("path_to_modify")

            if current_code is None: # current_code peut être "" mais pas None
                 logger.error(f"[{self.agent_name}] Code source manquant pour le fragment cible '{path_to_modify}'.")
                 return None, None, None

            prompt_lines = [
                f"Le fichier .templ suivant (situé à '{path_to_modify}') doit être modifié :",
                "--- CODE SOURCE ACTUEL DU FICHIER .templ ---",
                current_code,
                "--- FIN DU CODE SOURCE ACTUEL ---",
                "\nVoici les instructions précises pour la modification :",
                "--- INSTRUCTIONS DE L'ÉTAPE ---",
                step_instructions,
                "--- FIN DES INSTRUCTIONS DE L'ÉTAPE ---",
                "\nRépondez UNIQUEMENT avec le contenu COMPLET et MODIFIÉ du fichier .templ. N'ajoutez aucun autre texte."
            ]
        else:
            # Gérer la modification de plusieurs fichiers .templ en un seul appel LLM est complexe.
            # Il est préférable que le plan décompose cela en étapes distinctes.
            # Pour l'instant, on ne gère que le premier par simplicité ou on logue une erreur.
            logger.warning(f"[{self.agent_name}] Reçu {len(target_fragments)} fragments cibles. Cet agent ne traitera que le premier pour l'instant.")
            # Logique pour prendre le premier (comme ci-dessus) ou lever une erreur / retourner None
            # ... (similaire au cas len(target_fragments) == 1, en prenant target_fragments[0]) ...
            # Pour la robustesse, on pourrait refuser de traiter si plus d'un pour l'instant.
            logger.error(f"[{self.agent_name}] La modification de plusieurs fichiers .templ en une seule étape n'est pas supportée. Annulation.")
            return None, None, None


        # Ajouter l'erreur de build précédente si elle existe
        if previous_build_error := processed_context.get("previous_build_error"):
            prompt_lines.extend([
                "\n--- ERREUR DU BUILD PRÉCÉDENT (À CORRIGER SI PERTINENT) ---",
                previous_build_error,
                "--- FIN DE L'ERREUR DU BUILD PRÉCÉDENT ---"
            ])

        final_prompt_text = "\n".join(prompt_lines)
        logger.debug(f"[{self.agent_name}] Prompt LLM textuel préparé (début): {final_prompt_text[:300]}...")
        return final_prompt_text, None, None # Prompt textuel, pas d'historique, pas de JSON


    def _postprocess_response(self, response_text: Optional[str], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite la réponse texte du LLM (qui devrait être le code .templ modifié).
        L'encapsule dans la structure attendue par l'orchestrateur.
        """
        logger.debug(f"[{self.agent_name}] Post-traitement de la réponse texte...")

        if response_text is None or not response_text.strip():
            logger.error(f"[{self.agent_name}] Aucune réponse (ou réponse vide) reçue du LLM.")
            return {"status": "error", "message": "Aucune réponse code valide reçue du LLM."}

        # Le LLM est censé retourner directement le code modifié.
        # On peut faire un nettoyage simple (ex: enlever les ``` s'il en ajoute quand même)
        modified_code = response_text.strip()
        if modified_code.startswith("```") and modified_code.endswith("```"):
            # Enlever la première ligne de ``` et la dernière ligne de ```
            lines = modified_code.splitlines()
            if len(lines) > 1 : # Au moins ``` et le code et ```
                # Si la première ligne est ``` ou ```templ ou ```html
                if lines[0].startswith("```"): 
                    lines = lines[1:]
                if lines and lines[-1] == "```": # S'assurer qu'il reste quelque chose avant de pop
                    lines = lines[:-1]
                modified_code = "\n".join(lines).strip()
        
        logger.debug(f"[{self.agent_name}] Code .templ modifié (après nettoyage) reçu du LLM (début):\n{modified_code[:300]}...")

        # Déterminer le chemin du fichier à modifier.
        # S'il y avait plusieurs cibles, cette logique doit être plus complexe,
        # mais on suppose ici que _prepare_llm_prompt n'a traité qu'une cible ou le cas de création.
        target_fragments = context.get("target_fragments_with_code", [])
        path_to_modify_from_context: Optional[str] = None
        is_templ_source_from_context = False # Par défaut .go

        if target_fragments: # Si on modifiait un fichier existant
            path_to_modify_from_context = target_fragments[0].get("path_to_modify")
            is_templ_source_from_context = target_fragments[0].get("is_templ_source", False)
        else: # Cas de création de nouveau fichier
            # Les instructions de l'étape DOIVENT contenir le chemin du nouveau fichier.
            # Il faut une convention pour cela (ex: le LLM du Planner l'indique).
            # Pour cet exemple, on suppose que les instructions pour la création
            # ne sont pas encore gérées ici pour déterminer le path.
            # L'orchestrateur ou l'agent exécuteur aurait besoin de plus d'infos du plan.
            # Ceci est une simplification pour l'instant.
            # Idéalement, le plan indiquerait "new_file_path": "path/to/new.templ"
            logger.warning(f"[{self.agent_name}] Tentative de post-traitement pour une création de fichier, mais la détermination du chemin du nouveau fichier n'est pas implémentée ici. 'path_to_modify' sera None.")
            # On pourrait essayer d'extraire le chemin des instructions du LLM, mais c'est fragile.

        if not path_to_modify_from_context and not step_instructions_indicate_new_file_path(context.get("step_instructions","")) : # helper function à créer
             logger.error(f"[{self.agent_name}] Impossible de déterminer le chemin du fichier à modifier/créer. Les instructions de l'étape doivent le spécifier si c'est une création.")
             return {"status": "error", "message": "Chemin du fichier cible indéterminé."}
        
        # Si path_to_modify_from_context est None (création), il faudrait l'extraire des instructions
        # Pour l'instant, on assume qu'il est là pour la modification.
        if not path_to_modify_from_context:
            # TODO: Logique pour extraire le nom du nouveau fichier depuis `context.get("step_instructions")`
            # Pour l'instant, on retourne une erreur si on est en mode création et que le path n'est pas déductible
            # de manière simple (ce qui est le cas ici, car il n'est pas passé explicitement pour la création).
            # Le PLAN doit être plus explicite pour la création de fichiers.
            # On va supposer pour l'instant que si target_fragments est vide, c'est une erreur car on ne sait pas où écrire.
            # L'agent "creator" devrait peut-être être différent et retourner le path + content.
            # Ou, le PLANNEUR doit spécifier "output_file_path" dans l'étape.
            # Pour cet agent MODIFICATEUR, on s'attend à un path_to_modify.
             logger.error(f"[{self.agent_name}] 'path_to_modify' est manquant dans le contexte pour l'agent MODIFICATEUR.")
             return {"status": "error", "message": "'path_to_modify' manquant pour un agent modificateur."}


        modified_fragment_entry = {
            "path_to_modify": path_to_modify_from_context,
            "is_templ_source": is_templ_source_from_context, 
            "new_content": modified_code
        }
        
        return {
            "status": "success",
            "modified_fragments": [modified_fragment_entry],
            "message": "Fichier .templ modifié avec succès par le LLM."
        }

# Helper fictif pour l'idée ci-dessus (à implémenter si besoin)
def step_instructions_indicate_new_file_path(instructions: str) -> Optional[str]:
    # TODO: Logique pour parser les instructions et trouver un chemin de fichier
    # ex: chercher "Create new file at path/to/file.templ with content:"
    return None


# --- Point d'entrée pour test (optionnel) ---
if __name__ == "__main__":
    log_level = logging.INFO 
    log_format = '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    logging.basicConfig(level=log_level, format=log_format, stream=sys.stderr)

    _test_project_root = Path(__file__).resolve().parents[2] 
    if str(_test_project_root) not in sys.path:
        sys.path.insert(0, str(_test_project_root))

    agent_display_name = TemplFrontendAgent.__name__
    logger.info(f"--- Test de l'agent {agent_display_name} ---")

    # Simuler le contexte que l'agent recevrait de l'orchestrateur
    # (après context_builder.assemble_expert_context)
    mock_expert_context_modify = {
        "step_instructions": "Dans le composant 'MyButton', changez la couleur du bouton de 'blue' à 'red' et le texte de 'Click Me' à 'Submit'.",
        "target_fragments_with_code": [
            {
                "fragment_id": "components_buttons_templ_MyButton", # ID du manifeste
                "path_to_modify": "webroot/views/components/buttons.templ", # Chemin du fichier .templ
                "is_templ_source": True,
                "identifier": "MyButton",
                "current_code_block": """package components

templ MyButton(color string, text string) {
    <button class={ "button", "is-" + color }>{ text }</button>
}

templ OtherButton() {
    @MyButton(color="blue", text="Click Me")
}
"""
            }
        ],
        "previous_build_error": None,
        "context_definitions": {} # Vide pour ce test simple
    }

    logger.info(f"Contexte de test (modification):\n{json.dumps(mock_expert_context_modify, indent=2, ensure_ascii=False)}")

    try:
        agent_instance = TemplFrontendAgent()
        if agent_instance._config:
            logger.info(f"Config chargée pour {agent_instance.agent_name}: OK (Modèle: {agent_instance._config.get('model_name')})")
            logger.info(f"L'agent attend du JSON du LLM: {agent_instance.expects_json_response}") # Devrait être False

            logger.info("\nAppel de agent_instance.run(mock_expert_context_modify)...")
            result = agent_instance.run(mock_expert_context_modify)

            logger.info("\nRésultat retourné par run():")
            logger.info(json.dumps(result, indent=2, ensure_ascii=False))
            
            if result.get("status") == "success" and result.get("modified_fragments"):
                logger.info("--- Code Modifié Proposé ---")
                logger.info(result["modified_fragments"][0]["new_content"])
                logger.info("----------------------------")
                sys.exit(0)
            else:
                sys.exit(1)
        else:
            logger.critical("Impossible de charger la config pour le test.")
            sys.exit(1)
    except Exception as e:
        logger.critical(f"ERREUR test {agent_display_name}: {e}", exc_info=True)
        sys.exit(1)