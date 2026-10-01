import json
import time
from collections import defaultdict

import numpy as np
try:
    from sklearn.neighbors import NearestNeighbors
except ImportError:
    NearestNeighbors = None

from src.config import get_cache_paths, log_error, safe_atomic_write, safe_replace
from src.ignored_pairs import canonical_pair, canonical_path


def chunk_group_to_max(group, max_size=6):
    """
    Divide um grupo em subgrupos com no máximo max_size itens (padrão 6).
    Se len(group) <= max_size, retorna [group].
    Se len(group) > max_size, divide equilibradamente de modo que nenhum subgrupo exceda max_size.
    """
    n = len(group)
    if n <= max_size:
        return [group]
    k = (n + max_size - 1) // max_size
    base = n // k
    rem = n % k
    chunks = []
    start = 0
    for i in range(k):
        size = base + (1 if i < rem else 0)
        chunks.append(group[start:start + size])
        start += size
    return chunks


def exact_groups(paths, meta, ignored_pairs=None):
    by_sha = defaultdict(list)
    for p in paths:
        sp = str(p)
        info = meta.get(sp)
        if not info:
            continue
        sha = info.get("sha")
        if sha:
            by_sha[sha].append(p)

    final_groups = []
    for sha, group_paths in by_sha.items():
        if len(group_paths) <= 1:
            continue
        if not ignored_pairs:
            final_groups.append(group_paths)
            continue

        m = len(group_paths)
        parent = list(range(m))
        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        def union(i, j):
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri

        for i in range(m):
            for j in range(i + 1, m):
                if canonical_pair(group_paths[i], group_paths[j]) not in ignored_pairs:
                    union(i, j)

        groups_map = defaultdict(list)
        for i in range(m):
            root = find(i)
            groups_map[root].append(group_paths[i])

        for g in groups_map.values():
            if len(g) > 1:
                final_groups.append(g)

    chunked = []
    for g in final_groups:
        chunked.extend(chunk_group_to_max(g, max_size=6))
    return chunked


def load_phash_cache(root, paths, meta, distance):
    cp = get_cache_paths(root)
    p = cp["phash"]
    try:
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("distance") != distance:
            return None
        current = [str(x) for x in paths]
        current_set = set(current)
        keys = [meta.get(x, {}).get("key", "") for x in current]

        if data.get("paths") == current and data.get("keys") == keys:
            return data.get("groups", [])

        # Se os arquivos salvos contêm a quase totalidade dos caminhos atuais (ex: fotos excluídas)
        saved_paths = data.get("paths", [])
        saved_keys_map = dict(zip(saved_paths, data.get("keys", [])))
        if len(current) > 0:
            matches = sum(1 for p_str, k in zip(current, keys) if saved_keys_map.get(p_str) == k)
            if (matches / len(current)) >= 0.95:
                clean_groups = []
                for g in data.get("groups", []):
                    clean_g = [
                        p_str for p_str in g
                        if p_str in current_set and saved_keys_map.get(p_str) == meta.get(p_str, {}).get("key", "")
                    ]
                    if len(clean_g) > 1:
                        clean_groups.append(clean_g)
                return clean_groups
        return None
    except Exception as e:
        log_error(f"Erro ao ler cache pHash {p}", e)
        return None


def save_phash_cache(root, paths, meta, distance, groups):
    cp = get_cache_paths(root)
    target = cp["phash"]
    current = [str(x) for x in paths]
    data = {
        "version": 5,
        "distance": distance,
        "paths": current,
        "keys": [meta.get(x, {}).get("key", "") for x in current],
        "groups": groups,
    }
    try:
        safe_atomic_write(target, json.dumps(data, ensure_ascii=False))
    except Exception as e:
        log_error(f"Erro ao salvar cache pHash em {target}", e)


