import concurrent.futures as cf
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageFile, UnidentifiedImageError
from send2trash import send2trash
try:
    import torch
except ImportError:
    torch = None

ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = None

from src.config import (
    APP_CONFIG, PROGRAM_DIR, get_cache_paths, get_current_cache_dir,
    is_in_or_subdir, log_error, logger, safe_atomic_write
)
from src.embeddings import (
    compute_embeddings, load_embeddings
)
from src.hasher import file_key, sha256_file, phash_file
from src.scanner import collect_images
from src.clustering import (
    exact_groups, find_edges, load_edges, save_edges, edges_match,
    can_remap_edges, remap_edges,
    connected_components, phash_groups, load_phash_cache, save_phash_cache
)
from src.ignored_pairs import load_ignored_pairs, mark_photos_as_different, canonical_pair
from src.web_photo_detector import (
    compute_web_photo_scores, load_web_photos_cache, save_web_photos_cache
)


def load_cache(root):
    cp = get_cache_paths(root)
    target = cp["meta"]
    combined_files = {}

    candidates = [
        target,
        target.with_suffix(".tmp.json"),
        target.parent / "meta.json",
        get_current_cache_dir() / ".comparador_fotos_cache_v5.json",
        get_current_cache_dir() / ".comparador_fotos_cache_v5.tmp.json",
        PROGRAM_DIR / "cache" / ".comparador_fotos_cache_v5.json",
        PROGRAM_DIR / "cache" / ".comparador_fotos_cache_v5.tmp.json",
    ]

    for cand in candidates:
        if cand and cand.exists() and cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("files"), dict):
                    for k, v in data["files"].items():
                        if k not in combined_files or (isinstance(v, dict) and v.get("sha") and not combined_files[k].get("sha")):
                            combined_files[k] = v
            except Exception as e:
                log_error(f"Erro ao carregar cache de {cand}", e)

    if combined_files:
        result_cache = {"version": 5, "files": combined_files}
        try:
            if not target.exists() or (target.exists() and target.stat().st_size < 100000 and len(combined_files) > 1000):
                save_cache(root, result_cache)
        except Exception:
            pass
        return result_cache

    return {"version": 5, "files": {}}


def save_cache(root, cache):
    cp = get_cache_paths(root)
    target = cp["meta"]
    try:
        safe_atomic_write(target, json.dumps(cache, ensure_ascii=False))
    except Exception as e:
        log_error(f"Erro ao salvar cache em {target}", e)


def parallel_metadata(paths, cache, root, cb, stop_event, workers=None, on_update_exact=None):
    num_workers = workers or APP_CONFIG.get("cpu_workers") or (os.cpu_count() or 8)
    pool_workers = min(16, max(4, num_workers * 2))
    total = len(paths)
    started = time.time()
    result = {}
    done = 0
    since_save = 0

    def one(p):
        try:
            key = file_key(p)
        except Exception as e:
            return str(p), {"key": None, "error": str(e)}, False
        old = cache["files"].get(str(p))
        if old and old.get("key") == key and old.get("sha") and old.get("phash"):
            item = dict(old)
            item["key"] = key
            return str(p), item, True
        try:
            sha = sha256_file(p)
            ph = phash_file(p)
            return str(p), {"key": key, "sha": sha, "phash": ph}, False
        except (UnidentifiedImageError, OSError) as e:
            logger.warning(f"Arquivo corrompido ou ilegível ignorado: {p} ({e})")
            return str(p), {"key": key, "error": str(e)}, False
        except Exception as e:
            log_error(f"Erro ao extrair hash de {p}", e)
            return str(p), {"key": key, "error": str(e)}, False

    wave = max(pool_workers * 8, 128)
    with cf.ThreadPoolExecutor(max_workers=pool_workers) as ex:
        for start in range(0, total, wave):
            if stop_event.is_set():
                break
            futures = [ex.submit(one, p) for p in paths[start:start + wave]]
            for fut in cf.as_completed(futures):
                k, item, from_cache = fut.result()
                result[k] = item
                cache["files"][k] = item
                done += 1
                since_save += 1
                if since_save >= 1000:
                    save_cache(root, cache)
                    since_save = 0
                if done % 100 == 0 or done == total:
                    if on_update_exact:
                        on_update_exact(result)
                if done % 50 == 0 or done == total:
                    elapsed = max(0.001, time.time() - started)
                    rate = done / elapsed
                    rem = max(0, total - done)
                    eta = rem / rate if rate > 0 else 0
                    if cb:
                        cb({
                            "stage": "Hash/pHash", "done": done, "total": total,
                            "elapsed": elapsed, "rate": rate, "eta": eta,
                            "extra": "cache" if from_cache else f"threads={pool_workers}",
                            "stopped": False,
                        })

            if stop_event.is_set():
                break

    save_cache(root, cache)
    return result


