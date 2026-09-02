"""건국대학교 공학관 ↔ 일감호 영상용 정적 지도 데이터를 만든다.

이 파일은 촬영 당일 Jetson·STM32를 동시에 켤 수 없는 상황에서 쓰는 명시적 DEMO 전용이다.
공개 POI 외곽은 OpenStreetMap 객체를 오프라인 상수로 보관하고, 보행 경로는 기존
``map_engine.find_route``가 GraphML 위에서 계산한다. LLM은 좌표·방위·거리·경로를 만들지 않는다.

    cd OGTECH-frontend/MAP
    .venv/Scripts/python.exe kiosk/build_video_map_data.py
"""

from __future__ import annotations

import json
import sys
from math import cos, radians
from pathlib import Path

MAP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MAP_DIR))

from map_engine import OfflineMap  # noqa: E402

SOURCE = MAP_DIR / "sample_data" / "konkuk_walk.graphml"
OUTPUT = MAP_DIR / "kiosk" / "video_map.js"

CANVAS_ASPECT = 1024 / 420
CROP_MARGIN_M = 38.0

# 공개 POI 출처: OpenStreetMap contributors, ODbL 1.0.
# 일감호 relation 7885627, 공학관 way 369210727. 촬영 중 네트워크를 쓰지 않도록 외곽을 고정한다.
ILGAM_OUTER = [
    (127.0747808, 37.5399023), (127.0748020, 37.5397170),
    (127.0748870, 37.5396216), (127.0750356, 37.5395262),
    (127.0753188, 37.5394364), (127.0759487, 37.5393522),
    (127.0765929, 37.5393073), (127.0767203, 37.5393241),
    (127.0767698, 37.5393522), (127.0769963, 37.5401548),
    (127.0776001, 37.5409130), (127.0774345, 37.5410709),
    (127.0772722, 37.5411794), (127.0771818, 37.5411830),
    (127.0771317, 37.5412407), (127.0771519, 37.5413443),
    (127.0772085, 37.5414686), (127.0772872, 37.5415593),
    (127.0773618, 37.5417953), (127.0772866, 37.5418161),
    (127.0773998, 37.5420968), (127.0773644, 37.5421641),
    (127.0769680, 37.5422988), (127.0765716, 37.5423213),
    (127.0764159, 37.5422708), (127.0763027, 37.5422315),
    (127.0762319, 37.5421473), (127.0761682, 37.5420575),
    (127.0760903, 37.5419508), (127.0757222, 37.5410697),
    (127.0756373, 37.5409238), (127.0756161, 37.5408564),
    (127.0755523, 37.5407890), (127.0755523, 37.5407273),
    (127.0754249, 37.5406543), (127.0752621, 37.5405084),
    (127.0750781, 37.5403681), (127.0749011, 37.5401492),
    (127.0747808, 37.5399023),
]

ILGAM_ISLAND = [
    (127.0763593, 37.5400762), (127.0763593, 37.5399752),
    (127.0764018, 37.5399359), (127.0765150, 37.5399191),
    (127.0766141, 37.5399528), (127.0766920, 37.5400033),
    (127.0767344, 37.5400426), (127.0767486, 37.5400875),
    (127.0767344, 37.5401436), (127.0766637, 37.5401604),
    (127.0766212, 37.5401155), (127.0765150, 37.5401436),
    (127.0764371, 37.5401660), (127.0763664, 37.5401324),
    (127.0763593, 37.5400762),
]

ENGINEERING_BUILDING = [
    (127.0787826, 37.5421170), (127.0787696, 37.5420395),
    (127.0787264, 37.5417675), (127.0787070, 37.5416496),
    (127.0786918, 37.5415544), (127.0786405, 37.5412337),
    (127.0786273, 37.5411513), (127.0787294, 37.5411411),
    (127.0787346, 37.5411737), (127.0799888, 37.5410574),
    (127.0800030, 37.5411465), (127.0800449, 37.5411424),
    (127.0800506, 37.5411785), (127.0794436, 37.5412391),
    (127.0794238, 37.5412407), (127.0794208, 37.5412180),
    (127.0793803, 37.5412214), (127.0793869, 37.5412789),
    (127.0794569, 37.5417679), (127.0792893, 37.5417830),
    (127.0792194, 37.5412940), (127.0792135, 37.5412312),
    (127.0789642, 37.5412513), (127.0789662, 37.5412665),
    (127.0789424, 37.5412685), (127.0789448, 37.5412867),
    (127.0789017, 37.5412902), (127.0788419, 37.5412934),
    (127.0788752, 37.5415337), (127.0789553, 37.5415265),
    (127.0789715, 37.5416388), (127.0789868, 37.5417494),
    (127.0788929, 37.5417576), (127.0789165, 37.5419649),
    (127.0790042, 37.5419580), (127.0790748, 37.5419526),
    (127.0790700, 37.5419141), (127.0791567, 37.5419074),
    (127.0791657, 37.5419065), (127.0793006, 37.5418939),
    (127.0801611, 37.5418036), (127.0801745, 37.5418840),
    (127.0801293, 37.5418888), (127.0801505, 37.5420151),
    (127.0792038, 37.5421151), (127.0791165, 37.5421243),
    (127.0791049, 37.5420550), (127.0790198, 37.5420640),
    (127.0788728, 37.5420795), (127.0788774, 37.5421070),
    (127.0787826, 37.5421170),
]

ENGINEERING_CENTER = {"lat": 37.5415909, "lon": 127.0794009}
ILGAM_CENTER = {"lat": 37.5408227, "lon": 127.0765562}

