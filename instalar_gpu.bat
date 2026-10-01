@echo off
setlocal
title Comparador de Fotos com IA - Instalacao GPU

echo.
echo ============================================================
echo  Comparador de Fotos com IA - PyTorch com CUDA
echo  NVIDIA GTX 1650
echo ============================================================
echo.

python --version
if errorlevel 1 (
  echo Python nao foi encontrado no PATH.
  pause
  exit /b 1
)

echo.
echo [1/4] Removendo PyTorch CPU-only...
python -m pip uninstall -y torch torchvision torchaudio

echo.
echo [2/4] Instalando PyTorch 2.6.0 com CUDA 12.6...
python -m pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu126
if errorlevel 1 (
  echo.
  echo FALHA na instalacao do PyTorch CUDA.
  pause
  exit /b 1
)

echo.
echo [3/4] Instalando dependencias...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo FALHA na instalacao das dependencias.
  pause
  exit /b 1
)

echo.
echo [4/4] Testando CUDA...
python diagnostico_gpu.py

echo.
echo Instalacao concluida.
pause
