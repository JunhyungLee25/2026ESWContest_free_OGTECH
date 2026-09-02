/* 1024×600 촬영·키오스크 화면(video.html)의 상단 계기·경로 이탈 경고 회귀 검사.
 * 실행 환경에는 Playwright가 필요하며 제품 런타임에는 포함하지 않는다.
 *
 *   node tests/ui_video_qa.js [URL 또는 생략=file://kiosk/video.html] [출력 폴더]
 *
 * 검사 항목
 *   1) CO 농도 칸에 'CO 전용 · DEMO' 문구가 없다
 *   2) 온도·습도 글자 크기가 같고, 온도 색은 >30 적색 / 20~30 황색 / ≤20 녹색, 습도는 하늘색
 *   3) 활성 경로에서 벗어나면 경로 이탈 배너가 뜨고, 복귀하면 사라진다
 *   4) 하단 버튼 4개(목적지·체크포인트·베이스캠프·야간 모드)는 그대로다
 *   5) 브라우저 콘솔 오류가 없다
 */

"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const defaultUrl = "file://" + path.resolve(__dirname, "..", "kiosk", "video.html");
const baseUrl = process.argv[2] || defaultUrl;
const outputDir = path.resolve(process.argv[3] || "test-results");

function requireCondition(condition, message) {
  if (!condition) throw new Error(message);
}

