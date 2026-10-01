from pathlib import Path
from typing import Union

from PIL import Image, ImageFile

from src.config import log_error, log_info

ImageFile.LOAD_TRUNCATED_IMAGES = True


def load_image_with_orientation(img_path: Union[Path, str]) -> Image.Image:
    """
    Carrega a imagem exatamente como está no disco, sem nenhum alinhamento ou rotação automática.
    Retorna o objeto PIL.Image em modo RGB.
    """
    p = Path(img_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Imagem não encontrada: {p}")

    with Image.open(p) as im:
        return im.convert("RGB")


def detect_image_orientation_angle(im: Image.Image) -> int:
    """
    Alinhamento automático desativado.
    """
    return 0


def rotate_and_save_photo(file_path: Union[Path, str], degrees: int = 90) -> bool:
    """
    Gira manualmente uma foto no disco por solicitação explícita do usuário
    (ao clicar no botão específico de rotação).
    """
    p = Path(file_path).resolve()
    if not p.exists():
        return False

    try:
        from src.config import safe_replace
        with Image.open(p) as im:
            exif = im.info.get("exif")
            rotated = im.rotate(-degrees, expand=True)

            tmp_dst = p.with_name(f"{p.stem}_rot_{degrees}{p.suffix}")
            if exif and p.suffix.lower() in [".jpg", ".jpeg"]:
                rotated.save(tmp_dst, quality=95, exif=exif)
            else:
                rotated.save(tmp_dst, quality=95)

            safe_replace(tmp_dst, p)
            log_info(f"Foto '{p.name}' rotacionada manualmente em {degrees}° pelo usuário.")
            return True
    except Exception as e:
        log_error(f"Erro ao girar foto {p}", e)
        return False
