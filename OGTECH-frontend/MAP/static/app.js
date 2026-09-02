"use strict";

const canvas = document.querySelector("#mapCanvas");
const context = canvas.getContext("2d");
const fileInput = document.querySelector("#mapFile");
const fileButton = document.querySelector(".file-button");
const calculateButton = document.querySelector("#calculateRoute");
const notice = document.querySelector("#notice");
const processingOverlay = document.querySelector("#processingOverlay");
const processingCard = processingOverlay.querySelector(".processing-card");
const processingTitle = document.querySelector("#processingTitle");
const processingStage = document.querySelector("#processingStage");
const processingProgress = document.querySelector("#processingProgress");
const processingClose = document.querySelector("#processingClose");
const gpsSetup = document.querySelector("#gpsSetup");
const gpsPort = document.querySelector("#gpsPort");
const gpsBaud = document.querySelector("#gpsBaud");
const gpsSetupHelp = document.querySelector("#gpsSetupHelp");
let importStatusTimer = null;
let overlayHideTimer = null;
let gpsEventSource = null;
let selectedGpsMode = "replay";

const state = {
  map: null,
  route: null,
  mode: "destination",
  current: null,
  destination: null,
  source: "demo",
  gps: null,
};

function setNotice(message, kind = "") {
  notice.textContent = message;
  notice.className = `notice ${kind}`.trim();
}

function showProcessing(filename) {
  if (overlayHideTimer) clearTimeout(overlayHideTimer);
  processingCard.className = "processing-card";
  processingTitle.textContent = "대형 지도 변환 중";
  processingStage.textContent = `${filename} 파일을 Jetson으로 전달하고 있습니다.`;
  processingProgress.value = 10;
  processingClose.hidden = true;
  processingOverlay.hidden = false;
}

function hideProcessing() {
  processingOverlay.hidden = true;
}

async function refreshImportStatus() {
  try {
    const status = await readResponse(
      await fetch("/api/import-status", { cache: "no-store" })
    );
    if (status.state === "idle") return;
    processingStage.textContent = status.stage;
    processingProgress.value = status.percent;
  } catch (_error) {
    // 변환 요청 자체의 응답에서 최종 오류를 표시하므로 상태 조회 실패는 무시한다.
  }
}

function startImportStatusPolling() {
  stopImportStatusPolling();
  importStatusTimer = window.setInterval(refreshImportStatus, 700);
}

function stopImportStatusPolling() {
  if (importStatusTimer) {
    window.clearInterval(importStatusTimer);
    importStatusTimer = null;
  }
}

function showImportComplete(metadata) {
  processingCard.className = "processing-card complete";
  processingTitle.textContent = "지도 변환 완료";
  processingStage.textContent =
    `${metadata.source_name} · 노드 ${metadata.statistics.nodes.toLocaleString()} · ` +
    `엣지 ${metadata.statistics.edges.toLocaleString()}`;
  processingProgress.value = 100;
  processingClose.hidden = true;
  overlayHideTimer = window.setTimeout(hideProcessing, 1800);
}

function showImportFailed(message) {
  processingCard.className = "processing-card failed";
  processingTitle.textContent = "지도 변환 실패";
  processingStage.textContent = message;
  processingProgress.value = 0;
  processingClose.hidden = false;
}

async function readResponse(response) {
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "요청에 실패했습니다.");
  }
  return payload;
}

function selectGpsMode(mode) {
  selectedGpsMode = mode;
  document.querySelectorAll("[data-gps-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.gpsMode === mode);
  });
  const replay = mode === "replay";
  gpsPort.disabled = replay;
  gpsBaud.disabled = replay;
  if (mode === "air530") {
    gpsBaud.value = "9600";
    gpsPort.placeholder = "/dev/ttyUSB0";
    gpsSetupHelp.textContent = "Air530을 USB-UART로 Jetson에 직접 연결해 NMEA를 확인합니다.";
  } else if (mode === "stm32") {
    gpsBaud.value = "115200";
    gpsPort.placeholder = "/dev/ttyACM0";
    gpsSetupHelp.textContent = "STM32에 GET_FIX를 보내 한 줄 JSON 응답을 확인합니다.";
  } else {
    gpsSetupHelp.textContent = "하드웨어 없이 저장된 NMEA를 반복 재생합니다.";
  }
}

