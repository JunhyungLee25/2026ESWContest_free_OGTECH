/* OGTECH 1024x600 화면 — 제품(/product/)과 촬영(/video/)이 함께 쓴다.
 *
 * 두 화면의 디자인은 예외 없이 같아야 하므로 마크업(video.html)·CSS(video_styles.css)·
 * 그리기 코드를 한 벌만 두고, 이 파일이 경로로 데이터 소스만 가른다(LIVE_MODE).
 *
 *   /product/  좌표·경로·방위·거리·도착·일출몰을 /api/device 실측으로 채운다.
 *              하단 버튼은 /api/waypoints 로 실제 저장하고, /api/voice/events 를 구독한다.
 *              값이 없으면 없다고 적는다 — 마지막 좌표를 현재인 척하지 않는다.
 *   /video/    좌표·경로는 촬영 시나리오 고정값이고 장면 전환·자동 재생이 붙는다.
 *              `?live=1` 이면 온·습도·CO 만 실측, `?autoplay=1|loop` 는 자동 재생.
 *
 * 공개 POI와 보행망은 오프라인 파일이며, 경로·방위·거리·경로 이탈 거리는 아래 코드와
 * map_engine이 계산한다. LLM은 좌표나 숫자를 생성하지 않는다.
 */

"use strict";

const OFFICIAL_ZENITH_DEG = 90.833;
const SEOUL_TIME_ZONE = "Asia/Seoul";
// navigation_service.TRAIL_THRESHOLD_M 기본값(30 m)과 같은 기준으로 경로 이탈을 판정한다.
const ROUTE_DEVIATION_THRESHOLD_M = 30;
// D 키 시연용 이탈 거리. 임계값을 확실히 넘도록 1.5배로 둔다.
const ROUTE_DEVIATION_DEMO_OFFSET_M = 45;
// 촬영 시나리오 고정 환경값. 기온 색: 30°C 초과 적색, 20~30°C 황색, 20°C 이하 녹색.
const SCENARIO_ENVIRONMENT = Object.freeze({ temperatureC: 30.0, humidityPct: 55 });
const SCENARIO_CO = Object.freeze({ valid: true, ppm: 0, level: "normal", alarm: false, warmingUp: false });

// URL 파라미터. live=1 → 온·습도·CO를 /api/device 실값으로, autoplay=1|loop → 로드 즉시 자동 재생.
const PAGE_PARAMS = new URLSearchParams(window.location.search);
// 제품 화면(/product/)은 이 파일과 video.html·video_styles.css 를 그대로 쓰고 데이터만
// STM32 실측으로 바꾼다. 화면 코드를 한 벌만 두어야 두 화면 디자인이 어긋나지 않는다.
// 차이는 딱 두 가지다 — 좌표·경로가 실측인지, 촬영용 시나리오 기능이 붙는지.
const LIVE_MODE = window.location.pathname.startsWith("/product");
const LIVE_SENSORS = LIVE_MODE || PAGE_PARAMS.get("live") === "1";
const AUTOPLAY_MODE = LIVE_MODE
  ? null
  : ["1", "loop"].includes(PAGE_PARAMS.get("autoplay") || "")
    ? PAGE_PARAMS.get("autoplay")
    : null;
// 이 시간 동안 /api/device 갱신이 없으면 실값 대신 "—"를 보여 준다(꾸며낸 값 금지).
const LIVE_STALE_AFTER_MS = 10000;
const LIVE_POLL_INTERVAL_MS = 2000;
const AUTOPLAY_START_DELAY_MS = 800;
const AUTOPLAY_LOOP_PAUSE_MS = 5000;

const FALLBACK_MAP = {
  name: "건국대학교 · 공학관 ↔ 일감호",
  attribution: "© OpenStreetMap contributors · ODbL 1.0",
  bounds: { west: 127.0731, east: 127.0819, south: 37.53905, north: 37.54258 },
  trails: [
    [[127.0778118, 37.5409566], [127.0780455, 37.5410483], [127.0783704, 37.5413134]],
    [[127.0783704, 37.5413134], [127.0785116, 37.5423177], [127.0789165, 37.5422808]],
    [[127.0783704, 37.5413134], [127.0790730, 37.5415506]],
  ],
  water: [{
    name: "일감호",
    center: { lon: 127.0765562, lat: 37.5408227 },
    outer: [
      [127.0747808, 37.5399023], [127.0753188, 37.5394364],
      [127.0765929, 37.5393073], [127.0776001, 37.5409130],
      [127.0771519, 37.5413443], [127.0773998, 37.5420968],
      [127.0765716, 37.5423213], [127.0760903, 37.5419508],
      [127.0755523, 37.5407890], [127.0747808, 37.5399023],
    ],
    inner: [],
  }],
  buildings: [{
    name: "공학관",
    center: { lon: 127.0794009, lat: 37.5415909 },
    polygon: [
      [127.0786273, 37.5411513], [127.0799888, 37.5410574],
      [127.0801745, 37.5418840], [127.0791165, 37.5421243],
      [127.0787826, 37.5421170], [127.0786273, 37.5411513],
    ],
  }],
  basecamp: { lon: 127.0795165, lat: 37.5417937 },
  destination: { lon: 127.0774930, lat: 37.5424365 },
  routeOutbound: [
    [127.0795165, 37.5417937], [127.0795047, 37.5418378],
    [127.0791513, 37.5418885], [127.0791567, 37.5419074],
    [127.0792038, 37.5421151], [127.0792144, 37.5421609],
    [127.0789017, 37.5422025], [127.0789165, 37.5422808],
    [127.0785116, 37.5423177], [127.0778637, 37.5423923],
    [127.0775933, 37.5424271], [127.0774930, 37.5424365],
  ],
  routeReturn: [
    [127.0774930, 37.5424365], [127.0775933, 37.5424271],
    [127.0778637, 37.5423923], [127.0785116, 37.5423177],
    [127.0789165, 37.5422808], [127.0789017, 37.5422025],
    [127.0792144, 37.5421609], [127.0792038, 37.5421151],
    [127.0791567, 37.5419074], [127.0791513, 37.5418885],
    [127.0795047, 37.5418378], [127.0795165, 37.5417937],
  ],
};

const SCENES = {
  1: {
    title: "BASE CAMP 시작",
    current: "basecamp", target: null, route: null,
    routeValue: "BASE CAMP", routeSub: "공학관 뒤편",
    alert: null, arrival: null, toast: null,
  },
  2: {
    title: "음성 요청 → 일감호 설정",
    current: "basecamp", target: "destination", route: "routeOutbound",
    routeValue: "일감호", routeSub: "목적지",
    alert: null, arrival: null, toast: null,
  },
  3: {
    title: "일감호로 이동",
    current: "basecamp", target: "destination", route: "routeOutbound",
    routeValue: "이동 중", routeSub: "목적지",
    alert: null, arrival: null, toast: null,
  },
  4: {
    title: "일감호 도착",
    current: "destination", target: null, route: null,
    routeValue: "도착", routeSub: "일감호 북쪽 산책로",
    alert: null, arrival: "목적지에 도착하였습니다.", toast: null,
  },
  5: {
    title: "일조 잔여 경고",
    current: "destination", target: "basecamp", route: "routeReturn",
    routeValue: "복귀 필요", routeSub: "BASE CAMP 경로",
    alert: true,
    arrival: null, toast: null,
  },
  6: {
    title: "BASE CAMP 복귀",
    current: "destination", target: "basecamp", route: "routeReturn",
    routeValue: "복귀 중", routeSub: "BASE CAMP",
    alert: null, arrival: null, toast: null,
  },
  7: {
    title: "BASE CAMP 도착",
    current: "basecamp", target: null, route: null,
    routeValue: "도착", routeSub: "BASE CAMP",
    alert: null, arrival: "Base Camp에 도착하였습니다.", toast: null,
  },
};

const state = {
  map: window.KONKUK_VIDEO_MAP || FALLBACK_MAP,
  sceneKey: 1,
  scene: SCENES[1],
  night: false,
  checkpoint: null,
  // 베이스캠프는 눌러서 등록하기 전에는 지도에 없다. 화면을 켜자마자 등록된 척하지 않는다.
  basecampRegistered: false,
  destinationSelecting: false,
  daylightAlertSnapshot: null,
  environment: { ...SCENARIO_ENVIRONMENT },
  co: { ...SCENARIO_CO },
  live: { enabled: LIVE_SENSORS, connected: false, updatedAt: 0, updates: 0 },
  routeDeviationDemo: false,
};

// live 모드 전용. 시나리오 모드에서는 끝까지 비어 있다.
const live = {
  device: null,
  fix: null,           // {lon, lat} · fix 없으면 null
  target: null,        // 선택된 목적지/베이스캠프 좌표
  targetKind: null,    // "destination" | "basecamp"
  serverDestinationStamp: undefined,   // 서버 목적지 saved_at. 첫 스냅샷은 기준값으로만 쓴다
  route: null,         // [[lon, lat], ...]
  routeInfo: null,     // {bearing_deg, distance_m, eta_min}
  basecamp: null,
  sun: null,
  alertText: null,
  alertSpoken: false,  // CO 경보는 화면이 읽지 않는다(스피커는 Jetson 데몬이 낸다)
  arrivalText: null,
  selecting: false,
  lastVoiceSequence: 0,
};

function livePoint(waypoint) {
  if (!waypoint) return null;
  const lat = Number(waypoint.lat);
  const lon = Number(waypoint.lon);
  return Number.isFinite(lat) && Number.isFinite(lon) ? { lon, lat } : null;
}

const walk = {
  playing: false,
  meters: 0,
  speedMps: 1.4,
  lastFrame: 0,
  position: null,
  routeKey: null,
};

// 촬영용 전체 시퀀스의 장면 간 정지 시간이다. 이동 구간은 실제 경로 길이와
// speedMps로 끝날 때까지 재생하므로 여기에는 넣지 않는다.
const AUTO_DEMO_DELAYS_MS = Object.freeze({
  basecampRegistered: 3000,
  destinationFallback: 13000,
  arrivalFallback: 3600,
  warningFallback: 6200,
  returnRouteShown: 3000,
  basecampArrival: 3600,
  nightMode: 2800,
});

const autoDemo = {
  active: false,
  runId: 0,
  timers: new Set(),
  walkResolver: null,
};

const canvas = document.querySelector("#mapCanvas");
const context = canvas.getContext("2d");
let toastTimer = 0;
let walkStartTimer = 0;

const EARTH_RADIUS_M = 6371008.8;
const toRad = (degrees) => (degrees * Math.PI) / 180;
const toDeg = (radians) => (radians * 180) / Math.PI;

