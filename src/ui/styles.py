from tkinter import ttk


def apply_app_styles(root):
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass

    # Card compact buttons
    style.configure("Card.TButton", font=("Segoe UI", 7), padding=(1, 1))

    # Action buttons
    style.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))
    style.configure("Danger.TButton", font=("Segoe UI", 9, "bold"), foreground="#cc0000")

    # Badges
    style.configure("KeepBadge.TLabel", font=("Segoe UI", 7, "bold"), foreground="#008800")
    style.configure("DelBadge.TLabel", font=("Segoe UI", 7, "bold"), foreground="#cc0000")
    style.configure("Shortcut.TLabel", font=("Segoe UI", 8, "bold"), foreground="#ffffff", background="#0066cc")

    return style