async function loadGpsPorts() {
  try {
    const payload = await readResponse(
      await fetch("/api/gps/ports", { cache: "no-store" })
    );
    const list = document.querySelector("#gpsPortList");
    list.replaceChildren();
    payload.ports.forEach((port) => {
      const option = document.createElement("option");
      option.value = port.device;
      option.label = port.description;
      list.append(option);
    });
    if (!gpsPort.value && payload.ports.length === 1) {
      gpsPort.value = payload.ports[0].device;
    }
  } catch (_error) {
    // 포트 자동 검색이 안 되어도 사용자가 경로를 직접 입력할 수 있다.
  }
}

function openGpsSetup() {
  const currentMode = state.gps?.mode;
  selectGpsMode(["replay", "air530", "stm32"].includes(currentMode) ? currentMode : "replay");
  if (state.gps?.configuration?.port) gpsPort.value = state.gps.configuration.port;
  gpsSetup.hidden = false;
  loadGpsPorts();
}

function closeGpsSetup() {
  gpsSetup.hidden = true;
}

function updateGps(snapshot) {
  state.gps = snapshot;
  const status = document.querySelector("#gpsStatus");
  const fixBadge = document.querySelector("#gpsFixBadge");
  status.className = "";
  fixBadge.className = "";

  if (snapshot.error) {
    status.textContent = "입력 오류";
    status.classList.add("gps-error");
    fixBadge.textContent = "INPUT ERROR";
    fixBadge.classList.add("error");
  } else if (snapshot.fix) {
    status.textContent = snapshot.demo ? "SAMPLE FIX" : "GPS FIX";
    status.classList.add(snapshot.demo ? "gps-waiting" : "gps-live");
    fixBadge.textContent = snapshot.demo ? "SAMPLE FIX" : "LIVE FIX";
    fixBadge.classList.add(snapshot.demo ? "demo" : "live");
  } else if (snapshot.mode !== "off" && snapshot.connected) {
    status.textContent = "FIX 대기";
    status.classList.add("gps-waiting");
    fixBadge.textContent = "NO FIX";
  } else {
    status.textContent = "연결 안 됨";
    status.classList.add("gps-waiting");
    fixBadge.textContent = "OFFLINE";
  }

  const accuracy = snapshot.acc_m;
  document.querySelector("#gpsAccuracy").textContent =
    Number.isFinite(accuracy)
      ? `±${Number(accuracy).toFixed(1)} m`
      : snapshot.hdop
        ? `HDOP ${Number(snapshot.hdop).toFixed(1)} · ±—`
        : "±— m";
  document.querySelector("#gpsSatellites").textContent =
    `SAT ${snapshot.satellites ?? "—"}`;
  const age = snapshot.fix ? snapshot.age_s : snapshot.last_age_s;
  document.querySelector("#gpsAge").textContent =
    Number.isFinite(age) ? `AGE ${Number(age).toFixed(1)}s` : "AGE —";

  if (snapshot.fix) {
    state.current = { lat: snapshot.lat, lon: snapshot.lon };
    state.source = snapshot.demo ? "demo" : "sensor";
    document.querySelector("#currentSource").textContent = snapshot.demo
      ? "CURRENT / NMEA SAMPLE"
      : "CURRENT / GPS LIVE";
  } else if (snapshot.last_fix && state.source === "sensor") {
    state.current = {
      lat: snapshot.last_fix.lat,
      lon: snapshot.last_fix.lon,
    };
    document.querySelector("#currentSource").textContent = "CURRENT / LAST FIX";
  }
  updateMapMetadata();
  draw();
}

