# code/agents/base_agent.py
import json
import os
import sys
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Tuple
import traceback
import logging
import inspect
import yaml
import time
import tiktoken  # Pour l'estimation des tokens

# --- Logger Initialisation ---
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    _root_logger_check = logging.getLogger()
    if not _root_logger_check.hasHandlers():
        logger.addHandler(logging.NullHandler())

# --- Gestion des Chemins et Imports Critiques ---
try:
    CURRENT_FILE_PATH = Path(__file__).resolve()
    PROJECT_ROOT_FOR_BASE_AGENT = CURRENT_FILE_PATH.parents[1]
    if str(PROJECT_ROOT_FOR_BASE_AGENT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT_FOR_BASE_AGENT))
        logger.debug(
            f"INFO [BaseAgent Init]: Ajout de '{PROJECT_ROOT_FOR_BASE_AGENT}' à sys.path."
        )
    from lib import utils as shared_utils
    import global_config
except ImportError as e_imp:
    _err_msg = (
        f"Erreur CRITIQUE [BaseAgent Init]: Import essentiel échoué: {e_imp}\n"
        f"  PROJECT_ROOT_FOR_BASE_AGENT: '{PROJECT_ROOT_FOR_BASE_AGENT}'\n"
        f"  PYTHONPATH: {sys.path}")
    print(_err_msg, file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(2)
except Exception as e_init:
    _err_msg = f"Erreur inattendue init BaseAgent (imports/paths): {e_init}"
    print(_err_msg, file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(2)
# --- Fin Gestion des Chemins et Imports ---


class BaseAgent(ABC):
    """
    Classe de base abstraite pour agents LLM. Gère config, instructions,
    connaissances, cycle d'appel LLM avec estimation de tokens et retries.
    """
    expects_json_response: bool = False
    MAX_POSTPROCESS_RETRIES: int = 1
    POSTPROCESS_RETRY_DELAY: int = 3  # Secondes

    # Seuils de tokens par défaut (peuvent être surchargés dans config.yaml de l'agent)
    # Ces valeurs sont conservatrices, ajustez-les par agent/modèle.
    DEFAULT_TOKEN_WARNING_THRESHOLD: int = 6000  # Tokens
    DEFAULT_TOKEN_ERROR_THRESHOLD: int = 7500  # Tokens (proche de la limite 8k de certains modèles)

    def __init__(self):
        try:
            s_path = Path(inspect.getfile(self.__class__)).resolve()
            self.agent_dir = s_path.parent
            self.agent_name = self.agent_dir.name
        except Exception as e:
            logger.warning(
                f"Introspection agent nom/dossier échouée: {e}. Fallback.",
                exc_info=False)
            self.agent_name = self.__class__.__name__.replace("Agent",
                                                              "").lower()
            self.agent_dir = PROJECT_ROOT_FOR_BASE_AGENT / "agents" / self.agent_name
            logger.warning(
                f"Fallback: nom='{self.agent_name}', dir='{self.agent_dir}'")

        self._config: Optional[Dict[str, Any]] = None
        self._base_instructions: Optional[str] = None
        self._additional_knowledge: str = ""

        if not self.agent_dir.is_dir():
            raise FileNotFoundError(
                f"Répertoire agent '{self.agent_dir}' introuvable.")
        logger.info(f"[{self.agent_name}] Initialisation agent...")
        self._load_resources()
        logger.info(f"[{self.agent_name}] Agent initialisé.")

    def _load_config(self) -> Optional[Dict[str, Any]]:
        cfg_path = self.agent_dir / "config.yaml"
        if not cfg_path.is_file():
            logger.error(
                f"[{self.agent_name}] Config '{cfg_path}' introuvable.")
            return None
        try:
            with cfg_path.open('r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict) or not data.get("model_name"):
                logger.error(
                    f"[{self.agent_name}] Config YAML invalide ou 'model_name' manquant: '{cfg_path}'."
                )
                return None
            logger.debug(f"[{self.agent_name}] Config chargée: {cfg_path}")
            return data
        except Exception as e:
            logger.error(
                f"[{self.agent_name}] Erreur chargement/parsing config '{cfg_path}': {e}",
                exc_info=True)
            return None

    def _load_base_instructions(self) -> Optional[str]:
        p = self.agent_dir / "instructions.md"
        if not p.is_file():
            logger.debug(
                f"[{self.agent_name}] Fichier instructions '{p}' non trouvé.")
            return None
        try:
            content = p.read_text(encoding='utf-8')
            logger.debug(
                f"[{self.agent_name}] Instructions base chargées: {p}")
            return content.strip() or None
        except Exception as e:
            logger.error(
                f"[{self.agent_name}] Erreur chargement instructions '{p}': {e}",
                exc_info=True)
            return None

    def _load_additional_knowledge_from_docs(self) -> str:
        parts: List[str] = []
        docs_dir = self.agent_dir / "docs"
        count = 0
        if docs_dir.is_dir():
            logger.debug(
                f"[{self.agent_name}] Recherche docs connaissances dans: {docs_dir}"
            )
            for doc_f in sorted(docs_dir.iterdir()):
                if doc_f.is_file() and doc_f.suffix.lower() in ['.txt', '.md']:
                    try:
                        content = doc_f.read_text(encoding='utf-8').strip()
                        if content:
                            parts.append(
                                f"\n\n--- Source: {doc_f.name} ---\n{content}")
                            count += 1
                    except Exception as e:
                        logger.error(
                            f"[{self.agent_name}] ERREUR lecture doc '{doc_f}': {e}",
                            exc_info=True)
            if count > 0:
                logger.info(
                    f"[{self.agent_name}] {count} doc(s) connaissance chargés."
                )
        return "".join(parts).strip()

    def _load_resources(self):
        self._config = self._load_config()
        if self._config is None:
            raise ValueError(
                f"Échec critique chargement config pour agent {self.agent_name}."
            )
        self._base_instructions = self._load_base_instructions()
        self._additional_knowledge = self._load_additional_knowledge_from_docs(
        )
        logger.debug(f"[{self.agent_name}] Chargement ressources terminé.")

    def _preprocess_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        logger.debug(f"[{self.agent_name}] Prétraitement contexte (défaut).")
        return context

    @abstractmethod
    def _prepare_llm_prompt(
        self, ctx: Dict[str, Any]
    ) -> Tuple[Optional[str], Optional[List[Dict[str, str]]], Optional[str]]:
        pass

    @abstractmethod
    def _postprocess_response(self, resp_text: Optional[str],
                              ctx: Dict[str, Any]) -> Dict[str, Any]:
        pass

    def _add_dynamic_system_instructions(
            self, cfg: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        logger.debug(
            f"[{self.agent_name}] Ajout instructions système dynamiques (défaut)."
        )
        return cfg

    def _estimate_token_count(self, messages: List[Dict[str, str]],
                              model_name_for_encoding: str) -> int:
        """Estime le nombre de tokens pour une liste de messages et un modèle donné."""
        try:
            # Tenter d'obtenir l'encodage spécifique au modèle.
            # Pour les modèles non-OpenAI, tiktoken pourrait ne pas avoir d'encodage spécifique
            # et lèvera un KeyError. Dans ce cas, on utilise un fallback.
            encoding = tiktoken.encoding_for_model(model_name_for_encoding)
        except KeyError:
            # "cl100k_base" est l'encodage pour gpt-3.5-turbo et gpt-4.
            # C'est un fallback raisonnable pour de nombreux modèles modernes.
            logger.debug(
                f"[{self.agent_name}] Encodage tiktoken spécifique non trouvé pour '{model_name_for_encoding}'. Utilisation de 'cl100k_base'."
            )
            encoding = tiktoken.get_encoding("cl100k_base")
        except Exception as e_enc:  # Autres erreurs potentielles avec tiktoken
            logger.warning(
                f"[{self.agent_name}] Erreur récupération encodage tiktoken pour '{model_name_for_encoding}': {e_enc}. Utilisation de 'cl100k_base'."
            )
            encoding = tiktoken.get_encoding("cl100k_base")

        num_tokens = 0
        for message in messages:
            num_tokens += 4  # Approximation OpenAI: chaque message ajoute ~4 tokens (pour role, name, etc.)
            for key, value in message.items():
                if value is not None:  # S'assurer que la valeur n'est pas None avant l'encodage
                    try:
                        num_tokens += len(encoding.encode(
                            str(value)))  # Convertir en str explicitement
                    except Exception as e_encode_val:
                        logger.warning(
                            f"[{self.agent_name}] Erreur encodage valeur tiktoken pour clé '{key}': {e_encode_val}. Longueur de str() utilisée."
                        )
                        num_tokens += len(
                            str(value)
                        )  # Fallback grossier si l'encodage échoue pour une valeur spécifique
        num_tokens += 2  # Chaque réponse commence par <|im_start|>assistant<|im_sep|> (approximation OpenAI)
        return num_tokens

    def _prepare_llm_call_config(self, context: Dict[str,
                                                     Any]) -> Dict[str, Any]:
        logger.debug(f"[{self.agent_name}] Préparation config appel LLM...")
        if not self._config: raise RuntimeError("Config agent non chargée.")

        model_name_cfg = self._config.get(
            "model_name",
            "").lower()  # model_name doit être préfixé dans config.yaml
        if not model_name_cfg:
            raise ValueError(
                f"[{self.agent_name}] 'model_name' manquant dans config.")

        api_key_val = None
        if api_key_env := self._config.get("api_key_env_var"):
            api_key_val = os.getenv(api_key_env)
            is_ollama = model_name_cfg.startswith(
                "ollama/") or model_name_cfg.startswith("ollama_chat/")
            if not is_ollama and not api_key_val:
                logger.warning(
                    f"[{self.agent_name}] Clé API (env: {api_key_env}) pour modèle '{model_name_cfg}' non définie."
                )

        cfg = {
            k: self._config.get(k)
            for k in [
                "model_name", "api_key_env_var", "api_base_env_var",
                "api_base", "generation_config", "safety_settings",
                "max_retries", "retry_delay", "timeout"
            ] if self._config.get(k) is not None
        }
        cfg["api_key"] = api_key_val
        cfg["generation_config"] = cfg.get("generation_config", {})
        cfg.setdefault("max_retries", 2)
        cfg.setdefault("retry_delay", 5)
        cfg.setdefault("timeout", 300)

        sys_instr = []
        if self._base_instructions: sys_instr.append(self._base_instructions)
        if self._additional_knowledge:
            sys_instr.append(
                f"\n\n--- INFORMATIONS ADDITIONNELLES ---\n{self._additional_knowledge}"
            )
        cfg["system_instructions_for_init"] = "\n".join(
            sys_instr).strip() or None

        cfg["generation_config"].pop("response_mime_type", None)
        if self.expects_json_response:
            cfg["generation_config"]["response_format"] = {
                "type": "json_object"
            }
            logger.debug(
                f"[{self.agent_name}] Mode JSON: 'response_format' réglé pour '{model_name_cfg}'."
            )
        elif "response_format" in cfg["generation_config"]:
            del cfg["generation_config"]["response_format"]
        if not cfg["generation_config"]:
            if "generation_config" in cfg: del cfg["generation_config"]

        cfg = self._add_dynamic_system_instructions(cfg, context)
        # ... (logique de log_display_config comme avant) ...
        return cfg

    def run(self, context: dict) -> Dict[str, Any]:
        logger.info(f"--- Début Exécution Agent: {self.agent_name} ---")
        final_agent_result: Optional[Dict[str, Any]] = None
        postprocess_retry_count = 0

        llm_call_cfg: Dict[str, Any] = {}
        prompt_txt: Optional[str] = None
        prompt_hist: Optional[List[Dict[str, str]]] = None
        prompt_json: Optional[str] = None
        msgs_for_llm: List[Dict[str, str]] = []

        try:
            logger.debug(
                f"[{self.agent_name}] Contexte initial (aperçu): {{k:(str(v)[:100]+'...' if isinstance(v,(str,list,dict)) and len(str(v))>100 else v) for k,v in context.items()}}"
            )
            processed_ctx = self._preprocess_context(context)
            llm_call_cfg = self._prepare_llm_call_config(processed_ctx)
            prompt_txt, prompt_hist, prompt_json = self._prepare_llm_prompt(
                processed_ctx)

            if prompt_txt is None and prompt_hist is None and prompt_json is None:
                logger.warning(
                    f"[{self.agent_name}] Aucun prompt préparé. Appel LLM annulé."
                )
                return {
                    "status": "no_action_required",
                    "message": f"[{self.agent_name}] Aucun prompt LLM généré."
                }

            msgs_for_llm = shared_utils.prepare_litellm_messages(
                system_instructions=llm_call_cfg.get(
                    "system_instructions_for_init"),
                prompt_content_text=prompt_txt,
                prompt_history_list=prompt_hist,
                prompt_content_json_str=prompt_json)

            # Lire les seuils depuis la config de l'agent ou utiliser les défauts de la classe
            warn_thresh = self._config.get(
                "token_warning_threshold",
                self.DEFAULT_TOKEN_WARNING_THRESHOLD)
            err_thresh = self._config.get("token_error_threshold",
                                          self.DEFAULT_TOKEN_ERROR_THRESHOLD)
            model_name_for_tokens = llm_call_cfg.get(
                "model_name", "unknown_model_for_tokens")

            estimated_tokens = self._estimate_token_count(
                msgs_for_llm, model_name_for_tokens)
            logger.info(
                f"[{self.agent_name}] Estimation tokens prompt (modèle: {model_name_for_tokens}): ~{estimated_tokens} tokens."
            )

            if estimated_tokens > err_thresh:
                msg = f"[{self.agent_name}] Dépassement seuil tokens d'erreur. Estimé: {estimated_tokens}, Seuil: {err_thresh}. Appel LLM annulé."
                logger.error(msg)
                return {
                    "status": "error",
                    "message": msg,
                    "estimated_tokens": estimated_tokens
                }
            elif estimated_tokens > warn_thresh:
                logger.warning(
                    f"[{self.agent_name}] Tokens estimés ({estimated_tokens}) > seuil avertissement ({warn_thresh})."
                )

        except Exception as e_prep:
            err = "ValueError" if isinstance(
                e_prep, ValueError) else "RuntimeError" if isinstance(
                    e_prep, RuntimeError) else "Exception"
            msg = f"Erreur {err} (avant boucle LLM) agent {self.agent_name}: {e_prep}"
            logger.critical(msg, exc_info=True)
            return {
                "status": "error",
                "message": msg,
                "exception_type": type(e_prep).__name__
            }

        while True:  # Boucle pour retries de post-traitement si réponse LLM malformée
            try:
                if postprocess_retry_count > 0:
                    logger.warning(
                        f"[{self.agent_name}] Tentative post-traitement #{postprocess_retry_count+1}/{self.MAX_POSTPROCESS_RETRIES+1} après réponse LLM malformée. Délai {self.POSTPROCESS_RETRY_DELAY}s..."
                    )
                    time.sleep(self.POSTPROCESS_RETRY_DELAY)

                # L'appel LLM utilise les prompts originaux, car c'est le post-traitement qui a échoué, pas le prompt.
                # shared_utils.call_llm reconstruit les messages en interne.
                llm_resp_txt = shared_utils.call_llm(
                    llm_call_config=llm_call_cfg,
                    prompt_content_text=prompt_txt,
                    prompt_history_list=prompt_hist,
                    prompt_content_json_str=prompt_json)
                final_agent_result = self._postprocess_response(
                    llm_resp_txt, processed_ctx)

                if not isinstance(final_agent_result,
                                  dict) or "status" not in final_agent_result:
                    logger.error(
                        f"[{self.agent_name}] _postprocess_response format invalide. Type: {type(final_agent_result)}. Contenu: {str(final_agent_result)[:200]}"
                    )
                    orig_mal_res = final_agent_result
                    final_agent_result = {
                        "status":
                        "error",
                        "message":
                        f"[{self.agent_name}] Erreur interne: _postprocess_response format invalide.",
                        "original_malformed_postprocess_result":
                        str(orig_mal_res)[:500]
                    }
                    break

                if final_agent_result.get(
                        "status") != "error" or not final_agent_result.get(
                            "llm_response_malformed", False):
                    break
                if postprocess_retry_count >= self.MAX_POSTPROCESS_RETRIES:
                    logger.error(
                        f"[{self.agent_name}] Max retries ({self.MAX_POSTPROCESS_RETRIES}) atteint pour réponse LLM malformée."
                    )
                    break
                postprocess_retry_count += 1
            except Exception as e_loop_run:
                err_loop = "ValueError" if isinstance(
                    e_loop_run, ValueError) else "RuntimeError" if isinstance(
                        e_loop_run, RuntimeError) else "Exception"
                msg_loop = f"Erreur {err_loop} (cycle LLM/postprocess) agent {self.agent_name}: {e_loop_run}"
                logger.critical(msg_loop, exc_info=True)
                final_agent_result = {
                    "status": "error",
                    "message": msg_loop,
                    "exception_type": type(e_loop_run).__name__
                }
                break

        if final_agent_result is None:  # Fallback de sécurité
            final_agent_result = {
                "status":
                "error",
                "message":
                f"[{self.agent_name}] Erreur interne inexpliquée, résultat final non défini."
            }

        logger.info(
            f"--- Fin Exécution Agent: {self.agent_name} (Statut: {final_agent_result.get('status', 'ERR_NO_STATUS')}) ---"
        )
        summary = {
            k: v
            for k, v in final_agent_result.items()
        }  # Copie pour modification locale
        for k, v_sum in summary.items():  # Tronquer les logs de résumé
            if isinstance(v_sum, str) and len(v_sum) > 200:
                summary[k] = v_sum[:197] + "..."
            elif isinstance(v_sum, dict) and k in [
                    "plan", "raw_llm_response_data", "original_llm_plan",
                    "original_malformed_result", "original_postprocess_result",
                    "raw_llm_response_if_parsed", "relevant_code_fragments",
                    "target_fragments_with_code"
            ]:
                summary[
                    k] = f"<{type(v_sum).__name__} '{k}' (len: {len(v_sum) if hasattr(v_sum,'__len__') else 'N/A'})>"
            elif isinstance(v_sum, list) and len(
                    str(v_sum)) > 200 and k not in [
                        "relevant_fragment_ids", "similarity_scores"
                    ]:
                summary[k] = f"<List '{k}' len={len(v_sum)}>"
        logger.debug(
            f"[{self.agent_name}] Résultat final agent (résumé): {summary}")
        return final_agent_result
