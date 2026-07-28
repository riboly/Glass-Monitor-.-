from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

import app_storage
from alerting import AlertManager
from cards_meta import CARD_IDS, normalize_card_order
from metrics_worker import MetricsWorker
from traffic import TrafficCollector, parse_traffic_body


class CoreTests(unittest.TestCase):
    def test_card_order_normalization(self) -> None:
        order = normalize_card_order(["traffic", "hw", "traffic", "unknown"])
        self.assertEqual(order[:2], ["traffic", "hw"])
        self.assertEqual(set(order), set(CARD_IDS))
        self.assertEqual(len(order), len(CARD_IDS))

    def test_alert_duration_cooldown_and_hysteresis(self) -> None:
        manager = AlertManager()
        cfg = {
            "enabled": True,
            "cpu_temp": 80,
            "duration_sec": 10,
            "cooldown_sec": 30,
            "hysteresis": 3,
        }
        self.assertEqual(manager.evaluate({"cpu_temp": 85}, cfg, now=0), [])
        self.assertEqual(len(manager.evaluate({"cpu_temp": 85}, cfg, now=10)), 1)
        self.assertEqual(manager.evaluate({"cpu_temp": 84}, cfg, now=20), [])
        manager.evaluate({"cpu_temp": 76}, cfg, now=21)
        self.assertEqual(manager.evaluate({"cpu_temp": 85}, cfg, now=30), [])
        self.assertEqual(len(manager.evaluate({"cpu_temp": 85}, cfg, now=41)), 1)

    def test_traffic_parser(self) -> None:
        info = parse_traffic_body("upload=100; download=300; total=1000; expire=0")
        self.assertTrue(info.ok)
        self.assertEqual(info.used, 400)
        self.assertEqual(info.percent, 40.0)

    def test_atomic_storage_and_legacy_migration(self) -> None:
        names = (
            "ROOT", "DATA_ROOT", "CONFIG_PATH", "POS_PATH", "CRASH_LOG",
            "LOCAL_DATA_ROOT",
        )
        original = {name: getattr(app_storage, name) for name in names}
        try:
            with tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                program = base / "program"
                local_data = base / "local" / "GlassMonitor"
                program.mkdir()
                local_data.mkdir(parents=True)
                app_storage.ROOT = program
                app_storage.DATA_ROOT = program
                app_storage.CONFIG_PATH = program / "config.json"
                app_storage.POS_PATH = program / "window_pos.json"
                app_storage.CRASH_LOG = program / "crash.log"
                app_storage.LOCAL_DATA_ROOT = local_data
                (local_data / "config.json").write_text(
                    '{"secret": "kept"}', encoding="utf-8"
                )

                app_storage.migrate_legacy_data()
                self.assertFalse((local_data / "config.json").exists())
                self.assertEqual(
                    app_storage.load_json(app_storage.CONFIG_PATH, {}),
                    {"secret": "kept"},
                )
                app_storage.atomic_save_json(app_storage.CONFIG_PATH, {"version": 2})
                self.assertEqual(
                    app_storage.load_json(app_storage.CONFIG_PATH, {}),
                    {"version": 2},
                )
                self.assertTrue((program / "config.json.bak").is_file())
        finally:
            for name, value in original.items():
                setattr(app_storage, name, value)

    def test_metrics_worker_publishes_snapshot(self) -> None:
        @dataclass
        class FakeSample:
            cpu: float = 1.0

        class FakeCollector:
            def __init__(self):
                self.requirements = None

            def sample(self, requirements):
                self.requirements = dict(requirements)
                time.sleep(0.03)
                return FakeSample()

            def net_history(self):
                return [1.0], [2.0]

            def disk_parts(self, _limit):
                return []

            def top_processes(self, _limit):
                return []

        collector = FakeCollector()
        worker = MetricsWorker(interval_ms=500, collector=collector)
        worker.configure({"network": True}, 500)
        worker.start()
        deadline = time.monotonic() + 1.5
        while worker.get().sample is None and time.monotonic() < deadline:
            time.sleep(0.02)
        snapshot = worker.get()
        worker.stop()
        self.assertIsNotNone(snapshot.sample)
        self.assertGreaterEqual(snapshot.duration_ms, 25)
        self.assertEqual(snapshot.ups, [1.0])
        self.assertEqual(collector.requirements, {"network": True})
        self.assertIsNone(worker._thread)

        worker.configure({"network": True}, 500)
        worker.start()
        self.assertIsNotNone(worker._thread)
        self.assertTrue(worker._thread.is_alive())
        worker.configure({}, 500)
        worker.stop()
        self.assertIsNone(worker._thread)

    def test_metric_requirements_preserve_shared_dependencies(self) -> None:
        from glass_ui import GlassMonitorApp

        app = GlassMonitorApp.__new__(GlassMonitorApp)
        app.cards = {cid: False for cid in CARD_IDS}
        app.cfg = {"alerts": {"enabled": False}}

        self.assertFalse(any(app._metric_requirements().values()))

        app.cards["chart"] = True
        self.assertTrue(app._metric_requirements()["network"])
        app.cards["chart"] = False
        app.cards["speed"] = True
        self.assertTrue(app._metric_requirements()["network"])

        app.cards["speed"] = False
        app.cfg["alerts"] = {
            "enabled": True,
            "cpu_temp": 85,
            "gpu_temp": 0,
            "memory": 0,
            "disk": 0,
            "traffic": 0,
        }
        requirements = app._metric_requirements()
        self.assertTrue(requirements["cpu_temp"])
        self.assertFalse(requirements["basic"])
        self.assertFalse(requirements["nvml"])

        app.cfg["alerts"] = {"enabled": False}
        app.cards["sys"] = True
        requirements = app._metric_requirements()
        self.assertTrue(requirements["nvml"])
        self.assertTrue(requirements["battery"])
        self.assertFalse(requirements["gpu_stats"])

        app.cards["sys"] = False
        app.cards["traffic"] = False
        app.cfg["alerts"] = {"enabled": True, "traffic": 85}
        self.assertTrue(app._traffic_collection_enabled())

    def test_disabled_traffic_collector_has_no_thread_and_can_restart(self) -> None:
        collector = TrafficCollector(url="", enabled=False)
        collector.start()
        self.assertIsNone(collector._thread)

        collector.configure(enabled=True)
        collector.start()
        deadline = time.monotonic() + 1.0
        while collector.get().status == "pending" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(collector.get().status, "unconfigured")
        collector.configure(enabled=False)
        collector.stop()
        self.assertIsNone(collector._thread)

        collector.configure(enabled=True)
        collector.start()
        self.assertIsNotNone(collector._thread)
        collector.configure(enabled=False)
        collector.stop()
        self.assertIsNone(collector._thread)

    def test_hardware_rings_scale_without_overlap(self) -> None:
        from glass_ui import GlassMonitorApp

        diameters = []
        for width, margin in ((270, 10), (300, 12), (340, 16)):
            app = GlassMonitorApp.__new__(GlassMonitorApp)
            app.W = width
            app.H = 200
            app.content_margin = margin
            app.cards = {cid: cid == "hw" for cid in CARD_IDS}
            app.card_order = list(CARD_IDS)
            app._parts = []
            app._battery = None
            app._layout_cache = {}
            layout = app._layout()
            centers = layout["ring_centers"]
            diameter = layout["ring_d"]
            diameters.append(diameter)
            self.assertEqual(centers[1] * 2, centers[0] + centers[2])
            self.assertGreaterEqual(centers[1] - centers[0] - diameter, 9)
            self.assertLess(layout["ring_meta_y"], layout["hw"][1] + layout["hw"][3])
        self.assertEqual(diameters, sorted(diameters))
        self.assertGreater(diameters[-1], diameters[0])


if __name__ == "__main__":
    unittest.main()