function connectGpsEvents() {
  if (gpsEventSource) gpsEventSource.close();
  gpsEventSource = new EventSource("/api/gps/events");
  gpsEventSource.onmessage = (event) => {
    try {
      updateGps(JSON.parse(event.data));
    } catch (_error) {
      setNotice("GPS 상태 메시지를 읽을 수 없습니다.", "error");
    }
  };
}

async function configureGps() {
  if (selectedGpsMode !== "replay" && !gpsPort.value.trim()) {
    setNotice("직렬 포트 경로를 입력하세요.", "warning");
    return;
  }
  try {
    const snapshot = await readResponse(
      await fetch("/api/gps/configure", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: selectedGpsMode,
          port: selectedGpsMode === "replay" ? "" : gpsPort.value.trim(),
          baud: selectedGpsMode === "replay" ? null : Number(gpsBaud.value),
        }),
      })
    );
    if (selectedGpsMode !== "replay") {
      state.current = null;
      state.route = null;
      state.source = "sensor";
      document.querySelector("#currentSource").textContent = "CURRENT / GPS WAIT";
    }
    updateGps(snapshot);
    closeGpsSetup();
    setNotice(
      selectedGpsMode === "replay"
        ? "샘플 NMEA 재생을 시작했습니다."
        : "직렬 포트를 열고 GPS 입력을 기다립니다."
    );
  } catch (error) {
    setNotice(error.message, "error");
    gpsSetupHelp.textContent = error.message;
  }
}

async function stopGps() {
  try {
    const snapshot = await readResponse(
      await fetch("/api/gps/stop", { method: "POST", body: "{}" })
    );
    state.source = "demo";
    state.current = state.map ? { ...state.map.suggested_points.current } : null;
    state.route = null;
    document.querySelector("#currentSource").textContent = "CURRENT / SAMPLE";
    updateGps(snapshot);
    closeGpsSetup();
    setNotice("GPS 입력을 중지했습니다.");
  } catch (error) {
    setNotice(error.message, "error");
  }
}

function formatCoordinate(point) {
  if (!point) return "—";
  return `${point.lat.toFixed(6)}, ${point.lon.toFixed(6)}`;
}

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  setNotice(
    mode === "current"
      ? "지도에서 검증용 현재 위치를 누르세요."
      : "지도에서 목적지를 누르면 경로를 자동 계산합니다."
  );
}

function updateMapMetadata() {
  const metadata = state.map;
  document.querySelector("#mapStatus").textContent = metadata ? "변환 완료" : "데이터 없음";
  document.querySelector("#mapStatus").classList.toggle("ready", Boolean(metadata));
  document.querySelector("#mapName").textContent = metadata?.source_name || "지도 대기 중";
  document.querySelector("#mapScale").textContent = metadata
    ? `${metadata.statistics.nodes.toLocaleString()}N · ${metadata.statistics.edges.toLocaleString()}E · ${metadata.statistics.weak_components}C`
    : "WGS84 · OFFLINE";
  document.querySelector("#currentCoordinate").textContent = formatCoordinate(state.current);
  document.querySelector("#destinationCoordinate").textContent = formatCoordinate(state.destination);
}

function projection() {
  const bounds = state.map.bounds;
  const rect = canvas.getBoundingClientRect();
  const padding = 34;
  const midLat = (bounds.south + bounds.north) / 2;
  const lonFactor = Math.max(0.15, Math.cos((midLat * Math.PI) / 180));
  const worldWidth = Math.max(1e-9, (bounds.east - bounds.west) * lonFactor);
  const worldHeight = Math.max(1e-9, bounds.north - bounds.south);
  const scale = Math.min(
    (rect.width - padding * 2) / worldWidth,
    (rect.height - padding * 2) / worldHeight
  );
  const drawWidth = worldWidth * scale;
  const drawHeight = worldHeight * scale;
  const offsetX = (rect.width - drawWidth) / 2;
  const offsetY = (rect.height - drawHeight) / 2;
  return {
    rect,
    toScreen(lon, lat) {
      return [
        offsetX + (lon - bounds.west) * lonFactor * scale,
        offsetY + (bounds.north - lat) * scale,
      ];
    },
    toWorld(x, y) {
      return {
        lon: bounds.west + (x - offsetX) / (lonFactor * scale),
        lat: bounds.north - (y - offsetY) / scale,
      };
    },
    metersToPixels(meters) {
      return (meters / 111_320) * scale;
    },
  };
}

