import faulthandler
import hashlib
import json
import logging
import os
import platform
import sys
import threading
import time
from pathlib import Path

if getattr(sys, "frozen", False):
    PROGRAM_DIR = Path(sys.executable).resolve().parent
else:
    PROGRAM_DIR = Path(__file__).resolve().parent.parent

CONFIG_FILE = PROGRAM_DIR / "config_app.json"
LOG_FILE = PROGRAM_DIR / "comparador_erros.log"

DEFAULT_CONFIG = {
    "cache_dir": str((PROGRAM_DIR / "cache").resolve()),
    "max_ram_pct": 80,
    "cpu_workers": os.cpu_count() or 8,
    "batch_size": 48,
    "clip_threshold": 0.82,
    "last_folder": "",
    "last_folders": [],
    "enable_clip": True,
    "enable_hash": True,
    "enable_web_detector": True,
    "confirm_delete_2s": True,
    "max_active_groups": 0,
    "excluded_folders": [
        {"path": "WhatsApp Stickers", "include_subdirs": True, "enabled": True},
        {"path": "WhatsApp Animated Gifs", "include_subdirs": True, "enabled": True},
        {"path": ".thumbnails", "include_subdirs": True, "enabled": True},
        {"path": ".trash", "include_subdirs": True, "enabled": True},
    ],
    "log_level": "DEBUG",
    "shadow_removal_mode": "deep",
    "github_repo": "Guilherme-ai/comparador_fotos",
    "check_updates_on_startup": True,
}

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp",
    ".tif", ".tiff", ".heic", ".heif"
}

IGNORED_DIRS = {
    "$recycle.bin", "system volume information", ".git", ".idea",
    ".trash", "node_modules", "__pycache__",
    "whatsapp backup excluded stickers", "whatsapp business backup excluded stickers"
}


def cleanup_old_logs(max_age_days=3, max_size_bytes=5 * 1024 * 1024):
    """
    Remove arquivos de log antigos e limpa entradas obsoletas ao abrir o aplicativo.
    - Exclui arquivos de log temporários ou rotacionados (*.log.*, *.bak) com mais de max_age_days dias.
    - No arquivo principal comparador_erros.log:
      * Se o arquivo for mais antigo que max_age_days dias, trunca/apaga o conteúdo obsoleto.
      * Se contiver linhas com datas anteriores a max_age_days dias, filtra e remove apenas as entradas antigas.
      * Se exceder max_size_bytes, mantém as últimas 5.000 linhas mais recentes.
    """
    now = time.time()
    cutoff_time = now - (max_age_days * 86400)
    cutoff_date_str = time.strftime("%Y-%m-%d", time.localtime(cutoff_time))

    # 1. Limpa arquivos secundários/rotacionados de log no PROGRAM_DIR
    try:
        for p in PROGRAM_DIR.glob("*.log*"):
            if p.is_file() and p.name != LOG_FILE.name:
                try:
                    if p.stat().st_mtime < cutoff_time:
                        p.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception:
        pass

    # 2. Limpa e filtra o comparador_erros.log principal
    if LOG_FILE.exists() and LOG_FILE.is_file():
        try:
            st = LOG_FILE.stat()
            # Se o arquivo foi modificado antes do período de retenção, trunca para reiniciar limpo
            if st.st_mtime < cutoff_time:
                try:
                    LOG_FILE.write_text("", encoding="utf-8")
                    return
                except Exception:
                    pass

            # Lê o conteúdo para filtrar entradas por data
            try:
                content = LOG_FILE.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines(keepends=True)
            except Exception:
                lines = []

            if lines:
                new_lines = []
                keep_line = True
                for line in lines:
                    # Formato: [YYYY-MM-DD HH:MM:SS.mmm] ...
                    if line.startswith("[") and len(line) >= 11 and line[5] == "-" and line[8] == "-":
                        log_date = line[1:11]
                        keep_line = (log_date >= cutoff_date_str)
                    if keep_line:
                        new_lines.append(line)

                # Se exceder o tamanho limite, mantém as 5000 linhas mais recentes
                if len(new_lines) > 5000:
                    new_lines = new_lines[-5000:]

                # Se houve remoção de linhas antigas ou redução de tamanho, reescreve
                if len(new_lines) < len(lines) or st.st_size > max_size_bytes:
                    LOG_FILE.write_text("".join(new_lines), encoding="utf-8")
        except Exception:
            pass


