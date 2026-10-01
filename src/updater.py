# -*- coding: utf-8 -*-
"""Módulo de verificação e aplicação de auto-atualização do Comparador de Fotos.
Utiliza a API REST pública do GitHub Releases para consultar novas versões,
fazer download com exibição de progresso e substituir o executável de forma atômica no Windows.
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.config import APP_CONFIG, PROGRAM_DIR, log_error, log_info

logger = logging.getLogger("ComparadorFotos")

APP_VERSION = "1.0.0"
DEFAULT_GITHUB_REPO = "guilhermebsouza1982/comparador_fotos"


def parse_version_tuple(v_str: str) -> Tuple[int, ...]:
    """Converte uma string de versão (ex: 'v1.2.3', '1.0.0-beta') em uma tupla de inteiros para comparação."""
    if not v_str:
        return (0, 0, 0)
    clean = re.sub(r"^[vV]", "", v_str.strip())
    # Extrai grupos numéricos
    nums = re.findall(r"\d+", clean)
    if not nums:
        return (0, 0, 0)
    return tuple(int(n) for n in nums)


def is_version_newer(latest_version: str, current_version: str = APP_VERSION) -> bool:
    """Retorna True se a versão remota for superior à versão atual instalada."""
    v_remote = parse_version_tuple(latest_version)
    v_local = parse_version_tuple(current_version)
    return v_remote > v_local


def get_current_executable_path() -> Path:
    """Retorna o caminho do executável em execução (ou do interpretador em modo de desenvolvimento)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return (PROGRAM_DIR / "dist" / "ComparadorFotos_Launcher.exe").resolve()


def check_for_updates(
    repo: Optional[str] = None,
    timeout: int = 8
) -> Dict[str, Any]:
    """
    Consulta o GitHub Releases para verificar se há uma nova versão disponível.
    Retorna um dicionário estruturado com as informações da versão.
    """
    target_repo = repo or APP_CONFIG.get("github_repo") or DEFAULT_GITHUB_REPO
    url = f"https://api.github.com/repos/{target_repo}/releases/latest"

    headers = {
        "User-Agent": f"ComparadorFotos/{APP_VERSION} (+https://github.com/{target_repo})",
        "Accept": "application/vnd.github.v3+json",
    }

    result = {
        "success": False,
        "has_update": False,
        "current_version": APP_VERSION,
        "latest_version": APP_VERSION,
        "release_name": "",
        "release_notes": "",
        "published_at": "",
        "html_url": f"https://github.com/{target_repo}/releases",
        "download_url": "",
        "asset_name": "",
        "asset_size": 0,
        "error_message": "",
    }

    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                result["error_message"] = f"Resposta inesperada do GitHub (HTTP {response.status})"
                return result
            data = json.loads(response.read().decode("utf-8"))

        tag_name = data.get("tag_name", "").strip()
        result["latest_version"] = tag_name.lstrip("vV")
        result["release_name"] = data.get("name") or tag_name
        result["release_notes"] = data.get("body") or "Nenhuma nota de versão fornecida."
        result["published_at"] = data.get("published_at", "")[:10]
        result["html_url"] = data.get("html_url") or result["html_url"]
        result["success"] = True

        # Localiza o asset de executável Windows (.exe)
        assets = data.get("assets", [])
        chosen_asset = None
        for asset in assets:
            name = asset.get("name", "").lower()
            if name.endswith(".exe"):
                # Prioriza o launcher ou executável principal
                if "launcher" in name:
                    chosen_asset = asset
                    break
                elif chosen_asset is None:
                    chosen_asset = asset

        if chosen_asset:
            result["download_url"] = chosen_asset.get("browser_download_url", "")
            result["asset_name"] = chosen_asset.get("name", "ComparadorFotos_Launcher.exe")
            result["asset_size"] = chosen_asset.get("size", 0)

        result["has_update"] = bool(
            result["download_url"] and is_version_newer(result["latest_version"], APP_VERSION)
        )

    except HTTPError as e:
        if e.code == 404:
            result["error_message"] = f"Nenhum lançamento (Release) encontrado no repositório '{target_repo}'."
        else:
            result["error_message"] = f"Erro HTTP {e.code}: {e.reason}"
        logger.debug(f"Verificação de atualização: {result['error_message']}")
    except URLError as e:
        result["error_message"] = f"Falha de conexão com o GitHub: {e.reason}"
        logger.debug(f"Verificação de atualização: {result['error_message']}")
    except Exception as e:
        result["error_message"] = str(e)
        log_error("Erro ao verificar atualizações no GitHub Releases", e)

    return result


