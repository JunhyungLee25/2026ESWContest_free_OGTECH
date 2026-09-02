"""검증용 오프라인 보행 지도 변환·경로 계산 엔진."""

from __future__ import annotations

from dataclasses import dataclass
import json
from math import asin, ceil, cos, isfinite, radians, sin, sqrt
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable
import xml.etree.ElementTree as ET

import networkx as nx


EARTH_RADIUS_M = 6_371_009.0
DEFAULT_MAX_SNAP_M = 250.0
RUNTIME_SCHEMA_VERSION = 1
TRAIL_INDEX_CELL_DEG = 0.002

WALKABLE_HIGHWAYS = {
    "bridleway",
    "corridor",
    "footway",
    "living_street",
    "path",
    "pedestrian",
    "residential",
    "service",
    "steps",
    "track",
    "unclassified",
}
BLOCKED_ACCESS = {"no", "private"}
ALLOWED_FOOT_ACCESS = {"designated", "permissive", "yes"}


class MapValidationError(ValueError):
    """지도 파일이 런타임 계약을 만족하지 않을 때 발생한다."""


class RouteNotFound(RuntimeError):
    """두 스냅 노드 사이에 경로가 없을 때 발생한다."""


class SnapOutOfBounds(RuntimeError):
    """입력 좌표가 지도 보행망에서 지나치게 멀 때 발생한다."""


