# code/lib/utils.py

import json
import os
import shutil
import subprocess
import time
import sys
import re 
from pathlib import Path
from typing import Tuple, Optional, Set, Dict, Any, List, Union
import asyncio
import litellm
import traceback
import logging

logger = logging.getLogger(__name__)

try:
    PROJECT_ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(PROJECT_ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT_DIR))
    import global_config
    litellm.suppress_debug_info = True  # Pour la production
except ImportError as e:
    print(f"Erreur critique [Shared Utils Init]: Import échoué: {e}",
          file=sys.stderr)
    if 'litellm' in str(e).lower():
        print(
            "  -> Assurez-vous que 'litellm' est installé: 'pip install litellm'",
            file=sys.stderr)
    print(f"  PYTHONPATH actuel: {sys.path}", file=sys.stderr)
    sys.exit(2)
except Exception as e:
    print(f"Erreur inattendue à l'initialisation [Shared Utils]: {e}",
          file=sys.stderr)
    sys.exit(2)


def setup_logging(debug_mode: bool = False,
                  log_file: Optional[Union[str, Path]] = None):
    root_log_level = logging.DEBUG if debug_mode else logging.INFO
    log_level_console = root_log_level
    log_level_file = logging.DEBUG

    log_format = '%(asctime)s - %(levelname)-8s - [%(name)s:%(lineno)d] - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        for handler in root_logger.handlers[:]:
            if not (isinstance(handler, logging.StreamHandler)
                    and handler.stream in [sys.stdout, sys.stderr]):
                try:
                    handler.close()
                except Exception as e_close:
                    print(
                        f"Avertissement: Échec fermeture handler log: {e_close}",
                        file=sys.stderr)
            root_logger.removeHandler(handler)

    root_logger.setLevel(root_log_level)
    handlers_to_add = []
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level_console)
    console_handler.setFormatter(
        logging.Formatter(log_format, datefmt=date_format))
    handlers_to_add.append(console_handler)

    if log_file:
        try:
            log_path = Path(log_file).resolve()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_path,
                                               mode='a',
                                               encoding='utf-8')
            file_handler.setLevel(log_level_file)
            file_handler.setFormatter(
                logging.Formatter(log_format, datefmt=date_format))
            handlers_to_add.append(file_handler)
            init_msg_file = f"Logging fichier configuré vers: {log_path} (Niveau >= {logging.getLevelName(log_level_file)})"
            print(init_msg_file, file=sys.stderr)
            file_handler.handle(
                logging.LogRecord(name="setup_logging",
                                  level=logging.INFO,
                                  pathname="",
                                  lineno=0,
                                  msg=init_msg_file,
                                  args=(),
                                  exc_info=None,
                                  func=""))
        except Exception as e:
            print(
                f"ERREUR: Impossible configurer logging fichier vers {log_file}: {e}",
                file=sys.stderr)

    for handler in handlers_to_add:
        root_logger.addHandler(handler)
    console_level_name = logging.getLevelName(log_level_console)
    file_level_name = logging.getLevelName(log_level_file) if log_file and any(
        isinstance(h, logging.FileHandler)
        for h in handlers_to_add) else 'Non activé'
    logger.info(
        f"Logging configuré. Console >= {console_level_name}, Fichier >= {file_level_name}"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    litellm_logger = logging.getLogger("LiteLLM")
    litellm_log_level = logging.INFO if not debug_mode else logging.DEBUG
    litellm_logger.setLevel(litellm_log_level)
    logger.debug(
        f"Logger 'LiteLLM' configuré: niveau={logging.getLevelName(litellm_log_level)}, propagation={litellm_logger.propagate}"
    )


def prepare_litellm_messages(
        system_instructions: Optional[str] = None,
        prompt_content_text: Optional[str] = None,
        prompt_history_list: Optional[List[Dict[str, str]]] = None,
        prompt_content_json_str: Optional[str] = None) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    system_added = False
    if system_instructions and isinstance(system_instructions,
                                          str) and system_instructions.strip():
        messages.append({
            "role": "system",
            "content": system_instructions.strip()
        })
        system_added = True
    if prompt_history_list and isinstance(prompt_history_list, list):
        for entry in prompt_history_list:
            if isinstance(entry, dict) and isinstance(
                    entry.get("role"), str) and isinstance(
                        entry.get("content"), str):
                role = "assistant" if entry["role"].lower(
                ) == "model" else entry["role"].lower()
                if role in ["user", "assistant", "system"]:
                    messages.append({
                        "role": role,
                        "content": entry["content"]
                    })
                else:
                    logger.warning(
                        f"Rôle invalide '{entry['role']}' dans historique ignoré."
                    )
            else:
                logger.warning(
                    f"Entrée d'historique invalide ignorée: {str(entry)[:100]}"
                )
    if prompt_content_text is not None and isinstance(
            prompt_content_text, str) and prompt_content_text.strip():
        messages.append({"role": "user", "content": prompt_content_text})
    elif prompt_content_json_str is not None and isinstance(
            prompt_content_json_str, str) and prompt_content_json_str.strip():
        messages.append({
            "role":
            "user",
            "content":
            f"Veuillez traiter les données JSON suivantes :\n```json\n{prompt_content_json_str}\n```"
        })
    has_user_or_assistant_message = any(m['role'] in ['user', 'assistant']
                                        for m in messages)
    if not has_user_or_assistant_message:
        if system_added and len(messages) == 1:
            logger.warning("Prompt contient uniquement instructions système.")
        elif not system_added:
            raise ValueError("Aucun contenu de prompt valide fourni.")
    logger.debug(
        f"Messages préparés pour LiteLLM (nombre={len(messages)}). Premier rôle: {messages[0]['role'] if messages else 'Aucun'}."
    )
    return messages


def prepare_litellm_kwargs(llm_call_config: Dict[str, Any]) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    model_name_from_config = llm_call_config.get("model_name")
    if not model_name_from_config:
        raise ValueError("Config LLM: 'model_name' requis.")
    kwargs["model"] = model_name_from_config
    logger.debug(
        f"Utilisation model_name pour LiteLLM depuis config: '{kwargs['model']}'"
    )
    api_key = llm_call_config.get("api_key")
    if api_key: kwargs["api_key"] = api_key
    api_base_url = llm_call_config.get("api_base")
    if not api_base_url and (api_base_env_var :=
                             llm_call_config.get("api_base_env_var")):
        api_base_url = os.getenv(api_base_env_var)
    if api_base_url: kwargs["api_base"] = api_base_url.strip()
    elif (kwargs["model"].startswith("ollama/") or kwargs["model"].startswith("ollama_chat/")) and \
         not os.getenv("OLLAMA_API_BASE") and not kwargs.get("api_base"):
        logger.warning(
            f"Modèle Ollama '{kwargs['model']}' mais aucune 'api_base' trouvée. LiteLLM utilisera ses défauts."
        )
    gen_config = llm_call_config.get("generation_config")
    if isinstance(gen_config, dict):
        for key, value in gen_config.items():
            if value is not None: kwargs[key] = value
    if safety_settings := llm_call_config.get("safety_settings"):
        if isinstance(safety_settings,
                      list) and kwargs["model"].startswith("gemini/"):
            kwargs["safety_settings"] = safety_settings
    try:
        kwargs["timeout"] = int(llm_call_config.get("timeout", 300))
    except (ValueError, TypeError):
        kwargs["timeout"] = 300
    try:
        kwargs["num_retries"] = int(llm_call_config.get("max_retries", 2))
    except (ValueError, TypeError):
        kwargs["num_retries"] = 2
    log_display_kwargs = {k: v for k, v in kwargs.items() if k != 'api_key'}
    for k_log, v_log in log_display_kwargs.items():
        if isinstance(v_log, str) and len(v_log) > 100:
            log_display_kwargs[k_log] = v_log[:100] + "..."
        elif isinstance(v_log, (dict, list)) and (len(str(v_log)) > 100
                                                  or k_log == "messages"):
            log_display_kwargs[
                k_log] = f"<{type(v_log).__name__} len={len(v_log) if hasattr(v_log,'__len__') else 'N/A'}>"
    logger.debug(
        f"Paramètres (kwargs) préparés pour litellm.completion(): {log_display_kwargs}"
    )
    return kwargs


def prepare_embedding_call_kwargs(
        embedding_service_config: Dict[str, Any]) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    model_name_from_config = embedding_service_config.get("model_name")
    if not model_name_from_config:
        raise ValueError("Config embedding: 'model_name' requis.")
    kwargs["model"] = model_name_from_config
    logger.debug(
        f"Utilisation model_name pour embedding LiteLLM depuis config: '{kwargs['model']}'"
    )
    api_key = None
    if api_key_env_var := embedding_service_config.get("api_key_env_var"):
        api_key = os.getenv(api_key_env_var)
        if not kwargs["model"].lower().startswith("ollama/") and not api_key:
            logger.warning(
                f"Clé API (env: {api_key_env_var}) pour modèle embedding '{kwargs['model']}' non trouvée."
            )
    if api_key: kwargs["api_key"] = api_key
    api_base_url = embedding_service_config.get("api_base")
    if not api_base_url and (api_base_env_var :=
                             embedding_service_config.get("api_base_env_var")):
        api_base_url = os.getenv(api_base_env_var)
    if api_base_url:
        kwargs["api_base"] = api_base_url.strip()
        logger.debug(f"Utilisation api_base pour embedding: {api_base_url}")
    elif kwargs["model"].lower().startswith("ollama/") and not os.getenv(
            "OLLAMA_API_BASE") and not kwargs.get("api_base"):
        logger.warning(
            f"Modèle embedding Ollama '{kwargs['model']}' mais aucune 'api_base' configurée."
        )
    try:
        kwargs["timeout"] = int(embedding_service_config.get("timeout", 60))
    except (ValueError, TypeError):
        kwargs["timeout"] = 60
    log_display_kwargs = {k: v for k, v in kwargs.items() if k != 'api_key'}
    logger.debug(
        f"Paramètres préparés pour litellm.embedding(): {log_display_kwargs}")
    return kwargs


def _handle_litellm_response(response_object: Any, is_json_expected: bool,
                             call_type: str) -> Optional[str]:
    if not response_object:
        logger.warning(f"Réponse LiteLLM ({call_type}) None ou vide.")
        return None
    try:
        message_content = response_object.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        message_content = None
    if message_content:
        response_text = str(message_content).strip()
        logger.debug(
            f"Réponse {call_type} brute LLM (début): {response_text[:150]}...")
        if is_json_expected:
            cleaned_json = response_text
            if not response_text.startswith("{") or not response_text.endswith(
                    "}"):
                s_idx, e_idx = response_text.find('{'), response_text.rfind(
                    '}')
                if s_idx != -1 and e_idx != -1 and e_idx > s_idx:
                    cleaned_json = response_text[s_idx:e_idx + 1]
                    logger.debug(
                        f"Extraction JSON potentiel: '{cleaned_json[:100]}...'"
                    )
            try:
                json.loads(cleaned_json)
                logger.debug(f"Réponse {call_type} validée comme JSON.")
                return cleaned_json
            except json.JSONDecodeError as e:
                logger.error(
                    f"Réponse {call_type} attendue JSON invalide: {e}\nTexte (tronqué): {cleaned_json[:1000]}"
                )
                return None
        else:
            logger.debug(f"Réponse texte {call_type} retournée.")
            return response_text
    else:
        reason, block_msg = "Inconnu", ""
        try:
            if response_object.choices and response_object.choices[0]:
                reason = response_object.choices[0].finish_reason
                if hasattr(response_object.choices[0], 'finish_details'
                           ) and response_object.choices[0].finish_details:
                    fd = response_object.choices[0].finish_details
                    if isinstance(fd, dict) and fd.get(
                            "type") == "blocked" and fd.get("reason"):
                        block_msg += f", Blocage Fin: {fd.get('reason')}"
            if hasattr(response_object, 'prompt_feedback') and response_object.prompt_feedback and \
               hasattr(response_object.prompt_feedback, 'block_reason') and response_object.prompt_feedback.block_reason:
                block_msg = f", Blocage Prompt: {response_object.prompt_feedback.block_reason}"
        except:
            pass
        logger.warning(
            f"Réponse LiteLLM {call_type} invalide/vide/bloquée. Raison: {reason}{block_msg}."
        )
        logger.debug(
            f"Objet réponse {call_type} (diagnostic): {response_object}")
        return None


def _handle_litellm_exception(e: Exception, api_kwargs_used: Dict,
                              call_type: str) -> None:
    err_type = type(e).__name__
    model = api_kwargs_used.get('model', 'N/A')
    api_base = api_kwargs_used.get('api_base', 'N/A')
    logger.error(
        f"Erreur '{err_type}' appel {call_type} LiteLLM modèle '{model}' (Base: '{api_base}'). Erreur: {e}"
    )
    if isinstance(e, litellm.exceptions.APIConnectionError):
        logger.error(
            f"  -> Problème connexion API. Vérifiez URL ('{api_base}') et dispo."
        )
    elif isinstance(e, litellm.exceptions.BadRequestError):
        logger.error(
            f"  -> Mauvaise requête. Vérifiez params ('{model}'), prompt, ou config."
        )
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            logger.error(f"  -> Détail serveur: {e.response.text[:500]}...")
    elif isinstance(e, litellm.exceptions.AuthenticationError):
        logger.error(
            f"  -> Erreur authentification. Vérifiez clé API pour provider de '{model}'."
        )
    elif isinstance(e, litellm.exceptions.Timeout):
        logger.error(
            f"  -> Timeout ({getattr(e, 'timeout', api_kwargs_used.get('timeout', 'N/A'))}s) pour '{model}'."
        )
    elif isinstance(e, litellm.exceptions.RateLimitError):
        logger.error(f"  -> Rate Limit atteint pour '{model}'.")
    elif isinstance(e, litellm.exceptions.ServiceUnavailableError):
        logger.error(f"  -> Service indisponible pour '{model}'.")
    else:
        logger.exception(f"Exception inattendue appel {call_type} LiteLLM:")


def call_llm(*,
             llm_call_config: Dict[str, Any],
             prompt_content_text: Optional[str] = None,
             prompt_history_list: Optional[List[Dict[str, str]]] = None,
             prompt_content_json_str: Optional[str] = None) -> Optional[str]:
    logger.debug("Appel synchrone call_llm...")
    kwargs = {}
    resp_obj = None
    try:
        msgs = prepare_litellm_messages(
            llm_call_config.get("system_instructions_for_init"),
            prompt_content_text, prompt_history_list, prompt_content_json_str)
        kwargs = prepare_litellm_kwargs(llm_call_config)
        kwargs["messages"] = msgs
        is_json = isinstance(
            kwargs.get("response_format"),
            dict) and kwargs["response_format"].get("type") == "json_object"
        model_name = kwargs.get('model', 'Inconnu')
        logger.info(f"-> Appel API Sync LiteLLM (Modèle: {model_name})...")
        resp_obj = litellm.completion(**kwargs)
        logger.info(f"<- Réponse API Sync reçue de {model_name}.")
        return _handle_litellm_response(resp_obj, is_json, "sync")
    except ValueError as ve:
        logger.error(f"Erreur valeur prépa appel LLM sync: {ve}",
                     exc_info=True)
        return None
    except Exception as e:
        _handle_litellm_exception(e, kwargs, "sync")
        return None


async def async_call_llm(
        *,
        llm_call_config: Dict[str, Any],
        prompt_content_text: Optional[str] = None,
        prompt_history_list: Optional[List[Dict[str, str]]] = None,
        prompt_content_json_str: Optional[str] = None) -> Optional[str]:
    logger.debug("Appel asynchrone async_call_llm...")
    kwargs = {}
    resp_obj = None
    try:
        msgs = prepare_litellm_messages(
            llm_call_config.get("system_instructions_for_init"),
            prompt_content_text, prompt_history_list, prompt_content_json_str)
        kwargs = prepare_litellm_kwargs(llm_call_config)
        kwargs["messages"] = msgs
        is_json = isinstance(
            kwargs.get("response_format"),
            dict) and kwargs["response_format"].get("type") == "json_object"
        model_name = kwargs.get('model', 'Inconnu')
        logger.info(f"-> Appel API Async LiteLLM (Modèle: {model_name})...")
        resp_obj = await litellm.acompletion(**kwargs)
        logger.info(f"<- Réponse API Async reçue de {model_name}.")
        return _handle_litellm_response(resp_obj, is_json, "async")
    except ValueError as ve:
        logger.error(f"Erreur valeur prépa appel LLM async: {ve}",
                     exc_info=True)
        return None
    except Exception as e:
        _handle_litellm_exception(e, kwargs, "async")
        return None


def print_stage_header(title: str):
    width = len(title) + 6
    logger.info("=" * width)
    logger.info(f"== {title.upper()} ==")
    logger.info("=" * width)


def load_fragments_manifest(workspace_path: Path) -> Optional[Dict[str, Any]]:
    if not isinstance(workspace_path, Path):
        try:
            workspace_path = Path(workspace_path)
        except TypeError:
            logger.error(
                f"Chemin workspace invalide ('{workspace_path}'): {type(workspace_path)}"
            )
            return None
    manifest_path = workspace_path / "fragments_manifest.json"
    logger.info(f"Tentative chargement manifeste: {manifest_path}")
    if not manifest_path.is_file():
        logger.error(f"Manifeste introuvable: '{manifest_path}'.")
        return None
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data,
                          dict) or "fragments" not in data or not isinstance(
                              data["fragments"], dict):
            logger.error(
                f"Structure manifeste invalide dans '{manifest_path}'.")
            return None
        logger.info(
            f"Manifeste chargé ({len(data.get('fragments', {}))} fragments).")
        logger.debug(f"Contenu manifeste (aperçu): {str(data)[:500]}...")
        return data
    except json.JSONDecodeError as e:
        logger.error(f"Erreur parsing JSON manifeste '{manifest_path}': {e}",
                     exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Erreur lecture manifeste '{manifest_path}': {e}",
                     exc_info=True)
        return None


