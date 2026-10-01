import os
import sys
import threading
import time
import unittest
import tempfile
import tkinter as tk
from pathlib import Path
import json
from PIL import Image

# Ensure project root is in sys.path and offline mode is favored for test resilience
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from src.config import (
    APP_CONFIG, load_app_config, get_current_cache_dir, format_size, format_time,
    is_in_or_subdir
)
from src.hardware import (
    get_system_disks_info, get_system_ram_info, get_drive_free_space
)
from src.hasher import sha256_file, phash_file, file_key
from src.clustering import connected_components, exact_groups
from src.ui.main_window import MainWindow
from src.ui.dialogs.timed_confirm_dialog import TimedConfirmDeleteDialog
from src.ui.dialogs.performance_dialog import PerformanceDialog
from src.ui.dialogs.folder_rules_dialog import FolderRulesDialog
from src.photo_engine import (
    swap_file_locations, upgrade_photo_in_folder, partition_groups_by_priority, get_photo_quality_score
)


class TestConfigAndPaths(unittest.TestCase):
    def test_app_config(self):
        cfg = load_app_config()
        self.assertIn("cache_dir", cfg)
        self.assertIn("max_ram_pct", cfg)
        self.assertIn("enable_web_detector", cfg)
        self.assertGreaterEqual(cfg["max_ram_pct"], 40)
        self.assertLessEqual(cfg["max_ram_pct"], 95)

    def test_format_helpers(self):
        self.assertEqual(format_size(1024), "1 KB")
        self.assertEqual(format_size(1024 * 1024), "1.0 MB")
        self.assertEqual(format_size(1024 * 1024 * 1024), "1.00 GB")
        self.assertEqual(format_time(45), "45s")
        self.assertEqual(format_time(125), "2m 05s")

    def test_subdir_matching(self):
        p = Path("D:/Fotos/2026/Férias/foto1.jpg")
        self.assertTrue(is_in_or_subdir(p, "D:/Fotos", include_subdirs=True))
        self.assertFalse(is_in_or_subdir(p, "D:/Fotos", include_subdirs=False))
        self.assertTrue(is_in_or_subdir(p, "D:/Fotos/2026/Férias", include_subdirs=False))


class TestHardwareMonitoring(unittest.TestCase):
    def test_ram_info(self):
        ram = get_system_ram_info()
        self.assertIn("load_pct", ram)
        self.assertIn("total_gb", ram)
        self.assertIn("avail_gb", ram)
        self.assertGreater(ram["total_gb"], 0)
        self.assertGreaterEqual(ram["load_pct"], 0)
        self.assertLessEqual(ram["load_pct"], 100)

    def test_disks_info(self):
        disks = get_system_disks_info()
        self.assertIsInstance(disks, dict)
        self.assertTrue(len(disks) > 0)
        for dl, info in disks.items():
            self.assertIn("is_ssd", info)
            self.assertIn("type_desc", info)
            free_b, tot_b = get_drive_free_space(dl)
            self.assertGreaterEqual(tot_b, 0)


