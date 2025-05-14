# code/agents/qa_docstringenricher/agent.py
import sys
from pathlib import Path
import logging
import json
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

try:
    CURRENT_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = CURRENT_DIR.parents[1]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
        logger.debug(f"[{__name__} Init]: Added '{PROJECT_ROOT}' to sys.path.")
    from agents.base_agent import BaseAgent
except ImportError as e: print(f"Critical Error [QADocstringEnricherAgent Init]: {e}", file=sys.stderr); sys.exit(2)
except Exception as e: print(f"Unexpected Error [QADocstringEnricherAgent Init]: {e}", file=sys.stderr); sys.exit(2)

class QADocstringEnricherAgent(BaseAgent):
    expects_json_response: bool = True

    def __init__(self):
        super().__init__()
        logger.debug(f"[{self.agent_name}] QADocstringEnricherAgent initialized.")

    def _preprocess_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        logger.debug(f"[{self.agent_name}] Preprocessing context. Keys: {list(context.keys())}")
        req_keys = ["fragment_id", "identifier", "code_block", "fragment_type"]
        for k in req_keys:
            if k not in context or context[k] is None: raise ValueError(f"Context {self.agent_name} missing key: '{k}'")
        for k_opt in ["original_path", "package_name", "signature", "definition", "current_docstring", "context_code_around", "relevant_calls"]:
            context.setdefault(k_opt, None)
        return context

    def _prepare_llm_prompt(self, ctx: Dict[str,Any]) -> Tuple[Optional[str], Optional[List[Dict[str,str]]], Optional[str]]:
        data = {k: ctx.get(k) for k in ["fragment_id", "original_path", "is_templ_source", "fragment_type", "identifier", "package_name", "signature", "definition", "current_docstring", "code_block", "context_code_around", "relevant_calls"]}
        cleaned_data = {k:v for k,v in data.items() if v is not None or k == "current_docstring"}
        logger.debug(f"[{self.agent_name}] Data for LLM (keys): {list(cleaned_data.keys())}")
        try: return None, None, json.dumps(cleaned_data, ensure_ascii=False)
        except TypeError as e: logger.error(f"[{self.agent_name}] Error serializing data for LLM: {e}"); return None,None,None

    def _postprocess_response(self, response_text: Optional[str], context: Dict[str, Any]) -> Dict[str, Any]:
        frag_id_ctx = context.get("fragment_id", "unknown_fragment")
        base_error_response = lambda msg, malformed=False, data=None, raw_text=None: {
            "status": "error", "llm_response_malformed": malformed,
            "fragment_id": frag_id_ctx, "error_message": msg,
            **( {"raw_llm_response_data": data} if data and isinstance(data, dict) else {} ),
            **( {"raw_llm_response_text": raw_text if raw_text else response_text} ) # Garder une trace du texte brut
        }

        if response_text is None or not response_text.strip():
            logger.error(f"[{self.agent_name}] No response text from LLM for '{frag_id_ctx}'.")
            return base_error_response("No response or empty response from LLM.", malformed=True)

        logger.debug(f"[{self.agent_name}] Raw LLM response for '{frag_id_ctx}': {response_text[:300]}...")
        cleaned_text = response_text.strip()
        if cleaned_text.startswith("```json"): cleaned_text = cleaned_text[len("```json"):].strip()
        elif cleaned_text.startswith("```"):
            temp_cleaned = cleaned_text[3:].strip()
            if temp_cleaned.endswith("```"): cleaned_text = temp_cleaned[:-3].strip()
        if cleaned_text.endswith("```"): cleaned_text = cleaned_text[:-len("```")].strip()
        if cleaned_text != response_text: logger.debug(f"[{self.agent_name}] LLM response after cleaning for '{frag_id_ctx}': {cleaned_text[:300]}...")

        try:
            llm_data = json.loads(cleaned_text)
            if not isinstance(llm_data, dict):
                logger.error(f"[{self.agent_name}] LLM response for '{frag_id_ctx}' not a dict. Type: {type(llm_data)}")
                return base_error_response("LLM response malformed (not a dictionary).", malformed=True, raw_text=cleaned_text)

            llm_status = llm_data.get("status")
            llm_frag_id = llm_data.get("fragment_id")

            if not llm_status or not llm_frag_id:
                logger.error(f"[{self.agent_name}] LLM response for '{frag_id_ctx}' missing 'status' or 'fragment_id'.")
                return base_error_response("LLM response malformed (missing status/fragment_id).", malformed=True, data=llm_data)
            
            if llm_frag_id != frag_id_ctx:
                logger.warning(f"[{self.agent_name}] Mismatch fragment_id. Context: '{frag_id_ctx}', LLM: '{llm_frag_id}'. Using context ID.")
                llm_data["fragment_id"] = frag_id_ctx # Surcharger pour cohérence

            if llm_status == "success" and "proposed_docstring" not in llm_data: # Clé doit exister, même si valeur null
                logger.error(f"[{self.agent_name}] LLM status 'success' for '{frag_id_ctx}' but 'proposed_docstring' key missing.")
                return base_error_response("LLM 'success' but 'proposed_docstring' key missing.", malformed=True, data=llm_data)
            
            # Si le statut du LLM est "success", "no_change_needed", ou "error" (géré par le LLM),
            # on considère que le post-traitement a réussi à obtenir une réponse structurée.
            # Le statut global de l'agent sera celui retourné par le LLM.
            logger.info(f"[{self.agent_name}] Processed docstring for '{llm_frag_id}'. LLM status: {llm_status}")
            return llm_data # Le dict du LLM contient déjà "status"

        except json.JSONDecodeError as e:
            logger.error(f"[{self.agent_name}] Failed to decode JSON from LLM for '{frag_id_ctx}': {e}. Text: {cleaned_text[:500]}")
            return base_error_response(f"Failed to decode JSON response: {e}", malformed=True, raw_text=cleaned_text)
        except Exception as ex:
            logger.error(f"[{self.agent_name}] Unexpected error in postprocessing for '{frag_id_ctx}': {ex}", exc_info=True)
            return base_error_response(f"Unexpected postprocessing error: {ex}")

# ... (bloc if __name__ == "__main__" pour tests, inchangé) ...