def backup_files(relative_file_paths: List[str], backup_dir_path: Path,
                 source_base_dir: Path):
    if not relative_file_paths:
        logger.debug("backup_files: aucun fichier à sauvegarder.")
        return
    logger.info(
        f"Sauvegarde {len(relative_file_paths)} fichier(s) dans: {backup_dir_path}"
    )
    try:
        backup_dir_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.critical(
            f"Backup: Impossible créer dossier '{backup_dir_path}': {e}",
            exc_info=True)
        raise
    backed_up = 0
    for rel_path in relative_file_paths:
        if not isinstance(rel_path, str) or not rel_path.strip():
            logger.warning(f"Backup: Chemin relatif invalide: '{rel_path}'")
            continue
        try:
            clean_path = Path(rel_path).as_posix()
            if ".." in clean_path:
                logger.error(
                    f"Backup: Chemin suspect '..' ignoré: '{rel_path}'")
                continue
        except:
            logger.error(
                f"Backup: Échec normalisation chemin: '{rel_path}'. Ignoré.")
            continue
        src_file = source_base_dir / clean_path
        bak_name = clean_path.replace('/', '_').replace('\\', '_').replace(
            '.', '_dot_') + ".bak"
        bak_file = backup_dir_path / bak_name
        if src_file.is_file():
            try:
                shutil.copy2(src_file, bak_file)
                backed_up += 1
                logger.debug(f"Backup: '{src_file}' -> '{bak_file.name}'")
            except Exception as e:
                logger.error(
                    f"Backup: Erreur sauvegarde '{src_file}' -> '{bak_file.name}': {e}",
                    exc_info=True)
        else:
            logger.warning(
                f"Backup: Fichier source non trouvé: '{src_file}'. Ignoré.")
    logger.info(
        f"{backed_up}/{len(relative_file_paths)} fichier(s) sauvegardé(s) dans '{backup_dir_path.name}'."
    )