class PhotoEngine:
    def __init__(self, folder, threshold=0.82, phash=10, workers=None, batch=None, k=50, callback=None, enable_clip=None, enable_hash=None, enable_web_detector=None, excluded_folders=None):
        if isinstance(folder, (list, tuple, set)):
            self.roots = [Path(f).expanduser().resolve() for f in folder]
            self.root = self.roots[0] if self.roots else Path(".").resolve()
            self.folder_input = list(self.roots)
        else:
            self.root = Path(folder).expanduser().resolve()
            self.roots = [self.root]
            self.folder_input = self.root
        self.threshold = threshold
        self.phash_distance = phash
        self.workers = workers or APP_CONFIG.get("cpu_workers") or (os.cpu_count() or 8)
        has_cuda = bool(torch is not None and hasattr(torch, "cuda") and torch.cuda.is_available())
        self.batch = batch or APP_CONFIG.get("batch_size") or (48 if has_cuda else 16)
        self.max_ram_pct = APP_CONFIG.get("max_ram_pct", 80)
        has_ai = False
        try:
            from src.dependency_installer import is_ai_available
            has_ai = is_ai_available()
        except Exception:
            has_ai = bool(torch is not None)

        cfg_clip = APP_CONFIG.get("enable_clip", True)
        cfg_hash = APP_CONFIG.get("enable_hash", True)
        cfg_web = APP_CONFIG.get("enable_web_detector", True)

        self.enable_clip = bool(enable_clip if enable_clip is not None else cfg_clip) and has_ai
        self.enable_web_detector = bool(enable_web_detector if enable_web_detector is not None else cfg_web) and has_ai

        req_hash = bool(enable_hash if enable_hash is not None else cfg_hash)
        if not has_ai and (enable_clip if enable_clip is not None else cfg_clip) and not req_hash:
            logger.info("IA indisponível no ambiente: ativando busca por Hash (SHA-256 e pHash).")
            self.enable_hash = True
        else:
            self.enable_hash = req_hash

        self.excluded_folders = excluded_folders if excluded_folders is not None else APP_CONFIG.get("excluded_folders", [])
        self.k = k
        self.callback = callback
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.deleted_paths = set()
        self.paths = []
        self.meta = {}
        self.clip_paths = []
        self.emb = np.empty((0, 512), dtype=np.float32)
        self.edges = []
        self.exact = []
        self.phash_groups = []
        self.ignored_pairs = load_ignored_pairs(self.root)
        self.web_photos = load_web_photos_cache(self.root)
        self.device = "cuda" if has_cuda else "cpu"
        self.batch_used = self.batch
        self.completed = False

    def stop(self):
        self.stop_event.set()

    def register_deleted_photo(self, path):
        """Registra a exclusão de uma foto em tempo de execução, mantendo sincronia instantânea em memória O(1)."""
        with getattr(self, "lock", threading.RLock()):
            p_obj = Path(path)
            str_raw = str(path)
            str_pobj = str(p_obj)
            targets = {str_raw, str_pobj, p_obj}
            try:
                targets.add(str(p_obj.resolve()))
            except Exception:
                pass

            target_strs = {t for t in targets if isinstance(t, str)}
            self.deleted_paths.update(target_strs)

            # Remoção rápida em memória sem invocar I/O de disco ou Path.resolve() em loop
            self.paths = [p for p in self.paths if p not in targets and str(p) not in target_strs]

            # Remapeia o grafo de arestas CLIP mantendo correspondência exata de índices
            clip_paths = getattr(self, "clip_paths", None)
            edges = getattr(self, "edges", None)
            if clip_paths and edges:
                new_clip_paths = []
                old_to_new = {}
                for old_idx, p in enumerate(clip_paths):
                    if p in targets or str(p) in target_strs:
                        continue
                    old_to_new[old_idx] = len(new_clip_paths)
                    new_clip_paths.append(p)
                self.clip_paths = new_clip_paths

                new_edges = []
                for a, b, sim in edges:
                    new_a = old_to_new.get(a)
                    new_b = old_to_new.get(b)
                    if new_a is not None and new_b is not None and new_a != new_b:
                        new_edges.append((new_a, new_b, sim))
                self.edges = new_edges
            elif clip_paths is not None:
                self.clip_paths = [p for p in clip_paths if p not in targets and str(p) not in target_strs]

            for t in target_strs:
                self.meta.pop(t, None)
                self.web_photos.pop(t, None)

            # Remove das listas de duplicatas exatas
            for g in list(self.exact):
                to_rem = [p for p in g if p in targets or str(p) in target_strs]
                for p in to_rem:
                    try:
                        g.remove(p)
                    except ValueError:
                        pass
                if len(g) <= 1:
                    try:
                        self.exact.remove(g)
                    except ValueError:
                        pass

            # Remove dos grupos pHash
            for g in list(self.phash_groups):
                to_rem = [p for p in g if p in targets or str(p) in target_strs]
                for p in to_rem:
                    try:
                        g.remove(p)
                    except ValueError:
                        pass
                if len(g) <= 1:
                    try:
                        self.phash_groups.remove(g)
                    except ValueError:
                        pass

            # Salva o cache de web_photos atualizado
            if self.root:
                save_web_photos_cache(self.root, self.web_photos)

    def load_partial_state(self):
        with self.lock:
            self.ignored_pairs = load_ignored_pairs(self.root)
            raw_web = load_web_photos_cache(self.root) if self.enable_web_detector else {}
            cache = load_cache(self.root)
            saved = load_edges(self.root) if self.enable_clip else None

            if not self.paths:
                if saved and len(saved.get("paths", [])) > 0:
                    self.paths = [Path(p) for p in saved["paths"]]
                elif cache.get("files"):
                    self.paths = [Path(p) for p in cache["files"].keys()]
                else:
                    scan_target = self.roots if len(self.roots) > 1 else self.root
                    self.paths = collect_images(scan_target, self.excluded_folders)

            # Filtra caminhos inexistentes ou deletados
            self.paths = [p for p in self.paths if Path(p).is_file() and str(p) not in self.deleted_paths]

            # Limpa web_photos de arquivos inexistentes
            if self.enable_web_detector:
                self.web_photos = {k: v for k, v in raw_web.items() if Path(k).is_file() and k not in self.deleted_paths}
            else:
                self.web_photos = {}

            self.meta = cache.get("files", {})
            if self.enable_hash:
                self.exact = exact_groups(self.paths, self.meta, ignored_pairs=self.ignored_pairs)
                p_groups = load_phash_cache(self.root, self.paths, self.meta, self.phash_distance)
                if p_groups is not None:
                    raw_groups = [[Path(x) for x in g] for g in p_groups]
                    if self.ignored_pairs:
                        filtered = []
                        for g in raw_groups:
                            rem = [p for p in g if not any(canonical_pair(p, o) in self.ignored_pairs for o in g if o != p)]
                            if len(rem) > 1:
                                filtered.append(rem)
                        self.phash_groups = filtered
                    else:
                        self.phash_groups = raw_groups
                else:
                    self.phash_groups = phash_groups(self.paths, self.meta, self.phash_distance, ignored_pairs=self.ignored_pairs)
            else:
                self.exact = []
                self.phash_groups = []

            if self.enable_clip:
                p_map, emb, _ = load_embeddings(self.root)
                if emb is not None and len(p_map) > 0:
                    self.clip_paths = [Path(p) for p in p_map.keys() if Path(p).is_file() and p not in self.deleted_paths]
                    valid_idx = [i for i, p in enumerate(p_map.keys()) if Path(p).is_file() and p not in self.deleted_paths]
                    self.emb = emb[valid_idx] if valid_idx else np.empty((0, 512), dtype=np.float32)

                if saved and len(saved.get("paths", [])) > 0:
                    saved_paths_str = [str(p) for p in saved["paths"]]
                    if not self.clip_paths:
                        self.clip_paths = [Path(p) for p in saved["paths"] if Path(p).is_file() and str(p) not in self.deleted_paths]

                    curr_paths_str = [str(p) for p in self.clip_paths]
                    if saved_paths_str == curr_paths_str:
                        self.edges = list(zip(saved["a"].tolist(), saved["b"].tolist(), saved["sim"].tolist()))
                    else:
                        curr_path_to_new_idx = {p_str: i for i, p_str in enumerate(curr_paths_str)}
                        remapped = []
                        for old_a, old_b, sim in zip(saved["a"].tolist(), saved["b"].tolist(), saved["sim"].tolist()):
                            if old_a < len(saved_paths_str) and old_b < len(saved_paths_str):
                                p_a = saved_paths_str[old_a]
                                p_b = saved_paths_str[old_b]
                                new_a = curr_path_to_new_idx.get(p_a)
                                new_b = curr_path_to_new_idx.get(p_b)
                                if new_a is not None and new_b is not None and new_a != new_b:
                                    remapped.append((new_a, new_b, float(sim)))
                        self.edges = remapped
            else:
                self.clip_paths = []
                self.emb = np.empty((0, 512), dtype=np.float32)
                self.edges = []

    def run(self):
        started = time.time()
        self.ignored_pairs = load_ignored_pairs(self.root)
        self.web_photos = load_web_photos_cache(self.root) if self.enable_web_detector else {}
        if self.callback:
            self.callback({
                "stage": "Varredura", "done": 0, "total": 0, "elapsed": 0,
                "rate": 0, "eta": 0, "extra": "coletando arquivos...", "stopped": False
            })

        scan_target = self.roots if len(self.roots) > 1 else self.root
        self.paths = collect_images(scan_target, self.excluded_folders)
        if not self.paths:
            if self.callback:
                self.callback({
                    "stage": "Concluído", "done": 0, "total": 0, "elapsed": 0,
                    "rate": 0, "eta": 0, "extra": "Nenhuma imagem encontrada.", "stopped": False
                })
            return self

        cache = load_cache(self.root)

        # ETAPA 1, 2 & 3: Metadados, Hashes (SHA-256 e pHash)
        if self.enable_hash:
            def on_live_exact(meta_dict):
                with self.lock:
                    self.meta = meta_dict
                    self.exact = exact_groups(self.paths, self.meta, ignored_pairs=self.ignored_pairs)

            self.meta = parallel_metadata(
                self.paths, cache, self.root, self.callback, self.stop_event, self.workers,
                on_update_exact=on_live_exact
            )
            with self.lock:
                self.exact = exact_groups(self.paths, self.meta, ignored_pairs=self.ignored_pairs)

            if self.stop_event.is_set():
                self.load_partial_state()
                return self

            cached_ph = load_phash_cache(self.root, self.paths, self.meta, self.phash_distance)
            if cached_ph is not None:
                raw_groups = [[Path(x) for x in g] for g in cached_ph]
                with self.lock:
                    if self.ignored_pairs:
                        self.phash_groups = phash_groups(self.paths, self.meta, self.phash_distance, ignored_pairs=self.ignored_pairs)
                    else:
                        self.phash_groups = raw_groups
            else:
                if self.callback:
                    self.callback({
                        "stage": "Grupos pHash", "done": 0, "total": len(self.paths),
                        "elapsed": 0, "rate": 0, "eta": 0, "extra": "agrupando quase idênticas antecipadamente...", "stopped": False
                    })
                with self.lock:
                    self.phash_groups = phash_groups(self.paths, self.meta, self.phash_distance, ignored_pairs=self.ignored_pairs)
                save_phash_cache(
                    self.root, self.paths, self.meta, self.phash_distance,
                    [[str(x) for x in g] for g in self.phash_groups]
                )
        else:
            self.meta = cache.get("files", {})
            with self.lock:
                self.exact = []
                self.phash_groups = []

        if self.stop_event.is_set():
            self.load_partial_state()
            return self

        # ETAPA 4 & 5: Embeddings CLIP e Grafo de Similaridade Visual
        if self.enable_clip:
            self.clip_paths, self.emb, self.device, self.batch_used = compute_embeddings(
                self.paths, self.root, cache, self.callback, self.stop_event, self.batch,
                workers=self.workers, max_ram_pct=self.max_ram_pct
            )

            with self.lock:
                valid_indices = [
                    i for i, p in enumerate(self.clip_paths)
                    if str(p) not in self.deleted_paths and Path(p).exists()
                ]
                if len(valid_indices) < len(self.clip_paths):
                    self.clip_paths = [self.clip_paths[i] for i in valid_indices]
                    if self.emb is not None and len(self.emb) > 0:
                        self.emb = self.emb[valid_indices]
                if self.enable_hash:
                    self.exact = exact_groups(self.paths, self.meta, ignored_pairs=self.ignored_pairs)

            if self.stop_event.is_set():
                self.load_partial_state()
                return self

            keys = [file_key(Path(p)) for p in self.clip_paths]
            saved = load_edges(self.root)
            if edges_match(saved, self.clip_paths, keys, self.k, 0.72):
                with self.lock:
                    self.edges = list(zip(saved["a"].tolist(), saved["b"].tolist(), saved["sim"].tolist()))
                if self.callback:
                    self.callback({
                        "stage": "Vizinhos CLIP", "done": len(self.clip_paths), "total": len(self.clip_paths),
                        "elapsed": 0, "rate": 0, "eta": 0, "extra": "cache de comparações reutilizado (100%)", "stopped": False
                    })
            elif saved and can_remap_edges(saved, self.clip_paths, keys, self.k, 0.72):
                remapped_edges = remap_edges(saved, self.clip_paths, keys)
                with self.lock:
                    self.edges = remapped_edges
                save_edges(self.root, self.clip_paths, keys, self.edges, self.k, 0.72)
                if self.callback:
                    self.callback({
                        "stage": "Vizinhos CLIP", "done": len(self.clip_paths), "total": len(self.clip_paths),
                        "elapsed": 0, "rate": 0, "eta": 0, "extra": "cache de comparações remapeado com sucesso", "stopped": False
                    })
            else:
                found_edges = find_edges(
                    self.emb, self.k, 0.72, self.callback, self.stop_event
                )
                with self.lock:
                    self.edges = found_edges
                save_edges(self.root, self.clip_paths, keys, self.edges, self.k, 0.72)
        else:
            with self.lock:
                self.clip_paths = []
                self.emb = np.empty((0, 512), dtype=np.float32)
                self.edges = []

        if self.stop_event.is_set():
            self.load_partial_state()
            return self

        # Classificação Multimodal de Fotos da Web / Memes
        if self.enable_web_detector:
            existing_str_set = {str(p) for p in self.paths if Path(p).is_file() and str(p) not in self.deleted_paths}
            with self.lock:
                self.web_photos = {k: v for k, v in self.web_photos.items() if k in existing_str_set and Path(k).is_file()}

            # Se CLIP não foi executado antes para todas as fotos, computamos embeddings necessários para a classificação web
            if (not self.clip_paths or self.emb is None or len(self.emb) == 0) and self.paths:
                todo_web_paths = [p for p in self.paths if str(p) not in self.web_photos and Path(p).is_file() and str(p) not in self.deleted_paths]
                if todo_web_paths:
                    w_paths, w_emb, dev, _ = compute_embeddings(
                        todo_web_paths, self.root, cache, self.callback, self.stop_event, self.batch,
                        workers=self.workers, max_ram_pct=self.max_ram_pct
                    )
                    self.device = dev
                else:
                    w_paths, w_emb = [], np.empty((0, 512), dtype=np.float32)
                eval_paths = w_paths
                eval_emb = w_emb
            else:
                eval_paths = self.clip_paths
                eval_emb = self.emb

            todo_paths = [p for p in eval_paths if str(p) not in self.web_photos and Path(p).is_file() and str(p) not in self.deleted_paths]
            should_run_web = (
                len(eval_paths) > 0 and
                eval_emb is not None and
                len(eval_emb) > 0 and
                len(todo_paths) > 0
            )
            if should_run_web:
                if self.callback:
                    self.callback({
                        "stage": "Classificação Web", "done": 0, "total": len(todo_paths),
                        "elapsed": 0, "rate": 0, "eta": 0, "extra": f"classificando {len(todo_paths)} fotos novas com IA...", "stopped": False
                    })
                try:
                    from src.embeddings import get_model
                    clip_model, _, _ = get_model()

                    def on_live_web(curr_web_dict):
                        with self.lock:
                            self.web_photos.update(curr_web_dict)
                        if self.callback:
                            self.callback({
                                "stage": "Classificação Web", "done": len(curr_web_dict), "total": len(todo_paths),
                                "elapsed": 0, "rate": 0, "eta": 0,
                                "extra": f"analisadas {len(curr_web_dict)}/{len(todo_paths)} fotos...", "stopped": False
                            })

                    if not self.web_photos:
                        todo_emb = eval_emb
                    else:
                        path_to_idx = {str(p): i for i, p in enumerate(eval_paths)}
                        todo_indices = [path_to_idx[str(p)] for p in todo_paths if str(p) in path_to_idx]
                        todo_emb = eval_emb[todo_indices] if todo_indices else np.empty((0, 512), dtype=np.float32)

                    if len(todo_paths) > 0 and len(todo_emb) > 0:
                        new_scores = compute_web_photo_scores(
                            todo_paths, todo_emb, clip_model, self.device,
                            on_update=on_live_web, stop_event=self.stop_event
                        )
                        with self.lock:
                            self.web_photos.update(new_scores)
                        save_web_photos_cache(self.root, self.web_photos)
                except Exception as e:
                    log_error("Erro ao classificar fotos da web", e)
        else:
            with self.lock:
                self.web_photos = {}

        self.completed = not self.stop_event.is_set()
        if self.callback:
            elapsed = time.time() - started
            msg_parts = []
            if self.enable_hash:
                msg_parts.append(f"{len(self.exact)} grupos exatos; {len(self.phash_groups)} pHash")
            if self.enable_clip:
                msg_parts.append(f"{len(self.edges)} arestas CLIP")
            if self.enable_web_detector:
                msg_parts.append(f"{len(self.web_photos)} fotos web analisadas")
            summary_str = " | ".join(msg_parts) if msg_parts else "Nenhum tipo de busca selecionado"
            extra_msg = f"Finalizado em {elapsed:.1f}s — {summary_str}"
            self.callback({
                "stage": "Concluído", "done": len(self.paths), "total": len(self.paths),
                "elapsed": elapsed, "rate": 0, "eta": 0,
                "extra": extra_msg, "stopped": self.stop_event.is_set()
            })
        return self

    def recompute_web_photos(self, callback=None):
        """Força o recálculo de todas as fotos da pasta com o algoritmo multimodal atualizado."""
        try:
            # 1. Garante lista atualizada de imagens existentes no disco
            current_paths = collect_images(self.root, self.excluded_folders)
            valid_paths = [p for p in current_paths if str(p) not in self.deleted_paths and Path(p).is_file()]
            if not valid_paths:
                with self.lock:
                    self.web_photos = {}
                save_web_photos_cache(self.root, {})
                return {}

            with self.lock:
                self.paths = valid_paths

            # 2. Carrega ou computa embeddings CLIP necessários
            cache = load_cache(self.root)
            old_map, old_emb, old_keys = load_embeddings(self.root)

            current_keys = {
                str(p): cache.get("files", {}).get(str(p), {}).get("key") or file_key(p)
                for p in valid_paths
            }

            embeddings_ready = False
            clip_paths_to_use = []
            emb_to_use = None

            if (self.emb is not None and len(self.emb) == len(valid_paths) and
                [str(p) for p in self.clip_paths] == [str(p) for p in valid_paths]):
                embeddings_ready = True
                clip_paths_to_use = self.clip_paths
                emb_to_use = self.emb
            elif old_emb is not None and old_map:
                emb_list = []
                all_found = True
                for p in valid_paths:
                    sp = str(p)
                    idx = old_map.get(sp)
                    if idx is not None and old_keys.get(sp) == current_keys.get(sp):
                        emb_list.append(old_emb[idx])
                    else:
                        all_found = False
                        break
                if all_found and len(emb_list) == len(valid_paths):
                    embeddings_ready = True
                    clip_paths_to_use = valid_paths
                    emb_to_use = np.stack(emb_list).astype(np.float32)
                    with self.lock:
                        self.clip_paths = valid_paths
                        self.emb = emb_to_use

            if not embeddings_ready:
                def emb_cb(info):
                    if callback and isinstance(info, dict):
                        done = info.get("done", 0)
                        tot = info.get("total", len(valid_paths))
                        callback(done // 2, tot)

                new_clip_paths, new_emb, dev, _ = compute_embeddings(
                    valid_paths, self.root, cache, emb_cb, self.stop_event, self.batch,
                    workers=self.workers, max_ram_pct=self.max_ram_pct
                )
                with self.lock:
                    self.clip_paths = new_clip_paths
                    self.emb = new_emb
                    self.device = dev
                clip_paths_to_use = self.clip_paths
                emb_to_use = self.emb

            if self.stop_event.is_set():
                return self.web_photos

            from src.embeddings import get_model
            clip_model, _, _ = get_model()
            log_info(f"Reanalisando {len(clip_paths_to_use)} fotos para memes/web...")

            def on_up(curr_dict):
                with self.lock:
                    self.web_photos = dict(curr_dict)
                if callback:
                    callback(len(curr_dict), len(clip_paths_to_use))

            res = compute_web_photo_scores(
                clip_paths_to_use, emb_to_use, clip_model, self.device,
                on_update=on_up, stop_event=self.stop_event
            )

            # Filtra estritamente fotos existentes no disco
            clean_res = {k: v for k, v in res.items() if Path(k).is_file() and k not in self.deleted_paths}

            with self.lock:
                self.web_photos = clean_res
            save_web_photos_cache(self.root, self.web_photos)
            return self.web_photos
        except Exception as e:
            log_error("Erro ao recomputar fotos da web", e)
            return self.web_photos

    def groups(self, threshold=None):
        with getattr(self, "lock", threading.RLock()):
            t = threshold if threshold is not None else self.threshold
            idx_groups = connected_components(
                len(self.clip_paths), self.edges, t,
                clip_paths=self.clip_paths, ignored_pairs=self.ignored_pairs
            )
            result = []
            for g in idx_groups:
                if len(g) > 1:
                    result.append([Path(self.clip_paths[i]) for i in g])
            return result

    def mark_photos_different(self, photos):
        """Marca o par/grupo de fotos como fotos diferentes e atualiza coleções de forma instantânea O(1)."""
        with getattr(self, "lock", threading.RLock()):
            self.ignored_pairs = mark_photos_as_different(self.root, photos, self.ignored_pairs)
            photo_strs = {str(p) for p in photos}

            def clean_group(g):
                overlap = [p for p in g if str(p) in photo_strs]
                if len(overlap) >= 2:
                    remaining = []
                    for p in g:
                        p_str = str(p)
                        conflict = any(
                            canonical_pair(p, other) in self.ignored_pairs
                            for other in g if str(other) != p_str
                        )
                        if not conflict:
                            remaining.append(p)
                    return remaining if len(remaining) > 1 else None
                return g

            self.exact = [cg for cg in (clean_group(g) for g in self.exact) if cg and len(cg) > 1]
            self.phash_groups = [cg for cg in (clean_group(g) for g in self.phash_groups) if cg and len(cg) > 1]
            return self.exact

    def get_web_photos(self, min_score=0.50):
        """Retorna lista de tuplas (path, info) classificadas como imagem da web/meme de forma rápida em memória."""
        if not self.enable_web_detector or not self.web_photos:
            return []
        valid = []
        stale_keys = []
        with getattr(self, "lock", threading.RLock()):
            items_snapshot = list(self.web_photos.items())
            deleted = set(getattr(self, "deleted_paths", set()))

        for p_str, info in items_snapshot:
            if not isinstance(info, dict):
                continue
            if p_str in deleted:
                continue
            p = Path(p_str)
            # Verifica existência física do arquivo para impedir exibição de fotos já deletadas
            if not p.is_file():
                stale_keys.append(p_str)
                continue
            try:
                score = float(info.get("score", 0))
            except (TypeError, ValueError):
                score = 0.0
            if score >= min_score:
                valid.append((p, info))

        if stale_keys:
            with getattr(self, "lock", threading.RLock()):
                self.deleted_paths.update(stale_keys)
                for sk in stale_keys:
                    self.web_photos.pop(sk, None)
                save_web_photos_cache(self.root, self.web_photos)

        valid.sort(key=lambda x: x[1].get("score", 0), reverse=True)
        return valid

    def get_web_photo_groups(self, min_score=0.50, group_size=4):
        """Agrupa fotos da web/memes em blocos de group_size para visualização em grade."""
        web_list = self.get_web_photos(min_score)
        paths = [p for p, _ in web_list]
        return [paths[i:i + group_size] for i in range(0, len(paths), group_size)]


def get_photo_quality_score(p):
    """
    Retorna uma tupla (resolução_em_pixels, tamanho_bytes) para comparar qualidade.
    Resoluções maiores vencem; se empatarem, vence o maior tamanho de arquivo.
    """
    try:
        st = Path(p).stat()
        sz = st.st_size
    except Exception:
        sz = 0
    try:
        with Image.open(p) as im:
            w, h = im.size
        pixels = w * h
    except Exception:
        pixels = 0
    return (pixels, sz)


def swap_file_locations(path1, path2):
    """
    Troca fisicamente de lugar os arquivos path1 e path2 no disco de forma atômica/segura.
    Retorna a tupla com os novos caminhos físicos (new_path1, new_path2).
    """
    p1 = Path(path1).resolve()
    p2 = Path(path2).resolve()
    if p1 == p2 or not p1.exists() or not p2.exists():
        return str(p1), str(p2)

    d1, d2 = p1.parent, p2.parent
    if d1 == d2:
        # Mesmo diretório: apenas troca de nomes via arquivo temporário
        tmp = d1 / f"{p1.stem}_swaptmp_{uuid.uuid4().hex[:6]}{p1.suffix}"
        shutil.move(str(p1), str(tmp))
        shutil.move(str(p2), str(p1))
        shutil.move(str(tmp), str(p2))
        return str(p2), str(p1)

    # Diretórios diferentes: p1 vai para d2, p2 vai para d1
    target2_in_d1 = d1 / p2.name
    target1_in_d2 = d2 / p1.name

    if target2_in_d1.exists() and target2_in_d1 != p1:
        target2_in_d1 = d1 / f"{p2.stem}_swapped_{uuid.uuid4().hex[:4]}{p2.suffix}"
    if target1_in_d2.exists() and target1_in_d2 != p2:
        target1_in_d2 = d2 / f"{p1.stem}_swapped_{uuid.uuid4().hex[:4]}{p1.suffix}"

    tmp = d1 / f"{p1.stem}_swaptmp_{uuid.uuid4().hex[:6]}{p1.suffix}"
    shutil.move(str(p1), str(tmp))
    shutil.move(str(p2), str(target2_in_d1))
    shutil.move(str(tmp), str(target1_in_d2))

    return str(target2_in_d1), str(target1_in_d2)


def upgrade_photo_in_folder(larger_path, smaller_path):
    """
    Move a foto de maior qualidade (larger_path) para a pasta da menor (smaller_path),
    e envia a menor para a Lixeira do Windows.
    Retorna o novo caminho da foto de melhor qualidade.
    """
    larger = Path(larger_path).resolve()
    smaller = Path(smaller_path).resolve()
    if not larger.exists() or not smaller.exists():
        return str(larger)

    dest_dir = smaller.parent
    target = dest_dir / larger.name

    if target == smaller:
        # Mesmo nome de arquivo: move larger para temporário, apaga smaller e renomeia
        tmp = dest_dir / f"{larger.stem}_upgtmp_{uuid.uuid4().hex[:6]}{larger.suffix}"
        shutil.move(str(larger), str(tmp))
        send2trash(str(smaller))
        shutil.move(str(tmp), str(target))
        return str(target)
    else:
        if target.exists():
            target = dest_dir / f"{larger.stem}_upg_{uuid.uuid4().hex[:4]}{larger.suffix}"
        shutil.move(str(larger), str(target))
        send2trash(str(smaller))
        return str(target)


def partition_groups_by_priority(all_groups, keep_folders, del_folders, include_subdirs=True, upgrade_quality=False, return_upgrades=False):
    """
    Aplica regras de prioridade de pastas nos grupos detectados.
    Se upgrade_quality=True, se a melhor foto estiver fora da pasta mantida,
    ela é marcada para upgrade (substituir a foto de menor qualidade na pasta mantida).
    """
    to_keep = set()
    to_delete = set()
    to_upgrade = []

    for group in all_groups:
        keep_in_group = [
            p for p in group
            if any(is_in_or_subdir(p, kf, include_subdirs) for kf in keep_folders)
        ]
        del_in_group = [
            p for p in group
            if any(is_in_or_subdir(p, df, include_subdirs) for df in del_folders)
        ]

        if keep_in_group and (del_in_group or len(group) > len(keep_in_group)):
            candidates_outside = [p for p in group if p not in keep_in_group]
            if upgrade_quality and candidates_outside:
                best_keep = max(keep_in_group, key=get_photo_quality_score)
                best_outside = max(candidates_outside, key=get_photo_quality_score)
                score_keep = get_photo_quality_score(best_keep)
                score_outside = get_photo_quality_score(best_outside)
                if score_outside > score_keep:
                    to_upgrade.append((best_outside, best_keep))
                    to_keep.add(best_outside)
                    to_delete.add(best_keep)
                    for p in group:
                        if p != best_outside:
                            to_delete.add(p)
                    continue

            for p in keep_in_group:
                to_keep.add(p)
            for p in group:
                if p not in to_keep:
                    to_delete.add(p)

        elif del_in_group and not keep_in_group:
            candidates = [p for p in group if p not in del_in_group]
            master = candidates[0] if candidates else None
            for p in del_in_group:
                if p != master:
                    to_delete.add(p)

    if return_upgrades or upgrade_quality:
        return to_keep, to_delete, to_upgrade
    return to_keep, to_delete