def clear_log_file():
    """Limpa completamente o arquivo comparador_erros.log."""
    try:
        for handler in logger.handlers:
            if isinstance(handler, logging.FileHandler):
                try:
                    handler.flush()
                except Exception:
                    pass
        LOG_FILE.write_text("", encoding="utf-8")
        return True
    except Exception as e:
        log_error("Erro ao limpar arquivo de log", e)
        return False


class AutoFlushFileHandler(logging.FileHandler):
    """FileHandler que garante flush imediato em disco a cada mensagem de log para evitar perda de dados em crashes."""
    def emit(self, record):
        try:
            super().emit(record)
            self.flush()
        except Exception:
            self.handleError(record)


# Executa limpeza de logs antigos antes de abrir e anexar novos logs
try:
    cleanup_old_logs(max_age_days=3)
except Exception:
    pass

logger = logging.getLogger("ComparadorFotos")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    _formatter = logging.Formatter(
        "[%(asctime)s.%(msecs)03d] [%(levelname)-7s] [%(threadName)-12s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    try:
        _file_handler = AutoFlushFileHandler(str(LOG_FILE), encoding="utf-8")
        _file_handler.setLevel(logging.DEBUG)
        _file_handler.setFormatter(_formatter)
        logger.addHandler(_file_handler)
    except Exception as e:
        print(f"Aviso: Não foi possível criar AutoFlushFileHandler: {e}", file=sys.stderr)


# Ativação do faulthandler nativo do Python para registrar crashes em C/C++/CUDA/DLL
try:
    _fault_fp = open(str(LOG_FILE), "a", encoding="utf-8")
    faulthandler.enable(file=_fault_fp, all_threads=True)
except Exception:
    pass


def _global_sys_excepthook(exc_type, exc_value, exc_traceback):
    """Captura qualquer exceção não tratada na thread principal."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.critical(
        f"EXCEÇÃO NÃO TRATADA NO PROCESSO PRINCIPAL: {exc_type.__name__}: {exc_value}",
        exc_info=(exc_type, exc_value, exc_traceback)
    )


def _global_thread_excepthook(args):
    """Captura qualquer exceção não tratada em threads em segundo plano."""
    if issubclass(args.exc_type, KeyboardInterrupt):
        return
    logger.critical(
        f"EXCEÇÃO NÃO TRATADA NA THREAD [{args.thread.name}]: {args.exc_type.__name__}: {args.exc_value}",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
    )


sys.excepthook = _global_sys_excepthook
if hasattr(threading, "excepthook"):
    threading.excepthook = _global_thread_excepthook


def log_debug(msg):
    logger.debug(msg)


def log_info(msg):
    logger.info(msg)


def log_warning(msg):
    logger.warning(msg)


def log_error(msg, exc=None):
    if exc:
        logger.error(f"{msg}: {exc}", exc_info=True)
    else:
        logger.error(msg)


def log_critical(msg, exc=None):
    if exc:
        logger.critical(f"{msg}: {exc}", exc_info=True)
    else:
        logger.critical(msg)


def log_system_diagnostics():
    """Registra informações detalhadas do ambiente de execução no log de debug."""
    try:
        import torch
        cuda_avail = torch.cuda.is_available()
        gpu_info = f"CUDA {torch.version.cuda} (Device: {torch.cuda.get_device_name(0)})" if cuda_avail else "CPU (CUDA indisponível)"
        if cuda_avail:
            try:
                vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
                gpu_info += f" - VRAM: {vram_gb:.2f} GB"
            except Exception:
                pass
    except Exception as e:
        gpu_info = f"PyTorch não detectado ou erro: {e}"

    try:
        from src.hardware import get_system_ram_info
        ram = get_system_ram_info()
        ram_info = f"{ram['total_gb']:.2f} GB Total | {ram['avail_gb']:.2f} GB Livre ({ram['load_pct']}% em uso)"
    except Exception as e:
        ram_info = f"Erro ao ler RAM: {e}"

    logger.info("=" * 80)
    logger.info("INICIALIZAÇÃO DO COMPARADOR DE FOTOS COM IA")
    logger.info(f"Sistema Operacional: {platform.system()} {platform.release()} ({platform.architecture()[0]})")
    logger.info(f"Python: {platform.python_version()} ({sys.executable})")
    logger.info(f"Processador / Cores: {os.cpu_count()} núcleos lógicos")
    logger.info(f"Memória RAM do Sistema: {ram_info}")
    logger.info(f"Aceleração por Hardware: {gpu_info}")
    logger.info(f"Configuração Ativa: {json.dumps(APP_CONFIG, ensure_ascii=False)}")
    logger.info("=" * 80)


def load_app_config():
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg = dict(DEFAULT_CONFIG)
            cfg.update(data)
            return cfg
        except Exception as e:
            log_error("Erro ao carregar config_app.json", e)
    return dict(DEFAULT_CONFIG)


_ATOMIC_WRITE_LOCK = threading.RLock()


def safe_replace(src_path, dst_path, max_retries=8, initial_delay=0.05):
    """Substitui dst_path atomicamente de forma resiliente a travas transitórias do Windows/antivírus."""
    src = Path(src_path)
    dst = Path(dst_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    last_exc = None
    for attempt in range(max_retries):
        try:
            if dst.exists():
                try:
                    os.chmod(dst, 0o666)
                except Exception:
                    pass
            os.replace(src, dst)
            return True
        except (PermissionError, OSError) as e:
            last_exc = e
            time.sleep(initial_delay * (1.5 ** attempt))

    # Fallback seguro para Windows: se os.replace falhar por lock externo, copia e apaga tmp
    try:
        if dst.exists():
            try:
                os.chmod(dst, 0o666)
            except Exception:
                pass
        import shutil
        shutil.copyfile(src, dst)
        try:
            src.unlink(missing_ok=True)
        except Exception:
            pass
        return True
    except Exception as e:
        if last_exc:
            raise last_exc
        raise e


def safe_atomic_write(target_path, content, encoding="utf-8"):
    """Escreve conteúdo de forma segura e atômica com PID/Thread/Time único no tmp, evitando corrupção e WinError 5."""
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    unique_suffix = f"{os.getpid()}_{threading.get_ident()}_{int(time.time()*1000)}"
    tmp = target.with_name(f"{target.name}.{unique_suffix}.tmp")

    with _ATOMIC_WRITE_LOCK:
        try:
            if isinstance(content, str):
                tmp.write_text(content, encoding=encoding)
            elif isinstance(content, (bytes, bytearray)):
                tmp.write_bytes(content)
            else:
                tmp.write_text(json.dumps(content, ensure_ascii=False), encoding=encoding)
            safe_replace(tmp, target)
        except Exception:
            try:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise


def save_app_config(cfg):
    try:
        safe_atomic_write(CONFIG_FILE, json.dumps(cfg, indent=2, ensure_ascii=False))
    except Exception as e:
        log_error("Erro ao salvar config_app.json", e)


APP_CONFIG = load_app_config()


def get_current_cache_dir():
    configured = APP_CONFIG.get("cache_dir")
    if configured:
        p = Path(configured).expanduser()
        if not p.is_absolute():
            p = (PROGRAM_DIR / p).resolve()
        else:
            p = p.resolve()
        if p.exists():
            return p
    p = (PROGRAM_DIR / "cache").resolve()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def get_cache_paths(root):
    if isinstance(root, (list, tuple, set)):
        sorted_roots = sorted(str(Path(r).resolve()).lower() for r in root)
        canon = "|".join(sorted_roots)
    else:
        canon = str(Path(root).resolve()).lower()
    slug = hashlib.md5(canon.encode("utf-8")).hexdigest()[:10]
    base_cache = get_current_cache_dir()
    folder_cache_dir = base_cache / slug
    folder_cache_dir.mkdir(parents=True, exist_ok=True)

    paths_dict = {
        "meta": folder_cache_dir / "meta.json",
        "embeddings": folder_cache_dir / "embeddings.npz",
        "edges": folder_cache_dir / "edges.npz",
        "phash": folder_cache_dir / "phash.json",
        "ignored_pairs": folder_cache_dir / "ignored_pairs.json",
        "web_photos": folder_cache_dir / "web_photos.json",
    }
    return paths_dict


def get_cache_size_bytes(cache_dir=None):
    cd = Path(cache_dir) if cache_dir else get_current_cache_dir()
    total = 0
    if cd.exists():
        for f in cd.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except Exception:
                    pass
    return total


def format_size(bytes_val):
    if bytes_val >= 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 ** 3):.2f} GB"
    if bytes_val >= 1024 * 1024:
        return f"{bytes_val / (1024 ** 2):.1f} MB"
    if bytes_val >= 1024:
        return f"{bytes_val / 1024:.0f} KB"
    return f"{bytes_val} B"


def format_time(seconds):
    seconds = int(max(0, seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def normalize_dir_str(p):
    return str(Path(p).resolve()).lower().rstrip("\\/")


def is_in_or_subdir(file_path, base_dir, include_subdirs=True):
    try:
        f_norm = normalize_dir_str(Path(file_path).parent)
        b_norm = normalize_dir_str(base_dir)
        if f_norm == b_norm:
            return True
        if include_subdirs:
            return f_norm.startswith(b_norm + "\\") or f_norm.startswith(b_norm + "/")
    except Exception:
        pass
    return False


def is_path_excluded(target_path, excluded_rules=None, root=None):
    """
    Verifica se um arquivo ou diretório deve ser excluído do processamento das imagens.
    Avalia as regras configuradas (caminhos absolutos, relativos, nomes de subpastas e se subdiretórios devem ser incluídos).
    """
    if not target_path:
        return False

    try:
        p = Path(target_path).resolve()
        parts_lower = [part.lower() for part in p.parts]

        # 1. Pastas do sistema e lixeiras sempre ignoradas
        if any(ign in parts_lower for ign in IGNORED_DIRS):
            return True

        rules = excluded_rules if excluded_rules is not None else APP_CONFIG.get("excluded_folders", [])
        if not rules:
            return False

        p_is_dir = p.is_dir() if p.exists() else (p.suffix == "")
        p_dir_norm = normalize_dir_str(p if p_is_dir else p.parent)

        for rule in rules:
            if isinstance(rule, str):
                r_path = rule.strip()
                r_subdirs = True
                r_enabled = True
            elif isinstance(rule, dict):
                r_path = str(rule.get("path", "")).strip()
                r_subdirs = bool(rule.get("include_subdirs", True))
                r_enabled = bool(rule.get("enabled", True))
            else:
                continue

            if not r_enabled or not r_path:
                continue

            r_path_lower = r_path.lower()

            # Caso A: Regra é apenas um nome simples de pasta (ex: "WhatsApp Stickers", ".thumbnails", "Private")
            if ("\\" not in r_path) and ("/" not in r_path):
                if r_path_lower in parts_lower:
                    if r_subdirs:
                        return True
                    else:
                        # Apenas se a pasta imediata for correspondente
                        curr_name = p.name.lower() if p_is_dir else p.parent.name.lower()
                        if curr_name == r_path_lower:
                            return True
                continue

            # Caso B: Regra é um caminho completo ou absoluto
            try:
                rule_resolved = Path(r_path).resolve()
                r_norm = normalize_dir_str(rule_resolved)
                if p_dir_norm == r_norm:
                    return True
                if r_subdirs:
                    if p_dir_norm.startswith(r_norm + "\\") or p_dir_norm.startswith(r_norm + "/"):
                        return True
            except Exception:
                pass

            # Caso C: Subcaminho relativo a root (se root fornecido)
            if root:
                try:
                    rel_rule = (Path(root) / r_path).resolve()
                    r_norm = normalize_dir_str(rel_rule)
                    if p_dir_norm == r_norm:
                        return True
                    if r_subdirs:
                        if p_dir_norm.startswith(r_norm + "\\") or p_dir_norm.startswith(r_norm + "/"):
                            return True
                except Exception:
                    pass

    except Exception:
        pass

    return False