def run_build_command(command: str, project_dir: Path) -> Tuple[bool, str]:
    logger.info(f"Exécution dans '{project_dir}': $ {command}")
    if not project_dir.is_dir():
        err = "Répertoire build/run introuvable"
        logger.error(err + f": {project_dir}")
        return False, err
    try:
        proc = subprocess.run(command,
                              shell=True,
                              cwd=str(project_dir),
                              capture_output=True,
                              text=True,
                              encoding='utf-8',
                              errors='replace',
                              timeout=300)
        out_log = f"-- STDOUT --\n{proc.stdout or '<Aucun>'}\n-- STDERR --\n{proc.stderr or '<Aucun>'}"
        logger.debug(f"Sortie commande (code={proc.returncode}):\n{out_log}")
        if proc.returncode != 0:
            logger.error(
                f"Échec commande Build/Run (code={proc.returncode}) dans {project_dir}"
            )
            err_out = f"Code retour: {proc.returncode}\n"
            if proc.stderr and proc.stderr.strip():
                err_out += f"STDERR:\n{proc.stderr.strip()}\n"
                logger.error(
                    f"Stderr (extrait):\n{proc.stderr.strip()[:1000]}...")
            if proc.stdout and proc.stdout.strip() and not (
                    proc.stderr and proc.stderr.strip()):
                logger.error(
                    f"Stdout (stderr vide, extrait):\n{proc.stdout.strip()[:1000]}..."
                )
                err_out += f"STDOUT:\n{proc.stdout.strip()}"
            return False, err_out.strip() if err_out.strip(
            ) else f"Processus terminé code {proc.returncode} sans sortie."
        logger.info("Commande Build/Run exécutée avec succès.")
        return True, proc.stdout.strip(
        ) if proc.stdout else "Succès sans sortie stdout."
    except FileNotFoundError:
        err = "Commande ou shell introuvable"
        logger.critical(err + f" pour '{command}'.", exc_info=True)
        return False, err
    except subprocess.TimeoutExpired as e:
        logger.error(
            f"Timeout ({e.timeout}s) commande '{command}' dans {project_dir}.",
            exc_info=True)
        part_out = (e.stdout or "") + "\n" + (e.stderr or "")
        logger.error(
            f"Sortie partielle (limitée):\n{part_out.strip()[:1000]}...")
        return False, f"Timeout ({e.timeout}s) pour '{command}'. Sortie partielle:\n{part_out.strip()}"
    except Exception as e:
        logger.critical(
            f"Erreur exécution commande '{command}' dans {project_dir}: {e}",
            exc_info=True)
        return False, f"Erreur exécution '{command}': {e}"