class TestHasherRobustness(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_valid_image_hash(self):
        img_path = self.dir_path / "valid.jpg"
        im = Image.new("RGB", (64, 64), color="red")
        im.save(img_path)

        sha = sha256_file(img_path)
        ph = phash_file(img_path)
        self.assertIsNotNone(sha)
        self.assertIsNotNone(ph)
        self.assertEqual(len(sha), 64)

    def test_zero_byte_file(self):
        empty_path = self.dir_path / "empty.jpg"
        empty_path.touch()

        ph = phash_file(empty_path)
        self.assertIsNone(ph)

    def test_null_bytes_corrupted_file(self):
        null_path = self.dir_path / "nulls.jpg"
        with open(null_path, "wb") as f:
            f.write(b"\x00" * 4096)

        ph = phash_file(null_path)
        self.assertIsNone(ph)

    def test_zip_sticker_file(self):
        zip_path = self.dir_path / "sticker.webp"
        with open(zip_path, "wb") as f:
            f.write(b"PK\x03\x04dummyzipcontent")

        ph = phash_file(zip_path)
        self.assertIsNone(ph)


class TestClustering(unittest.TestCase):
    def test_connected_components(self):
        # 3 nodes: edge (0,1) with sim 0.9, edge (1,2) with sim 0.85
        edges = [(0, 1, 0.90), (1, 2, 0.85)]
        groups_high = connected_components(3, edges, 0.88)
        self.assertEqual(len(groups_high), 1)
        self.assertEqual(groups_high[0], [0, 1])

        groups_low = connected_components(3, edges, 0.80)
        self.assertEqual(len(groups_low), 1)
        self.assertEqual(sorted(groups_low[0]), [0, 1, 2])


class TestTimedConfirmDeleteDialog(unittest.TestCase):
    def test_dialog_countdown_and_cancel(self):
        root = tk.Tk()
        root.withdraw()

        temp_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        temp_file.write(b"test content")
        temp_file.close()
        p = Path(temp_file.name)

        confirmed_called = []
        cancelled_called = []

        dialog = TimedConfirmDeleteDialog(
            root,
            p,
            on_confirm=lambda path: confirmed_called.append(path),
            on_cancel=lambda: cancelled_called.append(True)
        )

        # Check initial countdown (configured to 2s)
        self.assertEqual(dialog.countdown, 2)

        # Test cancel immediately via ESC simulation
        dialog.cancel()
        self.assertTrue(dialog.cancelled)
        self.assertEqual(len(cancelled_called), 1)
        self.assertEqual(len(confirmed_called), 0)
        self.assertTrue(p.exists())

        # Cleanup
        try:
            os.remove(p)
        except Exception:
            pass
        root.destroy()

    def test_dialog_group_mode(self):
        root = tk.Tk()
        root.withdraw()

        p1 = Path("c:/dummy/img1.jpg")
        p2 = Path("c:/dummy/img2.jpg")
        confirmed_called = []
        cancelled_called = []

        dialog = TimedConfirmDeleteDialog(
            root,
            [p1, p2],
            on_confirm=lambda paths: confirmed_called.append(paths),
            on_cancel=lambda: cancelled_called.append(True)
        )

        self.assertTrue(dialog.is_group)
        self.assertEqual(len(dialog.paths), 2)
        self.assertEqual(dialog.countdown, 2)

        # Cancel test
        dialog.cancel()
        self.assertTrue(dialog.cancelled)
        self.assertEqual(len(cancelled_called), 1)

        root.destroy()

    def test_direct_deletion_without_confirmation(self):
        from src.config import APP_CONFIG
        from src.ui.actions import trigger_single_photo_deletion, trigger_group_deletion

        old_setting = APP_CONFIG.get("confirm_delete_2s", True)
        try:
            APP_CONFIG["confirm_delete_2s"] = False

            temp_dir = tempfile.TemporaryDirectory()
            dir_path = Path(temp_dir.name)

            f1 = dir_path / "foto1.jpg"
            f1.write_bytes(b"dummy1")
            f2 = dir_path / "foto2.jpg"
            f2.write_bytes(b"dummy2")

            group = [f1, f2]

            class DummyGui:
                def __init__(self):
                    self.root = tk.Tk()
                    self.root.withdraw()
                    self.thumb_cache = {}
                    self.info_cache = {}
                    self.active_tab = "clip"
                    self.engine = None
                    self._cached_clip_groups = None
                    self.compare_group_idx = 0
                    self.status = tk.StringVar(value="")

                def refresh_results(self, keep_page=False):
                    pass

                def current_groups(self):
                    return [group]

            gui = DummyGui()

            # 1. Single photo direct deletion
            trigger_single_photo_deletion(gui, f1, group)
            self.assertNotIn(f1, group)
            self.assertFalse(f1.exists())

            # 2. Group direct deletion
            trigger_group_deletion(gui, group)
            self.assertEqual(len(group), 0)
            self.assertFalse(f2.exists())

            gui.root.destroy()
            temp_dir.cleanup()
        finally:
            APP_CONFIG["confirm_delete_2s"] = old_setting


class TestFileSwapAndUpgrade(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.dir_koofr = self.base / "Koofr - Cloud Backup - Drive E"
        self.dir_bkps = self.base / "Bkps_Celulares"
        self.dir_koofr.mkdir()
        self.dir_bkps.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_swap_file_locations(self):
        p1 = self.dir_koofr / "photo_small.jpg"
        p2 = self.dir_bkps / "photo_large.jpg"
        p1.write_text("small content in koofr")
        p2.write_text("large highres content in bkps")

        new_p1, new_p2 = swap_file_locations(p1, p2)

        # File at koofr should now contain the large content
        self.assertTrue(Path(new_p1).exists())
        self.assertEqual(Path(new_p1).parent.resolve(), self.dir_koofr.resolve())
        self.assertEqual(Path(new_p1).read_text(), "large highres content in bkps")

        # File at bkps should now contain the small content
        self.assertTrue(Path(new_p2).exists())
        self.assertEqual(Path(new_p2).parent.resolve(), self.dir_bkps.resolve())
        self.assertEqual(Path(new_p2).read_text(), "small content in koofr")

    def test_upgrade_photo_in_folder(self):
        p_small = self.dir_koofr / "img.jpg"
        p_large = self.dir_bkps / "img.jpg"
        p_small.write_text("small")
        p_large.write_text("large original content")

        upgraded = upgrade_photo_in_folder(p_large, p_small)

        self.assertTrue(Path(upgraded).exists())
        self.assertEqual(Path(upgraded).parent.resolve(), self.dir_koofr.resolve())
        self.assertEqual(Path(upgraded).read_text(), "large original content")
        self.assertFalse(p_large.exists())

    def test_partition_groups_with_upgrade(self):
        # Create 2 actual image files with different dimensions
        im_small = Image.new("RGB", (100, 100), color="blue")
        p_small = self.dir_koofr / "pic.jpg"
        im_small.save(p_small)

        im_large = Image.new("RGB", (800, 800), color="blue")
        p_large = self.dir_bkps / "pic.jpg"
        im_large.save(p_large)

        group = [str(p_small), str(p_large)]
        keep_folders = [str(self.dir_koofr)]
        del_folders = [str(self.dir_bkps)]

        to_keep, to_delete, to_upgrade = partition_groups_by_priority(
            [group], keep_folders, del_folders, include_subdirs=True,
            upgrade_quality=True, return_upgrades=True
        )

        self.assertEqual(len(to_upgrade), 1)
        larger_cand, smaller_target = to_upgrade[0]
        self.assertEqual(larger_cand, str(p_large))
        self.assertEqual(smaller_target, str(p_small))
        self.assertIn(str(p_small), to_delete)
        self.assertIn(str(p_large), to_keep)


class TestFullscreenCompareRobustness(unittest.TestCase):
    def test_fullscreen_render_with_one_and_zero_photos(self):
        class DummyEngine:
            def __init__(self):
                self.threshold = 0.82
                self.exact = []
                self.phash_groups = []
                self.meta = {}
                self.paths = []
                self.edges = []
                self.clip_paths = []
            def groups(self, t=None):
                return []
            def stop(self):
                pass
            def load_partial_state(self):
                pass

        gui = MainWindow(DummyEngine(), processing=False)
        gui.root.withdraw()
        gui.view_mode = "fullscreen_compare"

        # 1. Test com grupo com 1 foto restante (ZeroDivisionError regression check)
        single_photo_group = [Path("c:/dummy/photo1.jpg")]
        gui.engine.exact = [single_photo_group]
        gui.active_tab = "exact"
        gui.compare_group_idx = 0
        try:
            gui.render_fullscreen_comparison()
        except ZeroDivisionError:
            self.fail("ZeroDivisionError raised when rendering fullscreen compare with 1 photo!")

        # 2. Test com grupo vazio (0 fotos restantes)
        single_photo_group.clear()
        try:
            gui.render_fullscreen_comparison()
        except ZeroDivisionError:
            self.fail("ZeroDivisionError raised when rendering fullscreen compare with 0 photos!")

        # 3. Test fechamento forçado
        gui._is_closing = True
        gui.close()

    def test_dialogs_and_menu_support(self):
        gui = MainWindow(engine=None, processing=False)
        gui.root.withdraw()

        # Testa abertura do PerformanceDialog sem erro de TclError ou widget
        perf_dlg = PerformanceDialog(gui)
        self.assertIsNotNone(perf_dlg.top)
        perf_dlg.top.destroy()

        # Testa abertura do FolderRulesDialog sem erro
        rules_dlg = FolderRulesDialog(gui)
        self.assertIsNotNone(rules_dlg.top)
        rules_dlg.top.destroy()

        # Testa renderização de tela vazia quando engine é None
        gui.render_page()

        gui._is_closing = True
        gui.close()

    def test_asterisk_shortcut_and_group_deletion(self):
        class DummyEngine:
            def __init__(self):
                self.threshold = 0.82
                self.exact = []
                self.phash_groups = []
                self.meta = {}
                self.paths = []
                self.edges = []
                self.clip_paths = []
            def groups(self, t=None):
                return []
            def stop(self):
                pass
            def load_partial_state(self):
                pass

        gui = MainWindow(DummyEngine(), processing=False)
        gui.root.withdraw()

        test_group = [Path("c:/dummy/p1.jpg"), Path("c:/dummy/p2.jpg")]
        gui.engine.exact = [test_group]
        gui.active_tab = "exact"
        gui.view_mode = "fullscreen_compare"
        gui.compare_group_idx = 0

        active_g = gui._get_active_comparison_group()
        self.assertEqual(active_g, test_group)

        opened_dialogs = []
        original_init = TimedConfirmDeleteDialog.__init__
        def mock_init(dlg, parent, photo_path, on_confirm=None, on_cancel=None, thumb_img=None, countdown=2):
            opened_dialogs.append(photo_path)
            original_init(dlg, parent, photo_path, on_confirm, on_cancel, thumb_img, countdown)
            dlg.cancel()

        old_cfg = APP_CONFIG.get("confirm_delete_2s", True)
        APP_CONFIG["confirm_delete_2s"] = True
        try:
            TimedConfirmDeleteDialog.__init__ = mock_init
            gui._handle_asterisk_shortcut()
            self.assertEqual(len(opened_dialogs), 1)
            self.assertEqual(opened_dialogs[0], test_group)
        finally:
            TimedConfirmDeleteDialog.__init__ = original_init
            APP_CONFIG["confirm_delete_2s"] = old_cfg

        gui._is_closing = True
        gui.close()


class TestSinglePhotoGroupDismissalAndIgnoredPairs(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        from src.config import get_cache_paths
        cp = get_cache_paths(self.dir_path)
        if cp.get("ignored_pairs") and cp["ignored_pairs"].exists():
            try:
                cp["ignored_pairs"].unlink()
            except Exception:
                pass

    def tearDown(self):
        from src.config import get_cache_paths
        try:
            cp = get_cache_paths(self.dir_path)
            if cp.get("ignored_pairs") and cp["ignored_pairs"].exists():
                cp["ignored_pairs"].unlink()
        except Exception:
            pass
        self.temp_dir.cleanup()

    def test_single_photo_remaining_group_dismissal(self):
        """Verifica se o grupo desaparece quando resta apenas 1 foto após exclusão."""
        class DummyEngine:
            def __init__(self):
                self.threshold = 0.82
                self.phash_distance = 10
                self.exact = [[Path("c:/dummy/p1.jpg"), Path("c:/dummy/p2.jpg")]]
                self.phash_groups = []
                self.clip_paths = []
                self.meta = {}
            def groups(self, t=None):
                return []
            def stop(self):
                pass
            def load_partial_state(self):
                pass

        gui = MainWindow(DummyEngine(), processing=False)
        gui.root.withdraw()
        gui.active_tab = "exact"

        # Inicialmente há 1 grupo de 2 fotos
        self.assertEqual(len(gui.current_groups()), 1)

        # Simula a exclusão da foto 1
        group = gui.engine.exact[0]
        deleted_photo = group[0]
        group.remove(deleted_photo)

        # Agora resta apenas 1 foto no grupo -> não deve mais ser listado como pendente
        gui._remove_group_from_engine_lists(group)
        self.assertEqual(len(gui.current_groups()), 0)

        gui._is_closing = True
        gui.close()

    def test_pairwise_ignored_pairs_marking_and_independence(self):
        """Verifica se marcar (A, B) como diferentes persiste e não afeta (A, C)."""
        from src.ignored_pairs import (
            canonical_pair, load_ignored_pairs, mark_photos_as_different
        )
        from src.clustering import exact_groups, phash_groups, connected_components

        p1 = self.dir_path / "foto1.jpg"
        p2 = self.dir_path / "foto2.jpg"
        p3 = self.dir_path / "foto3.jpg"
        for p in (p1, p2, p3):
            p.touch()

        # 1. Marca (p1, p2) como fotos diferentes
        ignored = mark_photos_as_different(self.dir_path, [p1, p2])
        self.assertIn(canonical_pair(p1, p2), ignored)
        self.assertNotIn(canonical_pair(p1, p3), ignored)

        # 2. Testa persistência no arquivo json
        loaded = load_ignored_pairs(self.dir_path)
        self.assertEqual(len(loaded), 1)
        self.assertIn(canonical_pair(p1, p2), loaded)

        # 3. Testa exact_groups: p1, p2 e p3 têm mesmo SHA
        meta = {
            str(p1): {"sha": "abc123"},
            str(p2): {"sha": "abc123"},
            str(p3): {"sha": "abc123"},
        }
        # Sem ignored: grupo com [p1, p2, p3]
        all_exact = exact_groups([p1, p2, p3], meta)
        self.assertEqual(len(all_exact), 1)
        self.assertEqual(len(all_exact[0]), 3)

        # Com ignored: p1 e p2 não são unidos diretamente, mas p1-p3 e p2-p3 se conectam
        # Se p1 e p2 são isolados como diferentes e apenas p1 e p2 são comparados:
        pair_exact = exact_groups([p1, p2], meta, ignored_pairs=ignored)
        self.assertEqual(len(pair_exact), 0)  # p1 e p2 não formam mais grupo!

        # Mas p1 e p3 formam grupo normalmente
        p1_p3_exact = exact_groups([p1, p3], meta, ignored_pairs=ignored)
        self.assertEqual(len(p1_p3_exact), 1)
        self.assertEqual(len(p1_p3_exact[0]), 2)

        # 4. Testa connected_components (CLIP)
        clip_paths = [str(p1), str(p2), str(p3)]
        edges = [(0, 1, 0.95), (0, 2, 0.92)]  # edge(p1, p2) e edge(p1, p3)

        # Com ignored_pairs, aresta (0,1) é desconsiderada, restando apenas (0,2)
        clip_groups = connected_components(
            3, edges, threshold=0.85,
            clip_paths=clip_paths, ignored_pairs=ignored
        )
        self.assertEqual(len(clip_groups), 1)
        self.assertEqual(sorted(clip_groups[0]), [0, 2])  # Apenas p1 e p3 agrupados!

    def test_gui_mark_group_as_different_action(self):
        """Verifica a ação do botão 'São fotos diferentes' na interface."""
        p1 = self.dir_path / "imgA.jpg"
        p2 = self.dir_path / "imgB.jpg"
        p1.touch()
        p2.touch()

        class DummyEngine:
            def __init__(self, root):
                self.root = root
                self.threshold = 0.82
                self.phash_distance = 10
                self.exact = [[p1, p2]]
                self.phash_groups = []
                self.meta = {str(p1): {"sha": "123"}, str(p2): {"sha": "123"}}
                self.paths = [p1, p2]
                self.edges = []
                self.clip_paths = [p1, p2]
                self.ignored_pairs = set()
            def groups(self, t=None):
                return []
            def stop(self):
                pass
            def load_partial_state(self):
                pass
            def mark_photos_different(self, photos):
                from src.photo_engine import PhotoEngine
                return PhotoEngine.mark_photos_different(self, photos)

        engine = DummyEngine(self.dir_path)
        gui = MainWindow(engine, processing=False)
        gui.root.withdraw()
        gui.active_tab = "exact"

        self.assertEqual(len(gui.current_groups()), 1)

        # Clica no botão "São fotos diferentes" para o grupo [p1, p2]
        gui.mark_group_as_different([p1, p2])

        # O grupo deve desaparecer imediatamente
        self.assertEqual(len(gui.current_groups()), 0)
        self.assertIn("fotos marcadas como diferentes", gui.status.get())

        gui._is_closing = True
        gui.close()


class TestWebPhotoDetector(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_camera_metadata_extraction(self):
        from src.web_photo_detector import get_image_camera_metadata
        plain_img = self.dir_path / "plain.jpg"
        im = Image.new("RGB", (100, 100), color="white")
        im.save(plain_img)

        has_exif, camera = get_image_camera_metadata(plain_img)
        self.assertFalse(has_exif)
        self.assertIsNone(camera)

    def test_cache_save_and_load(self):
        from src.web_photo_detector import save_web_photos_cache, load_web_photos_cache
        dummy_data = {
            "c:/photos/meme1.jpg": {"score": 0.92, "category": "Meme / Imagem da Web", "is_web": True}
        }
        save_web_photos_cache(self.dir_path, dummy_data)

        loaded = load_web_photos_cache(self.dir_path)
        self.assertIn("c:/photos/meme1.jpg", loaded)
        self.assertAlmostEqual(loaded["c:/photos/meme1.jpg"]["score"], 0.92)

    def test_engine_enable_web_detector_flag(self):
        from src.photo_engine import PhotoEngine
        from src.web_photo_detector import save_web_photos_cache

        meme_file = self.dir_path / "meme_test.jpg"
        meme_file.write_bytes(b"dummy image bytes")
        save_web_photos_cache(self.dir_path, {
            str(meme_file): {"score": 0.95, "category": "Meme / Imagem da Web", "is_web": True}
        })

        # Engine com detector habilitado
        engine_on = PhotoEngine(self.dir_path, enable_web_detector=True)
        self.assertTrue(engine_on.enable_web_detector)
        web_photos = engine_on.get_web_photos(min_score=0.70)
        self.assertEqual(len(web_photos), 1)
        self.assertEqual(web_photos[0][0], meme_file)

        # Engine com detector desabilitado
        engine_off = PhotoEngine(self.dir_path, enable_web_detector=False)
        self.assertFalse(engine_off.enable_web_detector)
        web_photos_off = engine_off.get_web_photos(min_score=0.70)
        self.assertEqual(len(web_photos_off), 0)

    def test_mainwindow_web_detector_toggle(self):
        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        # Testa toggle via menu
        gui.web_detector_menu_var.set(False)
        gui.toggle_web_detector()
        self.assertFalse(APP_CONFIG.get("enable_web_detector"))
        self.assertIn("Desativado", gui.tab_web_btn.cget("text"))

        gui.web_detector_menu_var.set(True)
        gui.toggle_web_detector()
        self.assertTrue(APP_CONFIG.get("enable_web_detector"))
        self.assertNotIn("Desativado", gui.tab_web_btn.cget("text"))

        gui.close()

    def test_modular_menus_and_realtime_progressive_updates(self):
        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        # 1. Verifica menus organizados
        menubar = gui.root.cget("menu")
        self.assertTrue(menubar)
        menu_obj = gui.root.nametowidget(menubar)
        total_entries = menu_obj.index("end") or 0
        labels = []
        for i in range(1, total_entries + 1):
            try:
                labels.append(menu_obj.entrycget(i, "label"))
            except Exception:
                pass
        self.assertIn("Arquivo", labels)
        self.assertIn("Fotos Duplicadas & Similares", labels)
        self.assertIn("Fotos da Web & Memes", labels)

        # 2. Verifica ausência do botão 'Ver Parciais'
        self.assertFalse(hasattr(gui, "partial_btn"))

        # 3. Simula chegada progressiva de resultados em tempo real no poll()
        class FakeLiveEngine:
            def __init__(self):
                self.exact = [["a.jpg", "b.jpg"]]
                self.edges = []
                self.phash_groups = [["c.jpg", "d.jpg"]]
                self.web_photos = {"meme.jpg": {"score": 0.9, "is_web": True}}
            def groups(self, t=None):
                return []
            def get_web_photos(self, min_score=0.70):
                return [(Path("meme.jpg"), {"score": 0.9, "is_web": True})]
            def load_partial_state(self):
                pass

        gui.engine = FakeLiveEngine()
        gui._last_exact_count = 0
        gui._last_phash_count = 0
        gui._last_web_count = 0
        gui._last_progressive_refresh = 0

        gui.holder = {
            "d": {"stage": "Indexação", "done": 10, "total": 100, "elapsed": 2, "rate": 5, "eta": 18, "extra": "progressivo"}
        }
        gui.poll()

        gui.close()


class TestLoggingAndDiagnostics(unittest.TestCase):
    def test_logging_functions(self):
        from src.config import (
            log_debug, log_info, log_warning, log_error, log_critical,
            log_system_diagnostics, LOG_FILE
        )
        test_msg = f"TEST_LOG_MSG_{time.time()}"
        log_debug(f"Debug: {test_msg}")
        log_info(f"Info: {test_msg}")
        log_warning(f"Warning: {test_msg}")
        log_error(f"Error: {test_msg}")
        log_critical(f"Critical: {test_msg}")

        self.assertTrue(LOG_FILE.exists())
        content = LOG_FILE.read_text(encoding="utf-8")
        self.assertIn(test_msg, content)

    def test_system_diagnostics(self):
        from src.config import log_system_diagnostics, LOG_FILE
        log_system_diagnostics()
        content = LOG_FILE.read_text(encoding="utf-8")
        self.assertIn("INICIALIZAÇÃO DO COMPARADOR DE FOTOS COM IA", content)
        self.assertIn("Sistema Operacional:", content)

    def test_cleanup_old_logs_and_clear_log(self):
        from src.config import cleanup_old_logs, clear_log_file, LOG_FILE, PROGRAM_DIR
        # Cria arquivo de log antigo simulado
        old_log = PROGRAM_DIR / "teste_antigo.log"
        old_log.write_text("antigo", encoding="utf-8")
        # Define mtime para 10 dias atrás
        ten_days_ago = time.time() - (10 * 86400)
        os.utime(str(old_log), (ten_days_ago, ten_days_ago))

        cleanup_old_logs(max_age_days=3)
        self.assertFalse(old_log.exists())

        # Testa limpeza manual
        success = clear_log_file()
        self.assertTrue(success)
        self.assertTrue(LOG_FILE.exists())
        self.assertEqual(LOG_FILE.stat().st_size, 0)


class TestWebPhotoDetectorMultimodal(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_heuristics_filename_and_dimensions(self):
        from src.web_photo_detector import inspect_image_heuristics

        # Imagem com nome típico de WhatsApp
        wa_path = self.dir_path / "WhatsApp Image 2026-04-10 at 12.00.00.jpeg"
        im = Image.new("RGB", (512, 512), color="white")
        im.save(wa_path)

        bonus, hints = inspect_image_heuristics(wa_path)
        self.assertGreater(bonus, 0.25)
        self.assertIn("WhatsApp", hints)

        # Imagem com proporção de tela de smartphone (Screenshot)
        screen_path = self.dir_path / "Screenshot_20260401_102030.png"
        im_screen = Image.new("RGB", (1080, 2340), color="blue")
        im_screen.save(screen_path)

        bonus_sc, hints_sc = inspect_image_heuristics(screen_path)
        self.assertGreater(bonus_sc, 0.35)
        self.assertIn("Screenshot", hints_sc)
        self.assertIn("Proporção de Tela de Celular", hints_sc)

    def test_optical_camera_vs_web(self):
        from src.web_photo_detector import get_optical_camera_metadata
        plain_path = self.dir_path / "downloaded_image.jpg"
        im = Image.new("RGB", (800, 600), color="green")
        im.save(plain_path)

        has_optical, cam = get_optical_camera_metadata(plain_path)
        self.assertFalse(has_optical)
        self.assertIsNone(cam)


class TestExcludedFoldersConfigurationAndScanner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_is_path_excluded_rules(self):
        from src.config import is_path_excluded

        # 1. System ignored folders
        recycle_file = self.dir_path / "$Recycle.Bin" / "deleted.jpg"
        self.assertTrue(is_path_excluded(recycle_file))

        # 2. Rule with include_subdirs=True
        sub_folder = self.dir_path / "WhatsApp Stickers" / "Pack1"
        sub_file = sub_folder / "sticker1.jpg"
        rules = [{"path": "WhatsApp Stickers", "include_subdirs": True, "enabled": True}]
        self.assertTrue(is_path_excluded(sub_file, rules, self.dir_path))

        # 3. Rule with include_subdirs=False (direct parent only)
        rules_no_sub = [{"path": "WhatsApp Stickers", "include_subdirs": False, "enabled": True}]
        direct_file = self.dir_path / "WhatsApp Stickers" / "sticker1.jpg"
        self.assertTrue(is_path_excluded(direct_file, rules_no_sub, self.dir_path))
        self.assertFalse(is_path_excluded(sub_file, rules_no_sub, self.dir_path))

        # 4. Disabled rule
        disabled_rules = [{"path": "WhatsApp Stickers", "include_subdirs": True, "enabled": False}]
        self.assertFalse(is_path_excluded(sub_file, disabled_rules, self.dir_path))

        # 5. Absolute path rule
        abs_excluded = self.dir_path / "MinhasFotosExcluidas"
        abs_file = abs_excluded / "sub" / "foto.png"
        abs_rules = [{"path": str(abs_excluded), "include_subdirs": True, "enabled": True}]
        self.assertTrue(is_path_excluded(abs_file, abs_rules))

    def test_scanner_collect_images_with_exclusions(self):
        from src.scanner import collect_images

        # Create directory hierarchy
        keep_dir = self.dir_path / "Fotos_Normais"
        keep_dir.mkdir(parents=True)
        (keep_dir / "foto1.jpg").write_bytes(b"dummy1")
        (keep_dir / "foto2.png").write_bytes(b"dummy2")

        excl_dir = self.dir_path / "Stickers"
        excl_sub = excl_dir / "Animados"
        excl_sub.mkdir(parents=True)
        (excl_dir / "stk1.png").write_bytes(b"dummy_stk1")
        (excl_sub / "stk2.webp").write_bytes(b"dummy_stk2")

        # Test scan with Stickers excluded recursively
        rules = [{"path": "Stickers", "include_subdirs": True, "enabled": True}]
        scanned = collect_images(self.dir_path, excluded_folders=rules)
        scanned_names = [p.name for p in scanned]

        self.assertIn("foto1.jpg", scanned_names)
        self.assertIn("foto2.png", scanned_names)
        self.assertNotIn("stk1.png", scanned_names)
        self.assertNotIn("stk2.webp", scanned_names)

    def test_excluded_folders_dialog_lifecycle(self):
        from src.ui.dialogs.excluded_folders_dialog import ExcludedFoldersDialog
        from src.config import APP_CONFIG

        root = tk.Tk()
        root.withdraw()

        class DummyGui:
            def __init__(self, r):
                self.root = r
                self.engine = None
                self.status = tk.StringVar(value="")

        gui = DummyGui(root)
        dialog = ExcludedFoldersDialog(gui)

        # Verify initial rules loaded
        self.assertIsInstance(dialog.rules, list)
        init_len = len(dialog.rules)

        # Add preset rule
        dialog._add_preset_rule("TestCustomPresetFolder")
        self.assertEqual(len(dialog.rules), init_len + 1)
        self.assertTrue(dialog.rules[-1]["include_subdirs"])

        # Toggle subdirs
        dialog.tree.selection_set(str(len(dialog.rules) - 1))
        dialog._toggle_selected_subdirs()
        self.assertFalse(dialog.rules[-1]["include_subdirs"])

        dialog.top.destroy()
        root.destroy()


class TestGroupLimitAndReplenishmentBuffer(unittest.TestCase):
    def test_default_config_has_max_active_groups(self):
        from src.config import DEFAULT_CONFIG, load_app_config
        self.assertIn("max_active_groups", DEFAULT_CONFIG)
        self.assertEqual(DEFAULT_CONFIG["max_active_groups"], 0)
        cfg = load_app_config()
        self.assertIn("max_active_groups", cfg)

    def test_main_window_limits_and_replenishes_groups(self):
        class MockEngine:
            def __init__(self, num_groups=150):
                self.threshold = 0.82
                # Create 150 unique mock groups of 2 photos each
                self.exact = [[Path(f"C:/Fotos/exact_{i}_a.jpg"), Path(f"C:/Fotos/exact_{i}_b.jpg")] for i in range(num_groups)]
                self.phash_groups = [[Path(f"C:/Fotos/phash_{i}_a.jpg"), Path(f"C:/Fotos/phash_{i}_b.jpg")] for i in range(num_groups)]
                self._clip_groups = [[Path(f"C:/Fotos/clip_{i}_a.jpg"), Path(f"C:/Fotos/clip_{i}_b.jpg")] for i in range(num_groups)]

            def groups(self, th=None):
                return list(self._clip_groups)

            def get_web_photo_groups(self, th=0.60):
                return []

            def mark_photos_different(self, group):
                if group in self._clip_groups:
                    self._clip_groups.remove(group)

        mock_engine = MockEngine(num_groups=150)
        APP_CONFIG["enable_clip"] = True
        gui = MainWindow(engine=mock_engine, processing=False)
        gui.max_groups_var.set(100)

        # Tab clip: 150 raw groups, but current_groups should return exactly 100
        self.assertEqual(len(gui.raw_groups_for_tab("clip")), 150)
        self.assertEqual(len(gui.current_groups()), 100)

        first_group = gui.current_groups()[0]
        group_101 = gui.raw_groups_for_tab("clip")[100]

        # Simulate marking the first group as different (removes it from collections)
        from src.ui.actions import mark_group_different_action
        mark_group_different_action(gui, first_group)

        # After removing 1 group: raw count is 149, active count is still 100 (replenished)
        self.assertEqual(len(gui.raw_groups_for_tab("clip")), 149)
        self.assertEqual(len(gui.current_groups()), 100)
        # The former 101st group should now be the 100th group in the active pool (index 99)
        self.assertEqual(gui.current_groups()[99], group_101)

        # Test changing limit dynamically to 50
        gui.max_groups_var.set(50)
        gui.on_max_groups_change()
        self.assertEqual(len(gui.current_groups()), 50)
        self.assertEqual(APP_CONFIG.get("max_active_groups"), 50)

        # Test changing limit to 200 (greater than total 149)
        gui.max_groups_var.set(200)
        gui.on_max_groups_change()
        self.assertEqual(len(gui.current_groups()), 149)

        # Reset back to 100
        gui.max_groups_var.set(100)
        # Test default limit of 0 (unlimited)
        gui.max_groups_var.set(0)
        gui.on_max_groups_change()
        self.assertEqual(len(gui.current_groups()), 149)
        self.assertEqual(APP_CONFIG.get("max_active_groups"), 0)

        gui.close()


class TestSafeAtomicWriteAndCacheRobustness(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_safe_atomic_write_concurrency(self):
        from src.config import safe_atomic_write
        import concurrent.futures

        target = self.dir_path / "test_cache.json"

        def write_op(i):
            payload = {"thread": i, "data": list(range(100))}
            safe_atomic_write(target, json.dumps(payload))
            return True

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(write_op, range(30)))

        self.assertTrue(all(results))
        self.assertTrue(target.exists())
        # Ensure final file is valid json
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertIn("thread", data)

    def test_engine_save_cache_no_permission_error(self):
        from src.photo_engine import save_cache, load_cache
        cache = {"version": 5, "files": {"C:/test.jpg": {"sha": "123"}}}
        # Repeated quick saves to simulate parallel_metadata
        for _ in range(5):
            save_cache(self.dir_path, cache)

        loaded = load_cache(self.dir_path)
        self.assertEqual(loaded["files"]["C:/test.jpg"]["sha"], "123")


class TestEarlyPHashAndLiveDeletion(unittest.TestCase):
    def test_register_deleted_photo(self):
        from src.photo_engine import PhotoEngine
        engine = PhotoEngine("C:/dummy_folder")
        p1 = Path("C:/Fotos/foto1.jpg")
        p2 = Path("C:/Fotos/foto2.jpg")
        p3 = Path("C:/Fotos/foto3.jpg")

        engine.paths = [p1, p2, p3]
        engine.meta = {str(p1): {"sha": "s1"}, str(p2): {"sha": "s1"}, str(p3): {"sha": "s2"}}
        engine.exact = [[p1, p2]]
        engine.phash_groups = [[p1, p2]]

        # Excluir foto1 durante o processamento
        engine.register_deleted_photo(p1)

        self.assertIn(str(p1), engine.deleted_paths)
        self.assertNotIn(p1, engine.paths)
        self.assertNotIn(str(p1), engine.meta)
        # O grupo exato e phash continha 2 fotos, agora que foto1 foi removida, resta <= 1 foto, logo deve ser removido
        self.assertEqual(len(engine.exact), 0)
        self.assertEqual(len(engine.phash_groups), 0)

    def test_register_deleted_photo_remaps_clip_edges_safely(self):
        from src.photo_engine import PhotoEngine
        from src.clustering import connected_components
        engine = PhotoEngine("C:/dummy_folder")
        p1 = Path("C:/Fotos/foto1.jpg")
        p2 = Path("C:/Fotos/foto2.jpg")
        p3 = Path("C:/Fotos/foto3.jpg")
        p4 = Path("C:/Fotos/foto4.jpg")

        engine.clip_paths = [p1, p2, p3, p4]
        # Arestas conectando (0, 1) e (2, 3)
        engine.edges = [(0, 1, 0.95), (2, 3, 0.92)]

        # Deletar foto2 (índice 1)
        engine.register_deleted_photo(p2)

        # Agora clip_paths tem 3 fotos: [p1, p3, p4]
        self.assertEqual(len(engine.clip_paths), 3)
        self.assertNotIn(p2, engine.clip_paths)

        # O edge (0, 1) foi removido pois p2 foi deletada.
        # O edge (2, 3) que conectava p3 e p4 deve ter sido remapeado para (1, 2)!
        self.assertEqual(len(engine.edges), 1)
        self.assertEqual(engine.edges[0][0], 1)
        self.assertEqual(engine.edges[0][1], 2)
        self.assertAlmostEqual(engine.edges[0][2], 0.92)

        # Chamar engine.groups não deve dar IndexError
        groups = engine.groups(0.80)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0], [p3, p4])

        # Teste de robustez de connected_components contra arestas corrompidas/fora dos limites
        corrupted_edges = [(0, 1, 0.99), (0, 9999, 0.98), (-5, 1, 0.97)]
        safe_groups = connected_components(len(engine.clip_paths), corrupted_edges, 0.80)
        self.assertIsInstance(safe_groups, list)


class TestScalabilityShortcutsAndWebDetectorFixes(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_web_detector_accepts_string_paths(self):
        from src.web_photo_detector import get_optical_camera_metadata, inspect_image_heuristics
        test_file = self.dir_path / "whatsapp_meme.jpg"
        img = Image.new("RGB", (300, 300), color="blue")
        img.save(test_file)

        # Pass as string (simulating compute_embeddings string path return)
        str_path = str(test_file)
        has_cam, cam = get_optical_camera_metadata(str_path)
        bonus, hints = inspect_image_heuristics(str_path)

        self.assertIsInstance(has_cam, bool)
        self.assertGreater(bonus, 0.0)
        self.assertIn("WhatsApp", hints)

    def test_phash_fast_integer_clustering(self):
        from src.clustering import phash_groups
        p1 = self.dir_path / "p1.jpg"
        p2 = self.dir_path / "p2.jpg"
        p3 = self.dir_path / "p3.jpg"

        # Hashes with known Hamming distance
        # p1: 0x0000000000000000, p2: 0x0000000000000003 (dist 2 <= 10)
        # p3: 0xFFFFFFFFFFFFFFFF (dist 64 > 10)
        meta = {
            str(p1): {"phash": "0000000000000000"},
            str(p2): {"phash": "0000000000000003"},
            str(p3): {"phash": "ffffffffffffffff"},
        }

        groups = phash_groups([p1, p2, p3], meta, max_dist=10)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)
        self.assertIn(p1, groups[0])
        self.assertIn(p2, groups[0])
        self.assertNotIn(p3, groups[0])

    def test_mark_photos_different_is_instant_and_filters_groups(self):
        from src.photo_engine import PhotoEngine
        engine = PhotoEngine(self.dir_path)
        p1 = self.dir_path / "a.jpg"
        p2 = self.dir_path / "b.jpg"
        p3 = self.dir_path / "c.jpg"

        engine.exact = [[p1, p2], [p2, p3]]
        engine.phash_groups = [[p1, p2]]
        engine.paths = [p1, p2, p3]

        # Mark (p1, p2) as different
        engine.mark_photos_different([p1, p2])

        # [p1, p2] should be removed from exact and phash_groups, while [p2, p3] remains
        self.assertEqual(len(engine.phash_groups), 0)
        self.assertEqual(len(engine.exact), 1)
        self.assertEqual(engine.exact[0], [p2, p3])


class TestWebThresholdAndTkinterExceptionSafety(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_clean_null_bytes_in_web_cache(self):
        from src.web_photo_detector import _clean_null_bytes, save_web_photos_cache, load_web_photos_cache

        dirty_data = {
            "p1.jpg": {
                "score": 0.45,
                "label": "Foto Pessoal (samsung\x00\x00 SM-M325FV\x00\x00)",
                "camera": "samsung\x00\x00 SM-M325FV\x00\x00",
                "hints": ["WhatsApp\x00"]
            }
        }
        cleaned = _clean_null_bytes(dirty_data)
        self.assertNotIn("\x00", cleaned["p1.jpg"]["label"])
        self.assertNotIn("\x00", cleaned["p1.jpg"]["camera"])
        self.assertNotIn("\x00", cleaned["p1.jpg"]["hints"][0])

        save_web_photos_cache(self.dir_path, dirty_data)
        loaded = load_web_photos_cache(self.dir_path)
        self.assertIn("p1.jpg", loaded)
        self.assertNotIn("\x00", loaded["p1.jpg"]["label"])
        self.assertNotIn("\x00", loaded["p1.jpg"]["camera"])

    def test_set_web_threshold_and_caching(self):
        from src.photo_engine import PhotoEngine
        p1 = self.dir_path / "web1.jpg"
        p2 = self.dir_path / "web2.jpg"
        p3 = self.dir_path / "web3.jpg"
        p1.touch()
        p2.touch()
        p3.touch()

        engine = PhotoEngine(self.dir_path)
        engine.web_photos = {
            str(p1): {"score": 0.25, "label": "Meme Leve", "is_web": True},
            str(p2): {"score": 0.55, "label": "Meme Médio", "is_web": True},
            str(p3): {"score": 0.85, "label": "Meme Forte", "is_web": True},
        }

        gui = MainWindow(engine, processing=False)
        gui.root.withdraw()
        gui.active_tab = "web"

        # Rigor 70%: apenas p3 (0.85)
        gui.set_web_threshold(0.70)
        self.assertEqual(gui.web_threshold, 0.70)
        groups_70 = gui.current_groups()
        all_photos_70 = [p for g in groups_70 for p in g]
        self.assertIn(p3, all_photos_70)
        self.assertNotIn(p1, all_photos_70)
        self.assertNotIn(p2, all_photos_70)

        # Rigor 20%: deve incluir p1 (0.25), p2 (0.55) e p3 (0.85)
        gui.set_web_threshold(0.20)
        self.assertEqual(gui.web_threshold, 0.20)
        groups_20 = gui.current_groups()
        all_photos_20 = [p for g in groups_20 for p in g]
        self.assertIn(p1, all_photos_20)
        self.assertIn(p2, all_photos_20)
        self.assertIn(p3, all_photos_20)

        gui.close()

    def test_tkinter_exception_handler(self):
        from unittest.mock import patch
        gui = MainWindow(engine=None, processing=False)
        gui.root.withdraw()

        # Testa handler customizado sem travar em modal
        with patch("tkinter.messagebox.showerror") as mock_err:
            try:
                raise ValueError("Erro de teste controlado")
            except ValueError as e:
                import sys
                exc_info = sys.exc_info()
                gui._on_tk_exception(exc_info[0], exc_info[1], exc_info[2])

            mock_err.assert_called_once()

        from src.config import LOG_FILE
        content = LOG_FILE.read_text(encoding="utf-8")
        self.assertIn("Erro de teste controlado", content)
        gui.close()

    def test_open_file_and_open_folder(self):
        from unittest.mock import patch
        gui = MainWindow(engine=None, processing=False)
        gui.root.withdraw()

        test_file = self.dir_path / "valid.jpg"
        test_file.touch()

        # Testa abertura de arquivo existente
        with patch("os.startfile", create=True) as mock_start:
            gui.open_file(test_file)
            mock_start.assert_called_once_with(test_file.resolve())

        # Testa abertura de pasta existente
        with patch("subprocess.Popen") as mock_popen, patch("os.startfile", create=True) as mock_start:
            gui.open_folder(test_file)
            if os.name == "nt":
                mock_popen.assert_called_once()
            else:
                mock_start.assert_called_once()

        # Testa com arquivo inexistente (não deve lançar exceção)
        non_existent = self.dir_path / "missing.jpg"
        with patch("tkinter.messagebox.showwarning"):
            gui.open_file(non_existent)
            gui.open_folder(non_existent)

        gui.close()

    def test_parallel_metadata_missing_file_resilience(self):
        from src.photo_engine import parallel_metadata
        non_existent = self.dir_path / "never_existed.jpg"
        stop_event = threading.Event()
        cache = {"version": 5, "files": {}}

        # Executa parallel_metadata com arquivo inexistente
        res = parallel_metadata([non_existent], cache, self.dir_path, None, stop_event, workers=2)
        self.assertIn(str(non_existent), res)
        self.assertIn("error", res[str(non_existent)])

    def test_get_thumb_and_file_info_clean_handling(self):
        gui = MainWindow(engine=None, processing=False)
        gui.root.withdraw()

        # 1. Arquivo de 0 bytes
        empty_file = self.dir_path / "empty.jpg"
        empty_file.touch()
        self.assertIsNone(gui.get_thumb(empty_file))
        self.assertEqual(gui.get_file_info(empty_file), "0x0 • 0 B")

        # 2. Arquivo inexistente
        missing_file = self.dir_path / "missing.jpg"
        self.assertIsNone(gui.get_thumb(missing_file))
        self.assertEqual(gui.get_file_info(missing_file), "")

        # 3. Arquivo de imagem válido
        valid_file = self.dir_path / "valid_img.jpg"
        im = Image.new("RGB", (64, 64), color="red")
        im.save(valid_file)
        thumb = gui.get_thumb(valid_file, size=(32, 32))
        self.assertIsNotNone(thumb)
        self.assertIn("64x64", gui.get_file_info(valid_file))

        gui.close()


class TestSwapAndNumberShortcutDeletion(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_swap_then_number_shortcut_deletion_flow(self):
        """Verifica se após PageUp/PageDown (swap) o atalho de número exclui a foto e atualiza a tela."""
        f1 = self.dir_path / "fotoA.jpg"
        f2 = self.dir_path / "fotoB.jpg"
        im1 = Image.new("RGB", (100, 100), color="red")
        im2 = Image.new("RGB", (100, 100), color="blue")
        im1.save(f1)
        im2.save(f2)

        class DummyEngine:
            def __init__(self, paths):
                self.root = None
                self.threshold = 0.82
                self.phash_distance = 10
                self.lock = threading.RLock()
                self.ignored_pairs = set()
                self.web_photos = {}
                self.exact = [[Path(p) for p in paths]]
                self.phash_groups = []
                self.clip_paths = [Path(p) for p in paths]
                self.paths = [Path(p) for p in paths]
                self.meta = {str(p): {"sha": "123"} for p in paths}
                self.deleted_paths = set()
            def groups(self, t=None):
                return [list(self.exact[0])] if self.exact and len(self.exact[0]) > 1 else []
            def stop(self):
                pass
            def load_partial_state(self):
                pass
            def register_deleted_photo(self, path):
                from src.photo_engine import PhotoEngine
                PhotoEngine.register_deleted_photo(self, path)

        engine = DummyEngine([f1, f2])
        gui = MainWindow(engine, processing=False)
        gui.root.withdraw()
        gui.view_mode = "fullscreen_compare"
        gui.active_tab = "exact"
        gui.compare_group_idx = 0

        # Verifica estado inicial
        initial_group = gui._get_active_comparison_group()
        self.assertEqual(len(initial_group), 2)
        self.assertEqual(Path(initial_group[0]), f1)
        self.assertEqual(Path(initial_group[1]), f2)

        # 1. Executa Page Up / Page Down (Swap)
        gui.swap_active_comparison_photos(0, 1)

        swapped_group = gui._get_active_comparison_group()
        self.assertIsInstance(swapped_group[0], Path)
        self.assertIsInstance(swapped_group[1], Path)
        self.assertEqual(swapped_group[0].name, "fotoB.jpg")
        self.assertEqual(swapped_group[1].name, "fotoA.jpg")
        target_to_delete = swapped_group[0]

        # 2. Simula o atalho da tecla '1' do teclado para apagar a foto da posição 1
        opened_dialogs = []
        original_init = TimedConfirmDeleteDialog.__init__
        def mock_init(dlg, parent, photo_path, on_confirm=None, on_cancel=None, thumb_img=None, countdown=2):
            opened_dialogs.append(photo_path)
            original_init(dlg, parent, photo_path, on_confirm, on_cancel, thumb_img, countdown)
            # Simula confirmação imediata
            dlg.confirm()

        old_cfg = APP_CONFIG.get("confirm_delete_2s", True)
        APP_CONFIG["confirm_delete_2s"] = True
        try:
            TimedConfirmDeleteDialog.__init__ = mock_init
            gui._handle_number_shortcut("1")

            self.assertEqual(len(opened_dialogs), 1)
            # A foto excluída foi a foto da posição 1 (fotoB)
            self.assertEqual(Path(opened_dialogs[0]).name, "fotoB.jpg")

            # 3. Verifica se o grupo com apenas 1 foto restante desapareceu da tela
            remaining_groups = gui.current_groups()
            self.assertEqual(len(remaining_groups), 0)

            # 4. Verifica se a engine foi atualizada
            self.assertEqual(len(gui.engine.exact), 0)
            self.assertIn(str(target_to_delete.resolve()), gui.engine.deleted_paths)

        finally:
            TimedConfirmDeleteDialog.__init__ = original_init
            APP_CONFIG["confirm_delete_2s"] = old_cfg

        gui.close()

    def test_swap_then_direct_deletion_without_confirmation(self):
        """Verifica exclusão direta após swap quando confirm_delete_2s é False."""
        old_cfg = APP_CONFIG.get("confirm_delete_2s", True)
        APP_CONFIG["confirm_delete_2s"] = False
        try:
            f1 = self.dir_path / "img1.jpg"
            f2 = self.dir_path / "img2.jpg"
            im1 = Image.new("RGB", (64, 64), color="green")
            im2 = Image.new("RGB", (64, 64), color="yellow")
            im1.save(f1)
            im2.save(f2)

            class DummyEngine:
                def __init__(self, paths):
                    self.root = None
                    self.threshold = 0.82
                    self.phash_distance = 10
                    self.lock = threading.RLock()
                    self.ignored_pairs = set()
                    self.web_photos = {}
                    self.exact = [[Path(p) for p in paths]]
                    self.phash_groups = []
                    self.clip_paths = [Path(p) for p in paths]
                    self.paths = [Path(p) for p in paths]
                    self.meta = {str(p): {"sha": "456"} for p in paths}
                    self.deleted_paths = set()
                def groups(self, t=None):
                    return [list(self.exact[0])] if self.exact and len(self.exact[0]) > 1 else []
                def stop(self):
                    pass
                def load_partial_state(self):
                    pass
                def register_deleted_photo(self, path):
                    from src.photo_engine import PhotoEngine
                    PhotoEngine.register_deleted_photo(self, path)

            engine = DummyEngine([f1, f2])
            gui = MainWindow(engine, processing=False)
            gui.root.withdraw()
            gui.view_mode = "fullscreen_compare"
            gui.active_tab = "exact"
            gui.compare_group_idx = 0

            # 1. Inverte fotos (swap)
            gui.swap_active_comparison_photos(0, 1)

            # 2. Pressiona tecla '2' (exclui a segunda foto)
            gui._handle_number_shortcut("2")

            # 3. O grupo agora tem 1 foto -> deve ser removido das pendências
            self.assertEqual(len(gui.current_groups()), 0)

            gui.close()
        finally:
            APP_CONFIG["confirm_delete_2s"] = old_cfg


class TestCacheOptimizationAndRemapping(unittest.TestCase):
    def test_can_remap_and_remap_edges(self):
        from src.clustering import can_remap_edges, remap_edges
        import numpy as np

        saved = {
            "paths": ["p1.jpg", "p2.jpg", "p3.jpg", "p4.jpg"],
            "keys": ["k1", "k2", "k3", "k4"],
            "a": np.array([0, 2], dtype=np.int32),
            "b": np.array([1, 3], dtype=np.int32),
            "sim": np.array([0.95, 0.88], dtype=np.float32),
            "k": 50,
            "min_similarity": 0.72
        }

        # Subconjunto onde p2 foi excluído: restam p1, p3, p4
        curr_paths = ["p1.jpg", "p3.jpg", "p4.jpg"]
        curr_keys = ["k1", "k3", "k4"]

        self.assertTrue(can_remap_edges(saved, curr_paths, curr_keys, 50, 0.72))
        remapped = remap_edges(saved, curr_paths, curr_keys)

        # Aresta (0,1) envolvia p2 (excluído) -> descartada
        # Aresta (2,3) envolvia p3 (novo idx 1) e p4 (novo idx 2) -> mantida!
        self.assertEqual(len(remapped), 1)
        self.assertEqual(remapped[0][0], 1)
        self.assertEqual(remapped[0][1], 2)
        self.assertAlmostEqual(remapped[0][2], 0.88, places=2)


class TestWebPhotoReanalysisAndDeletionSynchronization(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_deleted_photos_purged_from_web_photos_and_disk_cache(self):
        from src.photo_engine import PhotoEngine
        from src.web_photo_detector import save_web_photos_cache, load_web_photos_cache

        # Cria 2 arquivos: um continuará existindo, o outro será apagado
        f_alive = self.dir_path / "alive_meme.jpg"
        im = Image.new("RGB", (100, 100), color="blue")
        im.save(f_alive)

        f_deleted = self.dir_path / "deleted_meme.jpg"
        im.save(f_deleted)

        # Salva no cache ambas as imagens
        save_web_photos_cache(self.dir_path, {
            str(f_alive): {"score": 0.85, "label": "Meme", "is_web": True},
            str(f_deleted): {"score": 0.90, "label": "Meme", "is_web": True}
        })

        # Agora apaga o arquivo do disco para simular exclusão prévia
        f_deleted.unlink()
        self.assertFalse(f_deleted.exists())

        # Ao inicializar a engine e obter fotos web, f_deleted não deve aparecer
        engine = PhotoEngine(self.dir_path, enable_web_detector=True)
        web_photos = engine.get_web_photos(min_score=0.50)
        returned_paths = [p for p, _ in web_photos]

        self.assertIn(f_alive, returned_paths)
        self.assertNotIn(f_deleted, returned_paths)

        # Confere se f_deleted foi limpo do cache em disco
        reloaded_cache = load_web_photos_cache(self.dir_path)
        self.assertIn(str(f_alive), reloaded_cache)
        self.assertNotIn(str(f_deleted), reloaded_cache)

    def test_register_deleted_photo_cleans_web_photos(self):
        from src.photo_engine import PhotoEngine

        f1 = self.dir_path / "f1.jpg"
        f1.touch()

        engine = PhotoEngine(self.dir_path, enable_web_detector=True)
        engine.paths = [f1]
        engine.web_photos = {str(f1): {"score": 0.95, "is_web": True}}

        engine.register_deleted_photo(f1)
        self.assertNotIn(str(f1), engine.web_photos)
        self.assertNotIn(f1, engine.paths)
        self.assertIn(str(f1), engine.deleted_paths)


class TestSixPhotoLimitAndUltraFineClipThreshold(unittest.TestCase):
    def test_chunk_group_to_max(self):
        from src.clustering import chunk_group_to_max

        # 1. Grupo com 5 fotos permanece 1 grupo de 5
        g5 = [f"p{i}.jpg" for i in range(5)]
        c5 = chunk_group_to_max(g5, max_size=6)
        self.assertEqual(len(c5), 1)
        self.assertEqual(len(c5[0]), 5)

        # 2. Grupo com 6 fotos permanece 1 grupo de 6
        g6 = [f"p{i}.jpg" for i in range(6)]
        c6 = chunk_group_to_max(g6, max_size=6)
        self.assertEqual(len(c6), 1)
        self.assertEqual(len(c6[0]), 6)

        # 3. Grupo com 7 fotos dividido em 2 grupos equilibrados (4 e 3)
        g7 = [f"p{i}.jpg" for i in range(7)]
        c7 = chunk_group_to_max(g7, max_size=6)
        self.assertEqual(len(c7), 2)
        self.assertEqual(len(c7[0]), 4)
        self.assertEqual(len(c7[1]), 3)
        for sub in c7:
            self.assertLessEqual(len(sub), 6)
            self.assertGreaterEqual(len(sub), 2)

        # 4. Grupo com 12 fotos dividido em 2 grupos de 6
        g12 = [f"p{i}.jpg" for i in range(12)]
        c12 = chunk_group_to_max(g12, max_size=6)
        self.assertEqual(len(c12), 2)
        self.assertEqual(len(c12[0]), 6)
        self.assertEqual(len(c12[1]), 6)

        # 5. Grupo com 17 fotos dividido em 3 grupos de 6, 6, 5
        g17 = [f"p{i}.jpg" for i in range(17)]
        c17 = chunk_group_to_max(g17, max_size=6)
        self.assertEqual(len(c17), 3)
        for sub in c17:
            self.assertLessEqual(len(sub), 6)
            self.assertGreaterEqual(len(sub), 2)

    def test_exact_groups_enforces_max_6(self):
        from src.clustering import exact_groups
        paths = [Path(f"img{i}.jpg") for i in range(10)]
        meta = {str(p): {"sha": "same_hash_123"} for p in paths}
        groups = exact_groups(paths, meta)
        self.assertTrue(len(groups) >= 2)
        for g in groups:
            self.assertLessEqual(len(g), 6)
            self.assertGreaterEqual(len(g), 2)

    def test_gui_clip_threshold_range_and_step(self):
        gui = MainWindow(engine=None, processing=False)
        gui.root.withdraw()

        # Testa definição de threshold em 99.1%
        gui.set_clip_threshold(0.991)
        self.assertAlmostEqual(gui.threshold.get(), 0.991, places=4)
        self.assertEqual(gui.value_label.cget("text"), "99.1%")

        # Testa passo fino de +0.1% (de 99.1% para 99.2%)
        gui._step_threshold(0.001)
        self.assertAlmostEqual(gui.threshold.get(), 0.992, places=4)
        self.assertEqual(gui.value_label.cget("text"), "99.2%")

        # Testa passo fino até 99.9%
        gui.set_clip_threshold(0.999)
        self.assertAlmostEqual(gui.threshold.get(), 0.999, places=4)
        self.assertEqual(gui.value_label.cget("text"), "99.9%")

        gui.close()


class TestHierarchicalMultiFolderSelectorAndDefaults(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.dir_a = self.base / "PastaA"
        self.dir_b = self.base / "PastaB"
        self.dir_a.mkdir(parents=True, exist_ok=True)
        self.dir_b.mkdir(parents=True, exist_ok=True)

        # Cria imagens de teste nas duas pastas
        img1 = Image.new("RGB", (100, 100), color="blue")
        img1.save(self.dir_a / "foto_a1.jpg")
        img2 = Image.new("RGB", (100, 100), color="green")
        img2.save(self.dir_b / "foto_b1.jpg")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_collect_images_multi_folder(self):
        from src.scanner import collect_images
        images = collect_images([self.dir_a, self.dir_b])
        names = [p.name for p in images]
        self.assertIn("foto_a1.jpg", names)
        self.assertIn("foto_b1.jpg", names)
        self.assertEqual(len(images), 2)

    def test_get_cache_paths_multi_folder(self):
        from src.config import get_cache_paths
        cp_single = get_cache_paths(self.dir_a)
        cp_multi = get_cache_paths([self.dir_a, self.dir_b])
        self.assertTrue(cp_single["meta"].parent.exists())
        self.assertTrue(cp_multi["meta"].parent.exists())
        self.assertNotEqual(str(cp_single["meta"]), str(cp_multi["meta"]))

    def test_photo_engine_multi_folder_init(self):
        from src.photo_engine import PhotoEngine
        engine = PhotoEngine([self.dir_a, self.dir_b])
        self.assertEqual(len(engine.roots), 2)
        self.assertEqual(engine.root, self.dir_a.resolve())
        engine.run()
        self.assertEqual(len(engine.paths), 2)

    def test_folder_selector_dialog_and_persistence(self):
        from src.config import APP_CONFIG, save_app_config
        from src.ui.dialogs.folder_selector_dialog import FolderSelectorDialog

        APP_CONFIG["last_folders"] = [str(self.dir_a), str(self.dir_b)]
        APP_CONFIG["last_folder"] = str(self.dir_a)
        save_app_config(APP_CONFIG)

        started_folders = []
        def mock_start(folders):
            started_folders.extend(folders)

        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        dlg = FolderSelectorDialog(gui, on_start=mock_start)
        dlg.top.withdraw()

        sel_strs = [str(p) for p in dlg.selected_folders]
        self.assertIn(str(self.dir_a.resolve()), sel_strs)
        self.assertIn(str(self.dir_b.resolve()), sel_strs)

        dlg._confirm_and_start()
        self.assertEqual(len(started_folders), 2)
        self.assertIn(self.dir_a.resolve(), started_folders)

        gui.close()

    def test_folder_selector_dialog_centering_and_dimensions(self):
        """Verifica se o diálogo de pastas possui área expandida para pastas excluídas e se centraliza."""
        from src.ui.dialogs.folder_selector_dialog import FolderSelectorDialog
        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        dlg = FolderSelectorDialog(gui)
        dlg.top.withdraw()

        # Verifica altura expandida da listbox de excluídos (pelo menos 8 linhas)
        self.assertGreaterEqual(int(dlg.excl_listbox.cget("height")), 8)

        # Verifica método de centralização
        dlg._center_window(820, 720)
        geom = dlg.top.geometry()
        # Formato: WxH+X+Y
        self.assertIn("+", geom)

        gui.close()

    def test_folder_selector_dialog_last_folder_always_listed(self):
        """Verifica se a última pasta selecionada sempre aparece listada no diálogo."""
        from src.config import APP_CONFIG, save_app_config
        from src.ui.dialogs.folder_selector_dialog import FolderSelectorDialog

        APP_CONFIG["last_folder"] = str(self.dir_b)
        APP_CONFIG["last_folders"] = [str(self.dir_a)]
        save_app_config(APP_CONFIG)

        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        dlg = FolderSelectorDialog(gui)
        dlg.top.withdraw()

        sel_strs = [str(p.resolve()) for p in dlg.selected_folders]
        # Ambas devem estar presentes, especialmente last_folder
        self.assertIn(str(self.dir_b.resolve()), sel_strs)
        self.assertIn(str(self.dir_a.resolve()), sel_strs)

        gui.close()


class TestFocusAndSharpnessIndicator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

        # 1. Cria imagem muito nítida (linhas de alto contraste)
        from PIL import ImageDraw, ImageFilter
        sharp_img = Image.new("L", (400, 400), color=0)
        draw = ImageDraw.Draw(sharp_img)
        for i in range(40):
            draw.line([(i * 10, 0), (i * 10, 400)], fill=255, width=2)
        self.sharp_path = self.base / "sharp.jpg"
        sharp_img.save(self.sharp_path)

        # 2. Cria imagem intermediária (pouco desfocada)
        med_img = sharp_img.filter(ImageFilter.GaussianBlur(radius=2))
        self.med_path = self.base / "medium.jpg"
        med_img.save(self.med_path)

        # 3. Cria imagem muito desfocada (baixo foco)
        blur_img = sharp_img.filter(ImageFilter.GaussianBlur(radius=8))
        self.blur_path = self.base / "blur.jpg"
        blur_img.save(self.blur_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compute_sharpness_score_relative_levels(self):
        from src.hasher import compute_sharpness_score
        score_sharp = compute_sharpness_score(self.sharp_path)
        score_med = compute_sharpness_score(self.med_path)
        score_blur = compute_sharpness_score(self.blur_path)

        self.assertGreater(score_sharp, score_med)
        self.assertGreater(score_med, score_blur)
        self.assertGreater(score_blur, 0.0)

    def test_group_focus_ranks_two_photos(self):
        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        ranks = gui.get_group_focus_ranks([self.sharp_path, self.blur_path])
        self.assertIn(str(self.sharp_path), ranks)
        self.assertIn(str(self.blur_path), ranks)

        # Foto mais focada deve ser Verde (🟢 Mais Nítida)
        icon_sharp, label_sharp, fg_sharp, _ = ranks[str(self.sharp_path)]
        self.assertEqual(icon_sharp, "🟢")
        self.assertEqual(label_sharp, "Mais Nítida")
        self.assertEqual(fg_sharp, "#008800")

        # Foto menos focada deve ser Vermelha (🔴 Menos Nítida)
        icon_blur, label_blur, fg_blur, _ = ranks[str(self.blur_path)]
        self.assertEqual(icon_blur, "🔴")
        self.assertEqual(label_blur, "Menos Nítida")
        self.assertEqual(fg_blur, "#cc0000")

        gui.close()

    def test_group_focus_ranks_three_photos_with_intermediate(self):
        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        group = [self.sharp_path, self.med_path, self.blur_path]
        ranks = gui.get_group_focus_ranks(group)

        # Mais nítida: Verde
        self.assertEqual(ranks[str(self.sharp_path)][0], "🟢")
        self.assertEqual(ranks[str(self.sharp_path)][1], "Mais Nítida")

        # Intermediária: Amarelo (🟡 Nitidez Média)
        self.assertEqual(ranks[str(self.med_path)][0], "🟡")
        self.assertEqual(ranks[str(self.med_path)][1], "Nitidez Média")
        self.assertEqual(ranks[str(self.med_path)][2], "#b8860b")

        # Menos nítida: Vermelha
        self.assertEqual(ranks[str(self.blur_path)][0], "🔴")
        self.assertEqual(ranks[str(self.blur_path)][1], "Menos Nítida")

        gui.close()

    def test_photo_card_renders_focus_badge(self):
        from tkinter import ttk
        from src.ui.components.photo_card import PhotoCard
        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        group = [self.sharp_path, self.blur_path]
        frame = ttk.Frame(gui.root)
        card_sharp = PhotoCard(frame, self.sharp_path, 0, group, gui)
        card_blur = PhotoCard(frame, self.blur_path, 1, group, gui)

        # Verifica se os cards foram criados com sucesso
        self.assertEqual(card_sharp.path, self.sharp_path)
        self.assertEqual(card_blur.path, self.blur_path)

        gui.close()


class TestShadowRemoverAndBeforeAfterCompare(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

        # Cria imagem com área sombreada e área clara
        im = Image.new("RGB", (300, 300), color=(200, 200, 200))
        from PIL import ImageDraw
        draw = ImageDraw.Draw(im)
        draw.rectangle([50, 50, 250, 250], fill=(40, 30, 25))
        self.test_img_path = self.base / "photo_with_shadow.jpg"
        im.save(self.test_img_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_remove_shadows_local_and_file_generation(self):
        from src.shadow_remover import remove_shadows_local, remove_shadows_from_image
        enhanced_pil = remove_shadows_local(self.test_img_path)
        self.assertEqual(enhanced_pil.size, (300, 300))

        out_path = remove_shadows_from_image(self.test_img_path)
        self.assertTrue(out_path.exists())
        self.assertGreater(out_path.stat().st_size, 0)
        out_path.unlink(missing_ok=True)

    def test_before_after_dialog_keep_original(self):
        from src.shadow_remover import remove_shadows_from_image
        from src.ui.dialogs.before_after_compare_dialog import BeforeAfterCompareDialog

        enh_path = remove_shadows_from_image(self.test_img_path)
        self.assertTrue(enh_path.exists())

        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        decisions = []
        def on_dec(choice, chosen_path):
            decisions.append((choice, chosen_path))

        dlg = BeforeAfterCompareDialog(gui, self.test_img_path, enh_path, on_decision=on_dec)
        dlg.top.withdraw()

        # Escolhe manter original
        dlg.keep_original()

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0][0], "original")
        self.assertTrue(self.test_img_path.exists())
        self.assertFalse(enh_path.exists())

        gui.close()

    def test_before_after_dialog_keep_enhanced(self):
        from src.shadow_remover import remove_shadows_from_image
        from src.ui.dialogs.before_after_compare_dialog import BeforeAfterCompareDialog
        from unittest.mock import patch

        enh_path = remove_shadows_from_image(self.test_img_path)
        self.assertTrue(enh_path.exists())

        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        decisions = []
        def on_dec(choice, chosen_path):
            decisions.append((choice, chosen_path))

        dlg = BeforeAfterCompareDialog(gui, self.test_img_path, enh_path, on_decision=on_dec)
        dlg.top.withdraw()

        with patch("src.ui.dialogs.before_after_compare_dialog.send2trash") as mock_trash:
            dlg.keep_enhanced()
            mock_trash.assert_called_once()

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0][0], "enhanced")
        self.assertTrue(self.test_img_path.exists())

        gui.close()

    def test_remove_shadows_deep_local_pytorch_cuda(self):
        from src.shadow_remover import remove_shadows_deep_local, remove_shadows_from_image
        enhanced_pil = remove_shadows_deep_local(self.test_img_path)
        self.assertEqual(enhanced_pil.size, (300, 300))

        # Testa saída via orquestrador no modo deep padrão
        out_path = remove_shadows_from_image(self.test_img_path, method="deep")
        self.assertTrue(out_path.exists())
        self.assertGreater(out_path.stat().st_size, 0)
        out_path.unlink(missing_ok=True)

    def test_shadow_mode_configuration_and_gui_toggle(self):
        from src.config import APP_CONFIG
        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        gui.set_shadow_removal_mode("deep")
        self.assertEqual(APP_CONFIG.get("shadow_removal_mode"), "deep")
        self.assertIn("IA Profunda", gui.status.get())

        gui.set_shadow_removal_mode("msrcr")
        self.assertEqual(APP_CONFIG.get("shadow_removal_mode"), "msrcr")
        self.assertIn("Retinex", gui.status.get())

        gui.set_shadow_removal_mode("gemini")
        self.assertEqual(APP_CONFIG.get("shadow_removal_mode"), "gemini")
        self.assertIn("Gemini", gui.status.get())

        # Restaura para deep
        gui.set_shadow_removal_mode("deep")
        self.assertEqual(APP_CONFIG.get("shadow_removal_mode"), "deep")

        # Testa modo interativo e histograma
        gui.set_shadow_removal_mode("interactive")
        self.assertEqual(APP_CONFIG.get("shadow_removal_mode"), "interactive")
        self.assertIn("Interativo", gui.status.get())

        gui.set_shadow_removal_mode("histogram")
        self.assertEqual(APP_CONFIG.get("shadow_removal_mode"), "histogram")
        self.assertIn("Histograma", gui.status.get())

        gui.close()

    def test_alt1_2_strict_highlight_preservation(self):
        from src.shadow_remover import remove_shadows_strict_highlight, remove_shadows_from_image
        import numpy as np

        w, h = 60, 60
        arr = np.zeros((h, w, 3), dtype=np.uint8)
        arr[:, :30] = [30, 30, 30]
        arr[:, 30:] = [220, 220, 220]

        half_img = self.base / "half_test.jpg"
        Image.fromarray(arr).save(half_img)

        enh_soft = remove_shadows_strict_highlight(half_img, intensity=0.25)
        enh_strong = remove_shadows_strict_highlight(half_img, intensity=0.85)

        arr_soft = np.array(enh_soft)
        arr_strong = np.array(enh_strong)

        self.assertGreater(int(arr_soft[30, 10, 0]), 30)
        self.assertGreater(int(arr_strong[30, 10, 0]), int(arr_soft[30, 10, 0]))
        self.assertLessEqual(int(arr_strong[30, 45, 0]), 225)

        out_p = remove_shadows_from_image(half_img, method="interactive", intensity=0.60)
        self.assertTrue(out_p.exists())
        out_p.unlink(missing_ok=True)
        half_img.unlink(missing_ok=True)

    def test_alt4_auto_adaptive_histogram(self):
        from src.shadow_remover import remove_shadows_auto_histogram, remove_shadows_from_image

        res_img, meta = remove_shadows_auto_histogram(self.test_img_path)
        self.assertIn("scenario", meta)
        self.assertIn("auto_intensity", meta)
        self.assertGreater(meta["auto_intensity"], 0.1)
        self.assertEqual(res_img.size, (300, 300))

        out_p = remove_shadows_from_image(self.test_img_path, method="histogram")
        self.assertTrue(out_p.exists())
        out_p.unlink(missing_ok=True)

    def test_before_after_dialog_three_buttons_and_switching(self):
        from src.ui.dialogs.before_after_compare_dialog import BeforeAfterCompareDialog
        from src.shadow_remover import remove_shadows_from_image

        enh_path = remove_shadows_from_image(self.test_img_path)
        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        dlg = BeforeAfterCompareDialog(gui, self.test_img_path, enh_path)

        self.assertTrue(hasattr(dlg, "btn_alt1_2"))
        self.assertTrue(hasattr(dlg, "btn_alt3"))
        self.assertTrue(hasattr(dlg, "btn_alt4"))

        dlg.switch_mode("deep")
        self.assertEqual(dlg.current_mode, "deep")

        dlg.switch_mode("histogram")
        self.assertEqual(dlg.current_mode, "histogram")

        dlg.switch_mode("interactive")
        self.assertEqual(dlg.current_mode, "interactive")

        dlg._set_intensity_preset(75.0)
        self.assertEqual(dlg.intensity_val, 0.75)

        dlg.cancel()
        gui.close()


class TestSearchModesSelection(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp_dir.name)
        # Cria imagens sintéticas
        self.img1 = self.base / "foto_a.jpg"
        self.img2 = self.base / "foto_b.jpg"
        Image.new("RGB", (64, 64), color="blue").save(self.img1)
        Image.new("RGB", (64, 64), color="blue").save(self.img2)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_engine_skip_clip_when_disabled(self):
        """Verifica se ao desabilitar enable_clip, o PhotoEngine não executa CLIP e acelera o carregamento."""
        from src.photo_engine import PhotoEngine
        engine = PhotoEngine(self.base, enable_clip=False, enable_hash=True, enable_web_detector=False)
        engine.run()
        self.assertEqual(len(engine.edges), 0)
        self.assertEqual(len(engine.clip_paths), 0)
        self.assertGreaterEqual(len(engine.exact), 1)

    def test_engine_skip_hash_when_disabled(self):
        """Verifica se ao desabilitar enable_hash, o PhotoEngine não agrupa por SHA/pHash."""
        from src.photo_engine import PhotoEngine
        engine = PhotoEngine(self.base, enable_clip=True, enable_hash=False, enable_web_detector=False)
        engine.run()
        self.assertEqual(len(engine.exact), 0)
        self.assertEqual(len(engine.phash_groups), 0)
        self.assertGreaterEqual(len(engine.clip_paths), 1)

    def test_folder_selector_dialog_search_options(self):
        """Verifica checkboxes de seleção de tipos de busca no FolderSelectorDialog."""
        from src.config import APP_CONFIG
        from src.ui.dialogs.folder_selector_dialog import FolderSelectorDialog
        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        dlg = FolderSelectorDialog(gui)
        self.assertTrue(hasattr(dlg, "var_enable_hash"))
        self.assertTrue(hasattr(dlg, "var_enable_clip"))
        self.assertTrue(hasattr(dlg, "var_enable_memes"))

        dlg.var_enable_clip.set(False)
        dlg.var_enable_hash.set(True)
        dlg.var_enable_memes.set(False)
        dlg.selected_folders = [self.base]
        dlg._confirm_and_start()

        self.assertFalse(APP_CONFIG.get("enable_clip"))
        self.assertTrue(APP_CONFIG.get("enable_hash"))
        self.assertFalse(APP_CONFIG.get("enable_web_detector"))

        # Restaura defaults
        APP_CONFIG["enable_clip"] = True
        APP_CONFIG["enable_hash"] = True
        APP_CONFIG["enable_web_detector"] = True
        from src.config import save_app_config
        save_app_config(APP_CONFIG)
        gui.close()

    def test_mainwindow_search_toggles(self):
        """Verifica os métodos de alternância de modos de busca na MainWindow."""
        from src.config import APP_CONFIG
        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        gui.enable_clip_var.set(False)
        gui.toggle_enable_clip()
        self.assertFalse(APP_CONFIG.get("enable_clip"))
        self.assertIn("CLIP (Desativado)", gui.tab_clip_btn.cget("text"))

        gui.enable_hash_var.set(False)
        gui.toggle_enable_hash()
        self.assertFalse(APP_CONFIG.get("enable_hash"))
        self.assertIn("pHash (Desativado)", gui.tab_phash_btn.cget("text"))
        self.assertIn("SHA-256 (Desativado)", gui.tab_exact_btn.cget("text"))

        # Restaura defaults
        gui.enable_clip_var.set(True)
        gui.toggle_enable_clip()
        gui.enable_hash_var.set(True)
        gui.toggle_enable_hash()
        self.assertTrue(APP_CONFIG.get("enable_clip"))
        self.assertTrue(APP_CONFIG.get("enable_hash"))
        gui.close()

    def test_navigation_50_steps_fullscreen_and_grid(self):
        """Verifica se os botões e métodos de saltar 50 fotos/grupos funcionam corretamente."""
        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        # 1. Modo grade
        gui.view_mode = "grid"
        gui.total_pages = lambda: 120
        gui.current_page = 1

        gui.page_step_fwd_50()
        self.assertEqual(gui.current_page, 51)

        gui.page_step_fwd_50()
        self.assertEqual(gui.current_page, 101)

        gui.page_step_fwd_50()
        self.assertEqual(gui.current_page, 120)  # teto máximo

        gui.page_step_back_50()
        self.assertEqual(gui.current_page, 70)

        gui.page_step_back_50()
        self.assertEqual(gui.current_page, 20)

        gui.page_step_back_50()
        self.assertEqual(gui.current_page, 1)  # piso mínimo

        # 2. Modo tela cheia
        gui.view_mode = "fullscreen_compare"
        gui.current_groups = lambda: [["a", "b"]] * 150
        gui.compare_group_idx = 0

        gui.page_step_fwd_50()
        self.assertEqual(gui.compare_group_idx, 50)

        gui.page_step_fwd_50()
        self.assertEqual(gui.compare_group_idx, 100)

        gui.page_step_fwd_50()
        self.assertEqual(gui.compare_group_idx, 149)  # teto máximo (len-1)

        gui.page_step_back_50()
        self.assertEqual(gui.compare_group_idx, 99)

        gui.page_step_back_50()
        self.assertEqual(gui.compare_group_idx, 49)

        gui.page_step_back_50()
        self.assertEqual(gui.compare_group_idx, 0)  # piso mínimo

        gui.close()

    def test_clip_threshold_and_last_folder_persistence(self):
        """Verifica se a última pasta e o threshold do CLIP são persistidos e restaurados corretamente."""
        from src.config import APP_CONFIG
        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        # Altera threshold e verifica persistência
        gui.set_clip_threshold(0.885)
        self.assertAlmostEqual(APP_CONFIG.get("clip_threshold"), 0.885, places=3)

        # Inicializa nova MainWindow e confere carregamento do valor salvo
        gui2 = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui2.root.withdraw()
        self.assertAlmostEqual(gui2.threshold.get(), 0.885, places=3)

        gui.close()
        gui2.close()

    def test_folder_selector_dialog_excluded_folders_component(self):
        """Verifica o componente de pastas excluídas no FolderSelectorDialog."""
        from src.config import APP_CONFIG
        from src.ui.dialogs.folder_selector_dialog import FolderSelectorDialog
        gui = MainWindow(engine=None, processing=False, auto_open_selector=False)
        gui.root.withdraw()

        dlg = FolderSelectorDialog(gui)
        self.assertTrue(hasattr(dlg, "excl_listbox"))
        self.assertTrue(hasattr(dlg, "excluded_rules"))

        # Adiciona regra de teste
        test_rule = {"path": "C:\\TesteIgnorado", "include_subdirs": True, "enabled": True}
        dlg.excluded_rules.append(test_rule)
        dlg.selected_folders = [self.base]
        dlg._confirm_and_start()

        saved_rules = APP_CONFIG.get("excluded_folders", [])
        self.assertTrue(any(r.get("path") == "C:\\TesteIgnorado" for r in saved_rules if isinstance(r, dict)))

        # Remove regra de teste
        APP_CONFIG["excluded_folders"] = [r for r in saved_rules if not (isinstance(r, dict) and r.get("path") == "C:\\TesteIgnorado")]
        gui.close()


class TestDependencyInstaller(unittest.TestCase):
    """Testes unitários para o instalador e gerenciador dinâmico de IA."""
    def test_get_ai_runtime_dir_and_path_setup(self):
        from src.dependency_installer import get_ai_runtime_dir, setup_ai_runtime_path, is_ai_available, get_ai_details
        rdir = get_ai_runtime_dir()
        self.assertIsInstance(rdir, Path)
        self.assertTrue(rdir.exists() or rdir.parent.exists())

        setup_ai_runtime_path()
        details = get_ai_details()
        self.assertIsInstance(details, dict)
        self.assertIn("installed", details)
        self.assertIn("torch_version", details)
        self.assertIn("cuda_available", details)
        self.assertIn("runtime_path", details)

    def test_dependency_installer_dialog_creation(self):
        from src.dependency_installer import DependencyInstallerDialog
        root = tk.Tk()
        root.withdraw()
        dlg = DependencyInstallerDialog(parent=root, auto_start=False)
        self.assertTrue(hasattr(dlg, "progress_bar"))
        self.assertTrue(hasattr(dlg, "mode_var"))
        dlg.skip_without_ai()
        root.destroy()

    def test_ai_missing_graceful_import_and_error_handling(self):
        import src.embeddings as emb_mod
        orig_torch = emb_mod.torch
        orig_clip = emb_mod.open_clip
        try:
            emb_mod.torch = None
            emb_mod.open_clip = None
            emb_mod._MODEL = None
            with self.assertRaises(RuntimeError) as ctx:
                emb_mod.get_model()
            self.assertIn("PyTorch ou OpenCLIP não estão instalados", str(ctx.exception))
            self.assertEqual(emb_mod.choose_batch("cuda"), 16)
        finally:
            emb_mod.torch = orig_torch
            emb_mod.open_clip = orig_clip
            emb_mod._MODEL = None

    def test_photo_engine_fallback_when_ai_missing(self):
        from unittest.mock import patch
        from src.photo_engine import PhotoEngine
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with patch("src.dependency_installer.is_ai_available", return_value=False):
                pe = PhotoEngine(td, enable_clip=True, enable_hash=False, enable_web_detector=True)
                self.assertFalse(pe.enable_clip)
                self.assertFalse(pe.enable_web_detector)
                self.assertTrue(pe.enable_hash)


class TestAutoUpdater(unittest.TestCase):
    def test_parse_version_tuple_and_is_version_newer(self):
        from src.updater import parse_version_tuple, is_version_newer
        self.assertEqual(parse_version_tuple("v1.0.0"), (1, 0, 0))
        self.assertEqual(parse_version_tuple("1.2.3"), (1, 2, 3))
        self.assertEqual(parse_version_tuple("v2.1.0-beta"), (2, 1, 0))
        self.assertTrue(is_version_newer("v1.1.0", "1.0.0"))
        self.assertTrue(is_version_newer("2.0.0", "1.9.9"))
        self.assertFalse(is_version_newer("1.0.0", "1.0.0"))
        self.assertFalse(is_version_newer("0.9.0", "1.0.0"))

    def test_check_for_updates_mock_release(self):
        from unittest.mock import patch, MagicMock
        import io
        from src.updater import check_for_updates

        mock_payload = {
            "tag_name": "v1.2.0",
            "name": "Lançamento v1.2.0",
            "body": "Novas funcionalidades e correções.",
            "published_at": "2026-10-01T12:00:00Z",
            "html_url": "https://github.com/Guilherme-ai/comparador_fotos/releases/tag/v1.2.0",
            "assets": [
                {
                    "name": "ComparadorFotos_Launcher.exe",
                    "browser_download_url": "https://github.com/Guilherme-ai/comparador_fotos/releases/download/v1.2.0/ComparadorFotos_Launcher.exe",
                    "size": 54000000
                }
            ]
        }
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(mock_payload).encode("utf-8")
        mock_response.__enter__.return_value = mock_response

        with patch("src.updater.urlopen", return_value=mock_response):
            res = check_for_updates(repo="Guilherme-ai/comparador_fotos")
            self.assertTrue(res["success"])
            self.assertTrue(res["has_update"])
            self.assertEqual(res["latest_version"], "1.2.0")
            self.assertEqual(res["asset_name"], "ComparadorFotos_Launcher.exe")
            self.assertEqual(res["asset_size"], 54000000)

    def test_update_dialog_creation(self):
        from src.ui.dialogs.update_dialog import UpdateDialog
        root = tk.Tk()
        root.withdraw()
        info = {
            "latest_version": "1.2.0",
            "release_name": "Versão 1.2.0",
            "release_notes": "Correções e melhorias de desempenho.",
            "download_url": "https://example.com/ComparadorFotos_Launcher.exe",
            "asset_name": "ComparadorFotos_Launcher.exe"
        }
        dlg = UpdateDialog(parent=root, update_info=info)
        self.assertTrue(hasattr(dlg, "prog_bar"))
        self.assertTrue(hasattr(dlg, "txt_notes"))
        dlg.top.destroy()
        root.destroy()


def run_tests():
    from src.config import CONFIG_FILE, load_app_config, save_app_config
    orig_config_content = None
    if CONFIG_FILE.exists():
        try:
            orig_config_content = CONFIG_FILE.read_text(encoding="utf-8")
        except Exception:
            pass

    try:
        suite = unittest.TestSuite()
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestConfigAndPaths))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestHardwareMonitoring))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestHasherRobustness))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestClustering))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestTimedConfirmDeleteDialog))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestFileSwapAndUpgrade))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestFullscreenCompareRobustness))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestSinglePhotoGroupDismissalAndIgnoredPairs))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestWebPhotoDetector))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestWebPhotoDetectorMultimodal))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestLoggingAndDiagnostics))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestExcludedFoldersConfigurationAndScanner))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestGroupLimitAndReplenishmentBuffer))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestSafeAtomicWriteAndCacheRobustness))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestEarlyPHashAndLiveDeletion))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestScalabilityShortcutsAndWebDetectorFixes))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestWebThresholdAndTkinterExceptionSafety))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestSwapAndNumberShortcutDeletion))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestCacheOptimizationAndRemapping))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestWebPhotoReanalysisAndDeletionSynchronization))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestSixPhotoLimitAndUltraFineClipThreshold))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestHierarchicalMultiFolderSelectorAndDefaults))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestFocusAndSharpnessIndicator))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestShadowRemoverAndBeforeAfterCompare))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestSearchModesSelection))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestDependencyInstaller))
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestAutoUpdater))

        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        return result.wasSuccessful()
    finally:
        if orig_config_content is not None:
            try:
                CONFIG_FILE.write_text(orig_config_content, encoding="utf-8")
                APP_CONFIG.clear()
                APP_CONFIG.update(load_app_config())
            except Exception:
                pass


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)




