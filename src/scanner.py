import os
from pathlib import Path

from src.config import IMAGE_EXTS, is_path_excluded, log_error, APP_CONFIG


def collect_images(folder_or_folders, excluded_folders=None):
    """
    Varre recursivamente o(s) diretório(s) especificado(s) retornando lista de arquivos de imagem,
    ignorando e podando diretórios e subdiretórios configurados para exclusão.
    """
    if isinstance(folder_or_folders, (list, tuple, set)):
        folders = [Path(f).expanduser().resolve() for f in folder_or_folders]
    else:
        folders = [Path(folder_or_folders).expanduser().resolve()]

    paths = []
    seen = set()
    rules = excluded_folders if excluded_folders is not None else APP_CONFIG.get("excluded_folders", [])

    for root in folders:
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                # Poda diretórios excluídos na raiz da árvore para economizar I/O e tempo
                dirnames_to_keep = []
                for d in dirnames:
                    d_path = Path(dirpath) / d
                    if is_path_excluded(d_path, rules, root):
                        continue
                    dirnames_to_keep.append(d)
                dirnames[:] = dirnames_to_keep

                for f in filenames:
                    p = Path(dirpath) / f
                    if p.suffix.lower() in IMAGE_EXTS:
                        if not is_path_excluded(p, rules, root):
                            p_res = p.resolve()
                            if p_res not in seen:
                                seen.add(p_res)
                                paths.append(p)
        except Exception as e:
            log_error(f"Erro ao listar arquivos em {root}", e)

    return sorted(paths)
