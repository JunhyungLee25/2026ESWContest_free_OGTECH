# 다이어그램 원본

프로필의 구조 다이어그램 원본은 이 폴더에 있습니다.
PNG는 표시용이며, 상위 `assets/`에 같은 이름으로 있습니다.

| 원본 | 형식 | 굽는 법 |
| --- | --- | --- |
| `d0_system_overview.drawio.xml` | draw.io | draw.io에서 열고 File → Export as → PNG (Zoom 200%, Border 12) |
| `d1`~`d4` | SVG | 아래 headless 캡처 |

**PNG를 쓰는 이유** — GitHub 조직 프로필 페이지는 mermaid를 렌더링하지 않습니다.
2026-08-20 확인 결과 mermaid 블록이 원본 코드로 그대로 노출됐습니다. 그래서 이미지로 굽습니다.

## 고치는 법 (d1~d4)

SVG를 직접 편집한 뒤 2배 배율로 다시 굽습니다.
SVG를 브라우저로 직접 열면 body 여백 8px 때문에 아래가 잘린다. 여백 0 HTML로 감싸서 굽는다.

```bash
f=d1_dual_power_layers
{ echo '<style>html,body{margin:0;padding:0;background:#fff}svg{display:block}</style>'; cat $f.svg; } > /tmp/$f.html
google-chrome --headless=new --disable-gpu --hide-scrollbars \
  --force-device-scale-factor=2 --window-size=<W>,<H> --virtual-time-budget=3000 \
  --screenshot=$f.png "file:///tmp/$f.html"
```

`W`·`H`는 각 SVG의 `viewBox` 값을 씁니다. 900×470 / 900×430 / 900×330 / 900×420 순입니다.

`d0`은 SVG가 아니라 draw.io 파일입니다. 편집 후 위 표대로 PNG를 다시 내보내면 됩니다.
같은 도식의 보고서용 사본(하단 범례 포함)은 `slides/시스템전체구성도_간소화.xml`에 있습니다.

## 색 규율

배경 흰색(라이트 테마). 흰 바탕에서 읽히도록 제품 UI 색을 어둡게 조정한 값을 쓴다.

| 색 | 값 | 제품 UI 원색 | 의미 |
|---|---|---|---|
| 적색 | `#d92c2c` | `#ff5b5b` | 경고 — 즉시 행동 |
| 앰버 | `#b06f00` | `#f2a900` | 주의 — 미검증 · 성능저하 |
| 녹색 | `#128a63` | `#57d9a3` | 실제 센서로 확인됨 |
| 시안 | `#0d7f8f` | `#4dd8e6` | 계측 판독값 |
| 회색 | `#5f6f6f` | `#8b9a9a` | 데이터 없음 |

| 구조 색 | 값 |
|---|---|
| 배경 · 박스 채움 | `#ffffff` |
| 흐린(비활성) 박스 채움 | `#f4f7f7` |
| 제목 아래 구분선 | `#d7e0e0` |
| 본문 텍스트 | `#14201f` |
