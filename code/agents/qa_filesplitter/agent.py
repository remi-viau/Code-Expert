# code/agents/qa_filesplitter/agent.py
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
except ImportError as e: print(f"Critical Error [QAFileSplitterAgent Init]: {e}", file=sys.stderr); sys.exit(2)
except Exception as e: print(f"Unexpected Error [QAFileSplitterAgent Init]: {e}", file=sys.stderr); sys.exit(2)

class QAFileSplitterAgent(BaseAgent):
    expects_json_response: bool = True

    def __init__(self):
        super().__init__()
        logger.debug(f"[{self.agent_name}] QAFileSplitterAgent initialized.")

    def _preprocess_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        logger.debug(f"[{self.agent_name}] Preprocessing context. Keys: {list(context.keys())}")
        req_keys = ["original_file_path", "original_file_content", "package_name"] # is_templ_source est aussi important
        for k in req_keys:
            if k not in context or not context[k]: raise ValueError(f"Context {self.agent_name} missing key: '{k}'")
        context.setdefault("max_lines_per_file_target", 500)
        context.setdefault("is_templ_source", False) # S'assurer qu'il est présent
        return context

    def _prepare_llm_prompt(self, ctx: Dict[str,Any]) -> Tuple[Optional[str], Optional[List[Dict[str,str]]], Optional[str]]:
        data = {k: ctx.get(k) for k in ["original_file_path", "original_file_content", "max_lines_per_file_target", "package_name", "is_templ_source"]}
        logger.info(f"[{self.agent_name}] Analyzing file '{data['original_file_path']}' (len: {len(data['original_file_content'])} chars, templ: {data['is_templ_source']}) for splitting.")
        try: return None, None, json.dumps(data, ensure_ascii=False)
        except TypeError as e: logger.error(f"[{self.agent_name}] Error serializing data for LLM: {e}"); return None,None,None

    def _postprocess_response(self, response_text: Optional[str], context: Dict[str, Any]) -> Dict[str, Any]:
        orig_path_ctx = context.get("original_file_path", "unknown_file")
        base_error_response = lambda msg, malformed=False, data=None, raw_text=None: {
            "status": "error", "llm_response_malformed": malformed, # Erreur de l'agent, pas un plan "error" du LLM
            "original_file_path": orig_path_ctx, "error_message": msg,
            **( {"raw_llm_response_data": data} if data and isinstance(data, dict) else {} ),
            **( {"raw_llm_response_text": raw_text if raw_text else response_text} )
        }

        if response_text is None or not response_text.strip():
            logger.error(f"[{self.agent_name}] No response text from LLM for '{orig_path_ctx}'.")
            return base_error_response("No response or empty response from LLM.", malformed=True)

        logger.debug(f"[{self.agent_name}] Raw LLM response for splitting plan of '{orig_path_ctx}': {response_text[:300]}...")
        cleaned_text = response_text.strip()
        if cleaned_text.startswith("```json"): cleaned_text = cleaned_text[len("```json"):].strip()
        elif cleaned_text.startswith("```"):
            temp_cleaned = cleaned_text[3:].strip();
            if temp_cleaned.endswith("```"): cleaned_text = temp_cleaned[:-3].strip()
        if cleaned_text.endswith("```"): cleaned_text = cleaned_text[:-len("```")].strip()
        if cleaned_text != response_text: logger.debug(f"[{self.agent_name}] LLM response after cleaning for '{orig_path_ctx}': {cleaned_text[:300]}...")

        try:
            llm_plan_data = json.loads(cleaned_text)
            if not isinstance(llm_plan_data, dict):
                logger.error(f"[{self.agent_name}] LLM response for '{orig_path_ctx}' not a dict. Type: {type(llm_plan_data)}")
                return base_error_response("LLM response malformed (not a dictionary).", malformed=True, raw_text=cleaned_text)

            llm_status = llm_plan_data.get("status")
            llm_orig_file = llm_plan_data.get("original_file_path")

            if not llm_status: # original_file_path est optionnel si status est error
                logger.error(f"[{self.agent_name}] LLM response for '{orig_path_ctx}' missing 'status'.")
                return base_error_response("LLM response malformed (missing its internal status).", malformed=True, data=llm_plan_data)
            
            if llm_orig_file and llm_orig_file != orig_path_ctx : # Comparer seulement si le LLM le fournit
                logger.warning(f"[{self.agent_name}] Mismatch original_file_path. Context: '{orig_path_ctx}', LLM: '{llm_orig_file}'. Using context path.")
                llm_plan_data["original_file_path"] = orig_path_ctx # Surcharger
            elif not llm_orig_file and llm_status != "error_cannot_plan": # S'assurer qu'il est là si pas une erreur de plan
                llm_plan_data["original_file_path"] = orig_path_ctx


            if llm_status == "success_plan_generated":
                if not isinstance(llm_plan_data.get("proposed_new_files"), list) or \
                   not isinstance(llm_plan_data.get("declarations_to_keep_in_original"), list):
                    logger.error(f"[{self.agent_name}] LLM plan for '{orig_path_ctx}' invalid structure for 'success_plan_generated'.")
                    return base_error_response("LLM plan structure invalid.", malformed=True, data=llm_plan_data)
                logger.info(f"[{self.agent_name}] Processed splitting plan for '{orig_path_ctx}'. LLM status: {llm_status}")
                return llm_plan_data # Contient "status":"success_plan_generated"

            elif llm_status in ["no_action_needed", "error_cannot_plan"]:
                logger.info(f"[{self.agent_name}] LLM status for '{orig_path_ctx}': {llm_status}. Reasoning: {llm_plan_data.get('reasoning', llm_plan_data.get('error_message'))}")
                return llm_plan_data # Contient son propre "status"
            
            else: # Statut LLM inconnu
                logger.error(f"[{self.agent_name}] LLM returned unknown status '{llm_status}' for plan of '{orig_path_ctx}'.")
                return base_error_response(f"LLM returned unknown status for plan: {llm_status}", malformed=True, data=llm_plan_data)

        except json.JSONDecodeError as e:
            logger.error(f"[{self.agent_name}] Failed to decode JSON from LLM for '{orig_path_ctx}': {e}. Text: {cleaned_text[:500]}")
            return base_error_response(f"Failed to decode JSON response: {e}", malformed=True, raw_text=cleaned_text)
        except Exception as ex:
            logger.error(f"[{self.agent_name}] Unexpected error in postprocessing for '{orig_path_ctx}': {ex}", exc_info=True)
            return base_error_response(f"Unexpected postprocessing error: {ex}")

# ... (bloc if __name__ == "__main__" pour tests, inchangé) ...