function distanceMeters(from, to) {
  const dLat = toRad(to.lat - from.lat);
  const dLon = toRad(to.lon - from.lon);
  const lat1 = toRad(from.lat);
  const lat2 = toRad(to.lat);
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
}

function bearingDegrees(from, to) {
  const lat1 = toRad(from.lat);
  const lat2 = toRad(to.lat);
  const dLon = toRad(to.lon - from.lon);
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) -
    Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}

function pathLengthMeters(coordinates) {
  let total = 0;
  for (let index = 1; index < coordinates.length; index += 1) {
    total += distanceMeters(
      { lon: coordinates[index - 1][0], lat: coordinates[index - 1][1] },
      { lon: coordinates[index][0], lat: coordinates[index][1] }
    );
  }
  return total;
}

function coordinateKey(point) {
  return `${Number(point[0]).toFixed(7)},${Number(point[1]).toFixed(7)}`;
}

function buildTrailGraph() {
  const graph = new Map();
  const ensureNode = (point) => {
    const key = coordinateKey(point);
    if (!graph.has(key)) graph.set(key, { point: [point[0], point[1]], edges: new Map() });
    return key;
  };
  const connect = (from, to) => {
    const fromKey = ensureNode(from);
    const toKey = ensureNode(to);
    const weight = distanceMeters(
      { lon: from[0], lat: from[1] },
      { lon: to[0], lat: to[1] }
    );
    graph.get(fromKey).edges.set(toKey, weight);
    graph.get(toKey).edges.set(fromKey, weight);
  };
  (state.map.trails || []).forEach((trail) => {
    for (let index = 1; index < trail.length; index += 1) {
      connect(trail[index - 1], trail[index]);
    }
  });
  return graph;
}

function nearestGraphKey(graph, point) {
  let nearestKey = null;
  let nearestDistance = Number.POSITIVE_INFINITY;
  graph.forEach((node, key) => {
    const distance = distanceMeters(point, { lon: node.point[0], lat: node.point[1] });
    if (distance < nearestDistance) {
      nearestDistance = distance;
      nearestKey = key;
    }
  });
  return nearestKey;
}

function routeOnTrails(from, requestedDestination) {
  const graph = buildTrailGraph();
  const startKey = nearestGraphKey(graph, from);
  const destinationKey = nearestGraphKey(graph, requestedDestination);
  if (!startKey || !destinationKey) return null;

  const distances = new Map([[startKey, 0]]);
  const previous = new Map();
  const queue = [[0, startKey]];
  while (queue.length > 0) {
    queue.sort((left, right) => right[0] - left[0]);
    const [distance, key] = queue.pop();
    if (distance !== distances.get(key)) continue;
    if (key === destinationKey) break;
    graph.get(key).edges.forEach((weight, neighborKey) => {
      const candidate = distance + weight;
      if (candidate < (distances.get(neighborKey) ?? Number.POSITIVE_INFINITY)) {
        distances.set(neighborKey, candidate);
        previous.set(neighborKey, key);
        queue.push([candidate, neighborKey]);
      }
    });
  }
  if (!distances.has(destinationKey)) return null;

  const keys = [];
  for (let key = destinationKey; key; key = previous.get(key)) {
    keys.push(key);
    if (key === startKey) break;
  }
  keys.reverse();
  const route = keys.map((key) => graph.get(key).point);
  const first = route[0];
  if (distanceMeters(from, { lon: first[0], lat: first[1] }) > 0.5) {
    route.unshift([from.lon, from.lat]);
  }
  const destinationNode = graph.get(destinationKey).point;
  return {
    destination: { lon: destinationNode[0], lat: destinationNode[1] },
    route,
  };
}

function pointAlong(coordinates, meters) {
  let remaining = meters;
  for (let index = 1; index < coordinates.length; index += 1) {
    const from = { lon: coordinates[index - 1][0], lat: coordinates[index - 1][1] };
    const to = { lon: coordinates[index][0], lat: coordinates[index][1] };
    const segment = distanceMeters(from, to);
    if (remaining <= segment) {
      const ratio = segment === 0 ? 0 : remaining / segment;
      return {
        lon: from.lon + (to.lon - from.lon) * ratio,
        lat: from.lat + (to.lat - from.lat) * ratio,
        done: false,
      };
    }
    remaining -= segment;
  }
  const last = coordinates[coordinates.length - 1];
  return { lon: last[0], lat: last[1], done: true };
}

// 점을 방위각 방향으로 meters만큼 옮긴 좌표(구면 정방향 계산).
function offsetPoint(point, bearingDeg, meters) {
  const angular = meters / EARTH_RADIUS_M;
  const bearing = toRad(bearingDeg);
  const lat1 = toRad(point.lat);
  const lon1 = toRad(point.lon);
  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(angular) + Math.cos(lat1) * Math.sin(angular) * Math.cos(bearing)
  );
  const lon2 = lon1 + Math.atan2(
    Math.sin(bearing) * Math.sin(angular) * Math.cos(lat1),
    Math.cos(angular) - Math.sin(lat1) * Math.sin(lat2)
  );
  return { lon: toDeg(lon2), lat: toDeg(lat2) };
}

// 점에서 선분까지의 최단 거리(m). 수백 m 범위라 국지 등장방형 투영으로 충분하다.
function distanceToSegmentMeters(point, from, to) {
  const metersPerDegLat = 111320;
  const metersPerDegLon = 111320 * Math.cos(toRad(point.lat));
  const ax = (from[0] - point.lon) * metersPerDegLon;
  const ay = (from[1] - point.lat) * metersPerDegLat;
  const bx = (to[0] - point.lon) * metersPerDegLon;
  const by = (to[1] - point.lat) * metersPerDegLat;
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSquared = dx * dx + dy * dy;
  const t = lengthSquared === 0
    ? 0
    : Math.min(1, Math.max(0, -(ax * dx + ay * dy) / lengthSquared));
  return Math.hypot(ax + dx * t, ay + dy * t);
}

function nearestRouteSegment(point, coordinates) {
  if (!Array.isArray(coordinates) || coordinates.length < 2) return null;
  let nearest = null;
  for (let index = 1; index < coordinates.length; index += 1) {
    const distance = distanceToSegmentMeters(point, coordinates[index - 1], coordinates[index]);
    if (!nearest || distance < nearest.distance) {
      nearest = { distance, from: coordinates[index - 1], to: coordinates[index] };
    }
  }
  return nearest;
}

function routeOffsetMeters(point, coordinates) {
  const nearest = nearestRouteSegment(point, coordinates);
  return nearest ? nearest.distance : null;
}

function activeRoute() {
  if (LIVE_MODE) return live.route;
  return state.scene.route ? state.map[state.scene.route] || null : null;
}

