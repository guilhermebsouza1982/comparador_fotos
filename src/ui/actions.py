import threading
from tkinter import messagebox
from pathlib import Path
from send2trash import send2trash

from src.config import APP_CONFIG, is_in_or_subdir, log_error
from src.photo_engine import swap_file_locations, upgrade_photo_in_folder, get_photo_quality_score
from src.ui.dialogs.timed_confirm_dialog import TimedConfirmDeleteDialog


def remove_group_from_engine_collections(gui, group):
    """Remove o grupo de todas as listas da engine e caches locais de forma segura."""
    if gui.engine is not None:
        with getattr(gui.engine, "lock", threading.RLock()):
            if group in gui.engine.exact:
                try:
                    gui.engine.exact.remove(group)
                except ValueError:
                    pass
            if group in gui.engine.phash_groups:
                try:
                    gui.engine.phash_groups.remove(group)
                except ValueError:
                    pass

    if gui._cached_clip_groups and group in gui._cached_clip_groups:
        try:
            gui._cached_clip_groups.remove(group)
        except ValueError:
            pass

    if hasattr(gui, "_cached_web_groups") and gui._cached_web_groups and group in gui._cached_web_groups:
        try:
            gui._cached_web_groups.remove(group)
        except ValueError:
            pass

    curr_g = gui.current_groups()
    if group in curr_g:
        try:
            curr_g.remove(group)
        except ValueError:
            pass
    if gui.compare_group_idx >= len(curr_g):
        gui.compare_group_idx = max(0, len(curr_g) - 1)


def mark_group_different_action(gui, group):
    """Marca o par/grupo de fotos como fotos diferentes de modo persistente."""
    if not group or len(group) < 2:
        return

    if gui.engine is not None:
        gui.engine.mark_photos_different(group)
        if gui._cached_clip_groups and group in gui._cached_clip_groups:
            try:
                gui._cached_clip_groups.remove(group)
            except ValueError:
                pass

    remove_group_from_engine_collections(gui, group)
    gui.refresh_results(keep_page=True)
    gui.status.set(f"✓ {len(group)} fotos marcadas como diferentes. Elas não serão mais agrupadas entre si.")


def trigger_single_photo_deletion(gui, path, group):
    """Abre o diálogo de confirmação de 2s ou exclui diretamente caso a confirmação esteja desmarcada."""
    def on_confirmed(deleted_path):
        del_str = str(deleted_path)
        to_rem = [p for p in list(group) if str(p) == del_str or p == deleted_path]
        for p in to_rem:
            try:
                group.remove(p)
            except ValueError:
                pass
        gui.thumb_cache.pop(del_str, None)
        gui.info_cache.pop(del_str, None)

        if gui.engine is not None and hasattr(gui.engine, "register_deleted_photo"):
            gui.engine.register_deleted_photo(deleted_path)

        if gui.active_tab != "web" and len(group) <= 1:
            remove_group_from_engine_collections(gui, group)
        elif len(group) == 0:
            remove_group_from_engine_collections(gui, group)

        gui.refresh_results(keep_page=True)

    if not APP_CONFIG.get("confirm_delete_2s", True):
        try:
            p_obj = Path(path)
            if p_obj.exists():
                send2trash(p_obj)
            on_confirmed(p_obj)
            gui.status.set(f"🗑️ Foto '{p_obj.name}' enviada para a Lixeira do Windows.")
        except Exception as e:
            if not Path(path).exists():
                on_confirmed(path)
            else:
                log_error(f"Erro ao mover {path} para a lixeira", e)
                try:
                    on_confirmed(path)
                except Exception:
                    pass
                messagebox.showerror("Erro ao Excluir", f"Não foi possível mover para a Lixeira:\n{e}", parent=gui.root)
        return

    thumb = gui.get_thumb(path, size=(180, 180))
    TimedConfirmDeleteDialog(
        gui.root,
        path,
        on_confirm=on_confirmed,
        thumb_img=thumb,
        countdown=2
    )


def trigger_group_deletion(gui, group):
    """Abre o diálogo com temporizador de 2s ou exclui diretamente o grupo para a Lixeira."""
    if not group:
        return

    photos_to_delete = list(group)

    def on_confirmed(deleted_photos):
        del_set = {str(p) for p in deleted_photos} | set(deleted_photos)
        to_rem = [p for p in list(group) if str(p) in del_set or p in del_set]
        for p in to_rem:
            try:
                group.remove(p)
            except ValueError:
                pass
        for p in deleted_photos:
            gui.thumb_cache.pop(str(p), None)
            gui.info_cache.pop(str(p), None)
            if gui.engine is not None and hasattr(gui.engine, "register_deleted_photo"):
                gui.engine.register_deleted_photo(p)

        remove_group_from_engine_collections(gui, group)
        gui.refresh_results(keep_page=True)
        gui.status.set(f"🗑️ Grupo ({len(deleted_photos)} fotos) enviado para a Lixeira do Windows.")

    if not APP_CONFIG.get("confirm_delete_2s", True):
        deleted = []
        try:
            for p in photos_to_delete:
                p_obj = Path(p)
                try:
                    if p_obj.exists():
                        send2trash(p_obj)
                    deleted.append(p_obj)
                except Exception as e:
                    if not p_obj.exists():
                        deleted.append(p_obj)
                    else:
                        log_error(f"Erro ao mover {p} para a lixeira", e)
            on_confirmed(deleted)
        except Exception as e:
            log_error("Erro na exclusão direta do grupo", e)
        return

    TimedConfirmDeleteDialog(
        gui.root,
        photos_to_delete,
        on_confirm=on_confirmed,
        countdown=2
    )


