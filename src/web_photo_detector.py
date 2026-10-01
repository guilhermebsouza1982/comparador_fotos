import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image, ImageFile
try:
    import torch
except ImportError:
    torch = None

try:
    import open_clip
except ImportError:
    open_clip = None

from src.config import get_cache_paths, log_error, log_debug, log_info, safe_atomic_write

ImageFile.LOAD_TRUNCATED_IMAGES = True

_TEXT_FEATURES = None
_TEXT_CATEGORIES_NAMES = None
_NEGATIVE_FEATURE = None

# Conjuntos de prompts descritivos (Zero-Shot Ensemble) para cada categoria web/meme
_ENSEMBLE_PROMPTS = {
    "Meme / Humor": [
        "a funny internet meme with text caption",
        "a viral internet meme image with top and bottom text",
        "a humorous meme image shared on social media",
        "a picture with a funny joke caption or meme template",
        "a social media reaction image with text",
        "um meme engraçado da internet com legenda ou texto",
        "uma piada ou imagem de humor das redes sociais",
        "um meme viral da internet",
    ],
    "Print / Screenshot": [
        "a mobile screenshot of a smartphone screen",
        "a screenshot of a whatsapp conversation or chat message",
        "a screenshot of a tweet or twitter post on a phone",
        "a screenshot of instagram facebook or social media app feed",
        "a screenshot of a computer web browser or mobile app interface",
        "uma captura de tela de celular ou print de whatsapp",
        "um print de conversa de aplicativo de mensagens ou rede social",
    ],
    "Mensagem / Bom Dia": [
        "a decorative good morning greeting card with text and flowers",
        "an inspirational quote card with stylized typography and flowers",
        "a religious greeting card with motivational quote and glitter",
        "cartão de bom dia com flores e mensagem religiosa ou motivacional",
        "imagem de reflexão com flores, sol e texto de carinho ou fé",
        "a digital greeting card with inspirational bible verse or quote",
    ],
    "Figurinha / Sticker / Ilustração": [
        "a whatsapp sticker with transparent background or white outline",
        "a digital cartoon illustration or clipart character",
        "a funny comic strip panel or digital drawing",
        "a vector art graphic icon or mascot sticker",
        "figurinha ou sticker de whatsapp, desenho animado ou clipart",
        "an anime drawing or digital character illustration",
    ],
    "Banner / Anúncio / Infográfico": [
        "an infographic poster with diagrams, charts and typography",
        "a commercial promotional flyer or digital sales poster",
        "a marketing advertisement banner with bold text and discounts",
        "anúncio publicitário, panfleto digital ou banner comercial com texto",
        "a digital menu, price list or product catalog",
    ],
    "Arte / Wallpaper Web": [
        "a generic digital computer wallpaper or 3d abstract render",
        "a downloaded digital stock illustration or artwork from the internet",
        "papel de parede digital, arte abstrata ou imagem de banco de dados",
    ],
}

_NEGATIVE_PROMPTS = [
    "a real authentic photograph taken with a camera or phone by a person",
    "a personal family snapshot or portrait taken with a camera",
    "a real selfie photo of a person taken with smartphone camera",
    "a real natural outdoor travel vacation landscape photo",
    "a real photograph of food or a pet animal taken by a camera",
    "a real photograph of an everyday indoor scene or house room",
    "uma foto real tirada com câmera de celular por uma pessoa",
    "uma fotografia pessoal autêntica de família, amigos ou viagens",
]

# Padrões de nome de arquivo e diretórios fortemente associados a downloads da web
_WEB_FILENAME_REGEX = re.compile(
    r"(whatsapp|img-wa|wa\d{4}|stk-|sticker|fig-|ptt-|"
    r"screenshot|captura.?de.?tela|screen.?shot|print_|screencap|"
    r"fb_img|facebook|instagram|ig_|twitter|tweet|snapchat|tiktok|reddit|pinterest|pin-|"
    r"download|meme|figurinha|saved_|wallpaper)",
    re.IGNORECASE
)