// 활성 경로가 없으면 판정하지 않는다(null). 있으면 이탈 여부와 거리를 돌려준다.
function routeDeviation(point) {
  const route = activeRoute();
  if (!route || !point) return null;
  const offsetM = routeOffsetMeters(point, route);
  if (offsetM === null) return null;
  return {
    offRoute: offsetM > ROUTE_DEVIATION_THRESHOLD_M,
    offsetM,
    thresholdM: ROUTE_DEVIATION_THRESHOLD_M,
  };
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function projection() {
  const bounds = state.map.bounds;
  const rect = canvas.getBoundingClientRect();
  const padding = 24;
  const midLat = (bounds.south + bounds.north) / 2;
  const lonFactor = Math.max(0.15, Math.cos(toRad(midLat)));
  const worldWidth = Math.max(1e-9, (bounds.east - bounds.west) * lonFactor);
  const worldHeight = Math.max(1e-9, bounds.north - bounds.south);
  const scale = Math.min(
    (rect.width - padding * 2) / worldWidth,
    (rect.height - padding * 2) / worldHeight
  );
  const offsetX = (rect.width - worldWidth * scale) / 2;
  const offsetY = (rect.height - worldHeight * scale) / 2;
  return {
    rect,
    scale,
    toScreen(lon, lat) {
      return [
        offsetX + (lon - bounds.west) * lonFactor * scale,
        offsetY + (bounds.north - lat) * scale,
      ];
    },
    fromScreen(x, y) {
      return {
        lon: bounds.west + (x - offsetX) / (lonFactor * scale),
        lat: bounds.north - (y - offsetY) / scale,
      };
    },
    metersToPixels(meters) {
      return (meters / 111320) * scale;
    },
  };
}

function strokePath(coordinates, projector) {
  coordinates.forEach((point, index) => {
    const [x, y] = projector.toScreen(point[0], point[1]);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
}

function drawGrid(width, height) {
  context.strokeStyle = cssVar("--map-grid");
  context.lineWidth = 1;
  context.beginPath();
  for (let x = 0; x < width; x += 48) {
    context.moveTo(x, 0);
    context.lineTo(x, height);
  }
  for (let y = 0; y < height; y += 48) {
    context.moveTo(0, y);
    context.lineTo(width, y);
  }
  context.stroke();
}

function fillPolygon(coordinates, projector, fill, stroke) {
  if (!Array.isArray(coordinates) || coordinates.length < 3) return;
  context.beginPath();
  strokePath(coordinates, projector);
  context.closePath();
  context.fillStyle = fill;
  context.fill();
  context.strokeStyle = stroke;
  context.lineWidth = 2;
  context.stroke();
}

function drawMapLabel(point, label, projector, color, yOffset) {
  const [x, y] = projector.toScreen(point.lon, point.lat);
  context.save();
  context.font = "850 24px 'Malgun Gothic', sans-serif";
  context.textAlign = "center";
  context.lineWidth = 5;
  context.strokeStyle = cssVar("--map-bg");
  context.strokeText(label, x, y + (yOffset || 0));
  context.fillStyle = color;
  context.fillText(label, x, y + (yOffset || 0));
  context.restore();
}

const MARKER_LABEL_FONT = "800 20px 'Malgun Gothic', sans-serif";
const MARKER_LABEL_LINE_PX = 24;
/* 같은 자리에 마커가 겹치면(현재 위치를 그대로 체크포인트·베이스캠프로 저장한 경우)
 * 글자가 서로 위에 찍혀 읽을 수 없다. 마커 모양은 그리는 순서대로 그대로 쌓고,
 * 글자만 모아 두었다가 아래 순서로 자리를 잡는다 — 현재 위치가 제 자리를 갖고
 * 겹치는 것만 한 줄씩 위로 올라간다. */
const MARKER_LABEL_ORDER = { "현재": 0, "목적지": 1, "체크포인트": 2, "BASE CAMP": 3 };
const markerLabels = [];

function drawMarker(point, label, color, projector, shape) {
  if (!point) return;
  const [x, y] = projector.toScreen(point.lon, point.lat);
  context.save();
  context.translate(x, y);
  context.fillStyle = cssVar("--map-bg");
  context.strokeStyle = color;
  context.lineWidth = 4;
  context.beginPath();
  if (shape === "square") {
    context.rect(-11, -11, 22, 22);
  } else if (shape === "triangle") {
    context.moveTo(0, -13);
    context.lineTo(12, 9);
    context.lineTo(-12, 9);
    context.closePath();
  } else {
    context.arc(0, 0, 12, 0, Math.PI * 2);
  }
  context.fill();
  context.stroke();
  context.restore();
  markerLabels.push({
    x, y, label, color,
    order: MARKER_LABEL_ORDER[label] !== undefined ? MARKER_LABEL_ORDER[label] : 9,
  });
}

function drawMarkerLabels() {
  const placed = [];
  context.save();
  context.font = MARKER_LABEL_FONT;
  context.textAlign = "center";
  markerLabels
    .slice()
    .sort((left, right) => left.order - right.order)
    .forEach((item) => {
      const width = context.measureText(item.label).width;
      let y = item.y - 22;
      for (let guard = 0; guard < 6; guard += 1) {
        const clash = placed.some((box) =>
          Math.abs(box.x - item.x) < (box.width + width) / 2 + 6
          && Math.abs(box.y - y) < 22);
        if (!clash) break;
        y -= MARKER_LABEL_LINE_PX;
      }
      placed.push({ x: item.x, y, width });
      context.lineWidth = 5;
      context.strokeStyle = cssVar("--map-bg");
      context.strokeText(item.label, item.x, y);
      context.fillStyle = item.color;
      context.fillText(item.label, item.x, y);
    });
  context.restore();
  markerLabels.length = 0;
}

function drawAccuracyRing(point, projector) {
  if (!point) return;
  const [x, y] = projector.toScreen(point.lon, point.lat);
  const radius = Math.max(14, projector.metersToPixels(4.2));
  context.save();
  context.globalAlpha = 0.16;
  context.fillStyle = cssVar("--amber");
  context.beginPath();
  context.arc(x, y, radius, 0, Math.PI * 2);
  context.fill();
  context.globalAlpha = 0.8;
  context.strokeStyle = cssVar("--amber");
  context.lineWidth = 2;
  context.stroke();
  context.restore();
}

function drawNorthArrow(projector) {
  const x = projector.rect.width - 42;
  const banners = (state.scene.alert ? 1 : 0)
    + (document.querySelector("#routeAlert").hidden ? 0 : 1);
  const y = 44 + 72 * banners;
  context.save();
  context.translate(x, y);
  context.fillStyle = cssVar("--muted");
  context.beginPath();
  context.moveTo(0, -18);
  context.lineTo(8, 12);
  context.lineTo(0, 5);
  context.lineTo(-8, 12);
  context.closePath();
  context.fill();
  context.font = "700 15px Consolas, monospace";
  context.textAlign = "center";
  context.fillText("N", 0, -24);
  context.restore();
}

function basePoint() {
  if (LIVE_MODE) return live.fix;
  return walk.position || state.map[state.scene.current];
}

// 화면에 그릴 목적지. 시나리오는 지도 상수, live 는 저장된 웨이포인트다.
function targetPoint() {
  if (LIVE_MODE) return live.target;
  return state.scene.target ? state.map[state.scene.target] : null;
}

// D 키 시연 중에는 현재 위치를 가장 가까운 경로 선분의 직각 방향으로 밀어낸다.
function currentPoint() {
  const base = basePoint();
  const route = activeRoute();
  if (!state.routeDeviationDemo || !route || !base) return base;
  const nearest = nearestRouteSegment(base, route);
  if (!nearest) return base;
  const along = bearingDegrees(
    { lon: nearest.from[0], lat: nearest.from[1] },
    { lon: nearest.to[0], lat: nearest.to[1] }
  );
  return offsetPoint(base, along + 90, ROUTE_DEVIATION_DEMO_OFFSET_M);
}

function draw() {
  const rect = canvas.getBoundingClientRect();
  const ratio = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = Math.max(1, Math.round(rect.width * ratio));
  canvas.height = Math.max(1, Math.round(rect.height * ratio));
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.fillStyle = cssVar("--map-bg");
  context.fillRect(0, 0, rect.width, rect.height);
  drawGrid(rect.width, rect.height);

  const projector = projection();
  (state.map.water || []).forEach((feature) => {
    fillPolygon(feature.outer, projector, cssVar("--video-water"), cssVar("--video-water-line"));
    (feature.inner || []).forEach((inner) => {
      fillPolygon(inner, projector, cssVar("--map-bg"), cssVar("--video-water-line"));
    });
  });
  (state.map.buildings || []).forEach((feature) => {
    fillPolygon(feature.polygon, projector, cssVar("--video-building"), cssVar("--video-building-line"));
  });

  context.strokeStyle = cssVar("--map-trail");
  context.lineWidth = 2.5;
  context.lineJoin = "round";
  context.beginPath();
  state.map.trails.forEach((trail) => strokePath(trail, projector));
  context.stroke();

  const drawnRoute = activeRoute();
  if (drawnRoute) {
    const route = drawnRoute;
    context.lineCap = "round";
    context.strokeStyle = cssVar("--map-bg");
    context.lineWidth = 11;
    context.beginPath();
    strokePath(route, projector);
    context.stroke();
    context.strokeStyle = cssVar("--cyan");
    context.lineWidth = 5;
    context.beginPath();
    strokePath(route, projector);
    context.stroke();
    context.lineCap = "butt";
  }

  (state.map.water || []).forEach((feature) => {
    drawMapLabel(feature.center, feature.name, projector, cssVar("--cyan"), 0);
  });
  (state.map.buildings || []).forEach((feature) => {
    drawMapLabel(feature.center, feature.name, projector, cssVar("--text"), 0);
  });

  drawNorthArrow(projector);
  markerLabels.length = 0;
  const basecamp = LIVE_MODE
    ? live.basecamp
    : (state.basecampRegistered ? state.map.basecamp : null);
  if (basecamp) drawMarker(basecamp, "BASE CAMP", cssVar("--amber"), projector, "triangle");

  const goal = targetPoint();
  if (LIVE_MODE) {
    if (goal && live.targetKind !== "basecamp") {
      drawMarker(goal, "목적지", cssVar("--cyan"), projector, "square");
    }
  } else if (goal && state.scene.target !== "basecamp") {
    drawMarker(goal, "목적지", cssVar("--cyan"), projector, "square");
  } else if (state.sceneKey >= 2) {
    drawMarker(state.map.destination, "목적지", cssVar("--cyan"), projector, "square");
  }

  if (state.checkpoint) {
    drawMarker(state.checkpoint, "체크포인트", cssVar("--cyan"), projector, "square");
  }
  const current = currentPoint();
  // fix 가 없으면 현재 위치 마커를 그리지 않는다. 마지막 좌표를 현재인 척하지 않는다.
  if (current) {
    drawAccuracyRing(current, projector);
    drawMarker(current, "현재", cssVar("--amber"), projector, "circle");
  }
  drawMarkerLabels();
  updateScaleBar(projector);
}

function updateScaleBar(projector) {
  const candidates = [25, 50, 100, 200, 400];
  let chosen = candidates[0];
  candidates.forEach((meters) => {
    if (projector.metersToPixels(meters) <= 170) chosen = meters;
  });
  document.querySelector("#scaleLabel").textContent = `${chosen} m`;
  document.querySelector("#scaleBar").style.width =
    `${Math.round(projector.metersToPixels(chosen))}px`;
}

function setGlance(id, stateName, value, sub) {
  const element = document.querySelector(id);
  element.dataset.state = stateName;
  element.querySelector("strong").textContent = value;
  const subElement = element.querySelector(".sub");
  if (subElement && sub !== undefined) subElement.textContent = sub;
}

function temperatureLevel(celsius) {
  const value = Number(celsius);
  if (!Number.isFinite(value)) return "none";
  if (value > 30) return "hot";
  if (value > 20) return "warm";
  return "cool";
}

function formatTemperature(celsius) {
  const fahrenheit = celsius * 9 / 5 + 32;
  return `${celsius.toFixed(1)}°C (${fahrenheit.toFixed(1)}°F)`;
}

function setEnvironmentGlance(environment) {
  const temperature = document.querySelector("#envTemperature");
  const humidity = document.querySelector("#envHumidity");
  temperature.dataset.level = temperatureLevel(environment.temperatureC);
  temperature.textContent = Number.isFinite(environment.temperatureC)
    ? formatTemperature(environment.temperatureC)
    : "—";
  humidity.textContent = Number.isFinite(environment.humidityPct)
    ? `${Math.round(environment.humidityPct)}% RH`
    : "— RH";
}

// CO 칸 색: 시나리오 값은 앰버(합성값), 실값은 normal 녹색 / warning 앰버 / alarm 적색 / 없음 회색.
function coGlanceState(co) {
  if (!state.live.enabled) return "caution";
  if (co.alarm || co.level === "alarm") return "warn";
  if (co.level === "warning") return "caution";
  if (co.valid) return "live";
  return "none";
}

function setCoGlance(co) {
  const text = co.valid && Number.isFinite(co.ppm)
    ? `${Math.round(co.ppm)} ppm`
    : co.warmingUp
      ? "예열 중"
      : "—";
  setGlance("#glanceCo", coGlanceState(co), text);
}

// /api/device 스냅샷에서 온·습도·CO만 가져온다. stale/invalid 값은 숫자로 만들지 않는다.
function applyLiveDevice(device) {
  if (!device || typeof device !== "object") return;
  const env = device.environment || {};
  const co = device.co || {};
  const envValid = env.valid === true && env.stale !== true;
  const coValid = co.valid === true && co.stale !== true && Number.isFinite(Number(co.ppm));
  state.environment = {
    temperatureC: envValid && Number.isFinite(Number(env.temp_c)) ? Number(env.temp_c) : NaN,
    humidityPct: envValid && Number.isFinite(Number(env.humidity_pct)) ? Number(env.humidity_pct) : NaN,
  };
  state.co = {
    valid: coValid,
    ppm: coValid ? Number(co.ppm) : NaN,
    level: typeof co.level === "string" ? co.level : "unknown",
    alarm: co.alarm === true,
    warmingUp: co.warming_up === true,
  };
  state.live.connected = true;
  state.live.updatedAt = performance.now();
  state.live.updates += 1;
  if (LIVE_MODE) applyLiveNavigation(device);
  else applyServerDestination(device);
  render();
}

/* 음성(오지야 데몬)이 지도 서버에 등록한 목적지를 촬영 화면에도 보여 준다.
 * live=1 화면은 좌표·경로가 시나리오 상수라 서버 웨이포인트를 그리지 않는다. 그래서 페이지가 뜬 뒤
 * 서버 목적지의 saved_at 이 바뀌면 2번 장면(음성 요청 → 일감호 설정)으로 넘겨 목적지와 경로를 그리고,
 * 목적지가 지워지면 1번 장면으로 돌아간다. 첫 스냅샷은 기준값으로만 써서 이미 저장돼 있던 목적지는
 * 그리지 않는다. 소리는 내지 않는다 — 음성 응답은 데몬이 스피커로 낸다. */
function applyServerDestination(device) {
  if (LIVE_MODE || !state.live.enabled) return;
  const destination = (device.waypoints || {}).destination;
  const stamp = destination && typeof destination.saved_at === "string" ? destination.saved_at : null;
  if (live.serverDestinationStamp === undefined) {
    live.serverDestinationStamp = stamp;
    return;
  }
  if (stamp === live.serverDestinationStamp) return;
  live.serverDestinationStamp = stamp;
  if (stamp) {
    setScene(2, { audio: false });
    showToast("목적지를 지정했습니다.", 2400, { silent: true });
  } else if (state.sceneKey === 2) {
    setScene(1, { audio: false });
  }
}

/* /api/device 의 좌표·경로·일조·도착 판정을 화면 상태로 옮긴다.
 * 값을 만들어 내지 않는다 — 서버가 주지 않으면 null 로 두고 화면이 "없음"을 보여 준다. */
function applyLiveNavigation(device) {
  live.device = device;

  const gps = device.gps || {};
  live.fix = gps.fix === true && Number.isFinite(Number(gps.lat)) && Number.isFinite(Number(gps.lon))
    ? { lon: Number(gps.lon), lat: Number(gps.lat) }
    : null;

  const navigation = device.navigation || {};
  const route = navigation.active_route || {};
  const usable = route.available === true
    && Array.isArray(route.coordinates)
    && route.coordinates.length > 1;
  live.route = usable ? route.coordinates : null;
  live.routeInfo = route.available === true ? route : null;

  const waypoints = device.waypoints || {};
  live.basecamp = livePoint(waypoints.basecamp);
  live.targetKind = navigation.selected_target || null;
  live.target = live.targetKind === "basecamp"
    ? live.basecamp
    : livePoint(waypoints.destination);

  const checkpoints = waypoints.checkpoints || [];
  state.checkpoint = checkpoints.length
    ? livePoint(checkpoints[checkpoints.length - 1])
    : null;

  live.sun = device.sun || null;

  /* 서버가 주는 필드는 alert.text 다(navigation_service._alert). 없는 message 필드를
   * 읽던 종전 코드는 CO 경보에도 일조 경고 문구를 띄우고 읽었다. */
  const alert = device.alert;
  live.alertText = alert
    ? (alert.text || daylightWarningText())
    : null;
  /* CO 경보음과 음성은 Jetson 데몬(Co-LLM device_monitor.py)이 스피커로 낸다.
   * 화면까지 읽으면 같은 경보를 두 번 말한다. 게다가 데몬의 OGTECH_SPK_DEVICE 가
   * pulse 가 아니라 plughw 직접 접근이면(config.py 기본값) 브라우저가 스피커를
   * 쥔 사이 aplay 가 실패한다. 글자는 그대로 두고 소리는 데몬에 맡긴다. */
  live.alertSpoken = Boolean(alert) && !String(alert.kind || "").startsWith("co_");

  const arrival = navigation.arrival || {};
  live.arrivalText = arrival.arrived === true
    ? `${(arrival.target && arrival.target.name) || "목적지"}에 도착하였습니다.`
    : null;

  const night = device.interface && device.interface.night;
  if (typeof night === "boolean" && night !== state.night) setNight(night, false);
}

function markLiveDisconnected() {
  if (!state.live.connected) return;
  state.live.connected = false;
  state.environment = { temperatureC: NaN, humidityPct: NaN };
  state.co = { valid: false, ppm: NaN, level: "unknown", alarm: false, warmingUp: false };
  if (LIVE_MODE) {
    // 끊긴 뒤에도 마지막 좌표를 현재 위치인 척 남겨 두지 않는다.
    live.fix = null;
    live.route = null;
    live.routeInfo = null;
    live.alertText = null;
    live.alertSpoken = false;
    live.arrivalText = null;
  }
  render();
}

// SSE(/api/device/events)를 주 경로로, 폴링을 stale 감시·복구 경로로 쓴다.
function connectLiveSensors() {
  if (!state.live.enabled) return;
  const poll = async () => {
    try {
      const response = await fetch("/api/device", { cache: "no-store" });
      if (!response.ok) throw new Error("장치 상태 요청 실패");
      applyLiveDevice(await response.json());
    } catch (error) {
      // 서버가 아직 안 떴거나 끊긴 상태. 다음 주기에 다시 시도한다.
    }
  };
  let eventSource = null;
  if ("EventSource" in window) {
    eventSource = new EventSource("/api/device/events");
    eventSource.onmessage = (event) => {
      try {
        applyLiveDevice(JSON.parse(event.data));
      } catch (error) {
        // 깨진 이벤트는 버리고 폴링 경로가 복구한다.
      }
    };
  }
  state.environment = { temperatureC: NaN, humidityPct: NaN };
  state.co = { valid: false, ppm: NaN, level: "unknown", alarm: false, warmingUp: false };
  render();
  poll();
  window.setInterval(() => {
    const stale = performance.now() - state.live.updatedAt > LIVE_STALE_AFTER_MS;
    if (stale) markLiveDisconnected();
    if (stale || !eventSource) poll();
  }, LIVE_POLL_INTERVAL_MS);
}

function setRouteAlert(current) {
  const deviation = current ? routeDeviation(current) : null;
  const routeAlert = document.querySelector("#routeAlert");
  if (deviation && deviation.offRoute) {
    document.querySelector("#routeAlertText").textContent =
      `경로 이탈 · ${Math.round(deviation.offsetM)} m · 현재 위치와 경로를 확인하세요`;
    routeAlert.hidden = false;
    // 거리는 계속 변하므로 음성은 거리를 빼고 한 번만 읽는다.
    announce("routeAlert", "경로를 벗어났습니다. 현재 위치와 경로를 확인하세요.");
  } else {
    routeAlert.hidden = true;
    announce("routeAlert", "");
  }
  document.querySelector("#mapPanel").classList.toggle("has-route-alert", !routeAlert.hidden);
}

const seoulClockFormatter = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

const etaTimeFormatter = new Intl.DateTimeFormat("ko-KR", {
  timeZone: SEOUL_TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function updateSeoulClock() {
  const parts = {};
  seoulClockFormatter.formatToParts(new Date()).forEach((part) => {
    if (part.type !== "literal") parts[part.type] = part.value;
  });
  document.querySelector("#locationClock").textContent =
    `${parts.year}.${parts.month}.${parts.day} ${parts.hour}:${parts.minute} KST`;
}

function normalizedDegrees(value) {
  return ((value % 360) + 360) % 360;
}

function dayOfYear(year, month, day) {
  return Math.floor(
    (Date.UTC(year, month - 1, day) - Date.UTC(year, 0, 0)) / 86400000
  );
}

// NOAA 공개 근사식과 MAP/solar_service.py의 절차를 동일하게 적용한다.
function solarEventUtcHour(dateParts, latitude, longitude, sunrise) {
  const dayNumber = dayOfYear(dateParts.year, dateParts.month, dateParts.day);
  const longitudeHour = longitude / 15;
  const approximate = dayNumber + ((sunrise ? 6 : 18) - longitudeHour) / 24;
  const meanAnomaly = 0.9856 * approximate - 3.289;
  const trueLongitude = normalizedDegrees(
    meanAnomaly
      + 1.916 * Math.sin(toRad(meanAnomaly))
      + 0.020 * Math.sin(toRad(2 * meanAnomaly))
      + 282.634
  );
  let rightAscension = normalizedDegrees(
    toDeg(Math.atan(0.91764 * Math.tan(toRad(trueLongitude))))
  );
  const longitudeQuadrant = Math.floor(trueLongitude / 90) * 90;
  const ascensionQuadrant = Math.floor(rightAscension / 90) * 90;
  rightAscension = (rightAscension + longitudeQuadrant - ascensionQuadrant) / 15;

  const sinDeclination = 0.39782 * Math.sin(toRad(trueLongitude));
  const cosDeclination = Math.cos(Math.asin(sinDeclination));
  const denominator = cosDeclination * Math.cos(toRad(latitude));
  if (Math.abs(denominator) < 1e-12) return null;
  const cosineHour = (
    Math.cos(toRad(OFFICIAL_ZENITH_DEG))
      - sinDeclination * Math.sin(toRad(latitude))
  ) / denominator;
  if (cosineHour > 1 || cosineHour < -1) return null;

  const hourAngle = (
    sunrise
      ? 360 - toDeg(Math.acos(cosineHour))
      : toDeg(Math.acos(cosineHour))
  ) / 15;
  const localMeanTime = hourAngle + rightAscension - 0.06571 * approximate - 6.622;
  return normalizedDegrees((localMeanTime - longitudeHour) * 15) / 15;
}

function seoulDateParts(date) {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: SEOUL_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const parts = {};
  formatter.formatToParts(date).forEach((part) => {
    if (part.type !== "literal") parts[part.type] = Number(part.value);
  });
  return parts;
}

function localDateKey(date) {
  const parts = seoulDateParts(date);
  return `${parts.year}-${String(parts.month).padStart(2, "0")}-${String(parts.day).padStart(2, "0")}`;
}

function solarEventDate(dateParts, latitude, longitude, sunrise) {
  const utcHour = solarEventUtcHour(dateParts, latitude, longitude, sunrise);
  if (utcHour === null) return null;
  const utcMidnight = Date.UTC(dateParts.year, dateParts.month - 1, dateParts.day);
  const targetKey = `${dateParts.year}-${String(dateParts.month).padStart(2, "0")}-${String(dateParts.day).padStart(2, "0")}`;
  const candidates = [-1, 0, 1].map((dayShift) =>
    new Date(utcMidnight + dayShift * 86400000 + Math.round(utcHour * 3600000))
  );
  return candidates.find((candidate) => localDateKey(candidate) === targetKey) || candidates[1];
}

function todayDaylight(now) {
  const currentTime = now || new Date();
  const dateParts = seoulDateParts(currentTime);
  const current = currentPoint();
  const sunset = solarEventDate(dateParts, current.lat, current.lon, false);
  const differenceMs = sunset ? sunset.getTime() - currentTime.getTime() : 0;
  const pastSunset = Boolean(sunset) && differenceMs < 0;
  const remainingMinutes = sunset
    ? Math.ceil(Math.abs(differenceMs) / 60000)
    : 0;
  return { sunset, remainingMinutes, pastSunset };
}

function daylightForDisplay() {
  if (LIVE_MODE) return liveDaylight();
  return state.daylightAlertSnapshot || todayDaylight();
}

/* live 모드의 일출몰은 서버 solar_service 가 실제 좌표로 계산한 값을 쓴다.
 * 클라이언트 천문 계산과 결과가 갈리지 않도록 화면은 서버 값만 읽는다. */
function liveDaylight() {
  const sun = live.sun;
  if (!sun || sun.computed !== true || !Number.isFinite(Number(sun.remaining_min))) {
    return { remainingMinutes: null, pastSunset: false, sunset: null };
  }
  const remaining = Number(sun.remaining_min);
  return {
    remainingMinutes: Math.abs(remaining),
    pastSunset: remaining < 0,
    sunset: sun.sunset ? new Date(sun.sunset) : null,
  };
}

/* 화면 위 경고 배너 문구. 시나리오는 장면이, live 는 서버 판정이 정한다. */
function currentAlertText() {
  if (LIVE_MODE) return live.alertText;
  return state.scene.alert ? daylightWarningText() : null;
}

function currentArrivalText() {
  if (LIVE_MODE) return live.arrivalText;
  return state.scene.arrival || null;
}

function daylightWarningText() {
  const { remainingMinutes, pastSunset } = daylightForDisplay();
  if (pastSunset) {
    return "일몰 시간이 지났습니다. 귀환 권고 시각과 베이스캠프 경로를 확인하세요.";
  }
  return `해 지기까지 ${remainingMinutes}분 남았습니다. 귀환 권고 시각과 베이스캠프 경로를 확인하세요.`;
}

// 계기판 표기와 음성 답변이 같은 숫자를 쓰도록 길이 표현을 한 곳에서 만든다.
function spokenDaylightRemaining(minutes) {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours > 0 && rest === 0) return `${hours}시간`;
  if (hours > 0) return `${hours}시간 ${rest}분`;
  return `${rest}분`;
}

function formatDaylightRemaining(minutes) {
  return `${spokenDaylightRemaining(minutes)} 남음`;
}

function formatDaylightStatus(daylight) {
  if (daylight.remainingMinutes === null) return "계산 대기";
  if (daylight.pastSunset) return `${daylight.remainingMinutes}분 초과`;
  return formatDaylightRemaining(daylight.remainingMinutes);
}

function formatCoordinate(value) {
  return Number(value).toFixed(6);
}

function setCurrentCoordinateGlance(current) {
  const glance = document.querySelector("#glanceCoordinate");
  const latitude = document.querySelector("#currentLatitude");
  const longitude = document.querySelector("#currentLongitude");
  if (!current) {
    // GPS 미수신을 추정 좌표로 덮지 않는다(안전 경계).
    glance.dataset.state = "none";
    latitude.textContent = "좌표 없음";
    longitude.textContent = "GPS 미수신";
    return;
  }
  glance.dataset.state = "caution";
  latitude.textContent = `${formatCoordinate(current.lat)} N`;
  longitude.textContent = `${formatCoordinate(current.lon)} E`;
}

function setDaylightGlance(scene) {
  const element = document.querySelector("#glanceSun");
  const value = document.querySelector("#daylightValue");
  const sub = document.querySelector("#daylightSub");
  const daylight = daylightForDisplay();
  element.dataset.state = currentAlertText() ? "warn" : "normal";
  value.classList.remove("sun-times");
  value.textContent = formatDaylightStatus(daylight);
  sub.hidden = false;
  sub.textContent = daylight.sunset
    ? `금일 일몰 ${etaTimeFormatter.format(daylight.sunset)}`
    : LIVE_MODE ? "GPS 위치 필요" : "금일 일몰 계산 불가";
}

function showToast(message, duration, options) {
  const toast = document.querySelector("#statusToast");
  window.clearTimeout(toastTimer);
  if (!message) {
    toast.hidden = true;
    return;
  }
  toast.textContent = message;
  toast.hidden = false;
  /* silent: 음성 명령의 결과처럼 다른 주체(음성 데몬)가 이미 읽어 주는 문구는 글자만 띄운다.
   * 화면까지 읽으면 같은 말이 두 번 들린다(2026-09-02 실기). */
  if (!options || !options.silent) speak(message);
  toastTimer = window.setTimeout(() => { toast.hidden = true; }, duration || 2600);
}

function clipDurationMs(kind, fallback) {
  const entry = FIXED_AUDIO[kind];
  const buffer = entry ? speech.clips.get(entry.file) : null;
  const duration = buffer ? buffer.duration : Number.NaN;
  return Number.isFinite(duration) && duration > 0
    ? Math.ceil((duration + 0.45) * 1000)
    : fallback;
}

function cancelAutoDemo() {
  autoDemo.runId += 1;
  autoDemo.active = false;
  autoDemo.timers.forEach((timer) => window.clearTimeout(timer));
  autoDemo.timers.clear();
  if (autoDemo.walkResolver) {
    const resolve = autoDemo.walkResolver;
    autoDemo.walkResolver = null;
    resolve(false);
  }
}

function waitForAutoDemo(runId, milliseconds) {
  return new Promise((resolve) => {
    const timer = window.setTimeout(() => {
      autoDemo.timers.delete(timer);
      resolve(autoDemo.active && autoDemo.runId === runId);
    }, milliseconds);
    autoDemo.timers.add(timer);
  });
}

function waitForAutoWalk(runId) {
  return new Promise((resolve) => {
    autoDemo.walkResolver = (reachedDestination) => {
      autoDemo.walkResolver = null;
      resolve(reachedDestination && autoDemo.active && autoDemo.runId === runId);
    };
  });
}

function completeAutoWalk(reachedDestination) {
  if (!autoDemo.walkResolver) return;
  const resolve = autoDemo.walkResolver;
  autoDemo.walkResolver = null;
  resolve(reachedDestination);
}

function render() {
  const scene = state.scene;
  const current = currentPoint();
  setDaylightGlance(scene);
  updateSeoulClock();
  setCurrentCoordinateGlance(current);
  setEnvironmentGlance(state.environment);
  setCoGlance(state.co);
  setRouteAlert(current);

  const alertText = currentAlertText();
  const alertBox = document.querySelector("#alert");
  if (alertText) {
    document.querySelector("#alertText").textContent = alertText;
    alertBox.hidden = false;
  } else {
    alertBox.hidden = true;
  }
  document.querySelector("#mapPanel").classList.toggle("has-alert", Boolean(alertText));
  if (LIVE_MODE) announce("alert", live.alertSpoken ? alertText : "");
  const arrivalCard = document.querySelector("#arrivalCard");
  const arrivalText = currentArrivalText();
  if (arrivalText) {
    document.querySelector("#arrivalText").textContent = arrivalText;
    arrivalCard.hidden = false;
  } else {
    arrivalCard.hidden = true;
  }
  if (LIVE_MODE) announce("arrival", arrivalText);

  const target = targetPoint();
  const readout = document.querySelector("#readout");
  if (LIVE_MODE) {
    renderLiveReadout(readout, current);
  } else if (!target) {
    readout.hidden = true;
  } else {
    readout.hidden = false;
    const targetLabel = scene.target === "basecamp" ? "BASE CAMP" : "목적지";
    document.querySelector("#readoutLabel").textContent = targetLabel;
    document.querySelector("#readoutBearing").textContent =
      `${String(Math.round(bearingDegrees(current, target))).padStart(3, "0")}°`;
    const route = state.map[scene.route];
    const total = pathLengthMeters(route);
    const remaining = walk.routeKey === scene.route
      ? Math.max(0, total - walk.meters)
      : total;
    const remainingSeconds = remaining / walk.speedMps;
    const remainingMinutes = Math.max(1, Math.ceil(remainingSeconds / 60));
    const arrivalTime = new Date(Date.now() + remainingSeconds * 1000);
    document.querySelector("#readoutDistance").textContent = `${Math.round(remaining)} m`;
    document.querySelector("#readoutEta").textContent =
      `예상 도착 ${etaTimeFormatter.format(arrivalTime)} KST`;
    document.querySelector("#readoutRemainingTime").textContent =
      `약 ${remainingMinutes}분 남음`;
  }

  document.querySelector("#mapAttribution").textContent = state.map.attribution;
  if (!LIVE_MODE) {
    document.querySelector("#directorKey").textContent = String(state.sceneKey);
    document.querySelector("#directorScene").textContent = scene.title;
  }
  draw();
}

/* live 판독 카드. 방위·거리·예상 도착은 전부 map_engine 이 계산한 값이고
 * 화면은 그대로 읽어 준다(LLM 이 만든 값이 아니다). */
function renderLiveReadout(readout, current) {
  const info = live.routeInfo;
  if (!info || !current) {
    readout.hidden = true;
    return;
  }
  readout.hidden = false;
  document.querySelector("#readoutLabel").textContent =
    live.targetKind === "basecamp" ? "BASE CAMP" : "목적지";
  document.querySelector("#readoutBearing").textContent =
    `${String(Math.round(Number(info.bearing_deg) || 0)).padStart(3, "0")}°`;
  const distance = Math.round(Number(info.distance_m) || 0);
  document.querySelector("#readoutDistance").textContent = `${distance} m`;
  const minutes = Number.isFinite(Number(info.eta_min))
    ? Math.max(1, Math.ceil(Number(info.eta_min)))
    : Math.max(1, Math.ceil(distance / walk.speedMps / 60));
  document.querySelector("#readoutEta").textContent =
    `예상 도착 ${etaTimeFormatter.format(new Date(Date.now() + minutes * 60000))} KST`;
  document.querySelector("#readoutRemainingTime").textContent = `약 ${minutes}분 남음`;
}

function walkFrame(timestamp) {
  if (!walk.playing || !walk.routeKey) return;
  const elapsed = walk.lastFrame ? (timestamp - walk.lastFrame) / 1000 : 0;
  walk.lastFrame = timestamp;
  walk.meters += elapsed * walk.speedMps;
  const next = pointAlong(state.map[walk.routeKey], walk.meters);
  walk.position = { lon: next.lon, lat: next.lat };
  if (next.done) {
    walk.playing = false;
    walk.lastFrame = 0;
    setScene(state.sceneKey === 3 ? 4 : 7);
    completeAutoWalk(true);
    return;
  }
  render();
  if (walk.playing) window.requestAnimationFrame(walkFrame);
}

function startWalk() {
  if (!state.scene.route) return;
  walk.playing = true;
  walk.routeKey = state.scene.route;
  walk.meters = 0;
  walk.lastFrame = 0;
  const first = state.map[walk.routeKey][0];
  walk.position = { lon: first[0], lat: first[1] };
  window.requestAnimationFrame(walkFrame);
}

function stopWalk(keepPosition) {
  walk.playing = false;
  walk.lastFrame = 0;
  walk.routeKey = null;
  walk.meters = 0;
  if (!keepPosition) walk.position = null;
}

/* 화면에 뜬 문구는 전부 같은 목소리로 읽어 준다.
 *
 * 브라우저 speechSynthesis 는 쓰지 않는다 — Jetson Firefox 에서는 espeak 남성
 * 기계음으로 떨어져 제품 음성(sherpa KSS 여성 0.9배속)과 목소리가 갈린다.
 * 서버 /api/tts 가 같은 파라미터로 합성해 주고, 같은 문장은 서버가 캐시한다.
 *
 * 재생은 Web Audio(AudioBufferSourceNode)로 하고 WAV 는 decodeWav 가 직접
 * 뜯는다. 젯슨(L4T aarch64)의 Firefox 는 미디어 디코더가 죽어 있어 <audio> 도
 * decodeAudioData 도 못 쓴다 — 앞은 MEDIA_ERR_DECODE, 뒤는 EncodingError 로
 * 떨어지고 소리는 나지 않는다. 오실레이터는 정상 재생되므로 고장난 것은 출력이
 * 아니라 디코더뿐이다(2026-08-31 젯슨 실측, 모니터 녹음 RMS 로 확인).
 *
 * 음성은 보조 수단이다. 합성이 안 되면 조용히 넘어가고 글자는 그대로 남는다. */
const FIXED_AUDIO = {
  destination: {
    file: "destination_set.wav",
    text: "가장 가까운 지점에 호수가 있습니다. 이곳을 목적지로 지정할까요? 네, 목적지로 설정되었습니다.",
  },
  arrival: {
    file: "destination_arrived.wav",
    text: "목적지에 도착하였습니다.",
  },
  basecamp: {
    file: "return_to_base.wav",
    text: "Base Camp에 도착하였습니다.",
  },
};

const speech = {
  ctx: null,
  source: null,
  endsAt: 0,
  lastText: "",
  lastAt: 0,
  unavailable: false,   // 모델이 없는 환경에서 매번 요청하지 않는다
  clips: new Map(),     // 고정 음성은 한 번만 받아서 디코딩해 둔다
  texts: [],            // 합성해 둔 문장. clips 와 함께 오래된 것부터 버린다
};

// 합성해 둘 문장 수. 값이 바뀌면 문장도 바뀌므로 무한정 쌓이지 않게 막는다.
const SPEECH_TEXT_CACHE_MAX = 24;

// 눌렀을 때 바로 나와야 하는 고정 문구. 화면이 뜨자마자 합성해 둔다.
const WARM_SPEECH_PHRASES = [
  "현재 위치를 체크포인트로 저장했습니다.",
  "베이스캠프가 등록되었습니다.",
  "베이스캠프 복귀 경로가 설정되었습니다.",
];

function audioContext() {
  const Ctor = window.AudioContext || window.webkitAudioContext;
  if (!Ctor) return null;
  if (!speech.ctx) speech.ctx = new Ctor();
  if (speech.ctx.state === "suspended") speech.ctx.resume().catch(() => {});
  return speech.ctx;
}

/* WAV 를 직접 뜯어 AudioBuffer 로 만든다.
 *
 * 젯슨(L4T aarch64) Firefox 는 미디어 디코더가 죽어 있어 <audio> 는
 * MEDIA_ERR_DECODE, decodeAudioData 는 EncodingError 로 떨어진다. 소리 출력
 * 자체는 멀쩡해서(오실레이터는 정상 재생) 디코딩만 우리가 하면 된다.
 * 화면이 쓰는 음성은 녹음도 합성도 전부 22.05 kHz 16 bit PCM WAV 다. */
function decodeWav(ctx, bytes) {
  const view = new DataView(bytes);
  const tag = (offset) => String.fromCharCode(
    view.getUint8(offset), view.getUint8(offset + 1),
    view.getUint8(offset + 2), view.getUint8(offset + 3),
  );
  if (view.byteLength < 44 || tag(0) !== "RIFF" || tag(8) !== "WAVE") {
    throw new Error("WAV 가 아니다");
  }

  let format = 0;
  let channels = 0;
  let rate = 0;
  let bits = 0;
  let dataStart = -1;
  let dataSize = 0;
  let offset = 12;
  while (offset + 8 <= view.byteLength) {
    const id = tag(offset);
    const size = view.getUint32(offset + 4, true);
    const body = offset + 8;
    if (id === "fmt ") {
      format = view.getUint16(body, true);
      channels = view.getUint16(body + 2, true);
      rate = view.getUint32(body + 4, true);
      bits = view.getUint16(body + 14, true);
    } else if (id === "data") {
      dataStart = body;
      dataSize = Math.min(size, view.byteLength - body);
      break;
    }
    offset = body + size + (size % 2);   // 청크는 짝수 바이트 경계에 놓인다
  }
  if (dataStart < 0 || !channels || !rate) throw new Error("WAV 헤더가 불완전하다");
  if (format !== 1 || bits !== 16) throw new Error(`지원하지 않는 WAV(${format}/${bits}bit)`);

  const frames = Math.floor(dataSize / (2 * channels));
  if (frames <= 0) throw new Error("WAV 에 소리가 없다");
  const buffer = ctx.createBuffer(channels, frames, rate);
  for (let channel = 0; channel < channels; channel += 1) {
    const target = buffer.getChannelData(channel);
    let cursor = dataStart + channel * 2;
    for (let frame = 0; frame < frames; frame += 1) {
      target[frame] = view.getInt16(cursor, true) / 32768;
      cursor += channels * 2;
    }
  }
  return buffer;
}

/* 앞서 읽던 문장을 끊고 새로 재생한다. 재생을 시작했으면 true. */
function playBuffer(buffer) {
  const ctx = audioContext();
  if (!ctx || !buffer) return false;
  if (speech.source) {
    try {
      speech.source.stop();
    } catch (error) {
      /* 이미 끝난 소리는 멈출 것이 없다 */
    }
  }
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);
  source.onended = () => {
    if (speech.source !== source) return;
    speech.source = null;
    speech.endsAt = 0;
  };
  source.start();
  speech.source = source;
  speech.endsAt = Date.now() + buffer.duration * 1000;
  return true;
}

/* 녹음 파일을 받아 디코딩해 두고 재생한다. 두 번째부터는 받아 둔 것을 쓴다. */
async function playClip(file) {
  const cached = speech.clips.get(file);
  if (cached) return playBuffer(cached);
  const ctx = audioContext();
  if (!ctx) return false;
  try {
    const response = await fetch(file);
    if (!response.ok) return false;
    const buffer = decodeWav(ctx, await response.arrayBuffer());
    speech.clips.set(file, buffer);
    return playBuffer(buffer);
  } catch (error) {
    return false;   // 녹음이 없어도 글자는 그대로 보인다
  }
}

/* 장면이 바뀔 때 받아오느라 늦지 않게 미리 디코딩해 둔다. */
function warmFixedAudio() {
  if (!audioContext()) return;
  WARM_SPEECH_PHRASES.forEach((phrase) => prefetchSpeech(phrase));
  Object.values(FIXED_AUDIO).forEach(async (entry) => {
    if (speech.clips.has(entry.file)) return;
    const ctx = audioContext();
    try {
      const response = await fetch(entry.file);
      if (!response.ok) return;
      speech.clips.set(entry.file, decodeWav(ctx, await response.arrayBuffer()));
    } catch (error) {
      /* 못 받으면 재생하는 순간에 다시 받는다 */
    }
  });
}

/* 합성 음성이 왜 안 나오는지 촬영 보조 패널에 적는다. 정적 파일 서버(Live Server 등)로
 * 띄우면 /api/tts 가 없어 글자만 뜨고 소리는 없다 — 녹음 WAV 만 들리니 원인을 알기 어렵다. */
function noteVoiceStatus(text) {
  const line = document.querySelector("#directorVoice");
  if (line && line.textContent !== text) line.textContent = text;
}

/* 문장을 미리 합성해 둔다. 젯슨 합성은 0.6~1.6 s 라 물어본 뒤에 만들기 시작하면
 * 답이 한 박자 늦는다. 화면에 뜬 값이 바뀔 때마다 답변 문장을 미리 받아 두면
 * 실제로 물어본 순간에는 받아 둔 것을 그대로 재생한다. */
async function prefetchSpeech(text) {
  const cleaned = String(text || "").trim();
  if (!cleaned || speech.unavailable) return null;
  const cached = speech.clips.get(cleaned);
  if (cached) return cached;
  const ctx = audioContext();
  if (!ctx) return null;
  try {
    // 모델이 없는 개발 PC 에서 콘솔에 리소스 오류를 남기지 않도록 fetch 로 받는다.
    const response = await fetch(`/api/tts?text=${encodeURIComponent(cleaned)}`);
    if (!response.ok) {
      noteVoiceStatus(`합성 음성 없음: /api/tts ${response.status} — python3 app.py 로 띄운 화면에서만 합성 음성이 나온다`);
      return null;
    }
    // 음성이 없는 장치는 JSON 으로 그 사실을 알려 준다. 그 뒤로는 요청하지 않는다.
    if (!String(response.headers.get("Content-Type") || "").startsWith("audio/")) {
      speech.unavailable = true;
      noteVoiceStatus("합성 음성 없음: 서버에 sherpa-onnx 모델이 없다(녹음 WAV 만 재생)");
      return null;
    }
    const buffer = decodeWav(ctx, await response.arrayBuffer());
    noteVoiceStatus("합성 음성 사용 가능 (/api/tts)");
    speech.clips.set(cleaned, buffer);
    speech.texts.push(cleaned);
    while (speech.texts.length > SPEECH_TEXT_CACHE_MAX) {
      speech.clips.delete(speech.texts.shift());
    }
    return buffer;
  } catch (error) {
    return null;   // 음성이 없어도 글자는 그대로 보인다
  }
}

async function speak(text) {
  const cleaned = String(text || "").trim();
  if (!cleaned || speech.unavailable) return;
  // 같은 문장이 연달아 렌더될 때 겹쳐 읽지 않는다.
  const now = Date.now();
  if (cleaned === speech.lastText && now - speech.lastAt < 6000) return;
  speech.lastText = cleaned;
  speech.lastAt = now;

  const ready = speech.clips.get(cleaned);
  if (ready) {
    playBuffer(ready);
    return;
  }
  const buffer = await prefetchSpeech(cleaned);
  if (buffer) playBuffer(buffer);
}

/* 배너·카드처럼 render 마다 다시 그려지는 것은 문구가 바뀐 순간에만 읽는다.
 * 촬영 화면의 목적지·도착·복귀는 미리 녹음한 WAV 가 따로 있으므로 겹치지 않게
 * 제품 화면에서만 읽는다. 경로 이탈 경고는 녹음이 없어 두 화면 모두 읽는다. */
const announced = { alert: "", arrival: "", routeAlert: "" };

function announce(key, text) {
  const value = String(text || "");
  if (announced[key] === value) return;
  announced[key] = value;
  if (value) speak(value);
}

function playDaylightAudio() {
  speak(daylightWarningText());
}

/* 마이크로 물어본 것에 화면이 가진 값으로 답하는 문장들.
 *
 * 숫자는 계기판에 떠 있는 값을 그대로 읽는다 — 답변용으로 따로 만들어 내지 않고,
 * 값이 아직 없으면 없다고 말한다. 문장은 값만 짧게 말한다("현재 온도는 N도,
 * 습도는 N퍼센트입니다."). 꾸미는 말은 붙이지 않는다(2026-09-02 사용자 지시). */
function spokenDecimal(value, digits) {
  return Number(value).toFixed(digits).replace(/\.0+$/, "");
}

function environmentAnswerText() {
  const { temperatureC, humidityPct } = state.environment;
  if (!Number.isFinite(temperatureC) || !Number.isFinite(humidityPct)) {
    return "온도와 습도 값이 아직 없습니다.";
  }
  return `현재 온도는 ${spokenDecimal(temperatureC, 1)}도, `
    + `습도는 ${Math.round(humidityPct)}퍼센트입니다.`;
}

function coAnswerText() {
  const co = state.co;
  if (!co.valid || !Number.isFinite(co.ppm)) {
    return co.warmingUp
      ? "일산화탄소 센서는 예열 중입니다."
      : "일산화탄소 값이 아직 없습니다.";
  }
  // 정상이면 값만 말한다. 주의·경보일 때만 그 사실을 한 마디 덧붙인다.
  const level = co.alarm || co.level === "alarm"
    ? " 경보 수준입니다."
    : co.level === "warning"
      ? " 주의 수준입니다."
      : "";
  return `현재 일산화탄소는 ${Math.round(co.ppm)}피피엠입니다.${level}`;
}

function daylightAnswerText() {
  const daylight = daylightForDisplay();
  const minutes = Number(daylight.remainingMinutes);
  if (!daylight.sunset || !Number.isFinite(minutes)) {
    return "일몰 시간을 아직 계산하지 못했습니다.";
  }
  return daylight.pastSunset
    ? `일몰 후 ${spokenDaylightRemaining(minutes)} 지났습니다.`
    : `일몰까지 ${spokenDaylightRemaining(minutes)} 남았습니다.`;
}

const VOICE_ANSWERS = {
  environment: environmentAnswerText,
  co: coAnswerText,
  daylight: daylightAnswerText,
};

/* 값이 바뀌면 답변 문장도 바뀐다. 바뀔 때마다 미리 합성해 두어 물어본 순간에
 * 기다리지 않게 한다. 이미 합성해 둔 문장이면 prefetchSpeech 가 바로 돌아온다. */
function warmVoiceAnswers() {
  if (LIVE_MODE || speech.unavailable) return;
  Object.values(VOICE_ANSWERS).forEach((build) => prefetchSpeech(build()));
}

/* 물어본 것에 답한다. 같은 답을 화면에 띄우고 같은 목소리로 읽는다. */
function answerAloud(kind) {
  const build = VOICE_ANSWERS[kind];
  if (!build) return;
  const text = build();
  const buffer = speech.clips.get(text);
  // 답이 끝나기 전에 글자가 사라지지 않게 재생 길이에 맞춘다.
  const duration = buffer
    ? Math.ceil((buffer.duration + 1.2) * 1000)
    : Math.max(3200, text.length * 150);
  speech.lastText = "";   // 같은 것을 다시 물어보면 다시 읽는다
  showToast(text, duration);
}

/* 지금 읽고 있는 문장의 남은 길이(ms). 자동 시연이 문장 중간에 다음 장면으로
 * 넘어가 음성이 잘리지 않게 쓴다. 값이 이상하면 시연이 멈추지 않도록 상한을 둔다. */
function speechRemainingMs() {
  if (!speech.source) return 0;
  const remaining = speech.endsAt - Date.now();
  if (!Number.isFinite(remaining) || remaining <= 0) return 0;
  return Math.min(8000, Math.round(remaining));
}

function playFixedAudio(kind) {
  if (kind === "warning" || kind === "daylightDetail") {
    playDaylightAudio();
    return;
  }
  const selected = FIXED_AUDIO[kind] || FIXED_AUDIO.destination;
  playClip(selected.file).then((played) => {
    if (!played) speak(selected.text);   // 녹음을 못 받으면 합성으로 읽는다
  });
}

function setScene(key, options) {
  const sceneKey = Number(key);
  if (!SCENES[sceneKey]) return;
  window.clearTimeout(walkStartTimer);
  stopWalk(false);
  setDestinationSelection(false);
  state.routeDeviationDemo = false;
  state.sceneKey = sceneKey;
  state.scene = SCENES[sceneKey];
  // 5~7 은 베이스캠프로 돌아가는 장면이다. 등록되지 않은 지점으로 경로를 그리지 않는다.
  if (sceneKey >= 5) state.basecampRegistered = true;
  state.daylightAlertSnapshot = sceneKey === 5 ? todayDaylight() : null;
  showToast(state.scene.toast, sceneKey === 4 || sceneKey === 7 ? 4000 : 2600);
  render();

  const withAudio = !options || options.audio !== false;
  if (sceneKey === 2 && withAudio) playFixedAudio("destination");
  if (sceneKey === 4 && withAudio) playFixedAudio("arrival");
  if (sceneKey === 5 && withAudio) playFixedAudio("warning");
  if (sceneKey === 7 && withAudio) playFixedAudio("basecamp");
  const autoWalk = !options || options.autoWalk !== false;
  if ((sceneKey === 3 || sceneKey === 6) && autoWalk) {
    walkStartTimer = window.setTimeout(startWalk, 450);
  }
}

function nextScene() {
  setScene(state.sceneKey >= 7 ? 1 : state.sceneKey + 1);
}

function setNight(on, announce) {
  state.night = on;
  document.documentElement.dataset.night = on ? "on" : "off";
  document.querySelector("#btnNight").setAttribute("aria-pressed", String(on));
  if (announce) {
    showToast(on ? "야간 모드가 활성화되었습니다." : "야간 모드가 해제되었습니다.", 2800);
  }
  render();
}

function setDestinationSelection(on) {
  state.destinationSelecting = on;
  document.querySelector("#btnDestination").setAttribute("aria-pressed", String(on));
  canvas.classList.toggle("destination-selecting", on);
}

async function selectLiveDestination(event) {
  if (!live.selecting) return;
  const rect = canvas.getBoundingClientRect();
  const point = projection().fromScreen(event.clientX - rect.left, event.clientY - rect.top);
  try {
    await postWaypoint({
      action: "set", kind: "destination", lat: point.lat, lon: point.lon,
    });
    showToast("목적지를 지정했습니다.", 2400);
  } catch (error) {
    showToast(error.message, 2600);
  }
  live.selecting = false;
  setDestinationSelection(false);
  render();
}

function selectMapDestination(event) {
  if (LIVE_MODE) {
    selectLiveDestination(event);
    return;
  }
  cancelAutoDemo();
  if (!state.destinationSelecting) return;
  state.routeDeviationDemo = false;
  const rect = canvas.getBoundingClientRect();
  const requested = projection().fromScreen(event.clientX - rect.left, event.clientY - rect.top);
  const from = currentPoint();
  const result = routeOnTrails(from, requested);
  setDestinationSelection(false);
  if (!result) return;
  state.map.manualStart = { lon: from.lon, lat: from.lat };
  state.map.manualDestination = result.destination;
  state.map.routeToManualDestination = result.route;
  state.sceneKey = 2;
  state.scene = {
    ...SCENES[2],
    title: "터치 목적지 설정",
    current: "manualStart",
    target: "manualDestination",
    route: "routeToManualDestination",
    routeValue: "목적지",
    routeSub: "터치 지정",
    arrival: null,
    toast: null,
  };
  state.daylightAlertSnapshot = null;
  render();
}

function saveCheckpoint() {
  const point = currentPoint();
  state.checkpoint = { lon: point.lon, lat: point.lat };
  const label = document.querySelector("#btnCheckpoint .label");
  label.textContent = "저장됨";
  showToast("현재 위치를 체크포인트로 저장했습니다.", 2400);
  window.setTimeout(() => { label.textContent = "체크포인트"; }, 1400);
  render();
}

function routeFromCurrentToBasecamp(from) {
  const reference = state.map.routeReturn;
  let nearestIndex = 0;
  let nearestDistance = Number.POSITIVE_INFINITY;
  reference.forEach((point, index) => {
    const distance = distanceMeters(from, { lon: point[0], lat: point[1] });
    if (distance < nearestDistance) {
      nearestDistance = distance;
      nearestIndex = index;
    }
  });
  const remaining = reference.slice(nearestIndex + 1);
  return [[from.lon, from.lat], ...remaining];
}

function showBasecampRoute() {
  state.routeDeviationDemo = false;
  state.basecampRegistered = true;
  const from = currentPoint();
  window.clearTimeout(walkStartTimer);
  stopWalk(false);
  state.map.returnStart = { lon: from.lon, lat: from.lat };
  state.map.routeToBasecamp = routeFromCurrentToBasecamp(from);
  state.sceneKey = 6;
  state.scene = {
    ...SCENES[6],
    current: "returnStart",
    route: "routeToBasecamp",
    routeValue: "BASE CAMP",
    routeSub: "BASE CAMP",
  };
  state.daylightAlertSnapshot = null;
  render();
  showToast("베이스캠프 복귀 경로가 설정되었습니다.", 2800);
}

// `A` 또는 auto_demo_ssh.sh가 시작하는 촬영용 원테이크 시퀀스.
// 장면 3·6은 경로 길이를 코드로 계산해 마커가 끝에 도달한 뒤 다음 장면으로 넘어간다.
async function startAutoDemo() {
  cancelAutoDemo();
  const runId = autoDemo.runId;
  autoDemo.active = true;

  // 반복 재생해도 매번 같은 그림에서 시작한다 — 베이스캠프는 아래 버튼이 등록한다.
  state.checkpoint = null;
  state.basecampRegistered = false;
  setNight(false, false);
  setScene(1, { audio: false, autoWalk: false });
  handleBasecampButton();
  if (!await waitForAutoDemo(runId, AUTO_DEMO_DELAYS_MS.basecampRegistered)) return;

  setScene(2, { autoWalk: false });
  if (!await waitForAutoDemo(
    runId,
    clipDurationMs("destination", AUTO_DEMO_DELAYS_MS.destinationFallback)
  )) return;

  const outboundCompleted = waitForAutoWalk(runId);
  setScene(3);
  if (!await outboundCompleted) return;
  if (!await waitForAutoDemo(
    runId,
    clipDurationMs("arrival", AUTO_DEMO_DELAYS_MS.arrivalFallback)
  )) return;

  setScene(5, { autoWalk: false });
  if (!await waitForAutoDemo(runId, AUTO_DEMO_DELAYS_MS.warningFallback)) return;
  // 일조 경고는 합성 문장이라 길이가 그때그때 다르다(해 지기까지 남은 분이 들어간다).
  // 고정 대기(6.2 s)보다 길면 끝까지 들려주고 넘어간다 — 다음 장면 안내가 말을 자르지 않게.
  if (!await waitForAutoDemo(runId, speechRemainingMs())) return;

  const returnCompleted = waitForAutoWalk(runId);
  showBasecampRoute();
  if (!await waitForAutoDemo(runId, AUTO_DEMO_DELAYS_MS.returnRouteShown)) return;
  if (!autoDemo.active || autoDemo.runId !== runId) return;
  startWalk();
  if (!await returnCompleted) return;
  if (!await waitForAutoDemo(
    runId,
    clipDurationMs("basecamp", AUTO_DEMO_DELAYS_MS.basecampArrival)
  )) return;

  setNight(true, true);
  await waitForAutoDemo(runId, AUTO_DEMO_DELAYS_MS.nightMode);
  if (autoDemo.runId === runId) autoDemo.active = false;
}

// D 키: 현재 위치를 경로에서 45 m 밀어내 경로 이탈 경고를 시연한다. 자동 시연은 멈추지 않는다.
function toggleRouteDeviationDemo() {
  if (!activeRoute()) {
    showToast("활성 경로가 없어 이탈 판정을 하지 않습니다.", 2400);
    return;
  }
  state.routeDeviationDemo = !state.routeDeviationDemo;
  render();
}

function handleBasecampButton() {
  // 처음 누르는 순간 지금 서 있는 자리가 베이스캠프가 된다. 그 전에는 지도에 없다.
  if (state.sceneKey === 1 || !state.basecampRegistered) {
    const current = currentPoint();
    state.map.basecamp = { lon: current.lon, lat: current.lat };
    state.basecampRegistered = true;
    render();
    showToast("베이스캠프가 등록되었습니다.", 2800);
    return;
  }
  showBasecampRoute();
}

/* 처음 상태로 되돌린다. 저장한 체크포인트와 등록한 베이스캠프도 함께 지운다. */
function resetDemo() {
  cancelAutoDemo();
  state.checkpoint = null;
  state.basecampRegistered = false;
  setNight(false, false);
  setScene(1, { audio: false });
}

/* live 모드 조작. 저장·경로 선택은 전부 서버가 판정하고 화면은 결과만 받는다. */
async function postWaypoint(payload) {
  const response = await fetch("/api/waypoints", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "저장 지점 요청 실패");
  applyLiveDevice(result);
  return result;
}

async function postVoiceCommand(action) {
  const response = await fetch("/api/voice/commands", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
    cache: "no-store",
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "음성 지도 명령 실패");
  applyVoiceEvent(result);
  return result;
}

function applyVoiceEvent(payload) {
  if (!payload || typeof payload !== "object") return;
  if (payload.device) applyLiveDevice(payload.device);
  if (Number.isFinite(payload.sequence)) {
    live.lastVoiceSequence = Math.max(live.lastVoiceSequence, payload.sequence);
  }
  if (payload.message) showToast(payload.message, 3800, { silent: true });
  render();
}

function connectVoiceEvents() {
  if (!LIVE_MODE || !("EventSource" in window)) return;
  const source = new EventSource("/api/voice/events");
  source.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (Number.isFinite(payload.sequence) && payload.sequence <= live.lastVoiceSequence) return;
      applyVoiceEvent(payload);
    } catch (error) {
      showToast("음성 지도 명령을 화면에 반영하지 못했습니다.", 2600);
    }
  };
}

document.querySelector("#btnDestination").addEventListener("click", () => {
  if (LIVE_MODE) {
    live.selecting = !live.selecting;
    setDestinationSelection(live.selecting);
    showToast(live.selecting ? "지도에서 목적지를 터치하세요." : "목적지 지정을 취소했습니다.", 2400);
    return;
  }
  cancelAutoDemo();
  setDestinationSelection(!state.destinationSelecting);
});
document.querySelector("#btnCheckpoint").addEventListener("click", async () => {
  if (LIVE_MODE) {
    try {
      await postWaypoint({ action: "save_current", kind: "checkpoint" });
      showToast("현재 위치를 체크포인트로 저장했습니다.", 2400);
    } catch (error) {
      showToast(error.message, 2600);
    }
    return;
  }
  cancelAutoDemo();
  saveCheckpoint();
});
document.querySelector("#btnBasecamp").addEventListener("click", async () => {
  if (LIVE_MODE) {
    try {
      if (live.basecamp) {
        await postWaypoint({ action: "select", id: "basecamp" });
        showToast("베이스캠프 귀환 경로를 불러왔습니다.", 2800);
      } else {
        await postWaypoint({ action: "save_current", kind: "basecamp" });
        showToast("현재 위치를 베이스캠프로 저장했습니다.", 2800);
      }
    } catch (error) {
      showToast(error.message, 2600);
    }
    return;
  }
  cancelAutoDemo();
  handleBasecampButton();
});
document.querySelector("#btnNight").addEventListener("click", async () => {
  if (LIVE_MODE) {
    /* 제품 화면의 야간 모드는 서버(interface.night)가 정본이다. 화면만 바꾸면 다음 /api/device
     * 스냅샷(2 s)이 서버 값으로 되돌려 야간 모드가 유지되지 않는다(2026-09-02 실기). */
    try {
      await postVoiceCommand("night_toggle");
    } catch (error) {
      showToast(error.message, 2600);
    }
    return;
  }
  cancelAutoDemo();
  setNight(!state.night, true);
});
canvas.addEventListener("click", selectMapDestination);

// 아래 키 조작과 디렉터 패널은 촬영 전용이다. 제품 화면에는 달지 않는다.
if (!LIVE_MODE) window.addEventListener("keydown", (event) => {
  if (event.key === "a" || event.key === "A") {
    event.preventDefault();
    startAutoDemo();
  } else if (/^[1-7]$/.test(event.key)) {
    cancelAutoDemo();
    setScene(Number(event.key));
  } else if (event.code === "Space") {
    event.preventDefault();
    cancelAutoDemo();
    nextScene();
  } else if (event.key === "b" || event.key === "B") {
    cancelAutoDemo();
    const audioKind = state.sceneKey === 5
      ? "warning"
      : state.sceneKey === 7
        ? "basecamp"
        : state.sceneKey === 4
        ? "arrival"
        : "destination";
    playFixedAudio(audioKind);
  } else if (event.key === "t" || event.key === "T") {
    cancelAutoDemo();
    playFixedAudio("daylightDetail");
  } else if (event.key === "w" || event.key === "W") {
    answerAloud("environment");
  } else if (event.key === "o" || event.key === "O") {
    answerAloud("co");
  } else if (event.key === "s" || event.key === "S") {
    answerAloud("daylight");
  } else if (event.key === "r" || event.key === "R") {
    resetDemo();
  } else if (event.key === "n" || event.key === "N") {
    cancelAutoDemo();
    setNight(!state.night, true);
  } else if (event.key === "c" || event.key === "C") {
    cancelAutoDemo();
    saveCheckpoint();
  } else if (event.key === "d" || event.key === "D") {
    toggleRouteDeviationDemo();
  } else if (event.key === "h" || event.key === "H") {
    const panel = document.querySelector("#director");
    panel.hidden = !panel.hidden;
  }
});

// 브라우저 QA(tests/ui_video_qa.js) 전용 훅. 제품 조작 경로가 아니다.
window.ogtechVideoQa = Object.freeze({
  temperatureLevel,
  routeOffsetMeters,
  routeDeviation: () => routeDeviation(currentPoint()),
  setEnvironment(next) {
    state.environment = { ...state.environment, ...next };
    render();
  },
  setCo(next) {
    state.co = { ...state.co, ...next };
    render();
  },
  answerText: (kind) => (VOICE_ANSWERS[kind] ? VOICE_ANSWERS[kind]() : null),
  basecampRegistered: () => state.basecampRegistered,
  checkpoint: () => state.checkpoint,
  handleBasecampButton,
  saveCheckpoint,
  resetDemo,
  toggleRouteDeviationDemo,
});

// autoplay=1 은 1회, autoplay=loop 는 촬영이 끝날 때까지 반복한다.
async function startAutoplay() {
  await new Promise((resolve) => window.setTimeout(resolve, AUTOPLAY_START_DELAY_MS));
  for (;;) {
    await startAutoDemo();
    if (AUTOPLAY_MODE !== "loop") return;
    await new Promise((resolve) => window.setTimeout(resolve, AUTOPLAY_LOOP_PAUSE_MS));
  }
}

window.addEventListener("resize", draw);
warmFixedAudio();
setNight(false);
if (LIVE_MODE) {
  // 촬영 시나리오를 쓰지 않는다. 그림틀만 같고 값은 전부 /api/device 에서 온다.
  document.querySelector("#director").hidden = true;
  state.scene = { ...SCENES[1], current: null, target: null, route: null, alert: null, arrival: null };
  connectVoiceEvents();
  render();
} else {
  setScene(1, { audio: false });
}
window.setInterval(() => {
  updateSeoulClock();
  setDaylightGlance(state.scene);
  warmVoiceAnswers();
}, 1000);

// URL 파라미터 반영. 두 함수 모두 파라미터가 없으면 스스로 아무것도 하지 않는다.
connectLiveSensors();
if (AUTOPLAY_MODE) startAutoplay();
