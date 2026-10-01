# -*- coding: utf-8 -*-
"""Módulo de instalação e gerenciamento dinâmico de dependências de IA (PyTorch, OpenCLIP, Scipy).
Permite que o executável seja distribuído de forma ultraleve (~20-30 MB) e baixe/configure
os aceleradores de IA sob demanda na primeira execução.
"""

import os
import re
import sys
import logging
import threading
import subprocess
from pathlib import Path
from typing import Callable, Optional, Dict, Any

import tkinter as tk
from tkinter import ttk, messagebox

from src.config import PROGRAM_DIR, APP_CONFIG, save_app_config, log_error

logger = logging.getLogger("ComparadorFotos")


def get_ai_runtime_dir() -> Path:
    """Retorna o diretório prioritário para instalação dos pacotes de IA."""
    # 1. Tenta pasta local ai_runtime junto ao executável (modo portátil)
    local_dir = PROGRAM_DIR / "ai_runtime"
    try:
        local_dir.mkdir(parents=True, exist_ok=True)
        # Testa escrita
        test_file = local_dir / ".write_test"
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()
        return local_dir.resolve()
    except Exception:
        pass

    # 2. Fallback para pasta segura do usuário em %LOCALAPPDATA%
    appdata = os.environ.get("LOCALAPPDATA")
    if appdata:
        fallback = Path(appdata) / "ComparadorFotos" / "ai_runtime"
    else:
        fallback = Path.home() / ".comparador_fotos" / "ai_runtime"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback.resolve()


def setup_ai_runtime_path() -> None:
    """Configura o sys.path e diretórios de DLLs do Windows para carregar os módulos de IA."""
    runtime_dirs = [
        PROGRAM_DIR / "ai_runtime",
        Path(os.environ.get("LOCALAPPDATA", "")) / "ComparadorFotos" / "ai_runtime" if os.environ.get("LOCALAPPDATA") else None,
        Path.home() / ".comparador_fotos" / "ai_runtime"
    ]
    for rdir in runtime_dirs:
        if rdir and rdir.exists():
            rstr = str(rdir.resolve())
            if rstr not in sys.path:
                sys.path.insert(0, rstr)
            sp = rdir / "site-packages"
            if sp.exists() and str(sp.resolve()) not in sys.path:
                sys.path.insert(0, str(sp.resolve()))
            # Adiciona DLLs do PyTorch/CUDA se presentes
            if hasattr(os, "add_dll_directory"):
                for dll_sub in ["torch/lib", "scipy.libs", "numpy.libs"]:
                    p_dll = rdir / dll_sub
                    if p_dll.exists():
                        try:
                            os.add_dll_directory(str(p_dll.resolve()))
                        except Exception:
                            pass


# Garante que caminhos já existentes sejam adicionados ao iniciar
setup_ai_runtime_path()


def is_ai_available() -> bool:
    """Verifica se PyTorch, OpenCLIP e Scipy estão disponíveis para importação."""
    setup_ai_runtime_path()
    try:
        import torch
        import open_clip
        import scipy
        return True
    except ImportError:
        return False
    except Exception as e:
        logger.debug(f"Aviso ao verificar disponibilidade de IA: {e}")
        return False