def format_go_code(raw_code_string: str) -> Tuple[str, Optional[str]]:
    fmt_path = shutil.which("goimports") or shutil.which("gofmt")
    if not fmt_path:
        msg = "Ni 'goimports' ni 'gofmt' trouvés. Code Go non formaté."
        logger.warning(msg)
        return raw_code_string, msg
    fmt_name = Path(fmt_path).name
    logger.debug(f"Utilisation formateur Go: {fmt_name}")
    ws_path = getattr(global_config, 'WORKSPACE_PATH', None)
    if not isinstance(ws_path, Path):
        msg = f"WORKSPACE_PATH invalide. Formatage Go impossible."
        logger.error(msg)
        return raw_code_string, msg
    tmp_dir = ws_path / "tmp_go_format"
    formatted_code = raw_code_string
    err_msg: Optional[str] = None
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        msg = f"Impossible créer dossier tmp '{tmp_dir}': {e}"
        logger.error(msg, exc_info=True)
        return raw_code_string, msg
    tmp_file = tmp_dir / f"tmp_fmt_{os.urandom(6).hex()}.go"
    try:
        tmp_file.write_text(raw_code_string, encoding='utf-8')
        proc = subprocess.run([fmt_path, "-w", str(tmp_file)],
                              capture_output=True,
                              text=True,
                              encoding='utf-8',
                              errors='replace',
                              timeout=15)
        if proc.returncode == 0:
            formatted_code = tmp_file.read_text(encoding='utf-8')
            logger.debug(f"Formatage Go {fmt_name} réussi.")
        else:
            if tmp_file.exists():
                formatted_code = tmp_file.read_text(encoding='utf-8')
            fmt_err_details = (proc.stderr.strip() if proc.stderr else
                               "") or (proc.stdout.strip() if proc.stdout else
                                       "Aucune sortie d'erreur formateur.")
            err_msg = f"Échec formateur Go '{fmt_name}' (code {proc.returncode}): {fmt_err_details}"
            logger.warning(f"Formatage Go: {err_msg}")
    except subprocess.TimeoutExpired:
        err_msg = f"Timeout formatage Go {fmt_name}."
        logger.warning(err_msg)
    except Exception as e:
        err_msg = f"Erreur formatage Go {fmt_name}: {e}"
        logger.exception(err_msg)
    finally:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
                logger.debug(f"Fichier tmp formatage Go supprimé: {tmp_file}")
            except OSError as e:
                logger.warning(
                    f"Impossible supprimer fichier tmp formatage Go {tmp_file}: {e}"
                )
    return formatted_code, err_msg


