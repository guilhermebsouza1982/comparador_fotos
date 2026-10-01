import concurrent.futures as cf
import gc
import os
import queue
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFile, UnidentifiedImageError
try:
    import torch
except ImportError:
    torch = None

try:
    import open_clip
except ImportError:
    open_clip = None

from src.config import APP_CONFIG, get_cache_paths, log_error, logger, safe_replace
from src.hardware import get_system_ram_info
from src.hasher import file_key

ImageFile.LOAD_TRUNCATED_IMAGES = True

_MODEL = None
_PREPROCESS = None
_DEVICE = None


def get_model():
    global _MODEL, _PREPROCESS, _DEVICE
    if torch is None or open_clip is None:
        raise RuntimeError(
            "PyTorch ou OpenCLIP não estão instalados neste computador. "
            "Instale os aceleradores de IA através do menu ou utilize o modo rápido por Hashes (SHA-256 e pHash)."
        )
    if _MODEL is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        m, _, prep = open_clip.create_model_and_transforms("ViT-B-32-quickgelu", pretrained="openai")
        m = m.to(device).eval()
        _MODEL = m
        _PREPROCESS = prep
        _DEVICE = device
    return _MODEL, _PREPROCESS, _DEVICE


def choose_batch(device):
    if torch is None or device != "cuda":
        return 16
    try:
        vram = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
    except Exception:
        vram = 4
    if vram <= 4.5:
        return 48
    if vram <= 6.5:
        return 64
    if vram <= 10:
        return 96
    return 128


def load_embeddings(root):
    cp = get_cache_paths(root)
    p = cp["embeddings"]
    if not p.exists():
        return {}, None, {}
    try:
        z = np.load(p, allow_pickle=False)
        paths = z["paths"].astype(str).tolist()
        emb = z["embeddings"].astype(np.float32)
        keys = z["keys"].astype(str).tolist() if "keys" in z else [""] * len(paths)
        return {p: i for i, p in enumerate(paths)}, emb, {p: keys[i] for i, p in enumerate(paths)}
    except Exception as e:
        log_error(f"Erro ao carregar embeddings de {p}", e)
        return {}, None, {}


def save_embeddings(root, paths, keys, embeddings):
    cp = get_cache_paths(root)
    target = cp["embeddings"]
    tmp = target.with_suffix(".tmp.npz")
    try:
        np.savez_compressed(
            tmp,
            paths=np.array(paths, dtype=str),
            keys=np.array(keys, dtype=str),
            embeddings=embeddings.astype(np.float32),
        )
        safe_replace(tmp, target)
    except Exception as e:
        log_error(f"Erro ao salvar embeddings em {target}", e)