def get_optical_camera_metadata(path: Union[Path, str]) -> Tuple[bool, Optional[str]]:
    """
    Verifica se a foto possui metadados ÓPTICOS legítimos de hardware de câmera (lente, ISO, exposição).
    Softwares de edição, redes sociais e navegadores geralmente descartam essas tags na compressão web.
    Retorna (has_optical_camera_exif, camera_description).
    """
    path = Path(path)
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return False, None

            # Tags ópticas essenciais de captura por sensor fotográfico
            # 0x829A = ExposureTime, 0x829D = FNumber, 0x8827 = ISOSpeedRatings, 0x920A = FocalLength
            has_optical_tags = (
                (0x829A in exif) or  # ExposureTime
                (0x829D in exif) or  # FNumber
                (0x8827 in exif) or  # ISOSpeedRatings
                (0x920A in exif)     # FocalLength
            )

            make = str(exif.get(271) or exif.get(0x010F) or "").replace("\x00", "").strip()
            model = str(exif.get(272) or exif.get(0x0110) or "").replace("\x00", "").strip()
            camera = f"{make} {model}".strip() if (make or model) else None

            # Verifica sub-ifd EXIF caso o primário não tenha exposto diretamente
            if hasattr(im, "_getexif"):
                raw_exif = im._getexif()
                if raw_exif:
                    if not has_optical_tags:
                        has_optical_tags = (
                            (33434 in raw_exif) or  # ExposureTime
                            (33437 in raw_exif) or  # FNumber
                            (34855 in raw_exif) or  # ISOSpeedRatings
                            (37386 in raw_exif)     # FocalLength
                        )
                    if not camera:
                        raw_make = str(raw_exif.get(271) or raw_exif.get(0x010F) or "").replace("\x00", "").strip()
                        raw_model = str(raw_exif.get(272) or raw_exif.get(0x0110) or "").replace("\x00", "").strip()
                        if raw_make or raw_model:
                            camera = f"{raw_make} {raw_model}".strip()

            if has_optical_tags:
                return True, camera or "Câmera Fotográfica"

            # Se possui apenas Make/Model reconhecido mas sem parâmetros ópticos, pode ser foto de câmera
            if camera and len(camera) > 2 and any(brand in camera.lower() for brand in ("apple", "samsung", "xiaomi", "motorola", "sony", "canon", "nikon")):
                return True, camera
    except Exception:
        pass
    return False, None


# Alias de compatibilidade
get_image_camera_metadata = get_optical_camera_metadata


def inspect_image_heuristics(path: Union[Path, str]) -> Tuple[float, str]:
    """
    Analisa características estruturais do arquivo (nome, formato, dimensões, canais)
    para estimar bônus e indícios de ser imagem da web/meme.
    Retorna (heuristic_bonus, reason_hint).
    """
    path = Path(path)
    bonus = 0.0
    hints = []
    p_str = str(path).lower()

    # 1. Padrões de nome de arquivo ou pasta
    if _WEB_FILENAME_REGEX.search(p_str):
        if any(w in p_str for w in ("whatsapp", "wa0", "img-wa", "stk-", "sticker", "fig-")):
            bonus += 0.28
            hints.append("WhatsApp/Sticker")
        elif any(w in p_str for w in ("screenshot", "captura", "print")):
            bonus += 0.30
            hints.append("Screenshot/Print")
        elif any(w in p_str for w in ("meme", "humor")):
            bonus += 0.32
            hints.append("Nome Meme")
        else:
            bonus += 0.20
            hints.append("Nome de Download Web")

    # 2. Formato e propriedades de imagem
    suffix = path.suffix.lower()
    if suffix == ".webp":
        bonus += 0.16
        hints.append("Formato WebP")

    try:
        with Image.open(path) as im:
            w, h = im.size
            ratio = max(w, h) / max(1, min(w, h))

            # Transparência / Canal Alpha (Stickers / Logotipos)
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                bonus += 0.25
                hints.append("Canal Alpha (Sticker)")

            # Aspect ratio típico de telas de celulares modernos (Screenshots verticais)
            if h > w and 1.82 <= ratio <= 2.35 and h >= 900:
                bonus += 0.20
                hints.append("Proporção de Tela de Celular")

            # Resolução pequena / comprimida típica de memes e stickers
            if w <= 600 and h <= 600:
                bonus += 0.15
                hints.append("Baixa Resolução (Web)")
            elif w * h <= 900 * 900:
                bonus += 0.06
    except Exception:
        pass

    return min(0.50, bonus), ", ".join(hints) if hints else ""