def swap_photos_in_group(gui, idx_a=0, idx_b=1):
    """Inverte atômica e fisicamente a localização de duas fotos comparadas no disco."""
    group = gui._get_active_comparison_group()
    if not group or len(group) < 2:
        gui.status.set("⚠️ É necessário pelo menos 2 fotos no grupo ativo para inverter locais.")
        return

    if len(group) <= max(idx_a, idx_b) or idx_a == idx_b:
        return

    p_a = group[idx_a]
    p_b = group[idx_b]

    try:
        new_p_a, new_p_b = swap_file_locations(p_a, p_b)
        group[idx_a] = Path(new_p_a)
        group[idx_b] = Path(new_p_b)

        # Limpar caches de miniaturas e metadados das fotos alteradas
        for p in (p_a, p_b, new_p_a, new_p_b):
            gui.info_cache.pop(str(p), None)
            for k in list(gui.thumb_cache.keys()):
                if str(p) in k:
                    gui.thumb_cache.pop(k, None)

        # Atualizar caminhos na engine se presentes
        if gui.engine is not None:
            for old_p, new_p in [(str(p_a), str(new_p_a)), (str(p_b), str(new_p_b))]:
                if old_p in gui.engine.meta:
                    gui.engine.meta[new_p] = gui.engine.meta.pop(old_p)
                for i, ep in enumerate(gui.engine.paths):
                    if str(ep) == old_p:
                        gui.engine.paths[i] = Path(new_p)

        gui.render_page()
        gui.status.set(f"🔄 Locais de disco invertidos! [Foto {idx_a + 1} ↔ Foto {idx_b + 1}] (Pressione Page Up ou Page Down para desfazer)")
    except Exception as e:
        log_error(f"Erro ao inverter fotos {p_a} e {p_b}", e)
        messagebox.showerror("Erro ao Inverter Fotos", f"Não foi possível inverter os locais:\n{e}")


def promote_largest_photo_action(gui, group):
    """Move a foto de maior qualidade para a pasta principal e envia a menor para a Lixeira."""
    if len(group) <= 1:
        return

    # 1. Identificar foto prioritária (ou photo 0 como referência)
    keep_candidates = [
        p for p in group
        if any(is_in_or_subdir(p, kf, gui.include_subdirs) for kf in gui.keep_folders)
    ]
    keeper = keep_candidates[0] if keep_candidates else group[0]

    # 2. Identificar maior foto no grupo
    scored = sorted(group, key=get_photo_quality_score, reverse=True)
    largest = scored[0]

    if largest == keeper:
        messagebox.showinfo(
            "Qualidade Máxima",
            f"A foto de maior resolução/tamanho já se encontra na pasta principal:\n\n"
            f"📁 {Path(keeper).parent}\n"
            f"• {Path(keeper).name} ({gui.get_file_info(keeper)})"
        )
        return

    keeper_name = Path(keeper).name
    largest_name = Path(largest).name
    dest_folder = Path(keeper).parent

    confirm_msg = (
        f"Deseja substituir a foto menor pela de maior qualidade na pasta principal?\n\n"
        f"⭐ Foto de MAIOR qualidade (será MOVIDA):\n"
        f"• {largest_name} ({gui.get_file_info(largest)})\n"
        f"  De: {Path(largest).parent}\n"
        f"  Para: {dest_folder}\n\n"
        f"🗑️ Foto menor atual (será enviada para a LIXEIRA):\n"
        f"• {keeper_name} ({gui.get_file_info(keeper)})\n\n"
        f"Confirmar promoção e substituição?"
    )
    if not messagebox.askyesno("Promover Maior Foto para Pasta Principal", confirm_msg):
        return

    try:
        upgraded_path = upgrade_photo_in_folder(largest, keeper)
        if keeper in group:
            group.remove(keeper)
        if largest in group:
            group.remove(largest)
        group.insert(0, upgraded_path)

        for p in (keeper, largest, upgraded_path):
            gui.info_cache.pop(str(p), None)
            for k in list(gui.thumb_cache.keys()):
                if str(p) in k:
                    gui.thumb_cache.pop(k, None)

        gui.render_page()
        messagebox.showinfo("Sucesso", "Foto de maior qualidade promovida com sucesso para a pasta principal!")
    except Exception as e:
        log_error(f"Erro ao promover {largest} para {keeper}", e)
        messagebox.showerror("Erro ao Promover Foto", str(e))