def phash_groups(paths, meta, max_dist=10, ignored_pairs=None, max_cluster_size=16):
    valid = []
    for p in paths:
        info = meta.get(str(p), {})
        h_str = info.get("phash")
        if h_str and h_str != "None":
            try:
                # 64-bit uint conversion for instant POPCNT Hamming distance
                valid.append((p, int(h_str, 16)))
            except Exception:
                pass

    n = len(valid)
    if n <= 1:
        return []

    parent = list(range(n))
    size = [1] * n

    def find(i):
        path = []
        while parent[i] != i:
            path.append(i)
            i = parent[i]
        for node in path:
            parent[node] = i
        return i

    def union(i, j):
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            if max_cluster_size is None or (size[root_i] + size[root_j] <= max_cluster_size):
                parent[root_j] = root_i
                size[root_i] += size[root_j]

    # Processamento vetorial em blocos para coleções grandes (10k a 50k+ fotos)
    if hasattr(np, "bitwise_count") and n >= 256:
        hash_arr = np.array([h for _, h in valid], dtype=np.uint64)
        block = 2048
        for i_start in range(0, n, block):
            i_end = min(i_start + block, n)
            a_block = hash_arr[i_start:i_end]
            for j_start in range(i_start, n, block):
                j_end = min(j_start + block, n)
                b_block = hash_arr[j_start:j_end]
                diff = np.bitwise_count(a_block[:, None] ^ b_block[None, :])
                if i_start == j_start:
                    ii, jj = np.where(np.triu(diff <= max_dist, k=1))
                else:
                    ii, jj = np.where(diff <= max_dist)
                for i_sub, j_sub in zip(ii, jj):
                    idx_i = i_start + int(i_sub)
                    idx_j = j_start + int(j_sub)
                    p_i = valid[idx_i][0]
                    p_j = valid[idx_j][0]
                    if ignored_pairs and canonical_pair(p_i, p_j) in ignored_pairs:
                        continue
                    union(idx_i, idx_j)
    else:
        for i in range(n):
            p_i, h_i = valid[i]
            for j in range(i + 1, n):
                p_j, h_j = valid[j]
                if (h_i ^ h_j).bit_count() <= max_dist:
                    if ignored_pairs and canonical_pair(p_i, p_j) in ignored_pairs:
                        continue
                    union(i, j)

    groups_map = defaultdict(list)
    for i in range(n):
        root = find(i)
        groups_map[root].append(valid[i][0])

    raw_groups = [g for g in groups_map.values() if len(g) > 1]
    chunked = []
    for g in raw_groups:
        chunked.extend(chunk_group_to_max(g, max_size=6))
    return chunked


def load_edges(root):
    cp = get_cache_paths(root)
    p = cp["edges"]
    if not p.exists():
        return None
    try:
        z = np.load(p, allow_pickle=False)
        paths = z["paths"].astype(str).tolist()
        keys = z["keys"].astype(str).tolist()
        a = z["a"].astype(np.int32)
        b = z["b"].astype(np.int32)
        sim = z["sim"].astype(np.float32)
        k = int(z["k"][0])
        min_similarity = float(z["min_similarity"][0])
        return {
            "paths": paths, "keys": keys, "a": a, "b": b, "sim": sim,
            "k": k, "min_similarity": min_similarity
        }
    except Exception as e:
        log_error(f"Erro ao carregar arestas de {p}", e)
        return None


def save_edges(root, paths, keys, edges, k, min_similarity):
    cp = get_cache_paths(root)
    target = cp["edges"]
    tmp = target.with_suffix(".tmp.npz")
    if edges:
        a = np.array([x[0] for x in edges], dtype=np.int32)
        b = np.array([x[1] for x in edges], dtype=np.int32)
        sim = np.array([x[2] for x in edges], dtype=np.float32)
    else:
        a = np.empty(0, dtype=np.int32)
        b = np.empty(0, dtype=np.int32)
        sim = np.empty(0, dtype=np.float32)
    try:
        np.savez_compressed(
            tmp,
            paths=np.array(paths, dtype=str),
            keys=np.array(keys, dtype=str),
            a=a,
            b=b,
            sim=sim,
            k=np.array([k], dtype=np.int32),
            min_similarity=np.array([min_similarity], dtype=np.float32),
        )
        safe_replace(tmp, target)
    except Exception as e:
        log_error(f"Erro ao salvar arestas em {target}", e)