function line(coordinates, projector) {
  if (!coordinates || coordinates.length < 2) return;
  coordinates.forEach((point, index) => {
    const [x, y] = projector.toScreen(point[0], point[1]);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
}

function drawGrid(width, height) {
  context.strokeStyle = "#182121";
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

function drawMarker(point, label, color, projector, shape) {
  if (!point) return;
  const [x, y] = projector.toScreen(point.lon, point.lat);
  context.save();
  context.translate(x, y);
  context.fillStyle = "#0a0f0f";
  context.strokeStyle = color;
  context.lineWidth = 4;
  context.beginPath();
  if (shape === "square") context.rect(-10, -10, 20, 20);
  else context.arc(0, 0, 11, 0, Math.PI * 2);
  context.fill();
  context.stroke();
  context.fillStyle = color;
  context.font = "700 20px Malgun Gothic, sans-serif";
  context.textAlign = "center";
  context.fillText(label, 0, -20);
  context.restore();
}

function draw() {
  const rect = canvas.getBoundingClientRect();
  const ratio = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = Math.max(1, Math.round(rect.width * ratio));
  canvas.height = Math.max(1, Math.round(rect.height * ratio));
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.fillStyle = "#0a0f0f";
  context.fillRect(0, 0, rect.width, rect.height);
  drawGrid(rect.width, rect.height);
  if (!state.map) return;

  const projector = projection();
  context.strokeStyle = "#465553";
  context.lineWidth = 1.5;
  context.beginPath();
  state.map.edges.forEach((edge) => line(edge, projector));
  context.stroke();

  if (state.route?.route?.coordinates) {
    context.strokeStyle = "#071010";
    context.lineWidth = 9;
    context.beginPath();
    line(state.route.route.coordinates, projector);
    context.stroke();
    context.strokeStyle = "#3fe3e3";
    context.lineWidth = 4;
    context.beginPath();
    line(state.route.route.coordinates, projector);
    context.stroke();
  }

  const liveGps = state.source === "sensor";
  const currentColor = liveGps
    ? state.gps?.fix
      ? "#57d47b"
      : "#6e7a77"
    : "#f5b942";
  const currentLabel = liveGps && !state.gps?.fix ? "마지막" : "현재";
  drawMarker(state.current, currentLabel, currentColor, projector, "circle");
  drawMarker(state.destination, "목적지", "#3fe3e3", projector, "square");
}

async function loadMap() {
  try {
    state.map = await readResponse(await fetch("/api/map", { cache: "no-store" }));
    state.current = { ...state.map.suggested_points.current };
    state.destination = { ...state.map.suggested_points.destination };
    state.route = null;
    updateMapMetadata();
    draw();
    const warning = state.map.warnings?.[0];
    setNotice(warning || "샘플 지도를 변환했습니다. 목적지를 눌러 바꿀 수 있습니다.", warning ? "warning" : "");
    await calculateRoute();
  } catch (error) {
    setNotice(error.message, "error");
  }
}

async function calculateRoute() {
  if (!state.current || !state.destination) {
    setNotice("현재 위치와 목적지를 먼저 지정하세요.", "warning");
    return;
  }
  calculateButton.disabled = true;
  document.querySelector("#routeDistance").textContent = "계산 중";
  try {
    state.route = await readResponse(
      await fetch("/api/route", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current: state.current,
          destination: state.destination,
          accuracy_m: state.gps?.fix ? state.gps.acc_m : state.source === "sensor" ? null : 10,
          satellites: state.gps?.satellites ?? 0,
          age_s: state.gps?.age_s ?? state.gps?.last_age_s ?? 0,
          source: state.source,
          fix: state.gps?.fix === true,
        }),
      })
    );
    const route = state.route.route;
    document.querySelector("#routeDistance").textContent = `${route.distance_m.toFixed(0)} m`;
    document.querySelector("#routeDetail").textContent =
      `${route.node_count}개 노드 · 스냅 ${route.start.snap_distance_m.toFixed(1)}m`;
    const device = state.route.device_state;
    document.querySelector("#deviceState").textContent =
      `fix=${Number(device.gps.fix)} acc=${device.gps.acc_m ?? "—"}m · ` +
      `trail=${Number(device.route.on_trail)} offset=${device.route.offset_m}m next=${device.route.next_wp_m}m`;
    setNotice("경로·거리·스냅 값은 지도 엔진 코드가 계산했습니다.");
    draw();
  } catch (error) {
    state.route = null;
    document.querySelector("#routeDistance").textContent = "경로 없음";
    document.querySelector("#routeDetail").textContent = error.message;
    document.querySelector("#deviceState").textContent = "DEVICE_STATE 생성 안 됨";
    setNotice(error.message, "error");
    draw();
  } finally {
    calculateButton.disabled = false;
  }
}

