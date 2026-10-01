# -*- coding: utf-8 -*-
"""Janela modal para exibição de novidades da versão e download da auto-atualização."""

import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser
from typing import Dict, Any, Optional

from src.updater import APP_VERSION, DownloadUpdateThread, apply_update_and_restart


class UpdateDialog:
    """Caixa de diálogo moderna para exibição de atualização disponível e download integrado."""

    def __init__(self, parent=None, update_info: Optional[Dict[str, Any]] = None):
        self.parent = parent
        self.info = update_info or {}
        self.download_thread: Optional[DownloadUpdateThread] = None

        self.top = tk.Toplevel(parent) if parent else tk.Tk()
        self.top.title("Atualização Disponível — Comparador de Fotos")
        self.top.geometry("620x480")
        self.top.minsize(560, 420)

        # Centraliza na tela
        self.top.update_idletasks()
        w = self.top.winfo_width()
        h = self.top.winfo_height()
        ws = self.top.winfo_screenwidth()
        hs = self.top.winfo_screenheight()
        x = (ws // 2) - (w // 2)
        y = (hs // 2) - (h // 2)
        self.top.geometry(f"+{x}+{y}")

        self._build_ui()

    def _build_ui(self):
        container = ttk.Frame(self.top, padding=20)
        container.pack(fill="both", expand=True)

        # Cabeçalho
        hdr_frame = ttk.Frame(container)
        hdr_frame.pack(fill="x", pady=(0, 10))

        lbl_icon = ttk.Label(hdr_frame, text="🚀", font=("Segoe UI", 28))
        lbl_icon.pack(side="left", padx=(0, 15))

        title_box = ttk.Frame(hdr_frame)
        title_box.pack(side="left", fill="x", expand=True)

        latest_v = self.info.get("latest_version", "Desconhecida")
        rel_name = self.info.get("release_name", f"Versão {latest_v}")
        pub_date = self.info.get("published_at", "")
        date_str = f" • Lançado em {pub_date}" if pub_date else ""

        ttk.Label(
            title_box,
            text=f"Nova Versão Disponível: v{latest_v}",
            font=("Segoe UI", 13, "bold"),
            foreground="#1a5fb4"
        ).pack(anchor="w")

        ttk.Label(
            title_box,
            text=f"Sua versão atual: v{APP_VERSION}{date_str}",
            font=("Segoe UI", 9),
            foreground="#555555"
        ).pack(anchor="w")

        ttk.Separator(container, orient="horizontal").pack(fill="x", pady=(0, 10))

        # Título e Notas de Versão (Changelog)
        ttk.Label(container, text="Novidades desta versão:", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))

        txt_frame = ttk.Frame(container)
        txt_frame.pack(fill="both", expand=True, pady=(0, 10))

        scrollbar = ttk.Scrollbar(txt_frame)
        scrollbar.pack(side="right", fill="y")

        self.txt_notes = tk.Text(
            txt_frame,
            wrap="word",
            font=("Segoe UI", 9),
            relief="solid",
            borderwidth=1,
            yscrollcommand=scrollbar.set,
            background="#fcfcfc",
            height=10
        )
        self.txt_notes.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.txt_notes.yview)

        raw_notes = self.info.get("release_notes", "Correções de estabilidade e melhorias gerais.")
        self.txt_notes.insert("1.0", raw_notes)
        self.txt_notes.config(state="disabled")

        # Barra de Progresso
        self.prog_frame = ttk.Frame(container)
        self.prog_frame.pack(fill="x", pady=(0, 10))

        self.prog_bar = ttk.Progressbar(self.prog_frame, orient="horizontal", mode="determinate")
        self.prog_bar.pack(fill="x", pady=(0, 4))

        self.lbl_status = ttk.Label(
            self.prog_frame,
            text="Pronto para baixar e atualizar automaticamente.",
            font=("Segoe UI", 8),
            foreground="#444444"
        )
        self.lbl_status.pack(anchor="w")

        # Barra de Botões
        btn_bar = ttk.Frame(container)
        btn_bar.pack(fill="x", side="bottom")

        btn_github = ttk.Button(
            btn_bar,
            text="🌐 Ver no GitHub",
            command=self._open_github
        )
        btn_github.pack(side="left", padx=(0, 6))

        self.btn_cancel = ttk.Button(
            btn_bar,
            text="Lembrar Mais Tarde",
            command=self._on_cancel
        )
        self.btn_cancel.pack(side="left")

        self.btn_update = ttk.Button(
            btn_bar,
            text="⚡ Atualizar Agora e Reiniciar",
            command=self.start_update
        )
        self.btn_update.pack(side="right")

    def _open_github(self):
        url = self.info.get("html_url") or "https://github.com"
        webbrowser.open(url)

    def _on_cancel(self):
        if self.download_thread and self.download_thread.is_alive():
            self.download_thread.cancel()
            self.lbl_status.config(text="Cancelando download...")
            self.btn_cancel.config(state="disabled")
        else:
            self.top.destroy()

    def start_update(self):
        download_url = self.info.get("download_url")
        if not download_url:
            messagebox.showwarning(
                "Executável Não Encontrado",
                "O pacote executável (.exe) não foi encontrado diretamente neste lançamento.\n\n"
                "Você será redirecionado para a página do GitHub para download manual.",
                parent=self.top
            )
            self._open_github()
            self.top.destroy()
            return

        self.btn_update.config(state="disabled", text="⏳ Baixando...")
        self.btn_cancel.config(text="❌ Cancelar")

        self.download_thread = DownloadUpdateThread(
            download_url=download_url,
            asset_name=self.info.get("asset_name", "ComparadorFotos_Launcher.exe"),
            on_progress=self._on_progress_ui,
            on_complete=self._on_complete_ui
        )
        self.download_thread.start()

    def _on_progress_ui(self, val: float, msg: str):
        def _update():
            self.prog_bar["value"] = val
            self.lbl_status.config(text=msg)
        self.top.after(0, _update)

    def _on_complete_ui(self, success: bool, msg: str):
        def _update():
            if success:
                self.lbl_status.config(text="✅ " + msg, foreground="green")
                if messagebox.askyesno(
                    "Atualização Concluída",
                    "O download da nova versão foi concluído com sucesso!\n\n"
                    "Deseja reiniciar o aplicativo agora para aplicar a atualização?",
                    parent=self.top
                ):
                    try:
                        apply_update_and_restart(self.download_thread.downloaded_file)
                    except Exception as e:
                        messagebox.showerror("Erro ao Aplicar Atualização", str(e), parent=self.top)
            else:
                self.lbl_status.config(text="❌ " + msg, foreground="red")
                self.btn_update.config(state="normal", text="🔄 Tentar Novamente")
                self.btn_cancel.config(state="normal", text="Fechar", command=self.top.destroy)
                messagebox.showerror("Falha no Download", f"Não foi possível baixar a atualização:\n\n{msg}", parent=self.top)
        self.top.after(0, _update)

