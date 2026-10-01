import json
import os
from pathlib import Path
from typing import List, Set, Tuple, Union

from src.config import get_cache_paths, log_error, safe_atomic_write


def canonical_path(p: Union[str, Path]) -> str:
    """Retorna o caminho absoluto canônico normalizado em minúsculas de forma instantânea."""
    return os.path.normcase(os.path.abspath(str(p)))


def canonical_pair(p1: Union[str, Path], p2: Union[str, Path]) -> Tuple[str, str]:
    """Retorna o par ordenado canônico de dois caminhos normalizados."""
    s1 = canonical_path(p1)
    s2 = canonical_path(p2)
    return (s1, s2) if s1 < s2 else (s2, s1)


def load_ignored_pairs(root: Path) -> Set[Tuple[str, str]]:
    """Carrega o conjunto de pares de fotos marcadas como diferentes."""
    cp = get_cache_paths(root)
    p = cp.get("ignored_pairs") or (cp["meta"].parent / "ignored_pairs.json")
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {tuple(item) for item in data if len(item) == 2}
    except Exception as e:
        log_error(f"Erro ao carregar ignored_pairs de {p}", e)
    return set()


def save_ignored_pairs(root: Path, pairs: Set[Tuple[str, str]]):
    """Salva atomicamente o conjunto de pares de fotos marcadas como diferentes."""
    cp = get_cache_paths(root)
    target = cp.get("ignored_pairs") or (cp["meta"].parent / "ignored_pairs.json")
    data = sorted([list(pair) for pair in pairs])
    try:
        safe_atomic_write(target, json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        log_error(f"Erro ao salvar ignored_pairs em {target}", e)


def mark_photos_as_different(root: Path, photos: List[Union[str, Path]], existing_pairs: Set[Tuple[str, str]] = None) -> Set[Tuple[str, str]]:
    """
    Marca todas as combinações par-a-par do grupo de fotos fornecido como 'diferentes'
    e persiste a alteração em disco.
    """
    if existing_pairs is None:
        existing_pairs = load_ignored_pairs(root)

    n = len(photos)
    for i in range(n):
        for j in range(i + 1, n):
            pair = canonical_pair(photos[i], photos[j])
            existing_pairs.add(pair)

    save_ignored_pairs(root, existing_pairs)
    return existing_pairs