def get_ai_details() -> Dict[str, Any]:
    """Retorna detalhes sobre a instalação atual de IA."""
    setup_ai_runtime_path()
    details = {
        "installed": False,
        "torch_version": None,
        "cuda_available": False,
        "device_name": "N/A",
        "open_clip": False,
        "scipy": False,
        "runtime_path": str(get_ai_runtime_dir()),
    }
    try:
        import torch
        details["torch_version"] = getattr(torch, "__version__", "Desconhecido")
        details["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            details["device_name"] = torch.cuda.get_device_name(0)
        else:
            details["device_name"] = "CPU (Processador)"
    except Exception:
        pass

    try:
        import open_clip
        details["open_clip"] = True
    except Exception:
        pass

    try:
        import scipy
        details["scipy"] = True
    except Exception:
        pass

    details["installed"] = bool(details["torch_version"] and details["open_clip"] and details["scipy"])
    return details


class DependencyInstallerThread(threading.Thread):
    """Thread em segundo plano para baixar e instalar dependências de IA via pip."""
    def __init__(
        self,
        mode: str = "cpu",
        on_progress: Optional[Callable[[float, str], None]] = None,
        on_complete: Optional[Callable[[bool, str], None]] = None,
    ):
        super().__init__(daemon=True, name="AI-Dependency-Installer")
        self.mode = mode.lower()
        self.on_progress = on_progress
        self.on_complete = on_complete
        self.is_cancelled = False
        self.process = None

    def cancel(self):
        self.is_cancelled = True
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass

    def run(self):
        target_dir = get_ai_runtime_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target_str = str(target_dir)

        if self.on_progress:
            self.on_progress(5.0, "Preparando ambiente de download...")

        # Pacotes essenciais para IA
        packages = ["scipy", "open_clip_torch", "timm", "huggingface_hub"]

        if self.mode == "gpu":
            # PyTorch com CUDA 12.4
            torch_pkgs = ["torch", "torchvision"]
            index_url = "https://download.pytorch.org/whl/cu124"
        else:
            # PyTorch CPU (leve)
            torch_pkgs = ["torch", "torchvision"]
            index_url = "https://download.pytorch.org/whl/cpu"

        all_pkgs = torch_pkgs + packages

        # Determina o executável do Python ou wrapper
        exe = sys.executable
        cmd = [
            exe, "-m", "pip", "install",
            "--target", target_str,
            "--no-warn-script-location",
            "--index-url", index_url,
            "--extra-index-url", "https://pypi.org/simple"
        ] + all_pkgs

        logger.info(f"Iniciando instalação de IA ({self.mode.upper()}) em: {target_str}")
        logger.info(f"Comando: {' '.join(cmd)}")

        if self.on_progress:
            self.on_progress(10.0, f"Baixando pacotes de IA ({self.mode.upper()})...")

        try:
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                startupinfo=startupinfo,
                encoding="utf-8",
                errors="replace"
            )

            progress_val = 10.0
            last_line = ""

            for line in iter(self.process.stdout.readline, ""):
                if self.is_cancelled:
                    break
                line = line.strip()
                if not line:
                    continue
                last_line = line
                logger.debug(f"[pip install] {line}")

                # Detecta progresso do pip
                if "Downloading" in line:
                    pkg_match = re.search(r"Downloading\s+([^\s]+)", line)
                    pkg_name = pkg_match.group(1) if pkg_match else "componentes"
                    # Extrai tamanho se houver
                    sz_match = re.search(r"\(([^)]+)\)", line)
                    sz_str = f" ({sz_match.group(1)})" if sz_match else ""
                    progress_val = min(progress_val + 7.0, 85.0)
                    if self.on_progress:
                        self.on_progress(progress_val, f"Baixando {pkg_name}{sz_str}...")
                elif "Installing collected packages" in line:
                    progress_val = 88.0
                    if self.on_progress:
                        self.on_progress(progress_val, "Instalando e descompactando pacotes de IA...")
                elif "Successfully installed" in line:
                    progress_val = 98.0
                    if self.on_progress:
                        self.on_progress(progress_val, "Finalizando configuração...")

            self.process.stdout.close()
            ret_code = self.process.wait()

            if self.is_cancelled:
                logger.info("Instalação de IA cancelada pelo usuário.")
                if self.on_complete:
                    self.on_complete(False, "Instalação cancelada pelo usuário.")
                return

            if ret_code == 0:
                setup_ai_runtime_path()
                if is_ai_available():
                    logger.info("Instalação de dependências de IA concluída com sucesso!")
                    if self.on_progress:
                        self.on_progress(100.0, "Instalação concluída com sucesso!")
                    if self.on_complete:
                        self.on_complete(True, "Módulos de IA instalados e ativados com sucesso!")
                else:
                    msg = "O download terminou, mas ocorreu falha ao carregar os módulos de IA."
                    logger.error(msg)
                    if self.on_complete:
                        self.on_complete(False, msg)
            else:
                msg = f"Falha na instalação via pip (código {ret_code}). Última mensagem: {last_line}"
                logger.error(msg)
                if self.on_complete:
                    self.on_complete(False, msg)

        except Exception as e:
            err_msg = f"Erro durante download de dependências: {e}"
            log_error(err_msg)
            if self.on_complete:
                self.on_complete(False, err_msg)


