import torch

print("PyTorch:", torch.__version__)
print("CUDA disponivel:", torch.cuda.is_available())
print("CUDA compilado no PyTorch:", torch.version.cuda)

if torch.cuda.is_available():
    p = torch.cuda.get_device_properties(0)
    print("GPU:", p.name)
    print(f"VRAM: {p.total_memory / (1024**3):.2f} GB")
    print("Compute capability:", f"{p.major}.{p.minor}")
else:
    print("ATENCAO: PyTorch ainda esta sem CUDA.")
