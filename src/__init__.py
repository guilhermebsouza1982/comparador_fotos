"""Pacote principal do Comparador de Fotos com IA."""

__all__ = ["PhotoEngine", "MainWindow"]


def __getattr__(name):
    if name == "PhotoEngine":
        from src.photo_engine import PhotoEngine
        return PhotoEngine
    if name == "MainWindow":
        from src.ui.main_window import MainWindow
        return MainWindow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
