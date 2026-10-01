import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from PIL import Image, ImageTk
from send2trash import send2trash

from src.config import format_size, log_error


class TimedConfirmDeleteDialog:
    """Popup de confirmação de exclusão com contagem regressiva de 2s e tecla ESC para cancelamento.
    Suporta tanto uma única foto (Path/str) quanto um grupo de fotos (lista de Paths/str).
    """
    def __init__(self, parent, photo_path, on_confirm=None, on_cancel=None, thumb_img=None, countdown=2):
        self.parent = parent
        if isinstance(photo_path, (list, tuple, set)):
            self.paths = [Path(p) for p in photo_path]
            self.is_group = True
            self.path = self.paths[0] if self.paths else None
        else:
            self.path = Path(photo_path)
            self.paths = [self.path]
            self.is_group = False

        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.countdown = countdown
        self.timer_id = None
        self.cancelled = False

        self.top = tk.Toplevel(parent)
        if self.is_group:
            self.top.title(f"Confirmar Exclusão do Grupo ({len(self.paths)} fotos)")
            self.top.geometry("540x480")
            self.top.minsize(480, 420)
        else:
            self.top.title("Confirmar Exclusão — Lixeira do Windows")
            self.top.geometry("480x440")
            self.top.minsize(440, 400)

        self.top.resizable(False, False)
        self.top.transient(parent)
        try:
            if parent.winfo_viewable():
                self.top.grab_set()
        except Exception:
            pass

        # Keyboard bindings
        self.top.bind("<Escape>", lambda e: self.cancel())
        self.top.bind("<Return>", lambda e: self.confirm())
        self.top.protocol("WM_DELETE_WINDOW", self.cancel)

        if self.is_group:
            self._build_group_ui()
        else:
            self._build_ui(thumb_img)
        self._start_timer()

    def _build_ui(self, thumb_img):
        pad = ttk.Frame(self.top, padding=(18, 14, 18, 14))
        pad.pack(fill="both", expand=True)

        ttk.Label(
            pad,
            text="🗑️ Enviar foto para a Lixeira do Windows?",
            font=("Segoe UI", 11, "bold"),
            foreground="#cc0000"
        ).pack(anchor="center", pady=(0, 6))

        # Thumbnail
        img_frame = ttk.Frame(pad, relief="solid", borderwidth=1)
        img_frame.pack(pady=4)

        if thumb_img:
            try:
                lbl_img = ttk.Label(img_frame, image=thumb_img)
                lbl_img.image = thumb_img
                lbl_img.pack(padx=2, pady=2)
            except Exception:
                ttk.Label(img_frame, text="[Sem prévia]", width=16, anchor="center").pack(pady=40)
        else:
            try:
                with Image.open(self.path) as im:
                    im = im.convert("RGB")
                    im.thumbnail((140, 140), Image.Resampling.BILINEAR)
                    tk_im = ImageTk.PhotoImage(im)
                    lbl_img = ttk.Label(img_frame, image=tk_im)
                    lbl_img.image = tk_im
                    lbl_img.pack(padx=2, pady=2)
            except Exception:
                ttk.Label(img_frame, text="[Sem prévia]", width=16, anchor="center").pack(pady=40)

        # File information
        fname = self.path.name
        ttk.Label(pad, text=fname, font=("Segoe UI", 9, "bold")).pack(pady=(6, 2))

        try:
            sz = self.path.stat().st_size
            sz_str = format_size(sz)
        except Exception:
            sz_str = ""

        if sz_str:
            ttk.Label(pad, text=f"Tamanho: {sz_str}", font=("Segoe UI", 8)).pack()

        # Full path
        p_str = str(self.path)
        ttk.Label(
            pad,
            text=p_str,
            font=("Segoe UI", 7),
            foreground="#555555",
            wraplength=430,
            justify="center"
        ).pack(pady=(4, 8))

        # Countdown banner
        self.banner_frame = tk.Frame(pad, bg="#fff3cd", relief="groove", borderwidth=1)
        self.banner_frame.pack(fill="x", pady=(4, 10))

        sec_txt = "segundos" if self.countdown != 1 else "segundo"
        self.timer_label = tk.Label(
            self.banner_frame,
            text=f"⏳ Exclusão automática em {self.countdown} {sec_txt}... (Pressione ESC para cancelar)",
            font=("Segoe UI", 9, "bold"),
            bg="#fff3cd",
            fg="#856404",
            pady=6
        )
        self.timer_label.pack()

        # Buttons
        btn_bar = ttk.Frame(pad)
        btn_bar.pack(fill="x", pady=(6, 0))

        self.btn_cancel = ttk.Button(
            btn_bar,
            text="❌ Cancelar (ESC)",
            command=self.cancel,
            width=16
        )
        self.btn_cancel.pack(side="left", padx=8, expand=True)

        self.btn_confirm = ttk.Button(
            btn_bar,
            text=f"🗑️ Confirmar ({self.countdown}s)",
            command=self.confirm,
            width=16
        )
        self.btn_confirm.pack(side="right", padx=8, expand=True)
        self.btn_confirm.focus_set()

    def _build_group_ui(self):
        pad = ttk.Frame(self.top, padding=(18, 14, 18, 14))
        pad.pack(fill="both", expand=True)

        num = len(self.paths)
        ttk.Label(
            pad,
            text=f"🗑️ Enviar todas as {num} fotos do grupo para a Lixeira?",
            font=("Segoe UI", 11, "bold"),
            foreground="#cc0000"
        ).pack(anchor="center", pady=(0, 4))

        # Calculate total size
        total_size = 0
        for p in self.paths:
            try:
                total_size += p.stat().st_size
            except Exception:
                pass

        size_text = f"Total: {num} fotos • {format_size(total_size)}"
        ttk.Label(
            pad,
            text=size_text,
            font=("Segoe UI", 9, "bold"),
            foreground="#003366"
        ).pack(anchor="center", pady=(0, 6))

        # Preview list / thumbnails
        list_container = ttk.Frame(pad, relief="solid", borderwidth=1, padding=6)
        list_container.pack(fill="both", expand=True, pady=(2, 6))

        # If <= 4 photos, show mini thumbnails in a horizontal row
        if num <= 4:
            thumbs_row = ttk.Frame(list_container)
            thumbs_row.pack(fill="x", pady=2)
            self._loaded_thumbs = []
            for p in self.paths:
                col = ttk.Frame(thumbs_row)
                col.pack(side="left", fill="both", expand=True, padx=2)
                try:
                    with Image.open(p) as im:
                        im = im.convert("RGB")
                        im.thumbnail((80, 80), Image.Resampling.BILINEAR)
                        tk_im = ImageTk.PhotoImage(im)
                        self._loaded_thumbs.append(tk_im)
                        lbl = ttk.Label(col, image=tk_im)
                        lbl.image = tk_im
                        lbl.pack()
                except Exception:
                    ttk.Label(col, text="[Sem prévia]", font=("Segoe UI", 7)).pack(pady=20)

                short_fn = p.name if len(p.name) <= 14 else (p.name[:12] + "...")
                ttk.Label(col, text=short_fn, font=("Segoe UI", 7, "bold")).pack()
                try:
                    sz_str = format_size(p.stat().st_size)
                    ttk.Label(col, text=sz_str, font=("Segoe UI", 7), foreground="#555").pack()
                except Exception:
                    pass
        else:
            # List of photos
            for i, p in enumerate(self.paths[:6]):
                row = ttk.Frame(list_container)
                row.pack(fill="x", pady=1)
                ttk.Label(row, text=f"• #{i+1} {p.name}", font=("Segoe UI", 8, "bold")).pack(side="left")
                try:
                    sz_str = format_size(p.stat().st_size)
                    ttk.Label(row, text=f"({sz_str})", font=("Segoe UI", 8), foreground="#555").pack(side="right")
                except Exception:
                    pass
            if num > 6:
                ttk.Label(
                    list_container,
                    text=f"... e mais {num - 6} fotos no grupo",
                    font=("Segoe UI", 8, "italic"),
                    foreground="#666"
                ).pack(anchor="w", pady=(2, 0))

        # Folder location hint
        if self.paths:
            folder_str = str(self.paths[0].parent)
            ttk.Label(
                pad,
                text=f"📁 Pasta: {folder_str}",
                font=("Segoe UI", 7),
                foreground="#555555",
                wraplength=480,
                justify="center"
            ).pack(pady=(2, 6))

        # Countdown banner
        self.banner_frame = tk.Frame(pad, bg="#fff3cd", relief="groove", borderwidth=1)
        self.banner_frame.pack(fill="x", pady=(2, 8))

        sec_txt = "segundos" if self.countdown != 1 else "segundo"
        self.timer_label = tk.Label(
            self.banner_frame,
            text=f"⏳ Exclusão automática do grupo em {self.countdown} {sec_txt}... (Pressione ESC para cancelar)",
            font=("Segoe UI", 9, "bold"),
            bg="#fff3cd",
            fg="#856404",
            pady=6
        )
        self.timer_label.pack()

        # Buttons
        btn_bar = ttk.Frame(pad)
        btn_bar.pack(fill="x", pady=(4, 0))

        self.btn_cancel = ttk.Button(
            btn_bar,
            text="❌ Cancelar (ESC)",
            command=self.cancel,
            width=16
        )
        self.btn_cancel.pack(side="left", padx=8, expand=True)

        self.btn_confirm = ttk.Button(
            btn_bar,
            text=f"🗑️ Excluir Todas ({self.countdown}s)",
            command=self.confirm,
            width=18
        )
        self.btn_confirm.pack(side="right", padx=8, expand=True)
        self.btn_confirm.focus_set()

    def _start_timer(self):
        self.timer_id = self.top.after(1000, self._tick)

    def _tick(self):
        if self.cancelled or not self.top.winfo_exists():
            return
        self.countdown -= 1
        if self.countdown > 0:
            sec_txt = "segundos" if self.countdown != 1 else "segundo"
            if self.is_group:
                self.timer_label.config(
                    text=f"⏳ Exclusão automática do grupo em {self.countdown} {sec_txt}... (Pressione ESC para cancelar)"
                )
                self.btn_confirm.config(text=f"🗑️ Excluir Todas ({self.countdown}s)")
            else:
                self.timer_label.config(
                    text=f"⏳ Exclusão automática em {self.countdown} {sec_txt}... (Pressione ESC para cancelar)"
                )
                self.btn_confirm.config(text=f"🗑️ Confirmar ({self.countdown}s)")
            self.timer_id = self.top.after(1000, self._tick)
        else:
            self.confirm()

    def cancel(self):
        self.cancelled = True
        if self.timer_id:
            try:
                self.top.after_cancel(self.timer_id)
            except Exception:
                pass
            self.timer_id = None
        if self.on_cancel:
            self.on_cancel()
        if self.top.winfo_exists():
            try:
                self.top.grab_release()
            except Exception:
                pass
            self.top.destroy()

    def confirm(self):
        if self.cancelled:
            return
        if self.timer_id:
            try:
                self.top.after_cancel(self.timer_id)
            except Exception:
                pass
            self.timer_id = None

        deleted = []
        errors = []
        try:
            for p in self.paths:
                p_obj = Path(p)
                try:
                    if p_obj.exists():
                        send2trash(p_obj)
                    deleted.append(p_obj)
                except Exception as e:
                    if not p_obj.exists():
                        deleted.append(p_obj)
                    else:
                        log_error(f"Erro ao mover {p} para a lixeira", e)
                        errors.append(str(e))

            if self.on_confirm:
                if self.is_group:
                    self.on_confirm(deleted)
                else:
                    self.on_confirm(self.path)

            if errors and not self.is_group:
                try:
                    messagebox.showerror(
                        "Erro ao Enviar para a Lixeira",
                        f"Não foi possível mover o arquivo para a Lixeira do Windows:\n{errors[0]}",
                        parent=self.parent
                    )
                except Exception:
                    pass
        finally:
            if self.top.winfo_exists():
                try:
                    self.top.grab_release()
                except Exception:
                    pass
                self.top.destroy()
