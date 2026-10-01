import sys
import time
import tempfile
from pathlib import Path
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.hasher import sha256_file, phash_file
from src.embeddings import get_model, choose_batch


def run_benchmark():
    print("=" * 60)
    print("  BENCHMARK DE PERFORMANCE — Comparador de Fotos com IA")
    print("=" * 60)

    # 1. Create a set of synthetic test images
    temp_dir = tempfile.TemporaryDirectory()
    dir_path = Path(temp_dir.name)
    num_samples = 40
    print(f"Gerando {num_samples} imagens de teste sintéticas...")
    paths = []
    for i in range(num_samples):
        p = dir_path / f"bench_{i:03d}.jpg"
        color = ((i * 37) % 256, (i * 73) % 256, (i * 109) % 256)
        im = Image.new("RGB", (800, 600), color=color)
        im.save(p, quality=85)
        paths.append(p)

    # 2. Benchmark Hash & pHash
    t0 = time.time()
    meta_dict = {}
    for p in paths:
        sha = sha256_file(p)
        ph = phash_file(p)
        meta_dict[str(p)] = {"sha": sha, "phash": ph}
    dt_hash = max(0.001, time.time() - t0)
    print(f"• SHA-256 + pHash: {num_samples / dt_hash:.1f} fotos/s ({dt_hash:.2f}s para {num_samples} fotos)")

    # 3. Benchmark pHash Clustering Speed
    from src.clustering import phash_groups
    t0 = time.time()
    for _ in range(100):
        _ = phash_groups(paths, meta_dict, max_dist=10)
    dt_clust = max(0.001, time.time() - t0)
    print(f"• Agrupamento pHash bit_count: {100 / dt_clust:.0f} execuções completas/s ({dt_clust*10:.2f}ms por rodada)")

    # 4. Benchmark OpenCLIP Model & GPU
    print("\nInicializando modelo OpenCLIP e inferência...")
    t0 = time.time()
    model, prep, device = get_model()
    dt_load = time.time() - t0
    batch_size = choose_batch(device)
    print(f"• Dispositivo: {device.upper()} | Batch: {batch_size} | Carregamento do Modelo: {dt_load:.2f}s")

    # Preprocess & Encode
    t0 = time.time()
    import torch
    tensors = [prep(Image.open(p)) for p in paths]
    x = torch.stack(tensors).to(device)
    with torch.no_grad():
        feats = model.encode_image(x)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    dt_gpu = max(0.001, time.time() - t0)
    print(f"• Pré-processamento + Inferência CLIP ({device}): {num_samples / dt_gpu:.1f} fotos/s ({dt_gpu:.2f}s)")

    temp_dir.cleanup()
    print("\n" + "=" * 60)
    print("Benchmark concluído com sucesso!")
    print("=" * 60)


if __name__ == "__main__":
    run_benchmark()