class DependencyInstallerDialog:
    """Janela visual interativa para download e configuração dos módulos de IA."""
    def __init__(self, parent=None, on_finish=None, auto_start: bool = False):
        self.parent = parent
        self.on_finish = on_finish
        self.installer_thread = None
        self.success = False

        self.top = tk.Toplevel(parent) if parent else tk.Tk()
        self.top.title("Configuração de Inteligência Artificial — Comparador de Fotos")
        self.top.geometry("560x420")
        self.top.minsize(500, 380)
        self.top.resizable(False, False)

        # Centraliza na tela
        self.top.update_idletasks()
        w = self.top.winfo_width()
        h = self.top.winfo_height()
        ws = self.top.winfo_screenwidth()
        hs = self.top.winfo_screenheight()
        x = (ws // 2) - (w // 2)
        y = (hs // 2) - (h // 2)
        self.top.geometry(f"+{x}+{y}")

        self.mode_var = tk.StringVar(value="cpu")
        self._build_ui()

        if auto_start:
            self.start_download()

    def _build_ui(self):
        container = ttk.Frame(self.top, padding=20)
        container.pack(fill="both", expand=True)

        # Cabeçalho
        hdr_frame = ttk.Frame(container)
        hdr_frame.pack(fill="x", pady=(0, 10))

        lbl_icon = ttk.Label(hdr_frame, text="🧠", font=("Segoe UI", 24))
        lbl_icon.pack(side="left", padx=(0, 12))

        title_box = ttk.Frame(hdr_frame)
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="Módulos de Inteligência Artificial", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            title_box,
            text="Habilita busca visual avançada (OpenCLIP) e detector de memes.",
            font=("Segoe UI", 9),
            foreground="#555555"
        ).pack(anchor="w")

        ttk.Separator(container, orient="horizontal").pack(fill="x", pady=(0, 12))

        # Descrição explicativa
        desc_text = (
            "Para manter o aplicativo inicial ultraleve, os aceleradores de IA são baixados "
            "uma única vez e armazenados localmente no seu computador. "
            "Escolha a opção recomendada para o seu hardware:"
        )
        ttk.Label(container, text=desc_text, wraplength=510, justify="left", font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 12))

        # Opções de Instalação (Radio buttons em cards estilizados)
        opts_frame = ttk.LabelFrame(container, text=" Escolha a versão dos aceleradores ", padding=10)
        opts_frame.pack(fill="x", pady=(0, 12))

        rb_cpu = ttk.Radiobutton(
            opts_frame,
            text="🟢 Modo Padrão CPU (Recomendado — ~250 MB)\n     Excelente velocidade, funciona em qualquer computador Windows.",
            variable=self.mode_var,
            value="cpu"
        )
        rb_cpu.pack(anchor="w", pady=4)

        rb_gpu = ttk.Radiobutton(
            opts_frame,
            text="🚀 Modo Turbo GPU NVIDIA CUDA (~1,5 GB)\n     Máximo desempenho para computadores com placa NVIDIA GeForce/RTX.",
            variable=self.mode_var,
            value="gpu"
        )
        rb_gpu.pack(anchor="w", pady=4)

        # Barra de Progresso
        self.progress_frame = ttk.Frame(container)
        self.progress_frame.pack(fill="x", pady=(4, 8))

        self.progress_bar = ttk.Progressbar(self.progress_frame, orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill="x", pady=(0, 4))

        self.lbl_status = ttk.Label(self.progress_frame, text="Pronto para iniciar o download.", font=("Segoe UI", 8), foreground="#444444")
        self.lbl_status.pack(anchor="w")

        # Barra de Botões
        self.btn_bar = ttk.Frame(container)
        self.btn_bar.pack(fill="x", side="bottom", pady=(10, 0))

        self.btn_skip = ttk.Button(
            self.btn_bar,
            text="⚡ Continuar sem IA (Modo Rápido: Hash)",
            command=self.skip_without_ai
        )
        self.btn_skip.pack(side="left")

        self.btn_action = ttk.Button(
            self.btn_bar,
            text="🚀 Baixar e Ativar IA",
            command=self.start_download
        )
        self.btn_action.pack(side="right")

    def start_download(self):
        self.btn_action.config(state="disabled", text="⏳ Baixando...")
        self.btn_skip.config(text="❌ Cancelar", command=self.cancel_download)

        mode = self.mode_var.get()
        self.installer_thread = DependencyInstallerThread(
            mode=mode,
            on_progress=self._on_progress_ui,
            on_complete=self._on_complete_ui
        )
        self.installer_thread.start()

    def cancel_download(self):
        if self.installer_thread and self.installer_thread.is_alive():
            self.installer_thread.cancel()
            self.lbl_status.config(text="Cancelando download...")
            self.btn_skip.config(state="disabled")
        else:
            self.skip_without_ai()

    def _on_progress_ui(self, val: float, msg: str):
        def _update():
            self.progress_bar["value"] = val
            self.lbl_status.config(text=msg)
        self.top.after(0, _update)

    def _on_complete_ui(self, success: bool, msg: str):
        def _update():
            self.success = success
            if success:
                self.lbl_status.config(text="✅ " + msg, foreground="green")
                messagebox.showinfo("Inteligência Artificial Ativada", "Os aceleradores de IA foram instalados com sucesso!", parent=self.top)
                self.top.destroy()
                if self.on_finish:
                    self.on_finish(True)
            else:
                self.lbl_status.config(text="❌ " + msg, foreground="red")
                self.btn_action.config(state="normal", text="🔄 Tentar Novamente")
                self.btn_skip.config(state="normal", text="⚡ Continuar sem IA", command=self.skip_without_ai)
                messagebox.showwarning("Aviso de Instalação", f"Não foi possível concluir o download da IA:\n\n{msg}\n\nVocê pode continuar usando a busca rápida por Hash.", parent=self.top)
        self.top.after(0, _update)

    def skip_without_ai(self):
        """Prossegue sem módulos de IA, desativando CLIP e Memes para esta sessão."""
        APP_CONFIG["enable_clip"] = False
        APP_CONFIG["enable_web_detector"] = False
        save_app_config(APP_CONFIG)
        self.top.destroy()
        if self.on_finish:
            self.on_finish(False)