@dataclass(frozen=True)
class RouteResult:
    """지도 엔진이 계산한 경로 결과."""

    start_node: str
    goal_node: str
    nodes: tuple[str, ...]
    coordinates: tuple[tuple[float, float], ...]
    distance_m: float
    start_snap_m: float
    goal_snap_m: float
    start_snapped: tuple[float, float]
    goal_snapped: tuple[float, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "computed_by": "map_engine",
            "distance_m": round(self.distance_m, 2),
            "node_count": len(self.nodes),
            "coordinates": [list(point) for point in self.coordinates],
            "start": {
                "node_id": self.start_node,
                "snapped": list(self.start_snapped),
                "snap_distance_m": round(self.start_snap_m, 2),
            },
            "destination": {
                "node_id": self.goal_node,
                "snapped": list(self.goal_snapped),
                "snap_distance_m": round(self.goal_snap_m, 2),
            },
        }


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """두 WGS84 좌표의 대권 근사 거리를 미터로 반환한다."""

    lat1_r = radians(lat1)
    lat2_r = radians(lat2)
    delta_lat = lat2_r - lat1_r
    delta_lon = radians(lon2 - lon1)
    value = (
        sin(delta_lat / 2) ** 2
        + cos(lat1_r) * cos(lat2_r) * sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * asin(min(1.0, sqrt(value)))


def _coordinate(value: Any, label: str, lower: float, upper: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MapValidationError(f"{label} 값이 숫자가 아닙니다") from exc
    if not isfinite(number) or not lower <= number <= upper:
        raise MapValidationError(f"{label} 값이 유효 범위를 벗어났습니다")
    return number


def _edge_length(value: Any, edge_label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MapValidationError(f"{edge_label}의 length가 숫자가 아닙니다") from exc
    if not isfinite(number) or number <= 0:
        raise MapValidationError(f"{edge_label}의 length는 0보다 큰 유한값이어야 합니다")
    return number


_LINESTRING_RE = re.compile(
    r"^\s*LINESTRING(?:\s+Z)?\s*\((?P<body>.+)\)\s*$", re.IGNORECASE
)


def parse_linestring(value: Any) -> list[tuple[float, float]] | None:
    """OSMnx GraphML의 WKT LINESTRING을 외부 GIS 의존성 없이 읽는다."""

    if not isinstance(value, str):
        return None
    match = _LINESTRING_RE.match(value)
    if not match:
        return None
    coordinates: list[tuple[float, float]] = []
    for raw_point in match.group("body").split(","):
        parts = raw_point.strip().split()
        if len(parts) < 2:
            return None
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            return None
        if not isfinite(lon) or not isfinite(lat):
            return None
        coordinates.append((lon, lat))
    return coordinates if len(coordinates) >= 2 else None


def _safe_xml(path: Path) -> None:
    with path.open("rb") as stream:
        prefix = stream.read(4096).upper()
    if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
        raise MapValidationError("DTD 또는 외부 엔터티가 있는 XML은 받지 않습니다")


def _read_graphml(path: Path) -> nx.MultiDiGraph:
    """검증에 필요한 GraphML 부분집합을 표준 XML 파서로 읽는다.

    NetworkX 3.1의 GraphML 리더가 NumPy 2.x와 함께 설치된 개발 환경에서
    실패하는 조합을 피하고, Jetson과 개발 PC에서 같은 입력 계약을 사용한다.
    """

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise MapValidationError(f"GraphML XML 구문 오류: {exc}") from exc

    keys: dict[str, tuple[str, str, str | None]] = {}
    for element in root:
        if element.tag.rsplit("}", 1)[-1] != "key":
            continue
        key_id = element.attrib.get("id")
        name = element.attrib.get("attr.name")
        target = element.attrib.get("for", "all")
        if not key_id or not name:
            continue
        default_value = None
        for child in element:
            if child.tag.rsplit("}", 1)[-1] == "default":
                default_value = child.text or ""
        keys[key_id] = (target, name, default_value)

    graph_element = next(
        (
            element
            for element in root
            if element.tag.rsplit("}", 1)[-1] == "graph"
        ),
        None,
    )
    if graph_element is None:
        raise MapValidationError("GraphML에 graph 요소가 없습니다")
    directed = graph_element.attrib.get("edgedefault", "directed") == "directed"
    graph: nx.MultiGraph | nx.MultiDiGraph
    graph = nx.MultiDiGraph() if directed else nx.MultiGraph()

    defaults: dict[str, dict[str, str]] = {"graph": {}, "node": {}, "edge": {}}
    for target, name, default_value in keys.values():
        if default_value is None:
            continue
        targets = defaults.keys() if target == "all" else (target,)
        for item in targets:
            if item in defaults:
                defaults[item][name] = default_value

    def data_values(element: ET.Element, target: str) -> dict[str, str]:
        values = dict(defaults[target])
        for child in element:
            if child.tag.rsplit("}", 1)[-1] != "data":
                continue
            key = keys.get(child.attrib.get("key", ""))
            if key:
                values[key[1]] = child.text or ""
        return values

    graph.graph.update(data_values(graph_element, "graph"))
    fallback_edge_key = 0
    for element in graph_element:
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "node":
            node_id = element.attrib.get("id")
            if node_id is not None:
                graph.add_node(str(node_id), **data_values(element, "node"))
        elif tag == "edge":
            source = element.attrib.get("source")
            target = element.attrib.get("target")
            if source is None or target is None:
                raise MapValidationError("GraphML 엣지에 source 또는 target이 없습니다")
            raw_edge_key = element.attrib.get("id")
            edge_key = str(raw_edge_key) if raw_edge_key is not None else str(fallback_edge_key)
            fallback_edge_key += 1
            graph.add_edge(
                str(source),
                str(target),
                key=edge_key,
                **data_values(element, "edge"),
            )
    return nx.MultiDiGraph(graph if directed else graph.to_directed())


class OfflineMap:
    """검증을 통과한 WGS84 보행 그래프."""

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        *,
        source_name: str,
        source_type: str,
        warnings: Iterable[str] = (),
    ) -> None:
        if not isinstance(graph, nx.MultiDiGraph):
            graph = nx.MultiDiGraph(graph)
        self.graph = graph
        self.source_name = source_name
        self.source_type = source_type
        self.warnings = list(warnings)
        self._validate()

    @classmethod
    def from_graphml(cls, path: str | Path) -> "OfflineMap":
        source = Path(path)
        _safe_xml(source)
        try:
            graph = _read_graphml(source)
        except Exception as exc:
            if isinstance(exc, MapValidationError):
                raise
            raise MapValidationError(f"GraphML을 읽을 수 없습니다: {exc}") from exc
        warnings: list[str] = []
        crs = str(graph.graph.get("crs", "")).lower().replace(" ", "")
        if crs and "4326" not in crs and "wgs84" not in crs:
            raise MapValidationError(
                f"WGS84(EPSG:4326) 지도만 지원합니다. 입력 CRS: {crs}"
            )
        if not crs:
            warnings.append("CRS 메타데이터가 없어 WGS84로 간주했습니다")
        return cls(
            nx.MultiDiGraph(graph),
            source_name=source.name,
            source_type="graphml",
            warnings=warnings,
        )

    @classmethod
    def from_osm_xml(cls, path: str | Path) -> "OfflineMap":
        """원본 OSM XML에서 검증용 보행 그래프를 만든다.

        PBF와 OSM의 모든 접근 규칙을 다루는 생산용 변환기가 아니라, 팀의
        GraphML 변환 전후 결과를 대조하기 위한 작은 기준 구현이다.
        """

        source = Path(path)
        _safe_xml(source)
        nodes: dict[str, tuple[float, float]] = {}
        graph = nx.MultiDiGraph(crs="epsg:4326", source="osm_xml_validation")
        dropped_refs = 0
        accepted_ways = 0
        try:
            parser = ET.iterparse(source, events=("end",))
            for _, element in parser:
                tag = element.tag.rsplit("}", 1)[-1]
                if tag == "node":
                    node_id = element.attrib.get("id")
                    if node_id:
                        lon = _coordinate(element.attrib.get("lon"), "경도", -180, 180)
                        lat = _coordinate(element.attrib.get("lat"), "위도", -90, 90)
                        nodes[node_id] = (lon, lat)
                    element.clear()
                    continue
                if tag != "way":
                    continue

                refs: list[str] = []
                tags: dict[str, str] = {}
                for child in element:
                    child_tag = child.tag.rsplit("}", 1)[-1]
                    if child_tag == "nd" and child.attrib.get("ref"):
                        refs.append(child.attrib["ref"])
                    elif child_tag == "tag":
                        key = child.attrib.get("k")
                        value = child.attrib.get("v")
                        if key and value is not None:
                            tags[key] = value

                highway = tags.get("highway")
                foot = tags.get("foot", "").lower()
                access = tags.get("access", "").lower()
                walkable = highway in WALKABLE_HIGHWAYS
                blocked = foot in BLOCKED_ACCESS or (
                    access in BLOCKED_ACCESS and foot not in ALLOWED_FOOT_ACCESS
                )
                if walkable and not blocked and len(refs) >= 2:
                    accepted_ways += 1
                    oneway_foot = tags.get("oneway:foot", "").lower()
                    for left, right in zip(refs[:-1], refs[1:]):
                        if left not in nodes or right not in nodes:
                            dropped_refs += 1
                            continue
                        left_lon, left_lat = nodes[left]
                        right_lon, right_lat = nodes[right]
                        graph.add_node(left, x=left_lon, y=left_lat)
                        graph.add_node(right, x=right_lon, y=right_lat)
                        length = haversine_m(left_lon, left_lat, right_lon, right_lat)
                        attrs = {
                            "length": length,
                            "highway": highway,
                            "name": tags.get("name", ""),
                            "osmid": element.attrib.get("id", ""),
                        }
                        if oneway_foot == "-1":
                            graph.add_edge(right, left, **attrs)
                        elif oneway_foot in {"1", "true", "yes"}:
                            graph.add_edge(left, right, **attrs)
                        else:
                            graph.add_edge(left, right, **attrs)
                            graph.add_edge(right, left, **attrs)
                element.clear()
        except ET.ParseError as exc:
            raise MapValidationError(f"OSM XML 구문 오류: {exc}") from exc

        warnings = [
            "OSM XML 변환은 검증용 부분 구현입니다. 생산 지도는 OSMnx 변환 GraphML을 사용하세요"
        ]
        if dropped_refs:
            warnings.append(f"경계 밖 노드 참조 {dropped_refs}개를 제외했습니다")
        if accepted_ways == 0:
            raise MapValidationError("지원하는 보행 way가 OSM XML에 없습니다")
        return cls(
            graph,
            source_name=source.name,
            source_type="osm_xml",
            warnings=warnings,
        )

    @classmethod
    def from_runtime_dict(cls, payload: dict[str, Any]) -> "OfflineMap":
        if payload.get("schema_version") != RUNTIME_SCHEMA_VERSION:
            raise MapValidationError("지원하지 않는 런타임 지도 스키마입니다")
        graph = nx.MultiDiGraph(crs="epsg:4326")
        for item in payload.get("nodes", []):
            if not isinstance(item, list) or len(item) != 3:
                raise MapValidationError("런타임 노드 형식이 잘못되었습니다")
            graph.add_node(str(item[0]), x=item[1], y=item[2])
        for item in payload.get("edges", []):
            if not isinstance(item, dict):
                raise MapValidationError("런타임 엣지 형식이 잘못되었습니다")
            attrs: dict[str, Any] = {"length": item.get("length_m")}
            if item.get("geometry"):
                attrs["geometry"] = item["geometry"]
            graph.add_edge(
                str(item.get("u")),
                str(item.get("v")),
                key=str(item.get("key", "0")),
                **attrs,
            )
        return cls(
            graph,
            source_name=str(payload.get("source_name", "runtime.json")),
            source_type=str(payload.get("source_type", "runtime")),
            warnings=payload.get("warnings", []),
        )

    def _validate(self) -> None:
        if self.graph.number_of_nodes() == 0:
            raise MapValidationError("보행 그래프에 노드가 없습니다")
        if self.graph.number_of_edges() == 0:
            raise MapValidationError("보행 그래프에 엣지가 없습니다")

        self._nodes: list[tuple[str, dict[str, Any]]] = []
        for node_id, data in self.graph.nodes(data=True):
            lon = _coordinate(data.get("x"), f"노드 {node_id} 경도", -180, 180)
            lat = _coordinate(data.get("y"), f"노드 {node_id} 위도", -90, 90)
            data["x"] = lon
            data["y"] = lat
            self._nodes.append((str(node_id), data))

        for u, v, key, data in self.graph.edges(keys=True, data=True):
            label = f"엣지 {u}->{v}({key})"
            if "length" not in data:
                raise MapValidationError(f"{label}에 length가 없습니다")
            data["length"] = _edge_length(data["length"], label)

        lons = [data["x"] for _, data in self._nodes]
        lats = [data["y"] for _, data in self._nodes]
        self.bounds = {
            "west": min(lons),
            "south": min(lats),
            "east": max(lons),
            "north": max(lats),
        }
        self.weak_component_count = sum(
            1 for _ in nx.weakly_connected_components(self.graph)
        )
        self.strong_component_count = 0
        self._largest_strong_component: set[str] = set()
        for component in nx.strongly_connected_components(self.graph):
            self.strong_component_count += 1
            if len(component) > len(self._largest_strong_component):
                self._largest_strong_component = set(component)
        if self.weak_component_count > 1:
            self.warnings.append(
                f"서로 끊긴 보행망이 {self.weak_component_count}개입니다. 컴포넌트 간 경로는 만들 수 없습니다"
            )
        self._trail_segments: list[tuple[float, float, float, float]] = []
        self._trail_grid: dict[tuple[int, int], list[int]] = {}
        self._build_trail_index()

    @staticmethod
    def _trail_cell(lat: float, lon: float) -> tuple[int, int]:
        return (
            int((lon + 180.0) // TRAIL_INDEX_CELL_DEG),
            int((lat + 90.0) // TRAIL_INDEX_CELL_DEG),
        )

    def _build_trail_index(self) -> None:
        """1 Hz 이탈 판정을 위해 보행로 선분을 작은 격자에 한 번만 배치한다."""

        seen: set[tuple[str, str, str]] = set()
        for u, v, _, data in self.graph.edges(keys=True, data=True):
            coordinates = self._edge_coordinates(str(u), str(v), data)
            geometry_key = ";".join(f"{lon:.7f},{lat:.7f}" for lon, lat in coordinates)
            edge_key = (*sorted((str(u), str(v))), geometry_key)
            if edge_key in seen:
                continue
            seen.add(edge_key)
            for start, end in zip(coordinates[:-1], coordinates[1:]):
                segment_index = len(self._trail_segments)
                self._trail_segments.append((start[0], start[1], end[0], end[1]))
                west_cell, south_cell = self._trail_cell(
                    min(start[1], end[1]), min(start[0], end[0])
                )
                east_cell, north_cell = self._trail_cell(
                    max(start[1], end[1]), max(start[0], end[0])
                )
                for column in range(west_cell, east_cell + 1):
                    for row in range(south_cell, north_cell + 1):
                        self._trail_grid.setdefault((column, row), []).append(segment_index)

    @staticmethod
    def _point_segment_distance_m(
        lat: float,
        lon: float,
        segment: tuple[float, float, float, float],
    ) -> float:
        """짧은 보행로 구간을 국소 평면으로 투영해 점-선분 거리를 구한다."""

        lon_a, lat_a, lon_b, lat_b = segment
        latitude_scale = EARTH_RADIUS_M * radians(1.0)
        longitude_scale = latitude_scale * max(0.01, cos(radians(lat)))
        ax = (lon_a - lon) * longitude_scale
        ay = (lat_a - lat) * latitude_scale
        bx = (lon_b - lon) * longitude_scale
        by = (lat_b - lat) * latitude_scale
        dx = bx - ax
        dy = by - ay
        length_squared = dx * dx + dy * dy
        if length_squared <= 1e-12:
            return sqrt(ax * ax + ay * ay)
        projection = max(0.0, min(1.0, -(ax * dx + ay * dy) / length_squared))
        nearest_x = ax + projection * dx
        nearest_y = ay + projection * dy
        return sqrt(nearest_x * nearest_x + nearest_y * nearest_y)

    def trail_offset_m(self, lat: float, lon: float) -> float:
        """가장 가까운 보행로 선분까지의 거리를 반환한다.

        노드까지의 거리가 아니라 선분까지의 거리이므로 긴 직선 보행로 중앙에서도
        이탈 거리가 과장되지 않는다.
        """

        lat = _coordinate(lat, "현재 위도", -90, 90)
        lon = _coordinate(lon, "현재 경도", -180, 180)
        center_column, center_row = self._trail_cell(lat, lon)
        candidates: set[int] = set()
        for radius in range(1, 6):
            for column in range(center_column - radius, center_column + radius + 1):
                for row in range(center_row - radius, center_row + radius + 1):
                    candidates.update(self._trail_grid.get((column, row), ()))
            if candidates:
                break
        if not candidates:
            _, node_distance = self._nearest_node(lat, lon)
            return node_distance
        return min(
            self._point_segment_distance_m(lat, lon, self._trail_segments[index])
            for index in candidates
        )

    def _nearest_node(self, lat: float, lon: float) -> tuple[str, float]:
        node_id, data = min(
            self._nodes,
            key=lambda item: haversine_m(lon, lat, item[1]["x"], item[1]["y"]),
        )
        return node_id, haversine_m(lon, lat, data["x"], data["y"])

    def _heuristic(self, node: str, goal: str) -> float:
        current = self.graph.nodes[node]
        target = self.graph.nodes[goal]
        return haversine_m(current["x"], current["y"], target["x"], target["y"])

    def _edge_coordinates(self, u: str, v: str, data: dict[str, Any]) -> list[tuple[float, float]]:
        start = (self.graph.nodes[u]["x"], self.graph.nodes[u]["y"])
        end = (self.graph.nodes[v]["x"], self.graph.nodes[v]["y"])
        geometry = parse_linestring(data.get("geometry"))
        if not geometry:
            return [start, end]
        forward = haversine_m(*start, *geometry[0]) + haversine_m(*end, *geometry[-1])
        reverse = haversine_m(*start, *geometry[-1]) + haversine_m(*end, *geometry[0])
        return list(reversed(geometry)) if reverse < forward else geometry

    def find_route(
        self,
        *,
        start_lat: float,
        start_lon: float,
        goal_lat: float,
        goal_lon: float,
        max_snap_m: float = DEFAULT_MAX_SNAP_M,
    ) -> RouteResult:
        start_lat = _coordinate(start_lat, "현재 위도", -90, 90)
        start_lon = _coordinate(start_lon, "현재 경도", -180, 180)
        goal_lat = _coordinate(goal_lat, "목적지 위도", -90, 90)
        goal_lon = _coordinate(goal_lon, "목적지 경도", -180, 180)
        if not isfinite(max_snap_m) or max_snap_m <= 0:
            raise MapValidationError("최대 스냅 거리는 0보다 커야 합니다")

        start, start_snap_m = self._nearest_node(start_lat, start_lon)
        goal, goal_snap_m = self._nearest_node(goal_lat, goal_lon)
        if start_snap_m > max_snap_m:
            raise SnapOutOfBounds(
                f"현재 위치가 보행망에서 {start_snap_m:.1f}m 떨어져 있습니다"
            )
        if goal_snap_m > max_snap_m:
            raise SnapOutOfBounds(
                f"목적지가 보행망에서 {goal_snap_m:.1f}m 떨어져 있습니다"
            )

        try:
            node_path = nx.astar_path(
                self.graph,
                start,
                goal,
                heuristic=self._heuristic,
                weight="length",
            )
        except nx.NetworkXNoPath as exc:
            raise RouteNotFound("현재 위치와 목적지 사이에 연결된 보행로가 없습니다") from exc

        distance_m = 0.0
        route_coordinates: list[tuple[float, float]] = []
        for u, v in zip(node_path[:-1], node_path[1:]):
            _, edge_data = min(
                self.graph[u][v].items(), key=lambda item: item[1]["length"]
            )
            distance_m += edge_data["length"]
            segment = self._edge_coordinates(u, v, edge_data)
            if route_coordinates and route_coordinates[-1] == segment[0]:
                route_coordinates.extend(segment[1:])
            else:
                route_coordinates.extend(segment)
        if not route_coordinates:
            only = self.graph.nodes[start]
            route_coordinates.append((only["x"], only["y"]))

        start_data = self.graph.nodes[start]
        goal_data = self.graph.nodes[goal]
        return RouteResult(
            start_node=start,
            goal_node=goal,
            nodes=tuple(node_path),
            coordinates=tuple(route_coordinates),
            distance_m=distance_m,
            start_snap_m=start_snap_m,
            goal_snap_m=goal_snap_m,
            start_snapped=(start_data["x"], start_data["y"]),
            goal_snapped=(goal_data["x"], goal_data["y"]),
        )

    def suggested_points(self) -> dict[str, dict[str, float]]:
        candidates = [
            (node, self.graph.nodes[node])
            for node in self._largest_strong_component
        ]
        start_node, start_data = min(candidates, key=lambda item: (item[1]["y"], item[1]["x"]))
        goal_node, goal_data = max(
            candidates,
            key=lambda item: haversine_m(
                start_data["x"], start_data["y"], item[1]["x"], item[1]["y"]
            ),
        )
        return {
            "current": {"lat": start_data["y"], "lon": start_data["x"]},
            "destination": {"lat": goal_data["y"], "lon": goal_data["x"]},
        }

    def overview(self, render_limit: int = 12_000) -> dict[str, Any]:
        if render_limit <= 0:
            raise MapValidationError("화면 도로 상한은 0보다 커야 합니다")

        mid_lat = (self.bounds["south"] + self.bounds["north"]) / 2
        lon_span = max(
            1e-12,
            (self.bounds["east"] - self.bounds["west"])
            * max(0.15, cos(radians(mid_lat))),
        )
        lat_span = max(1e-12, self.bounds["north"] - self.bounds["south"])
        aspect = max(0.25, min(4.0, lon_span / lat_span))
        target_cells = max(1, render_limit // 2)
        grid_columns = max(1, ceil(sqrt(target_cells * aspect)))
        grid_rows = max(1, ceil(target_cells / grid_columns))
        per_cell = max(1, ceil(render_limit / (grid_columns * grid_rows)))

        buckets: dict[tuple[int, int], list[list[list[float]]]] = {}
        seen: set[tuple[str, str]] = set()
        for u, v, _, data in self.graph.edges(keys=True, data=True):
            pair = tuple(sorted((str(u), str(v))))
            if pair in seen:
                continue
            seen.add(pair)
            coordinates = [
                [lon, lat]
                for lon, lat in self._edge_coordinates(str(u), str(v), data)
            ]
            midpoint = coordinates[len(coordinates) // 2]
            column = min(
                grid_columns - 1,
                max(
                    0,
                    int(
                        (midpoint[0] - self.bounds["west"])
                        / max(1e-12, self.bounds["east"] - self.bounds["west"])
                        * grid_columns
                    ),
                ),
            )
            row = min(
                grid_rows - 1,
                max(
                    0,
                    int(
                        (midpoint[1] - self.bounds["south"])
                        / max(1e-12, self.bounds["north"] - self.bounds["south"])
                        * grid_rows
                    ),
                ),
            )
            bucket = buckets.setdefault((row, column), [])
            if len(bucket) < per_cell:
                bucket.append(coordinates)

        render_edges: list[list[list[float]]] = []
        ordered_buckets = [buckets[key] for key in sorted(buckets)]
        for slot in range(per_cell):
            for bucket in ordered_buckets:
                if slot < len(bucket):
                    render_edges.append(bucket[slot])
                    if len(render_edges) >= render_limit:
                        break
            if len(render_edges) >= render_limit:
                break
        truncated = len(seen) > len(render_edges)
        warnings = list(dict.fromkeys(self.warnings))
        if truncated:
            warnings.append(
                f"화면 성능을 위해 지도 전체에서 도로 {len(render_edges)}개를 공간 균등 표본으로 표시합니다"
            )
        return {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "bounds": self.bounds,
            "statistics": {
                "nodes": self.graph.number_of_nodes(),
                "edges": self.graph.number_of_edges(),
                "weak_components": self.weak_component_count,
                "strong_components": self.strong_component_count,
                "render_edges": len(render_edges),
            },
            "warnings": warnings,
            "suggested_points": self.suggested_points(),
            "edges": render_edges,
        }

    def to_runtime_dict(self) -> dict[str, Any]:
        nodes = [
            [str(node_id), data["x"], data["y"]]
            for node_id, data in self.graph.nodes(data=True)
        ]
        edges: list[dict[str, Any]] = []
        for u, v, key, data in self.graph.edges(keys=True, data=True):
            item: dict[str, Any] = {
                "u": str(u),
                "v": str(v),
                "key": str(key),
                "length_m": round(data["length"], 6),
            }
            if isinstance(data.get("geometry"), str):
                item["geometry"] = data["geometry"]
            edges.append(item)
        return {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "bounds": self.bounds,
            "warnings": list(dict.fromkeys(self.warnings)),
            "nodes": nodes,
            "edges": edges,
        }

    def write_runtime(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        compact = {"ensure_ascii": False, "separators": (",", ":")}
        header = {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "bounds": self.bounds,
            "warnings": list(dict.fromkeys(self.warnings)),
        }
        # 같은 디렉터리의 고유 임시 파일에 쓴 뒤 원자적으로 교체한다.
        # 고정 이름(active_map.json.tmp)은 import 2건이 겹치면 서로를 덮었다(WORKLOG #18).
        stream = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=str(target.parent),
            prefix=f"{target.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary = Path(stream.name)
        try:
            with stream:
                stream.write(json.dumps(header, **compact)[:-1])
                stream.write(',"nodes":[')
                first = True
                for node_id, data in self.graph.nodes(data=True):
                    if not first:
                        stream.write(",")
                    stream.write(json.dumps([str(node_id), data["x"], data["y"]], **compact))
                    first = False
                stream.write('],"edges":[')
                first = True
                for u, v, key, data in self.graph.edges(keys=True, data=True):
                    item: dict[str, Any] = {
                        "u": str(u),
                        "v": str(v),
                        "key": str(key),
                        "length_m": round(data["length"], 6),
                    }
                    if isinstance(data.get("geometry"), str):
                        item["geometry"] = data["geometry"]
                    if not first:
                        stream.write(",")
                    stream.write(json.dumps(item, **compact))
                    first = False
                stream.write("]}")
            temporary.replace(target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def load_map_source(path: str | Path) -> OfflineMap:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".graphml":
        return OfflineMap.from_graphml(source)
    if suffix in {".osm", ".xml"}:
        return OfflineMap.from_osm_xml(source)
    raise MapValidationError("지원 형식은 .graphml, .osm, .xml입니다")


def load_runtime(path: str | Path) -> OfflineMap:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MapValidationError(f"런타임 지도를 읽을 수 없습니다: {exc}") from exc
    if not isinstance(payload, dict):
        raise MapValidationError("런타임 지도 루트는 객체여야 합니다")
    return OfflineMap.from_runtime_dict(payload)