def edges_match(saved, paths, keys, k, min_sim):
    if not saved:
        return False
    if saved["paths"] != [str(x) for x in paths]:
        return False
    if saved["keys"] != keys:
        return False
    if saved["k"] < k:
        return False
    if saved["min_similarity"] > min_sim:
        return False
    return True


def can_remap_edges(saved, paths, keys, k, min_sim):
    """Verifica se é possível reaproveitar o grafo de arestas CLIP sem rodar k-NN completo."""
    if not saved:
        return False
    if saved["k"] < k:
        return False
    if saved["min_similarity"] > min_sim:
        return False

    saved_paths = saved.get("paths", [])
    saved_keys = saved.get("keys", [])
    if len(saved_paths) == 0 or len(paths) == 0:
        return False

    saved_keys_map = dict(zip(saved_paths, saved_keys))
    current_paths = [str(x) for x in paths]

    matches = sum(1 for cp_item, k_val in zip(current_paths, keys) if saved_keys_map.get(cp_item) == k_val)
    match_ratio = matches / len(current_paths)
    return match_ratio >= 0.95


def remap_edges(saved, paths, keys):
    """Remapeia instantaneamente as arestas do grafo CLIP preservando similaridades já calculadas."""
    saved_paths = saved["paths"]
    saved_keys = saved["keys"]
    saved_keys_map = dict(zip(saved_paths, saved_keys))

    current_paths = [str(x) for x in paths]
    curr_path_to_new_idx = {
        p_str: i for i, (p_str, k_val) in enumerate(zip(current_paths, keys))
        if saved_keys_map.get(p_str) == k_val
    }

    a_arr = saved["a"]
    b_arr = saved["b"]
    sim_arr = saved["sim"]

    remapped = []
    seen = set()

    for old_a, old_b, sim in zip(a_arr, b_arr, sim_arr):
        if old_a >= len(saved_paths) or old_b >= len(saved_paths):
            continue
        p_a = saved_paths[old_a]
        p_b = saved_paths[old_b]

        new_a = curr_path_to_new_idx.get(p_a)
        new_b = curr_path_to_new_idx.get(p_b)

        if new_a is not None and new_b is not None and new_a != new_b:
            pair = (min(new_a, new_b), max(new_a, new_b))
            if pair not in seen:
                seen.add(pair)
                remapped.append((pair[0], pair[1], float(sim)))

    return remapped


def find_edges(embeddings, k, min_similarity, cb, stop_event, on_update_edges=None):
    n = len(embeddings)
    if n <= 1:
        return []
    k_actual = min(k + 1, n)
    started = time.time()

    if cb:
        cb({
            "stage": "Vizinhos CLIP", "done": 0, "total": n,
            "elapsed": 0, "rate": 0, "eta": 0,
            "extra": f"indexando {n} vetores...", "stopped": False
        })

    nn = NearestNeighbors(n_neighbors=k_actual, metric="cosine", algorithm="auto") if NearestNeighbors is not None else None
    if nn is not None:
        nn.fit(embeddings)

    edges_set = set()
    edges = []
    chunk = 2000

    for start in range(0, n, chunk):
        if stop_event.is_set():
            break
        end = min(n, start + chunk)
        if nn is not None:
            distances, indices = nn.kneighbors(embeddings[start:end])
        else:
            sims = np.dot(embeddings[start:end], embeddings.T)
            top_k_indices = np.argsort(-sims, axis=1)[:, :k_actual]
            distances = 1.0 - np.take_along_axis(sims, top_k_indices, axis=1)
            indices = top_k_indices
        for row_idx, (dists, idxs) in enumerate(zip(distances, indices)):
            i = start + row_idx
            for dist, j in zip(dists, idxs):
                if i == j:
                    continue
                sim = 1.0 - float(dist)
                if sim >= min_similarity:
                    edge = (min(i, j), max(i, j))
                    if edge not in edges_set:
                        edges_set.add(edge)
                        edges.append((edge[0], edge[1], round(sim, 4)))

        done = end
        elapsed = max(0.001, time.time() - started)
        rate = done / elapsed
        rem = max(0, n - done)
        eta = rem / rate if rate > 0 else 0
        if cb:
            cb({
                "stage": "Vizinhos CLIP", "done": done, "total": n,
                "elapsed": elapsed, "rate": rate, "eta": eta,
                "extra": f"{len(edges)} pares similares encontrados", "stopped": False
            })

    if on_update_edges:
        on_update_edges(edges)

    return edges


