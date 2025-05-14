# code/embedding/cli.py
import argparse
from pathlib import Path

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m embedding.main",
        description="Outil pour gérer les embeddings de fragments de code (génération, mise à jour).",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "action",
        choices=["generate"], # Pour l'instant, une seule action : générer/mettre à jour
        default="generate",
        nargs="?", # "?" signifie 0 ou 1 argument, si absent, 'generate' est utilisé.
        help="Action à effectuer (actuellement seulement 'generate' pour créer/mettre à jour les embeddings)."
    )
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Force la regénération de tous les embeddings, ignorant les code_digests existants."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Active les logs de débogage détaillés."
    )
    # D'autres options pourraient être ajoutées ici (ex: --manifest-path, --output-path)
    return parser.parse_args()