def restore_from_backup(backup_dir_path: Path, target_project_dir: Path,
                        relative_files_to_restore: List[str]):
    logger.info(f"Tentative restauration depuis backup: {backup_dir_path}")
    if not backup_dir_path.is_dir():
        logger.warning(
            f"Dossier backup non trouvé: {backup_dir_path}. Aucune restauration."
        )
        return
    if not relative_files_to_restore:
        logger.info("Restauration: Aucun fichier spécifié.")
        return
    restored = 0
    for rel_path in relative_files_to_restore:
        if not isinstance(rel_path, str) or not rel_path.strip():
            logger.warning(
                f"Restauration: Chemin relatif invalide: '{rel_path}'")
            continue
        try:
            clean_path = Path(rel_path).as_posix()
            if ".." in clean_path:
                logger.error(
                    f"Restauration: Chemin suspect '..' ignoré: '{rel_path}'")
                continue
        except:
            logger.error(
                f"Restauration: Échec normalisation chemin: '{rel_path}'. Ignoré."
            )
            continue
        target_file = target_project_dir / clean_path
        bak_name = clean_path.replace('/', '_').replace('\\', '_').replace(
            '.', '_dot_') + ".bak"
        bak_file = backup_dir_path / bak_name
        if bak_file.is_file():
            try:
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bak_file, target_file)
                logger.debug(
                    f"Fichier restauré: '{bak_file.name}' -> '{target_file}'")
                restored += 1
            except Exception as e:
                logger.error(
                    f"Erreur restauration '{bak_file.name}' -> '{target_file}': {e}",
                    exc_info=True)
        else:
            logger.warning(
                f"Restauration: Fichier backup non trouvé: '{bak_file.name}'. Fichier '{target_file}' non restauré."
            )
    logger.info(
        f"Restauration terminée. {restored}/{len(relative_files_to_restore)} fichier(s) tentés/restaurés."
    )


def extract_function_body(filepath: Path, start_line: int,
                          end_line: int) -> Optional[str]:
    if not filepath.is_file():
        logger.warning(
            f"extract_function_body: Fichier source non trouvé: {filepath}")
        return None
    try:
        lines = filepath.read_text(encoding='utf-8',
                                   errors='ignore').splitlines(keepends=True)
        start_idx, end_idx = start_line - 1, end_line
        if not (0 <= start_idx < len(lines)
                and start_idx < end_idx <= len(lines)):
            logger.warning(
                f"extract_function_body: Lignes invalides ({start_line}-{end_line}) pour '{filepath.name}' ({len(lines)} lignes)."
            )
            return None
        extracted = "".join(lines[start_idx:end_idx])
        logger.debug(
            f"extract_function_body: Extrait {len(extracted)} chars de {filepath.name} (lignes {start_line}-{end_line})."
        )
        return extracted
    except Exception as e:
        logger.error(
            f"extract_function_body: Erreur lecture/traitement '{filepath.name}': {e}",
            exc_info=True)
        return None