def connected_components(num_nodes, edges, threshold, clip_paths=None, ignored_pairs=None, max_cluster_size=16):
    if not edges or num_nodes <= 1:
        return []

    # Otimização de alta performance: se ignored_pairs existir, pré-indexar para busca O(1) de inteiros
    ignored_idx_pairs = None
    if ignored_pairs and clip_paths:
        path_to_idx = {canonical_path(p): idx for idx, p in enumerate(clip_paths)}
        ignored_idx_pairs = set()
        for p1_str, p2_str in ignored_pairs:
            i1 = path_to_idx.get(canonical_path(p1_str))
            i2 = path_to_idx.get(canonical_path(p2_str))
            if i1 is not None and i2 is not None:
                ignored_idx_pairs.add((min(i1, i2), max(i1, i2)))

    # 1. Filtra arestas pelo limiar de similaridade, pares ignorados e limites válidos de nós
    valid_edges = []
    if ignored_idx_pairs:
        for a, b, sim in edges:
            if sim >= threshold:
                ea, eb = (int(a), int(b)) if a <= b else (int(b), int(a))
                if 0 <= ea < num_nodes and 0 <= eb < num_nodes and ea != eb:
                    if (ea, eb) not in ignored_idx_pairs:
                        valid_edges.append((ea, eb, float(sim)))
    else:
        for a, b, sim in edges:
            if sim >= threshold:
                ea, eb = (int(a), int(b)) if a <= b else (int(b), int(a))
                if 0 <= ea < num_nodes and 0 <= eb < num_nodes and ea != eb:
                    valid_edges.append((ea, eb, float(sim)))

    if not valid_edges:
        return []

    # 2. Ordena por similaridade decrescente para unir primeiro os pares mais semelhantes (Kruskal-style)
    valid_edges.sort(key=lambda x: x[2], reverse=True)

    parent = list(range(num_nodes))
    size = [1] * num_nodes

    def find(i):
        if i < 0 or i >= len(parent):
            return i
        path = []
        while parent[i] != i:
            path.append(i)
            i = parent[i]
        for node in path:
            parent[node] = i
        return i

    # 3. Agrupamento seguro com prevenção de encadeamento infinito (evita mega-grupos de milhares de fotos)
    for a, b, _ in valid_edges:
        root_a = find(a)
        root_b = find(b)
        if root_a != root_b:
            if max_cluster_size is None or (size[root_a] + size[root_b] <= max_cluster_size):
                parent[root_b] = root_a
                size[root_a] += size[root_b]

    groups_map = defaultdict(list)
    for i in range(num_nodes):
        root = find(i)
        groups_map[root].append(i)

    raw_groups = [g for g in groups_map.values() if len(g) > 1]
    chunked = []
    for g in raw_groups:
        chunked.extend(chunk_group_to_max(g, max_size=6))
    return chunked