class DownloadUpdateThread(threading.Thread):
    """Thread em segundo plano para baixar o novo binário e orquestrar a substituição no Windows."""

    def __init__(
        self,
        download_url: str,
        asset_name: str = "ComparadorFotos_Launcher.exe",
        on_progress: Optional[Callable[[float, str], None]] = None,
        on_complete: Optional[Callable[[bool, str], None]] = None,
    ):
        super().__init__(daemon=True, name="App-Auto-Updater")
        self.download_url = download_url
        self.asset_name = asset_name
        self.on_progress = on_progress
        self.on_complete = on_complete
        self.is_cancelled = False
        self.downloaded_file: Optional[Path] = None

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        target_exe = get_current_executable_path()
        dest_dir = target_exe.parent
        temp_file = dest_dir / f"{target_exe.name}.new"

        if self.on_progress:
            self.on_progress(5.0, "Conectando ao servidor do GitHub...")

        try:
            req = Request(
                self.download_url,
                headers={"User-Agent": f"ComparadorFotos/{APP_VERSION}"}
            )

            with urlopen(req, timeout=30) as response:
                total_size = int(response.headers.get("Content-Length", 0))
                block_size = 64 * 1024  # 64 KB
                downloaded = 0
                started_time = time.time()

                with open(temp_file, "wb") as f_out:
                    while True:
                        if self.is_cancelled:
                            temp_file.unlink(missing_ok=True)
                            if self.on_complete:
                                self.on_complete(False, "Download cancelado pelo usuário.")
                            return

                        chunk = response.read(block_size)
                        if not chunk:
                            break
                        f_out.write(chunk)
                        downloaded += len(chunk)

                        if total_size > 0:
                            pct = min(98.0, (downloaded / total_size) * 100.0)
                            elapsed = max(0.1, time.time() - started_time)
                            kb_s = (downloaded / 1024.0) / elapsed
                            speed_str = f"{kb_s / 1024.0:.1f} MB/s" if kb_s > 1024 else f"{kb_s:.0f} KB/s"
                            msg = f"Baixando atualização: {downloaded / (1024*1024):.1f} MB de {total_size / (1024*1024):.1f} MB ({speed_str})"
                        else:
                            pct = 50.0
                            msg = f"Baixando atualização: {downloaded / (1024*1024):.1f} MB transferidos..."

                        if self.on_progress:
                            self.on_progress(pct, msg)

            self.downloaded_file = temp_file
            if self.on_progress:
                self.on_progress(100.0, "Download concluído com sucesso! Pronto para reiniciar.")

            log_info(f"Nova versão baixada com sucesso em: {temp_file} ({downloaded} bytes)")

            if self.on_complete:
                self.on_complete(True, "Download concluído com sucesso!")

        except Exception as e:
            temp_file.unlink(missing_ok=True)
            err_msg = f"Erro durante o download da atualização: {e}"
            log_error(err_msg)
            if self.on_complete:
                self.on_complete(False, err_msg)


def apply_update_and_restart(new_file_path: Optional[Path] = None):
    """
    Gera um script batch temporário que aguarda o processo atual fechar,
    substitui o executável antigo pelo novo e reinicia o aplicativo.
    """
    target_exe = get_current_executable_path()
    new_exe = new_file_path or (target_exe.parent / f"{target_exe.name}.new")

    if not new_exe.exists():
        raise FileNotFoundError(f"Arquivo de atualização não encontrado: {new_exe}")

    bat_script = target_exe.parent / f"update_launcher_{int(time.time())}.bat"
    bat_content = f"""@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
:: Aguarda o processo pai fechar completamente
timeout /t 2 /nobreak >nul

:: Tenta mover/substituir o executavel
move /y "{new_exe}" "{target_exe}" >nul
if errorlevel 1 (
    timeout /t 2 /nobreak >nul
    move /y "{new_exe}" "{target_exe}" >nul
)

:: Inicia o aplicativo atualizado
start "" "{target_exe}"

:: Remove este script temporario
del "%~f0"
"""
    bat_script.write_text(bat_content, encoding="utf-8")
    log_info(f"Disparando script de atualização: {bat_script}")

    # Executa o script em processo independente e oculto
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    subprocess.Popen(
        ["cmd.exe", "/c", str(bat_script)],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        startupinfo=startupinfo,
        close_fds=True,
    )

    # Encerra o processo atual imediatamente para liberar a trava do arquivo .exe
    os._exit(0)