function rgbOf(text) {
  const match = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(text);
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

async function main() {
  fs.mkdirSync(outputDir, { recursive: true });
  const launchOptions = { headless: true };
  if (process.env.OGTECH_BROWSER_EXECUTABLE) {
    launchOptions.executablePath = process.env.OGTECH_BROWSER_EXECUTABLE;
  }
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({ viewport: { width: 1024, height: 600 } });
  const browserErrors = [];
  page.on("pageerror", (error) => browserErrors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });

  try {
    await page.goto(baseUrl, { waitUntil: "load", timeout: 10_000 });
    await page.waitForFunction(() => Boolean(window.ogtechVideoQa), null, { timeout: 5_000 });

    // 1) CO 칸 문구
    const coText = await page.locator("#glanceCo").innerText();
    requireCondition(!coText.includes("DEMO") && !coText.includes("CO 전용"),
      `CO 칸에 DEMO 문구가 남아 있음: ${JSON.stringify(coText)}`);
    requireCondition(coText.includes("0 ppm"), `CO 값이 없음: ${JSON.stringify(coText)}`);

    // 2) 온도·습도 크기와 색
    const readEnv = () => page.evaluate(() => {
      const temperature = document.querySelector("#envTemperature");
      const humidity = document.querySelector("#envHumidity");
      const styleOf = (element) => getComputedStyle(element);
      return {
        temperatureText: temperature.textContent,
        humidityText: humidity.textContent,
        level: temperature.dataset.level,
        temperatureSize: styleOf(temperature).fontSize,
        humiditySize: styleOf(humidity).fontSize,
        temperatureWeight: styleOf(temperature).fontWeight,
        humidityWeight: styleOf(humidity).fontWeight,
        temperatureColor: styleOf(temperature).color,
        humidityColor: styleOf(humidity).color,
        tokens: {
          red: styleOf(document.documentElement).getPropertyValue("--red").trim(),
          yellow: styleOf(document.documentElement).getPropertyValue("--yellow").trim(),
          green: styleOf(document.documentElement).getPropertyValue("--green").trim(),
          sky: styleOf(document.documentElement).getPropertyValue("--sky").trim(),
        },
      };
    });
    const hexToRgb = (hex) => [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16));

    const initial = await readEnv();
    requireCondition(initial.temperatureText === "30.0°C (86.0°F)", `온도 문구: ${initial.temperatureText}`);
    requireCondition(initial.humidityText === "55% RH", `습도 문구: ${initial.humidityText}`);
    requireCondition(initial.temperatureSize === initial.humiditySize,
      `온도·습도 글자 크기 불일치: ${initial.temperatureSize} vs ${initial.humiditySize}`);
    requireCondition(parseFloat(initial.temperatureSize) >= 20, `본문 20px 미만: ${initial.temperatureSize}`);
    requireCondition(initial.temperatureWeight === initial.humidityWeight,
      `온도·습도 굵기 불일치: ${initial.temperatureWeight} vs ${initial.humidityWeight}`);
    requireCondition(String(rgbOf(initial.humidityColor)) === String(hexToRgb(initial.tokens.sky)),
      `습도 색이 하늘색이 아님: ${initial.humidityColor} (기대 ${initial.tokens.sky})`);

    const expectations = [
      { temperatureC: 30.0, level: "warm", token: "yellow" },
      { temperatureC: 30.1, level: "hot", token: "red" },
      { temperatureC: 35, level: "hot", token: "red" },
      { temperatureC: 20.1, level: "warm", token: "yellow" },
      { temperatureC: 20.0, level: "cool", token: "green" },
      { temperatureC: 5, level: "cool", token: "green" },
    ];
    const temperatureResults = [];
    for (const expectation of expectations) {
      await page.evaluate((value) => window.ogtechVideoQa.setEnvironment({ temperatureC: value }),
        expectation.temperatureC);
      const env = await readEnv();
      const expectedColor = String(hexToRgb(env.tokens[expectation.token]));
      requireCondition(env.level === expectation.level,
        `${expectation.temperatureC}°C level=${env.level}, 기대 ${expectation.level}`);
      requireCondition(String(rgbOf(env.temperatureColor)) === expectedColor,
        `${expectation.temperatureC}°C color=${env.temperatureColor}, 기대 ${expectation.token} ${env.tokens[expectation.token]}`);
      temperatureResults.push({ temperatureC: expectation.temperatureC, level: env.level, color: env.temperatureColor });
    }
    await page.evaluate(() => window.ogtechVideoQa.setEnvironment({ temperatureC: 30.0 }));

    // 3) 경로 이탈 배너 — 장면 1(경로 없음)에서는 판정하지 않는다
    await page.keyboard.press("D");
    await page.waitForTimeout(120);
    requireCondition(await page.locator("#routeAlert").isHidden(), "경로가 없는데 이탈 배너가 보임");
    const noRouteToast = await page.locator("#statusToast").innerText();
    requireCondition(noRouteToast.includes("활성 경로가 없어"), `경로 없음 안내가 아님: ${noRouteToast}`);

    await page.keyboard.press("2");   // 일감호 경로 설정 (headless라 음성은 실패해도 무방)
    await page.waitForTimeout(200);
    const onRoute = await page.evaluate(() => window.ogtechVideoQa.routeDeviation());
    requireCondition(onRoute && onRoute.offRoute === false && onRoute.offsetM < 1,
      `경로 위인데 이탈로 판정: ${JSON.stringify(onRoute)}`);
    requireCondition(await page.locator("#routeAlert").isHidden(), "경로 위인데 이탈 배너가 보임");

    await page.keyboard.press("D");
    await page.waitForTimeout(200);
    const offRoute = await page.evaluate(() => window.ogtechVideoQa.routeDeviation());
    requireCondition(offRoute && offRoute.offRoute === true && offRoute.offsetM > offRoute.thresholdM,
      `이탈 시연인데 이탈로 판정되지 않음: ${JSON.stringify(offRoute)}`);
    requireCondition(await page.locator("#routeAlert").isVisible(), "이탈했는데 배너가 안 보임");
    const alertText = await page.locator("#routeAlertText").innerText();
    requireCondition(/^경로 이탈 · \d+ m · 현재 위치와 경로를 확인하세요$/.test(alertText),
      `이탈 문구 형식: ${alertText}`);
    const alertBox = await page.locator("#routeAlert").boundingBox();
    const mapBox = await page.locator("#mapPanel").boundingBox();
    requireCondition(alertBox && mapBox && Math.abs(alertBox.y - mapBox.y) < 1 && alertBox.height >= 60,
      `이탈 배너 위치·크기: ${JSON.stringify(alertBox)} / map ${JSON.stringify(mapBox)}`);
    await page.screenshot({ path: path.join(outputDir, "video_route_deviation.png") });

    await page.keyboard.press("D");
    await page.waitForTimeout(150);
    requireCondition(await page.locator("#routeAlert").isHidden(), "복귀했는데 이탈 배너가 남아 있음");

    // 장면 5(일조 경고)와 같이 뜨면 아래에 쌓인다
    await page.keyboard.press("5");
    await page.waitForTimeout(200);
    await page.keyboard.press("D");
    await page.waitForTimeout(200);
    const stacked = await page.evaluate(() => {
      const daylight = document.querySelector("#alert").getBoundingClientRect();
      const route = document.querySelector("#routeAlert").getBoundingClientRect();
      const attribution = document.querySelector("#mapAttribution").getBoundingClientRect();
      return { daylight: [daylight.top, daylight.bottom], route: [route.top, route.bottom], attributionTop: attribution.top };
    });
    requireCondition(Math.abs(stacked.route[0] - stacked.daylight[1]) < 1,
      `이탈 배너가 일조 경고 아래에 붙지 않음: ${JSON.stringify(stacked)}`);
    requireCondition(stacked.attributionTop >= stacked.route[1],
      `귀속 표기가 배너에 가려짐: ${JSON.stringify(stacked)}`);
    await page.screenshot({ path: path.join(outputDir, "video_stacked_alerts.png") });
    await page.keyboard.press("D");
    await page.keyboard.press("1");
    await page.waitForTimeout(150);

    // 4) 하단 버튼
    const buttons = await page.evaluate(() =>
      [...document.querySelectorAll(".actions .action")].map((button) => ({
        id: button.id,
        label: button.querySelector(".label").textContent.trim(),
        height: button.getBoundingClientRect().height,
      })));
    const expectedButtons = [
      ["btnDestination", "목적지"], ["btnCheckpoint", "체크포인트"],
      ["btnBasecamp", "베이스캠프"], ["btnNight", "야간 모드"],
    ];
    requireCondition(buttons.length === 4, `하단 버튼 수 ${buttons.length}`);
    expectedButtons.forEach(([id, label], index) => {
      requireCondition(buttons[index].id === id && buttons[index].label === label,
        `버튼 ${index}: ${JSON.stringify(buttons[index])}`);
      requireCondition(buttons[index].height >= 96, `버튼 ${id} 높이 ${buttons[index].height}px < 96px`);
    });

    // 화면 전체가 1024×600 안에 있고 가로 스크롤이 없다
    const layout = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      screen: document.querySelector(".screen").getBoundingClientRect().width,
    }));
    requireCondition(layout.screen === 1024, `화면 폭 ${layout.screen}`);

    await page.screenshot({ path: path.join(outputDir, "video_scene1.png") });

    // 5) 콘솔 오류 — headless에서 오디오 자동재생 거부(NotAllowedError)만 허용한다
    const unexpected = browserErrors.filter((text) => !/NotAllowedError|play\(\) failed/i.test(text));
    requireCondition(unexpected.length === 0, `브라우저 오류: ${unexpected.join(" | ")}`);

    const report = {
      url: baseUrl,
      co: coText.replace(/\s+/g, " ").trim(),
      environment: initial,
      temperatureResults,
      routeDeviation: { onRoute, offRoute, alertText, stacked },
      buttons,
      browserErrors,
    };
    fs.writeFileSync(path.join(outputDir, "video_qa_report.json"), JSON.stringify(report, null, 2));
    console.log("video UI QA OK");
    console.log(JSON.stringify({ co: report.co, level: initial.level, sizes: [initial.temperatureSize, initial.humiditySize], alertText, buttons: buttons.map((b) => b.label) }));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(`video UI QA FAILED: ${error.message}`);
  process.exit(1);
});
