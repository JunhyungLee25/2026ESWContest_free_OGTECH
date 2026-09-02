"""오프라인 지도 엔진 회귀 테스트."""

from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

import networkx as nx

from map_engine import (
    MapValidationError,
    OfflineMap,
    SnapOutOfBounds,
    load_runtime,
)
from gps_service import GpsService


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_MAP = ROOT / "sample_data" / "konkuk_walk.graphml"
NMEA_REPLAY = ROOT / "sample_data" / "air530_replay.nmea"


class OfflineMapTest(unittest.TestCase):
    def test_utf8_demo_nmea_replay_produces_fix(self) -> None:
        service = GpsService(NMEA_REPLAY)
        try:
            service.configure(mode="replay")
            for _ in range(20):
                snapshot = service.snapshot()
                if snapshot["received_lines"] > 0:
                    break
                time.sleep(0.05)
            else:
                self.fail("NMEA 재생 문장을 받지 못했습니다")

            self.assertTrue(snapshot["demo"])
            self.assertTrue(snapshot["connected"])
            self.assertIsNone(snapshot["error"])
            self.assertTrue(snapshot["fix"])
            self.assertEqual(snapshot["satellites"], 10)
        finally:
            service.close()

    def test_team_sample_route_matches_verified_result(self) -> None:
        offline_map = OfflineMap.from_graphml(SAMPLE_MAP)
        result = offline_map.find_route(
            start_lat=37.5465126,
            start_lon=127.0757141,
            goal_lat=37.5405289551,
            goal_lon=127.0794396497,
        )
        self.assertAlmostEqual(result.distance_m, 913.08, places=1)
        self.assertEqual(len(result.nodes), 26)
        self.assertGreater(len(result.coordinates), len(result.nodes))

    def test_far_coordinate_is_not_silently_snapped(self) -> None:
        offline_map = OfflineMap.from_graphml(SAMPLE_MAP)
        with self.assertRaises(SnapOutOfBounds):
            offline_map.find_route(
                start_lat=35.1796,
                start_lon=129.0756,
                goal_lat=37.5405289551,
                goal_lon=127.0794396497,
            )

    def test_missing_edge_length_is_rejected(self) -> None:
        graph = nx.MultiDiGraph(crs="epsg:4326")
        graph.add_node("a", x=127.0, y=37.0)
        graph.add_node("b", x=127.001, y=37.0)
        graph.add_edge("a", "b")
        with self.assertRaises(MapValidationError):
            OfflineMap(graph, source_name="bad.graphml", source_type="graphml")

    def test_edge_geometry_is_preserved_in_route(self) -> None:
        graph = nx.MultiDiGraph(crs="epsg:4326")
        graph.add_node("a", x=127.0, y=37.0)
        graph.add_node("b", x=127.001, y=37.0)
        graph.add_edge(
            "a",
            "b",
            length=120.0,
            geometry="LINESTRING (127.0 37.0, 127.0005 37.0005, 127.001 37.0)",
        )
        offline_map = OfflineMap(
            graph, source_name="curve.graphml", source_type="graphml"
        )
        result = offline_map.find_route(
            start_lat=37.0,
            start_lon=127.0,
            goal_lat=37.0,
            goal_lon=127.001,
        )
        self.assertEqual(len(result.coordinates), 3)
        self.assertEqual(result.coordinates[1], (127.0005, 37.0005))

    def test_trail_offset_uses_edge_segment_not_only_nodes(self) -> None:
        graph = nx.MultiDiGraph(crs="epsg:4326")
        graph.add_node("a", x=127.0, y=37.0)
        graph.add_node("b", x=127.01, y=37.0)
        graph.add_edge("a", "b", length=890.0)
        offline_map = OfflineMap(
            graph, source_name="straight.graphml", source_type="graphml"
        )

        offset = offline_map.trail_offset_m(37.0001, 127.005)

        self.assertLess(offset, 12.0)
        self.assertGreater(offset, 10.0)

    def test_runtime_round_trip_keeps_route(self) -> None:
        offline_map = OfflineMap.from_graphml(SAMPLE_MAP)
        with tempfile.TemporaryDirectory() as directory:
            runtime_path = Path(directory) / "map.json"
            offline_map.write_runtime(runtime_path)
            restored = load_runtime(runtime_path)
            result = restored.find_route(
                start_lat=37.5465126,
                start_lon=127.0757141,
                goal_lat=37.5405289551,
                goal_lon=127.0794396497,
            )
        self.assertAlmostEqual(result.distance_m, 913.08, places=1)

    def test_osm_xml_is_converted_to_bidirectional_walk_graph(self) -> None:
        osm = """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6'>
  <node id='1' lat='37.0' lon='127.0'/>
  <node id='2' lat='37.0' lon='127.001'/>
  <node id='3' lat='37.001' lon='127.001'/>
  <way id='10'>
    <nd ref='1'/><nd ref='2'/><nd ref='3'/>
    <tag k='highway' v='path'/>
  </way>
</osm>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trail.osm"
            path.write_text(osm, encoding="utf-8")
            offline_map = OfflineMap.from_osm_xml(path)
            forward = offline_map.find_route(
                start_lat=37.0,
                start_lon=127.0,
                goal_lat=37.001,
                goal_lon=127.001,
            )
            reverse = offline_map.find_route(
                start_lat=37.001,
                start_lon=127.001,
                goal_lat=37.0,
                goal_lon=127.0,
            )
        self.assertEqual(len(forward.nodes), 3)
        self.assertAlmostEqual(forward.distance_m, reverse.distance_m, places=6)

    def test_render_sample_covers_distant_map_regions(self) -> None:
        graph = nx.MultiDiGraph(crs="epsg:4326")
        for index in range(20):
            left = f"left-{index}"
            right = f"right-{index}"
            lon = 127.0 + index * 0.0001
            graph.add_node(left, x=lon, y=37.0)
            graph.add_node(right, x=lon + 0.00005, y=37.00005)
            graph.add_edge(left, right, length=10.0)
        graph.add_node("far-left", x=128.0, y=37.5)
        graph.add_node("far-right", x=128.001, y=37.501)
        graph.add_edge("far-left", "far-right", length=150.0)

        offline_map = OfflineMap(
            graph, source_name="wide.graphml", source_type="graphml"
        )
        overview = offline_map.overview(render_limit=6)
        rendered_lons = [
            point[0]
            for edge in overview["edges"]
            for point in edge
        ]

        self.assertLess(min(rendered_lons), 127.1)
        self.assertGreater(max(rendered_lons), 127.9)


if __name__ == "__main__":
    unittest.main()