async function importMap(file) {
  if (!file) return;
  showProcessing(file.name);
  startImportStatusPolling();
  fileButton.classList.add("busy");
  calculateButton.disabled = true;
  setNotice(`${file.name} 검증 및 변환 중…`);
  try {
    const response = await fetch("/api/maps/import", {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Filename": encodeURIComponent(file.name),
      },
      body: file,
    });
    state.map = await readResponse(response);
    if (state.source !== "sensor") {
      state.current = { ...state.map.suggested_points.current };
    }
    state.destination = { ...state.map.suggested_points.destination };
    state.route = null;
    updateMapMetadata();
    draw();
    showImportComplete(state.map);
    const warning = state.map.warnings?.[0];
    setNotice(warning || "지도 검증과 런타임 변환을 완료했습니다.", warning ? "warning" : "");
    await calculateRoute();
  } catch (error) {
    setNotice(error.message, "error");
    showImportFailed(error.message);
  } finally {
    stopImportStatusPolling();
    fileButton.classList.remove("busy");
    calculateButton.disabled = false;
    fileInput.value = "";
  }
}

canvas.addEventListener("click", async (event) => {
  if (!state.map) return;
  const rect = canvas.getBoundingClientRect();
  const point = projection().toWorld(event.clientX - rect.left, event.clientY - rect.top);
  const bounds = state.map.bounds;
  if (
    point.lon < bounds.west ||
    point.lon > bounds.east ||
    point.lat < bounds.south ||
    point.lat > bounds.north
  ) {
    setNotice("지도 데이터 경계 안쪽을 누르세요.", "warning");
    return;
  }
  state[state.mode] = point;
  state.route = null;
  updateMapMetadata();
  draw();
  await calculateRoute();
});

document.querySelectorAll("[data-mode]").forEach((button) => {
  button.addEventListener("click", () => setMode(button.dataset.mode));
});
fileInput.addEventListener("change", () => importMap(fileInput.files[0]));
calculateButton.addEventListener("click", calculateRoute);
processingClose.addEventListener("click", hideProcessing);
document.querySelector("#openGpsSetup").addEventListener("click", openGpsSetup);
document.querySelector("#gpsSetupClose").addEventListener("click", closeGpsSetup);
document.querySelector("#gpsConnect").addEventListener("click", configureGps);
document.querySelector("#gpsStop").addEventListener("click", stopGps);
document.querySelectorAll("[data-gps-mode]").forEach((button) => {
  button.addEventListener("click", () => selectGpsMode(button.dataset.gpsMode));
});
window.addEventListener("resize", draw);

loadMap().finally(connectGpsEvents);