def keep_largest_and_delete_others_action(gui, group):
    """Mantém a foto de maior resolução/tamanho e envia as outras cópias para a Lixeira."""
    if len(group) <= 1:
        return

    scored = []
    for p in group:
        res, sz = get_photo_quality_score(p)
        scored.append((res, sz, p))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    keep = scored[0][2]
    others = [x[2] for x in scored[1:]]

    def on_confirmed(deleted_photos):
        del_set = {str(p) for p in deleted_photos} | set(deleted_photos)
        to_rem = [p for p in list(group) if str(p) in del_set or p in del_set]
        for p in to_rem:
            try:
                group.remove(p)
            except ValueError:
                pass
        for p in deleted_photos:
            gui.thumb_cache.pop(str(p), None)
            gui.info_cache.pop(str(p), None)
            if gui.engine is not None and hasattr(gui.engine, "register_deleted_photo"):
                gui.engine.register_deleted_photo(p)
        if gui.active_tab != "web" and len(group) <= 1:
            remove_group_from_engine_collections(gui, group)
        elif len(group) == 0:
            remove_group_from_engine_collections(gui, group)
        gui.refresh_results(keep_page=True)
        gui.status.set(f"🗑️ Mantida maior foto ({Path(keep).name}). {len(deleted_photos)} cópias enviadas para a Lixeira.")

    if not APP_CONFIG.get("confirm_delete_2s", True):
        deleted = []
        try:
            for p in others:
                try:
                    send2trash(p)
                    deleted.append(p)
                except Exception as e:
                    log_error(f"Erro ao mover {p} para a lixeira", e)
            on_confirmed(deleted)
        except Exception as e:
            log_error("Erro na exclusão direta das cópias menores", e)
        return

    TimedConfirmDeleteDialog(
        gui.root,
        others,
        on_confirm=on_confirmed,
        countdown=2
    )


def trigger_shadow_removal_action(gui, photo_path):
    """Executa a remoção de sombras em thread assíncrona e abre o comparativo Antes e Depois em tela cheia."""
    p_obj = Path(photo_path).resolve()
    if not p_obj.exists():
        messagebox.showwarning("Arquivo Não Encontrado", f"O arquivo {p_obj.name} não existe mais no disco.", parent=gui.root)
        return

    from src.config import APP_CONFIG
    mode = APP_CONFIG.get("shadow_removal_mode", "deep")
    mode_name = "Modo Local IA Profunda (PyTorch CUDA)" if mode in ("deep", "deep_local") else ("Google Gemini Vision" if mode == "gemini" else "Modo Rápido Retinex")
    gui.status.set(f"✨ Removendo sombras de '{p_obj.name}' via {mode_name}...")

    def _worker():
        try:
            from src.shadow_remover import remove_shadows_from_image
            enh_path = remove_shadows_from_image(p_obj)

            def _open_dialog():
                gui.status.set(f"✨ Iluminação corrigida para '{p_obj.name}'. Exibindo comparativo Antes e Depois...")
                from src.ui.dialogs.before_after_compare_dialog import BeforeAfterCompareDialog
                BeforeAfterCompareDialog(gui, p_obj, enh_path)

            gui.root.after(0, _open_dialog)
        except Exception as e:
            log_error(f"Erro ao remover sombras de {p_obj}", e)
            def _show_err():
                gui.status.set("Erro ao processar remoção de sombras.")
                messagebox.showerror("Erro na Remoção de Sombras", f"Ocorreu um erro ao processar a imagem:\n{e}", parent=gui.root)
            gui.root.after(0, _show_err)

    threading.Thread(target=_worker, name=f"ShadowRemover-{p_obj.stem[:8]}", daemon=True).start()


def rotate_photo_action(gui, photo_path, degrees=90):
    """Gira a foto permanentemente em múltiplos de 90 graus e atualiza a interface."""
    p = Path(photo_path).resolve()
    if not p.exists():
        return

    from src.auto_orienter import rotate_and_save_photo
    ok = rotate_and_save_photo(p, degrees=degrees)
    if ok:
        gui.info_cache.pop(str(p), None)
        for k in list(gui.thumb_cache.keys()):
            if str(p) in k:
                gui.thumb_cache.pop(k, None)
        gui.render_page()
        gui.status.set(f"Foto '{p.name}' rotacionada com sucesso em {degrees} graus!")
    else:
        gui.status.set(f"Falha ao rotacionar a foto '{p.name}'.")
