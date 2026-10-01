import argparse
import sys
from pathlib import Path

# Suporte a execução de subprocessos Python e pip embutido para download de dependências de IA no executável
if len(sys.argv) > 1:
    _idx = 1
    while _idx < len(sys.argv) and sys.argv[_idx].startswith("-") and sys.argv[_idx] not in ("-c", "-m"):
        _idx += 1
    if _idx < len(sys.argv):
        _action = sys.argv[_idx]
        if _action == "-c" and _idx + 1 < len(sys.argv):
            _code = sys.argv[_idx + 1]
            sys.argv = [sys.argv[0]] + sys.argv[_idx + 2:]
            exec(_code)
            sys.exit(0)
        elif _action == "-m" and _idx + 1 < len(sys.argv):
            _mod_name = sys.argv[_idx + 1]
            _remaining = sys.argv[_idx + 2:]
            if _mod_name == "pip":
                try:
                    import os
                    import pip._vendor.distlib.resources as distlib_res
                    from pip._vendor.distlib.resources import ResourceFinder

                    class _PyInstallerFinder(ResourceFinder):
                        def __init__(self, module):
                            super().__init__(module)
                            if hasattr(sys, '_MEIPASS'):
                                pkg_rel = getattr(module, '__name__', '').replace('.', os.sep)
                                cand = os.path.join(getattr(sys, '_MEIPASS'), pkg_rel)
                                if os.path.isdir(cand):
                                    self.base = cand

                    class _PyInstallerFinderRegistry(dict):
                        def get(self, k, default=None):
                            return _PyInstallerFinder
                        def __getitem__(self, k):
                            return _PyInstallerFinder

                    distlib_res._finder_registry = _PyInstallerFinderRegistry(distlib_res._finder_registry)
                except Exception:
                    pass

                try:
                    from pip._internal.cli.main import main as pip_entry
                    sys.exit(pip_entry(_remaining))
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    print(f"Erro ao inicializar pip embutido: {e}", file=sys.stderr)
                    sys.exit(1)
            else:
                import runpy
                sys.argv = [sys.argv[0]] + _remaining
                runpy.run_module(_mod_name, run_name="__main__", alter_sys=True)
                sys.exit(0)

# Adiciona a raiz do projeto ao path de importação
if getattr(sys, "frozen", False):
    PROGRAM_ROOT = Path(sys.executable).resolve().parent
else:
    PROGRAM_ROOT = Path(__file__).resolve().parent

if str(PROGRAM_ROOT) not in sys.path:
    sys.path.insert(0, str(PROGRAM_ROOT))

# Configura o path dos módulos dinâmicos de IA
try:
    from src.dependency_installer import setup_ai_runtime_path
    setup_ai_runtime_path()
except Exception:
    pass

from src.config import APP_CONFIG, save_app_config, cleanup_old_logs
from src.photo_engine import PhotoEngine
from src.ui.main_window import MainWindow

# Alias para compatibilidade reversa com scripts anteriores
GUI = MainWindow


def main():
    try:
        cleanup_old_logs()
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Comparador de Fotos com IA (CLIP, pHash, SHA-256)")
    parser.add_argument("folders", nargs="*", help="Pasta(s) das fotos a comparar")
    parser.add_argument("--threshold", type=float, default=0.82, help="Similaridade mínima CLIP (padrão: 0.82)")
    parser.add_argument("--phash", type=int, default=10, help="Distância máxima de pHash (padrão: 10)")
    parser.add_argument("--batch", type=int, default=0, help="Tamanho do lote de inferência")
    parser.add_argument("--workers", type=int, default=0, help="Número de threads para leitura")
    parser.add_argument("--knn", type=int, default=50, help="Número de vizinhos KNN")
    parser.add_argument("--cache-dir", type=str, default="", help="Diretório onde salvar/ler os caches de indexação")
    parser.add_argument("--max-ram", type=int, default=0, help="Limite de uso de RAM do sistema em porcentagem (ex: 80)")
    args = parser.parse_args()

    if args.cache_dir:
        APP_CONFIG["cache_dir"] = str(Path(args.cache_dir).expanduser().resolve())
        save_app_config(APP_CONFIG)
    if args.max_ram and args.max_ram > 0:
        APP_CONFIG["max_ram_pct"] = int(args.max_ram)
        save_app_config(APP_CONFIG)

    folders = args.folders or []
    if isinstance(folders, str):
        folders = [folders]

    cleaned_folders = []
    for f in folders:
        if f:
            clean = f.strip().strip('"').strip("'")
            while clean.endswith('"') or clean.endswith("'"):
                clean = clean[:-1].strip()
            if clean:
                p = Path(clean).resolve()
                if p.exists():
                    cleaned_folders.append(p)

    if cleaned_folders:
        folder_strs = [str(f) for f in cleaned_folders]
        APP_CONFIG["last_folders"] = folder_strs
        APP_CONFIG["last_folder"] = folder_strs[-1]
        save_app_config(APP_CONFIG)
        print(f"Iniciando Comparador de Fotos em {len(cleaned_folders)} pasta(s): {', '.join(folder_strs)}")

        from src.dependency_installer import is_ai_available
        has_ai = is_ai_available()
        enable_clip = bool(APP_CONFIG.get("enable_clip", True)) and has_ai
        enable_web_detector = bool(APP_CONFIG.get("enable_web_detector", True)) and has_ai
        if not has_ai and (APP_CONFIG.get("enable_clip", True) or APP_CONFIG.get("enable_web_detector", True)):
            print("Aviso: Módulos de IA (PyTorch / OpenCLIP) não detectados. Executando em modo rápido de Hashes (SHA-256 e pHash).")

        engine = PhotoEngine(
            cleaned_folders if len(cleaned_folders) > 1 else cleaned_folders[0],
            threshold=args.threshold,
            phash=args.phash,
            workers=args.workers or APP_CONFIG.get("cpu_workers") or None,
            batch=args.batch or APP_CONFIG.get("batch_size") or None,
            k=args.knn,
            enable_clip=enable_clip,
            enable_web_detector=enable_web_detector,
        )
        gui = MainWindow(engine, processing=True)
        gui.run_processing(engine.run)
        gui.run()
    else:
        # Inicializa a interface gráfica diretamente com seletor hierárquico aberto por padrão
        gui = MainWindow(engine=None, processing=False, auto_open_selector=True)
        gui.run()


if __name__ == "__main__":
    main()