# 첨부 이미지의 빨간 표시를 따른다. BASE CAMP는 공학관 쪽, 목적지는 일감호 북쪽 산책로다.
# 실제 경로 시작·끝은 안전하게 보행망에 스냅한 좌표를 사용한다.
BASECAMP_REQUEST = ENGINEERING_CENTER
DESTINATION_REQUEST = {"lat": 37.54215, "lon": 127.07736}


def round_pairs(pairs):
    return [[round(float(lon), 7), round(float(lat), 7)] for lon, lat in pairs]


def round_point(point):
    return {"lon": round(float(point[0]), 7), "lat": round(float(point[1]), 7)}


def main() -> int:
    if not SOURCE.exists():
        print(f"FAIL: 원본이 없습니다: {SOURCE}")
        return 1

    offline_map = OfflineMap.from_graphml(SOURCE)
    outbound = offline_map.find_route(
        start_lat=BASECAMP_REQUEST["lat"],
        start_lon=BASECAMP_REQUEST["lon"],
        goal_lat=DESTINATION_REQUEST["lat"],
        goal_lon=DESTINATION_REQUEST["lon"],
    )
    returning = offline_map.find_route(
        start_lat=DESTINATION_REQUEST["lat"],
        start_lon=DESTINATION_REQUEST["lon"],
        goal_lat=BASECAMP_REQUEST["lat"],
        goal_lon=BASECAMP_REQUEST["lon"],
    )

    basecamp = round_point(outbound.start_snapped)
    destination = round_point(outbound.goal_snapped)

    focus = [
        *ILGAM_OUTER,
        *ENGINEERING_BUILDING,
        *outbound.coordinates,
        *returning.coordinates,
    ]
    min_lon = min(point[0] for point in focus)
    max_lon = max(point[0] for point in focus)
    min_lat = min(point[1] for point in focus)
    max_lat = max(point[1] for point in focus)
    center_lon = (min_lon + max_lon) / 2
    center_lat = (min_lat + max_lat) / 2
    lat_scale = 111_320.0
    lon_scale = 111_320.0 * cos(radians(center_lat))
    half_lon_m = (max_lon - min_lon) * lon_scale / 2 + CROP_MARGIN_M
    half_lat_m = (max_lat - min_lat) * lat_scale / 2 + CROP_MARGIN_M
    if half_lon_m / half_lat_m < CANVAS_ASPECT:
        half_lon_m = half_lat_m * CANVAS_ASPECT
    else:
        half_lat_m = half_lon_m / CANVAS_ASPECT

    crop = {
        "west": center_lon - half_lon_m / lon_scale,
        "east": center_lon + half_lon_m / lon_scale,
        "south": center_lat - half_lat_m / lat_scale,
        "north": center_lat + half_lat_m / lat_scale,
    }

    def inside(lon: float, lat: float) -> bool:
        return crop["west"] <= lon <= crop["east"] and crop["south"] <= lat <= crop["north"]

    seen_pairs: set[tuple[str, str]] = set()
    trails: list[list[list[float]]] = []
    for u, v, _, data in offline_map.graph.edges(keys=True, data=True):
        pair = tuple(sorted((str(u), str(v))))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        coordinates = [
            (float(lon), float(lat))
            for lon, lat in offline_map._edge_coordinates(str(u), str(v), data)  # noqa: SLF001
        ]
        if any(inside(lon, lat) for lon, lat in coordinates):
            trails.append(round_pairs(coordinates))

    payload = {
        "name": "건국대학교 · 공학관 ↔ 일감호",
        "source": SOURCE.name,
        "attribution": "© OpenStreetMap contributors · ODbL 1.0",
        "bounds": {key: round(float(value), 7) for key, value in crop.items()},
        "trails": trails,
        "water": [
            {
                "name": "일감호",
                "osm": "relation/7885627",
                "center": ILGAM_CENTER,
                "outer": round_pairs(ILGAM_OUTER),
                "inner": [round_pairs(ILGAM_ISLAND)],
            }
        ],
        "buildings": [
            {
                "name": "공학관",
                "osm": "way/369210727",
                "center": ENGINEERING_CENTER,
                "polygon": round_pairs(ENGINEERING_BUILDING),
            }
        ],
        "basecamp": basecamp,
        "destination": destination,
        "routeOutbound": round_pairs(outbound.coordinates),
        "routeReturn": round_pairs(returning.coordinates),
        "computed": {
            "engine": "map_engine.find_route (A*)",
            "outboundMeters": round(outbound.distance_m, 1),
            "returnMeters": round(returning.distance_m, 1),
            "basecampSnapMeters": round(outbound.start_snap_m, 1),
            "destinationSnapMeters": round(outbound.goal_snap_m, 1),
        },
    }

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    OUTPUT.write_text(
        "/* 자동 생성 파일 — 직접 고치지 마세요.\n"
        " * 만든 명령: .venv/Scripts/python.exe kiosk/build_video_map_data.py\n"
        f" * 보행망: sample_data/{SOURCE.name}\n"
        " * POI 외곽: OpenStreetMap relation/7885627, way/369210727 (ODbL 1.0)\n"
        " * 경로·거리·방위는 코드가 계산하며 LLM이 만든 값이 아닙니다. */\n"
        f"window.KONKUK_VIDEO_MAP = {body};\n",
        encoding="utf-8",
    )

    print(f"source   : {SOURCE.name}")
    print(f"graph    : {offline_map.graph.number_of_nodes()} nodes / {offline_map.graph.number_of_edges()} edges")
    print(f"drawn    : {len(trails)} polylines")
    print(f"window   : {half_lon_m * 2:.0f} m x {half_lat_m * 2:.0f} m")
    print(f"outbound : {outbound.distance_m:.1f} m")
    print(f"return   : {returning.distance_m:.1f} m")
    print(f"output   : {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
