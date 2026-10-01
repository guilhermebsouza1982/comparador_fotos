import hashlib
from pathlib import Path
from PIL import Image, ImageFile, UnidentifiedImageError
import imagehash

ImageFile.LOAD_TRUNCATED_IMAGES = True

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass


def file_key(path):
    st = Path(path).stat()
    return f"{path}|{st.st_size}|{st.st_mtime_ns}"


def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def phash_file(path):
    try:
        p = Path(path)
        st = p.stat()
        if st.st_size == 0:
            return None
        with open(p, "rb") as f:
            head = f.read(16)
        if head.startswith(b"PK") or not head or set(head) == {0}:
            return None
        with Image.open(p) as im:
            im = im.convert("RGB")
            return str(imagehash.phash(im))
    except (UnidentifiedImageError, OSError):
        return None
    except Exception:
        return None


def compute_sharpness_score(path):
    """
    Calcula a pontuação de nitidez/foco da imagem utilizando a variância do operador Laplaciano.
    Valores maiores indicam bordas mais definidas e melhor foco.
    Valores próximos de zero indicam desfoque ou imagem sem textura.
    """
    try:
        p = Path(path)
        if not p.exists() or p.stat().st_size == 0:
            return 0.0
        with Image.open(p) as im:
            im = im.convert("L")
            w, h = im.size
            if max(w, h) > 800:
                scale = 800.0 / max(w, h)
                im = im.resize((int(w * scale), int(h * scale)), Image.Resampling.BILINEAR)
            # Filtro Laplaciano 3x3 de convolução
            from PIL import ImageFilter
            import numpy as np
            lap = im.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=0))
            arr = np.array(lap, dtype=np.float32)
            return float(np.var(arr))
    except Exception:
        return 0.0
