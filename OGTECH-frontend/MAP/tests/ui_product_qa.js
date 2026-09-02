/* 제품 화면(/product/) 회귀 검사.
 *
 * 마크업·CSS·그리기 코드는 촬영 화면(/video/)과 한 벌을 공유하므로 배치 검사는
 * ui_video_qa.js 가 맡는다. 여기서는 제품 화면에만 해당하는 것만 본다.
 *   - 촬영 전용 요소(디렉터 패널·장면 키)가 붙지 않는다
 *   - 값이 없을 때 꾸며내지 않는다(좌표·온습도·CO)
 *   - 터치 타깃이 물리 규격을 지킨다
 * 실행 환경에는 Playwright 가 필요하며 제품 런타임에는 포함하지 않는다. */

"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const baseUrl = process.argv[2] || "http://127.0.0.1:8899/product/";
const outputDir = path.resolve(process.argv[3] || "test-results");

function requireCondition(condition, message) {
  if (!condition) throw new Error(message);
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
    await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 10_000 });
    await page.locator("#btnDestination").waitFor({ state: "visible" });
    await page.waitForTimeout(3_000);
    await page.screenshot({ path: path.join(outputDir, "product_1024x600.png") });

    const product = await page.evaluate(() => {
      const text = (selector) => document.querySelector(selector)?.textContent ?? null;
      return {
        directorHidden: document.querySelector("#director")?.hidden,
        latitude: text("#currentLatitude"),
        longitude: text("#currentLongitude"),
        coordinateState: document.querySelector("#glanceCoordinate")?.dataset.state,
        temperature: text("#envTemperature"),
        humidity: text("#envHumidity"),
        co: text("#coValue"),
        readoutHidden: document.querySelector("#readout")?.hidden,
        night: document.documentElement.dataset.night,
        targets: [...document.querySelectorAll(".action")].map((item) => {
          const rect = item.getBoundingClientRect();
          return { id: item.id, width: rect.width, height: rect.height };
        }),
        document: {
          scrollWidth: document.documentElement.scrollWidth,
          scrollHeight: document.documentElement.scrollHeight,
        },
      };
    });

    // 촬영 전용 보조 패널은 제품 화면에 뜨지 않는다.
    requireCondition(product.directorHidden === true, "촬영 보조 패널이 제품 화면에 보임");

    // 장면 키는 제품 화면에서 동작하지 않아야 한다.
    await page.keyboard.press("2");
    await page.waitForTimeout(400);
    const afterSceneKey = await page.evaluate(
      () => document.querySelector("#readout")?.hidden
    );
    requireCondition(
      afterSceneKey === product.readoutHidden,
      "숫자 키로 촬영 장면이 전환됨 — 제품 화면에서는 동작하면 안 됨"
    );

    // GPS·센서가 없으면 값을 만들어 내지 않는다.
    requireCondition(
      product.latitude === "좌표 없음" && product.longitude === "GPS 미수신",
      `GPS 미수신인데 좌표를 표시함: ${product.latitude} / ${product.longitude}`
    );
    requireCondition(
      product.coordinateState === "none",
      `좌표 칸 상태가 none 이 아님: ${product.coordinateState}`
    );
    requireCondition(
      product.temperature === "—" && product.co === "—",
      `센서 값이 없는데 숫자를 표시함: ${product.temperature} / ${product.co}`
    );

    // 터치 타깃 물리 규격(styles.css 규칙: 바닥 80px, 넓은 컨트롤 72px).
    requireCondition(product.targets.length === 4, "하단 조작 버튼이 4개가 아님");
    product.targets.forEach((target) => {
      const wide = target.width >= target.height * 2;
      const floor = wide ? 72 : 80;
      requireCondition(
        target.height >= floor,
        `${target.id} 높이 ${target.height}px 가 ${floor}px 미만`
      );
    });

    requireCondition(product.document.scrollWidth <= 1024, "가로 스크롤이 생김");
    requireCondition(browserErrors.length === 0, `브라우저 오류: ${browserErrors.join(" | ")}`);

    fs.writeFileSync(
      path.join(outputDir, "product_ui_1024x600.json"),
      JSON.stringify({ product, browser_errors: browserErrors }, null, 2)
    );
    console.log("product UI QA OK");
    console.log(JSON.stringify({
      director_hidden: product.directorHidden,
      coordinate: `${product.latitude} / ${product.longitude}`,
      environment: `${product.temperature} / ${product.humidity} / ${product.co}`,
      buttons: product.targets.map((t) => `${t.id} ${Math.round(t.height)}px`),
    }));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(String(error && error.message ? error.message : error));
  process.exit(1);
});