def get_text_features(model, device: str):
    """
    Gera e armazena em cache os centróides normalizados das categorias positivas
    e o centróide normalizado dos exemplos negativos.
    """
    global _TEXT_FEATURES, _TEXT_CATEGORIES_NAMES, _NEGATIVE_FEATURE
    if _TEXT_FEATURES is not None and _NEGATIVE_FEATURE is not None:
        return _TEXT_FEATURES, _TEXT_CATEGORIES_NAMES, _NEGATIVE_FEATURE

    if torch is None or open_clip is None:
        raise RuntimeError(
            "PyTorch ou OpenCLIP não estão instalados neste computador. "
            "A detecção de fotos da web requer os módulos de IA."
        )

    tokenizer = open_clip.get_tokenizer("ViT-B-32-quickgelu")

    categories_names = list(_ENSEMBLE_PROMPTS.keys())
    cat_vectors = []

    with torch.no_grad():
        for cat_name in categories_names:
            prompts = _ENSEMBLE_PROMPTS[cat_name]
            tokens = tokenizer(prompts).to(device)
            feats = model.encode_text(tokens)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            # Média dos embeddings de prompt da categoria (Prompt Ensembling)
            mean_feat = feats.mean(dim=0, keepdim=True)
            mean_feat = mean_feat / mean_feat.norm(dim=-1, keepdim=True)
            cat_vectors.append(mean_feat.cpu().numpy()[0])

        # Centróide negativo (fotos reais de câmera)
        neg_tokens = tokenizer(_NEGATIVE_PROMPTS).to(device)
        neg_feats = model.encode_text(neg_tokens)
        neg_feats = neg_feats / neg_feats.norm(dim=-1, keepdim=True)
        mean_neg = neg_feats.mean(dim=0, keepdim=True)
        mean_neg = mean_neg / mean_neg.norm(dim=-1, keepdim=True)
        neg_vector = mean_neg.cpu().numpy()[0]

    _TEXT_FEATURES = np.stack(cat_vectors).astype(np.float32)  # shape (num_cats, 512)
    _TEXT_CATEGORIES_NAMES = categories_names
    _NEGATIVE_FEATURE = neg_vector.astype(np.float32)          # shape (512,)

    log_info(f"Detector Web: {len(categories_names)} centróides textuais de categorias compilados no OpenCLIP.")
    return _TEXT_FEATURES, _TEXT_CATEGORIES_NAMES, _NEGATIVE_FEATURE


