# code/manifest/ast_interface.py
import subprocess
import json
from pathlib import Path
import sys
import os
import shutil
import logging

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler()) # Evite msg si non configuré

try:
    CURRENT_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = CURRENT_DIR.parent # manifest -> code
except Exception as e:
     print(f"Erreur critique [AST Interface Init]: {e}", file=sys.stderr)
     PROJECT_ROOT = Path.cwd()

AST_PARSER_BIN_DIR = CURRENT_DIR / "bin"
AST_PARSER_PATHS = [ AST_PARSER_BIN_DIR / "ast_parser.exe", AST_PARSER_BIN_DIR / "ast_parser", PROJECT_ROOT / "ast_parser.exe", PROJECT_ROOT / "ast_parser", ]

def find_ast_parser() -> str | None:
    logger.debug(f"Recherche ast_parser: {[str(p) for p in AST_PARSER_PATHS]}")
    for p in AST_PARSER_PATHS:
        try:
            if p.exists() and p.is_file() and os.access(p, os.X_OK):
                parser_path = str(p.resolve()); logger.debug(f"ast_parser trouvé: {parser_path}"); return parser_path
        except OSError as e_os: logger.debug(f"Erreur accès OS {p}: {e_os}")
        except Exception as e: logger.warning(f"Erreur vérif chemin {p}: {e}", exc_info=True)
    logger.error("Exécutable 'ast_parser' non trouvé/exécutable.")
    logger.error("Compilez-le (cd code/manifest/bin && go build ...) et vérifiez permissions.")
    return None

def run_ast_parser(go_project_path: Path) -> dict | None:
    parser_exe = find_ast_parser()
    if not parser_exe: return None
    logger.info(f"Exécution analyse AST via: {parser_exe}"); logger.info(f"Sur projet: {go_project_path}")
    cmd = [parser_exe, str(go_project_path)]; logger.debug(f"Commande AST: {' '.join(cmd)}")
    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=180, encoding='utf-8', errors='ignore')
        logger.info("Analyse AST externe succès."); stdout = process.stdout.strip()
        if not stdout: logger.error("Sortie analyseur AST vide."); return None
        try: logger.debug(f"Sortie AST (début): {stdout[:500]}..."); return json.loads(stdout)
        except json.JSONDecodeError as e: logger.error(f"Parsing JSON sortie AST échoué: {e}", exc_info=True); logger.error(f"Sortie AST (tronquée):\n{stdout[:2000]}..."); return None
    except subprocess.CalledProcessError as e:
        logger.error(f"Exécution analyseur AST échouée (code: {e.returncode}):"); stderr_output = e.stderr.strip() if e.stderr else "<N/A>"; logger.error(f"Stderr ast_parser:\n{stderr_output}"); return None
    except subprocess.TimeoutExpired: logger.error("Timeout analyse AST (180s)."); return None
    except FileNotFoundError: logger.error(f"Exécutable ast_parser non trouvé: {parser_exe}"); return None
    except Exception as e: logger.critical(f"Erreur inattendue analyseur AST: {e}", exc_info=True); return None

if __name__ == "__main__":
     logging.basicConfig(level=logging.DEBUG, format='%(asctime)s-%(levelname)s-[%(name)s]-%(message)s')
     logger.info("Module ast_interface.py exécuté."); parser_loc = find_ast_parser(); logger.info(f"ast_parser: {parser_loc}")