def compute_embeddings(paths, root, cache, cb, stop_event, batch_size=None, workers=None, max_ram_pct=None):
    old_map, old_emb, old_keys = load_embeddings(root)
    current_keys = {
        str(p): cache["files"].get(str(p), {}).get("key") or file_key(p)
        for p in paths
    }

    model, preprocess, device = get_model()
    batch_size = batch_size or APP_CONFIG.get("batch_size") or choose_batch(device)
    num_workers = workers or APP_CONFIG.get("cpu_workers") or (os.cpu_count() or 8)
    ram_limit = max_ram_pct or APP_CONFIG.get("max_ram_pct", 80)

    kept = {}
    todo = []
    for p in paths:
        sp = str(p)
        idx = old_map.get(sp)
        if idx is not None and old_emb is not None and old_keys.get(sp) == current_keys[sp]:
            kept[sp] = old_emb[idx]
        else:
            todo.append(p)

    vectors = dict(kept)
    total = len(paths)
    done = len(kept)
    started = time.time()

    if cb:
        cb({
            "stage": "CLIP/GPU",
            "done": done,
            "total": total,
            "elapsed": 0,
            "rate": 0,
            "eta": 0,
            "extra": f"cache={len(kept)}; novos={len(todo)}; device={device}; batch={batch_size}; workers={num_workers}",
            "stopped": False,
        })

    def checkpoint():
        if not vectors:
            return
        ordered = [str(p) for p in paths if str(p) in vectors]
        arr = np.stack([vectors[p] for p in ordered]).astype(np.float32)
        save_embeddings(root, ordered, [current_keys[p] for p in ordered], arr)

    def load_single_image(p):
        try:
            st = Path(p).stat()
            if st.st_size == 0:
                return str(p), None
            with open(p, "rb") as f:
                head = f.read(16)
            if head.startswith(b"PK") or not head or set(head) == {0}:
                return str(p), None
            with Image.open(p) as im:
                im = im.convert("RGB")
                tensor = preprocess(im)
                return str(p), tensor
        except (UnidentifiedImageError, OSError) as e:
            logger.warning(f"Imagem corrompida ignorada no CLIP: {p} ({e})")
            return str(p), None
        except Exception as e:
            log_error(f"Erro inesperado ao processar imagem para CLIP: {p}", e)
            return str(p), None

    batch_queue = queue.Queue(maxsize=12)

    def prefetch_producer():
        with cf.ThreadPoolExecutor(max_workers=num_workers) as pool:
            for start in range(0, len(todo), batch_size):
                if stop_event.is_set():
                    break
                chunk = todo[start:start + batch_size]

                # Monitoramento de RAM seguro: se RAM estiver muito apertada (< 600 MB livres ou > 95%),
                # roda coleta de lixo e pausa brevemente, prosseguindo com segurança sem travar
                ram = get_system_ram_info()
                if ram.get("avail_gb", 2.0) < 0.6 or ram.get("load_pct", 50) >= max(ram_limit, 93):
                    gc.collect()
                    time.sleep(0.3)

                results = list(pool.map(load_single_image, chunk))
                tensors = [t for _, t in results if t is not None]
                good_paths = [p_str for p_str, t in results if t is not None]

                if tensors:
                    batch_queue.put((good_paths, tensors))

        batch_queue.put(None)

    producer_thread = threading.Thread(target=prefetch_producer, daemon=True)
    producer_thread.start()

    since_checkpoint = 0
    while True:
        if stop_event.is_set():
            break

        try:
            item = batch_queue.get(timeout=0.3)
        except queue.Empty:
            if not producer_thread.is_alive():
                break
            continue

        if item is None:
            break

        good_paths, tensors = item
        if tensors:
            x = torch.stack(tensors).to(device, non_blocking=True)
            try:
                with torch.no_grad():
                    feats = model.encode_image(x)
                    feats = feats / feats.norm(dim=-1, keepdim=True)
                feats = feats.cpu().numpy()
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                batch_size = max(8, batch_size // 2)
                logger.warning(f"VRAM cheia; reduzindo batch para {batch_size}")
                with torch.no_grad():
                    feats = model.encode_image(x)
                    feats = feats / feats.norm(dim=-1, keepdim=True)
                feats = feats.cpu().numpy()

            for p_str, v in zip(good_paths, feats):
                vectors[p_str] = v
                done += 1
                since_checkpoint += 1

            if since_checkpoint >= 300:
                checkpoint()
                since_checkpoint = 0

            elapsed = max(0.001, time.time() - started)
            rate = done / elapsed
            rem = max(0, total - done)
            eta = rem / rate if rate > 0 else 0
            if cb:
                ram_curr = get_system_ram_info()
                cb({
                    "stage": "CLIP/GPU",
                    "done": done,
                    "total": total,
                    "elapsed": elapsed,
                    "rate": rate,
                    "eta": eta,
                    "extra": f"GPU: {device}; batch: {batch_size}; workers: {num_workers}; RAM: {ram_curr['load_pct']}% / {ram_limit}%",
                    "stopped": False,
                })

    checkpoint()

    ordered = [str(p) for p in paths if str(p) in vectors]
    if ordered:
        arr = np.stack([vectors[p] for p in ordered]).astype(np.float32)
    else:
        arr = np.empty((0, 512), dtype=np.float32)

    return ordered, arr, device, batch_size