def compute_web_photo_scores(
    paths: List[Path],
    embeddings: np.ndarray,
    model=None,
    device: str = "cpu",
    on_update=None,
    stop_event=None
) -> Dict[str, dict]:
    """
    Classifica de forma multimodal as fotos para identificar memes, prints de celular,
    mensagens com texto, figurinhas e imagens baixadas da web.
    Combina:
    1. Similaridade semântica CLIP com Zero-Shot Prompt Ensembling e Margem Cosseno.
    2. Detecção de metadados ópticos de câmera (hardware real vs imagem web sem dados de lente).
    3. Heurísticas estruturais de formato, aspecto e assinaturas de nome de arquivo.
    """
    if len(paths) == 0 or embeddings is None or len(embeddings) == 0:
        return {}

    text_features, cat_names, neg_feature = get_text_features(model, device)

    # 1. Projeção de Similaridade Cosseno com todas as categorias positivas
    pos_sims = np.dot(embeddings, text_features.T)   # shape (N, num_cats)
    neg_sims = np.dot(embeddings, neg_feature)        # shape (N,)

    results = {}
    total_imgs = len(paths)
    up_interval = max(1, min(10, total_imgs // 50))

    for i, p in enumerate(paths):
        if stop_event and stop_event.is_set():
            break

        p_obj = Path(p)
        if not p_obj.is_file():
            continue

        p_str = str(p_obj)
        best_cat_idx = int(np.argmax(pos_sims[i]))
        best_cat_name = cat_names[best_cat_idx]
        best_pos_sim = float(pos_sims[i, best_cat_idx])
        neg_sim = float(neg_sims[i])

        # 2. Margem de separação semântica CLIP: diferença entre melhor categoria web e foto real
        margin = best_pos_sim - neg_sim

        # Sigmoid suave calibrada para converter margem cosseno em probabilidade [0.0, 1.0]
        # Ponto de inflexão em -0.015 (memes sutis já começam com sinal visual relevante)
        clip_prob = 1.0 / (1.0 + np.exp(-18.0 * (margin - (-0.015))))

        # 3. Metadados Ópticos de Câmera Real
        has_optical_camera, camera_desc = get_optical_camera_metadata(p_obj)

        # 4. Heurísticas de arquivo, formato e dimensões
        heuristic_bonus, hints = inspect_image_heuristics(p_obj)

        # 5. Fusão Multimodal dos Sinais
        final_score = (clip_prob * 0.70) + (heuristic_bonus * 0.40)

        if has_optical_camera:
            # Foto de câmera física autêntica: reduz score a menos que o sinal de meme seja esmagador
            if clip_prob < 0.88:
                final_score = final_score * 0.40
            else:
                final_score = final_score * 0.75
        else:
            # Imagem sem metadados ópticos de lente (comum em redes sociais/WhatsApp): leve bônus
            final_score = min(1.0, final_score + 0.08)

        final_score = float(np.clip(round(final_score, 3), 0.01, 0.99))

        # Ajuste do rótulo exibido
        if has_optical_camera and final_score < 0.50:
            cam_clean = (camera_desc or 'Câmera').replace("\x00", "").strip()
            display_label = f"Foto Pessoal ({cam_clean})"
        elif heuristic_bonus >= 0.28 and "Screenshot" in hints:
            display_label = "Print / Screenshot"
        elif heuristic_bonus >= 0.25 and "Sticker" in hints:
            display_label = "Figurinha / Sticker"
        else:
            display_label = best_cat_name
        display_label = display_label.replace("\x00", "").strip()

        is_web = final_score >= 0.50

        results[p_str] = {
            "score": final_score,
            "label": display_label,
            "is_web": is_web,
            "has_optical_camera": has_optical_camera,
            "camera": camera_desc,
            "hints": hints
        }

        if on_update and (i % up_interval == 0 or i == total_imgs - 1):
            on_update(results)

    web_detected = sum(1 for v in results.values() if v.get("is_web"))
    log_info(
        f"Detector Web Multimodal: {total_imgs} fotos avaliadas | "
        f"{web_detected} identificadas como Memes/Web (>=50% certeza)."
    )
    return results


def _clean_null_bytes(obj):
    if isinstance(obj, str):
        return obj.replace("\x00", "").strip()
    elif isinstance(obj, dict):
        return {_clean_null_bytes(k): _clean_null_bytes(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_clean_null_bytes(x) for x in obj]
    return obj


def load_web_photos_cache(root: Path, validate_paths: bool = False) -> Dict[str, dict]:
    cp = get_cache_paths(root)
    cache_file = cp.get("web_photos") or (cp["meta"].parent / "web_photos.json")
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cleaned = _clean_null_bytes(data)
                if validate_paths:
                    cleaned = {k: v for k, v in cleaned.items() if Path(k).is_file()}
                log_debug(f"Cache de fotos web carregado ({len(cleaned)} entradas) de: {cache_file}")
                return cleaned
        except Exception as e:
            log_error(f"Erro ao ler cache de fotos web em {cache_file}", e)
    return {}


def save_web_photos_cache(root: Path, data: Dict[str, dict]):
    cp = get_cache_paths(root)
    cache_file = cp.get("web_photos") or (cp["meta"].parent / "web_photos.json")
    try:
        cleaned = _clean_null_bytes(data)
        safe_atomic_write(cache_file, json.dumps(cleaned, ensure_ascii=False, indent=2))
        log_debug(f"Cache de fotos web salvo ({len(cleaned)} entradas) em: {cache_file}")
    except Exception as e:
        log_error(f"Erro ao salvar cache de fotos web em {cache_file}", e)
