# 작업·점검 일지

규칙: 날짜 역순. 항목마다 `[심각도] 저장소 · 파일:줄 · 증거 · 상태`. P0 안전/크래시 · P1 동작 오류 · P2 문서/품질.
테스트 결과는 실행한 명령과 출력 요약만 적는다. 실행하지 않은 것은 통과라고 적지 않는다.

## 2026-08-30

### 한 일

| 저장소 | 커밋 | 내용 |
|---|---|---|
| OGTECH-llm | 이 푸시 | 시연 LLM 하네스 신설(`config/ harness/ eval/ runner/ tests/ results/`, 설계 `docs2/13_demo_harness_design.md`) · `product_voice.py`에 `DemoAssistant` 주입(구성 실패 시 기존 경로 폴백) · 정본 `keyword_rules.yaml` 수정(#25 refuse 9패턴 · #26 weather exclude · #27 yes 패턴 · daylight `해야` exclude) · 회귀 테스트 `tests/test_product_rules_regression.py` |
| OGTECH-backend | 이 푸시 | `config/keyword_rules.yaml` 정본과 바이트 동기화 |
| 5개 저장소 | 이 푸시 | 파일명 영문화 · SafeAid→OGTECH 명칭 · 환경변수 `SAFEAID_*`→`OGTECH_*` · `MAP/시연용`→`MAP/kiosk` · systemd `smartaid-*`→`ogtech-*`(별 세션 작업분을 커밋) |
| .github | 이 커밋 | File Architecture 하네스 항목, LLM 역할 설명(enum 2개·다듬기 off), 이 일지 |
| OGTECH-frontend | 오후 푸시 | Jetson 실동작 브리지 `MAP/kiosk/uart_server.py`(`$SA1` 파서 + `/api/telemetry`) 반입 · `STM32_JETSON_SETUP.md` 5절 앞에 실제 배치(40핀 ttyTHS0·nvgetty) callout과 문제 해결 2행 · 브랜치 `jetson/live-2026-08-30`에 Jetson `시연용/` 화면 스냅샷 |
| Jetson(kit-desktop) | 설정 | `nvgetty` 비활성화, `kit`→`dialout`. 재부팅 후 STM32 리셋 없이 `connected:true` |
| OGTECH-llm | `f289357` | Jetson 실측 하네스 결과 `results/`(intent/latency/demo/preflight) · 의도 프롬프트 v5(`config/system_prompt_ko.txt`·`fewshot_intent.jsonl`, 평가 문장과 겹치는 few-shot 0) · sherpa-onnx VITS 여성 TTS(`Co-LLM/scripts/engines.py SherpaOnnxTTS`, `config.py SHERPA_TTS_*`, 발화 속도 0.9배 = `SHERPA_TTS_LENGTH_SCALE=1.22`) · TTS 캐시 키에 음성 서명 포함(`tts_pipeline.py voice_signature`) · 고정 안내 클립 4개 재생성 · systemd 유닛 부팅 순서 수정 |
| OGTECH-frontend | `bcc110d` | Jetson 부팅 자동 UI: `MAP/jetson/ogtech-map.service`(`After=basic.target`)·`ogtech-kiosk.service`(`WantedBy=graphical-session.target`)·`start-kiosk.sh`(MAP 응답 대기·전용 Firefox 프로필)·`MAP/jetson/user/`(pulse 기본 장치 고정) · 고정 클립 4개(`MAP/kiosk/*.wav`) 새 음성 |
| OGTECH-embedded | `1e51a2a` | 정본 Core 모듈 정리 · `cubemx/`(README 빌드 절차 필수 산출물) 반입 · uart4_integration 검증 기록 |
| Vscode-Workspace-Setting | push 불가 | 로컬 `615fc20`(1 ahead) — GitHub 저장소가 아카이브(읽기 전용)라 push 403. 보관 해제 후 push 필요 |
| OGTECH-backend | 변경 없음 | 정본 규칙과 바이트 일치 유지 |
| .github | 이 커밋 | 조직 프로필: UI 캡처 01~08 재촬영(현행 `/video/` 화면) + 09 Jetson 실기기 화면, 깨진 한글 파일명 링크 13개 수정, 4️⃣ 음성 파이프라인·실측 상세, 5️⃣ 포트/브라우저 현행화(MAP :8790, Firefox kiosk `/product/`)·글랜서블 5개, 6️⃣ 전원 인가 즉시 기동 절 신설, File Architecture·STM32↔Jetson 링크 표(`$SA1` 실장 행) 갱신 · 이 일지 |
| Jetson(kit-desktop) | 설정 | `loginctl enable-linger kit` · sherpa-onnx 1.13.6 + `vits-mimic3-ko_KO-kss_low` · 스피커 믹서 0→85%(`alsactl store`) · pulse 기본 sink/source=USB · `~/.config/autostart/update-notifier.desktop`(Hidden) 로 키오스크 위 업데이트 팝업 차단 |

### 테스트 실행 결과 (2026-08-30, 이 머신)

| 저장소 | 명령 | 결과 |
|---|---|---|
| OGTECH-llm | `cd Co-LLM && python3 -B eval/run_eval.py` | 14라벨 280/280 · refuse 50/50 · 누출 0 |
| OGTECH-llm | `cd Co-LLM && python3 -B -m unittest discover -s tests` | 55 OK |
| OGTECH-llm | `python3 -B -m unittest discover -s tests` (하네스) | 54 OK |
| OGTECH-llm | `python3 -B eval/run_demo_script.py --llm none --runs 20` | 11턴 · 변형 66 · 20회 동일 · 정본 규칙 회귀 5건 OK |
| OGTECH-llm(Jetson) | `eval/run_intent_eval.py --llm http://127.0.0.1:8080/…` (프롬프트 5판) | 라벨 241/280 = **86.07%**(게이트 90% 미달) · refuse 모델 단독 38/50 · 오류 0 · max 0.875 s. 판별 이력 78.60 → 81.68 → 82.78(2판) → 78.18(3판 기각) → 82.86(4판) → 86.07(5판 채택). 4판→5판 보정: 물 정수·음용은 water, 야간 모드·화면 색은 gear, 텐트 칠 자리는 shelter, 불·난로는 sleep_safety 경계 명시 + few-shot 15→14(프리픽스 추정 1,398 ≤ 1,400) |
| OGTECH-llm(Jetson) | `eval/latency_bench.py --runs 20` (5판) | 프리픽스 1,549 tok · cold 워밍업 0.748 s · warm max 0.751 s / 중앙값 0.631 s · 예산 2.0 s 통과 · 오류 0 |
| OGTECH-llm(Jetson) | `eval/run_demo_script.py --llm http://127.0.0.1:8080/… --runs 20` | 11턴 · 변형 66 · 20회 동일 · 통과(정본 대사 LLM 호출 0) |
| OGTECH-llm(Jetson) | `cd Co-LLM && python3 -B -m unittest discover -s tests` | 63 OK (Python 3.8) |
| OGTECH-llm | `python3 -m pytest -q tests` (하네스, 5판 설정) | 54 passed |
| OGTECH-frontend | root 5 · `MAP` 87(부팅 모달 제거 반영) · `node MAP/tests/ui_product_qa.js`(Playwright, Chromium 1024×600) | OK · 모달 없음 · 터치 96 px · 야간 모드 · 브라우저 오류 0 |
| 전체 | `git diff --check` 5개 저장소 · 변경 .py 26개 py3.8 AST · `bash -n` start-*.sh · 좌표/비밀 grep | OK · 신규 좌표/비밀 없음 |
| Jetson | 부팅 자동 기동(17:46 콜드 부팅) | 5개 user 서비스 active · `/api/gps connected:true` · LLM `/health ok` · pulse 기본 장치 USB · 키오스크 `/product/` 모달 없이 표시 |
| OGTECH-llm | `--llm mock --runs 20` · `--mock-mode timeout/http500/garbage/empty` | 통과(고정 카드 폴백) |
| OGTECH-llm | `product_voice.py --text … --no-tts` × MAP `app.py --gps-mode replay` | 베이스캠프 저장 → 수원 탐색 → "그래 거기로 해 줘" 확정 → 일몰 361분/18:32 → 버너 카드 → 복귀 경로 → 체크포인트. 전부 규칙·오버레이, LLM 호출 0 |
| OGTECH-backend | `python3 -B -m unittest discover -s tests` | 9 OK |
| OGTECH-frontend | root 5 · `MAP` 80 | OK |
| OGTECH-embedded | `unittest` 11 · `tests/host/run_host_tests.sh` | OK |

llama-server·모델은 이 머신에 없다 — 하네스 LLM 경로는 mock으로 검증하고, 실측은 Jetson(위 4행)에서 돌려 `OGTECH-llm/results/`(intent_eval_jetson · latency_intent_jetson · demo_script_jetson · preflight.log)에 회수했다. 사람 육성 STT 검수·물리 버튼 이벤트는 여전히 `[미검증]`(펌웨어 버튼 이벤트 0건).

### 조치한 문제

- **Jetson 부팅 시 UI 자동 기동 안 됨(오후)** — 원인: user unit이 `After=default.target`+`WantedBy=default.target` → systemd 순서 순환 → 부팅 시 map/kiosk 잡 삭제(수동 start만 가능). 조치: map/llm-server `After=basic.target`, kiosk `WantedBy=graphical-session.target`+`PartOf`, linger, Firefox 전용 프로필(정전 후 복구 대화상자 차단), `start-kiosk.sh`가 MAP 응답 대기. 검증: 재부팅 3회(16:46·16:54·17:19) 모두 5개 user 서비스 active, `wmctrl` 창 제목 "OGTECH 오프라인 항법 — Firefox", `/api/gps connected:true`, LLM `/health ok`.
- **USB 스피커 무음** — 믹서 0% → 85%, `alsactl store`(재부팅 후 84% 유지). 키오스크 Firefox가 PulseAudio로 장치를 열면 직접 ALSA는 `Device or resource busy` → `audio.env`를 pulse + `PULSE_SINK/PULSE_SOURCE` 고정, `~/.config/pulse/default.pa`로 기본 장치 고정(재부팅 후 USB 유지).
- **TTS 속도 0.9배(사용자 청취 "너무 빨라 못 알아듣겠다")** — sherpa-onnx VITS `generate(speed=)`는 length_scale을 1/speed로 **대체**해서(A/B: ls1.1 기준 speed 1.0=2.23 s, 0.9=2.32 s, 0.8=2.54 s) `SHERPA_TTS_SPEED=0.9`로는 효과 없음. `SHERPA_TTS_LENGTH_SCALE 1.1→1.22`(=1.1/0.9)로 구현, speed 1.0 고정. 캐시 키에 sid/speed/ls/noise를 넣어 옛 클립 재생 차단(`test_cache_key_changes_when_voice_speed_changes`). 고정 클립 4개 재렌더(2.42/1.86/2.02/4.51 s), Jetson `tts_cache` 삭제·음성 서비스 재시작. 사람 청취 재확인은 `[미검증]`.
- **부팅 안내 모달 제거(사용자 지시, 17:33)** — `/product/`의 `#bootNotice`("이 장치는 구조 요청 수단이 아닙니다" + 자가진단 6항목 + 5초 ACK)를 `MAP/kiosk/index.html`에서 삭제, `live_app.js`는 모달이 없으면 잠금 없이 시작(`bootLocked:false`, `setupBootNotice/loadBootDiagnostics` 조기 반환). `/api/diagnostics`는 유지. 테스트 갱신: `MAP/tests/test_gps_api.py`(모달 부재 단언), `MAP/tests/ui_product_qa.js`(Playwright: 모달 없음·화면 inert 아님·터치 96px·야간 모드·브라우저 오류 0 통과, `product_boot_1024x600.png`). **주의: `.github/PLAN.md:412` "부팅 시 1회, 건너뛸 수 없게 표시" 원칙과 충돌 — 팀 결정 필요(되돌리려면 index.html 섹션 복원만 하면 됨).**
- **저장소 정리 조사(18:10, 삭제 없음)** — 6개 저장소 전수 조사 결과 코드·README·systemd·프로필이 참조하지 않는 추적 파일은 16개(≈0.5 MB: `OGTECH-llm/results/*mock*`·`rules_only` 8, `Co-LLM/docs/08_05_log.txt`·`00_OUTLINE.md`, `MAP/SCRIPT_REVIEW.md`·`MAP_USAGE.md`·`kiosk/VIDEO_DEMO_2026-08-09.md`·`auto_demo_ssh.sh`·`daylight_detail.wav`)뿐이며 팀 결정으로 **유지**. `docs2/*`·`Co-LLM/0*.md`·`sample_data`·`TEST_images`·`video.html`·`uart4_integration/`은 참조되는 필수 자산. 로컬 `__pycache__`·`MAP/runtime/*`(gitignore 생성물)만 삭제. 저장소 밖 루트 `1~20.png`(9.7 MB)·`slides/` 타팀 PDF(6.5 MB)·`_workspace/`는 git이 아니라 손대지 않음.
- **키오스크 위 Ubuntu "Update-notifier" 팝업** — `~/.config/autostart/update-notifier.desktop`(`Hidden=true`)로 차단, 프로세스 종료.
- **세션 충돌** — 같은 작업 트리에서 Claude 세션 2개가 동시에 `OGTECH-llm` 프롬프트·TTS·Jetson을 편집(16:41~16:54, Jetson 재부팅 2회 포함). 파일 md5·타임스탬프로 분리 확인 후 한 세션이 이어받음. 재발 방지: 한 저장소는 한 세션만.

- #25 → refuse 9패턴: 음절 `약` 제거(`약물|약품|알약|진통제…`, `(^|\s)약…(몇 알|몇 개|몇 정|복용|먹|용량)`), 야생동물 패턴에서 `독|괜찮` 제거. "일몰까지 약 몇 분 남았어"→daylight/status, "약수터까지 얼마나 걸려"→route/status, "야생동물이 근처에 있는데 괜찮아"→wildlife. refuse 50/50 유지.
- #26 → weather status 규칙 `exclude_patterns [저체온, 떨려, 한기, 체온]`. "지금 너무 추워 저체온증 같아"→warmth, "지금 여기 온도 얼마야"→weather/status 유지.
- #27 → yes 패턴 `(설정|지정).*(해|진행)`을 `^(설정|지정) ?(해|진행)`으로 한정하고 짧은 긍정 접두 패턴(`^(네|응|그래…)[,. ]+(그렇게|거기로…)?(설정|지정|진행)?(해|해 줘…)?$`) 추가. pending 중 "야영지는 여기로 설정해 줘"→save_basecamp, "네 설정해 줘"·"설정 진행해"→confirm.
- 신규 → daylight scenario 규칙 `exclude_patterns ["해야 ?(하|되|돼|할|해|지)"]`. "지금 뭘 해야 하지"가 daylight 카드로 가던 오탐 제거.
- 신규(#38 원인) → **Jetson이 STM32 센서값을 못 받음(오후)**: JetPack 기본 `nvgetty.service`가 부팅 시 STM32 UART(`/dev/ttyTHS0`)에 `getty -L 115200`을 띄워 프레임을 로그인 입력으로 소비하고 프롬프트·에코 1,172 B를 STM32로 송신(저널 `session closed for user $SA1,62,…`) → STM32 송출 정지 → 브리지 24분 0프레임(UART 오버런 70,349회) → 화면 OFFLINE. 조치: `systemctl disable --now nvgetty` + `usermod -aG dialout kit`. 재부팅 검증: 저널 getty 0건, 오류 카운터 0, STM32 리셋 없이 즉시 `connected:true`. 기각한 가설: `$OGT1`/`$SA1` 접두어 불일치(보드는 `$SA1` 송출, 브리지 파서 6/6 수락) · 권한 · Jetson UART wedge(open/close·보율 hopping 재현 불가) · 포트 open에 의한 리셋(uptime 연속).

### 발견한 문제 — 미조치

35. [P2→조치] LLM 프리픽스 cold prefill > 타임아웃 2.0 s — `ogtech-llm-server.service` `ExecStartPost` 워밍업(`results/preflight.log`) 배치. Jetson 실측: 프리픽스 1,490 tok, cold 0.829 s, warm max 0.706 s(`eval/latency_bench.py --runs 20`, 오류 0). 부팅 후 첫 질문도 warm 경로.
36. [P2] 시연 프로필 `polish.mode=off` 고정 — `--parallel 1`에서 다듬기 호출이 intent 프리픽스 KV 캐시를 지운다. shadow/speak로 켜려면 `config/llama_server.args`를 `--parallel 2 -c 4096`으로.
37. [P2→조치] Jetson 사본을 `/home/kit/ogtech/OGTECH-{frontend,llm}`로 재배치해 개명(`kiosk/`, `OGTECH_*`, `ogtech-*.service`)을 반영했다(오후). 키오스크는 저장소 `MAP/kiosk/index.html`(`/product/`)을 띄운다.
38. [P1] 보드에 올라간 펌웨어의 소스가 어느 저장소에도 없다 — 40핀 `ttyTHS0`로 `$SA1,seq,uptime_ms,dht_valid,temp_x10,hum_x10,co_state,co_ppm,gps_state,lat_e7,lon_e7,sats*XOR`(1 Hz) 송출, `PING`/`STATUS` 무응답(콘솔 명령 없음). 정본 `Core/`(JSONL v1)도 `uart4_integration`(`$OGT1`)도 아님. 소스 커밋 필요.
39. [P1] 저장소 `MAP/kiosk/`와 Jetson `시연용/`이 분기 — Jetson본(팀원 08-29 20:45, 원본은 `시연용.before_uart/`)은 글랜서블을 TEMPERATURE/HUMIDITY/CO + LIVE/OFFLINE 배지로 바꾸고 `/api/telemetry`를 1초 폴링, 저장소본은 `glanceEnv`·`/product/` SSE 설계. #37 개명 반영 때 저장소본으로 덮으면 실측 표시·브리지가 사라진다. 스냅샷: frontend 브랜치 `jetson/live-2026-08-30`. 정본 결정 필요.
40. [P2] `STM32_JETSON_SETUP.md` 5~8절·`jetson/start-map.sh`(`--gps-mode stm32`, `/dev/ttyACM0`)·`gps_service` JSONL 파서는 실제 배치(40핀·`$SA1`·`uart_server.py`)와 불일치 — 5절 앞 callout만 추가함. 브리지는 수동 실행(systemd 유닛·무프레임 재오픈 워치독 없음). 본문 정리는 #38·#39 결정 후.
41. [P1] **Jetson HDMI no signal(오후 16:54 이후)** — 커널이 드라이버 probe 시점부터 `tegradc 15200000.display: dc_poll_register 0x41: timeout / dc timeout waiting for DC to stop / tegra_nvdisp_head_enable, failed head enable`을 반복. EDID(MPI7002, 1024x600 선호)·HPD는 정상, X는 1024x600으로 렌더(스크린샷 정상)하므로 케이블·모니터·X가 아니라 Tegra 디스플레이 컨트롤러가 레지스터에 응답하지 않는 상태. 시도해 실패: `xrandr --off/--auto`, 1920x1080 모드 전환, 모니터 전원·HDMI 재연결, 보드 콜드 부팅(17:19, 부팅 직후 동일 실패). 17:27 부팅에서 **다른 HDMI 모니터(3840x2160, 600×340 mm)로 바꾸자 헤드 활성화 실패 0건, 화면 정상** → 보드가 아니라 7인치 패널(MPI7002, 1024x600@49 MHz 선호 모드)과의 조합 문제. 미시도: 그 패널을 다른 케이블/DP로, 패널 EDID 무시하고 1920x1080 강제(`xorg.conf` `UseEDID`, sudo 필요), 저널 영속화(현재 `journalctl`은 이번 부팅만 남음). 키오스크·음성·API는 어느 경우에도 정상 동작.
42. [P1→원인 확정] **배터리 전압 저하 시 Jetson이 LLM 부하 직후 정지** — 17:05 이후 부팅 4회(17:17·17:21·17:31·17:40) 모두 llama-server 모델 적재/평가 시작 1~4분 내 SSH·ping 동시 두절, 재기동은 사용자 전원 투입. 당시 Jetson은 배터리 팩(사용자 전압계 12.8→11.8 V)에 물려 있었고 같은 부하가 12.8 V대(16:24~17:10, 평가 4회)에서는 문제 없음 → 부하 순간 전압 강하로 BMS/보드 저전압 차단. 사용자 확인: 배터리 문제 맞음. 17:46 부팅부터 Jetson 전용 어댑터로 교체 → 3분 무부하 + 평가 부하에서 생존(아래 평가 행 참조). 커널 저널이 휘발성이라 직전 기록은 없음. 남은 조치: (a) 배터리 전압을 STM32 텔레메트리(`$SA1`)에 추가해 BATTERY 칸 표시, (b) 임계 전압 이하에서 LLM 호출을 막고 규칙·고정 카드만 쓰는 저전력 모드(`harness/device_state.py` 게이트), (c) 저널 영속화(`/var/log/journal`, sudo). 시연은 만충 배터리 또는 어댑터로. **추가(18:00)**: 어댑터 전원·4K 모니터 상태의 실화면에 Ubuntu 알림 "System throttled due to Over-current."가 떠 있음(nvpmodel 15W 6코어) — 어댑터에서도 전류 여유가 없어 스로틀링 중. 정격 어댑터 확인 필요.

## 2026-08-29

### 한 일

| 저장소 | 커밋 | 내용 |
|---|---|---|
| OGTECH-frontend | `7618bdc` | 키오스크 화면(`MAP/kiosk/video.html`) 경로 이탈 경고 배너 추가(30 m 임계, `D` 키 시연), CO 칸 `CO 전용 · DEMO` 문구 삭제, 온도·습도 20px 동일 크기, 온도 색 규칙(30°C 초과 적색 / 20~30°C 황색 / 20°C 이하 녹색), 습도 하늘색. Playwright QA `MAP/tests/ui_video_qa.js` 신규 |
| OGTECH-embedded | `fb63e30` (팀 커밋, pull) | `uart4_integration/`(sensor_hub · jetson_link · CubeMX main/it/msp) + `jetson/uart_receiver.py` + `tests/test_uart_receiver.py` 확인 |
| .github | 이 커밋 | 조직 프로필 File Architecture 재작성(두 Jetson 링크 병기), 이 일지 신설 |

### 테스트 실행 결과 (2026-08-29, 이 머신)

| 저장소 | 명령 | 결과 |
|---|---|---|
| OGTECH-embedded | `tests/host/run_host_tests.sh` | test_protocol · test_firmware_sim all passed |
| OGTECH-embedded | `python3 -B -m unittest discover -s tests` | 11 OK (protocol_contract 8 + uart_receiver 3) |
| OGTECH-backend | `python3 -B -m unittest discover -s tests` | 9 OK |
| OGTECH-llm | `cd Co-LLM && python3 -B -m unittest discover -s tests` | 55 OK |
| OGTECH-frontend | `python3 -B -m unittest discover -s tests` | 5 OK |
| OGTECH-frontend | `cd MAP && python3 -B -m unittest discover -s tests` | 80 OK |
| OGTECH-frontend | `node MAP/tests/ui_video_qa.js` (Playwright 1.57, Chromium 143) | OK — 온습도 20px 동일, 색 규칙 6단계, 경로 이탈 배너 표시/해제/일조 경고 스택 |

CubeIDE 실빌드·실장 검증은 이 머신에서 불가. `uart4_integration/README.md`의 "0 errors, 0 warnings"는 팀원 보고값.

### 발견한 문제 — 미조치 (팀 결정 필요)

리뷰 에이전트 3개(임베디드 6,387줄 · 프런트 6,779줄 · Co-LLM/backend 5,532줄 재독) 보고 중 소스에서 직접 재확인하거나 재현한 것만 적었다. 항목 1~15는 임베디드·문서, 16~ 은 프런트엔드·Co-LLM.

**P1 — 동작 오류**

1. **STM32↔Jetson 프로토콜이 둘로 갈라짐** — OGTECH-embedded
   - 정본 `Core/Src/telemetry_protocol.c`: USART3 · JSONL + CRC-16/CCITT · 2 s. 수신 = `OGTECH-frontend/MAP/gps_service.py` → `/api/device` → 키오스크 화면.
   - 신규 `uart4_integration/Core/Src/jetson_link.c`: UART4(PC10/PC11) · `$OGT1,…*XOR` CSV · 1 s. 수신 = `OGTECH-embedded/jetson/uart_receiver.py` → JSON stdout(화면 연동 없음).
   - 증거: `$OGT1,7,12345,1,234,567,1,12,2,375465126,1270757141,9*78`(정상 프레임)을 `gps_service.parse_stm32_telemetry`에 넣으면 `GpsInputError: STM32 응답이 JSON이 아닙니다`. `uart4_integration/README.md:4-5`도 비호환을 명시. 포트도 `/dev/ttyACM0`(ST-LINK VCP, `STM32_JETSON_SETUP.md:250,325`) vs `/dev/ttyTHS*`로 다름.
   - 영향: `uart4_integration` 펌웨어를 플래시하면 키오스크 `/api/device`에 센서 값이 전혀 올라오지 않는다.
   - 선택지: (a) `uart4_integration`에 `telemetry_protocol.c`(JSONL+CRC16)를 이식하고 Jetson 링크만 USART3→UART4로 옮긴다(`gps_service`는 포트만 변경) / (b) `gps_service.py`에 `$OGT1` 파서 추가(단 `$OGT1`에는 CO 경보 레벨·게이트 상태·명령 채널이 없어 화면 기능이 줄어든다). (a) 권장.
2. **`uart4_integration`에 상시 안전 기능이 없고 PC9가 미설정** — OGTECH-embedded
   - `sensor_hub.c`(658줄)·`main.c`에 CO 경보 판정·부저(PB0)·Jetson 전원 게이트(PC9)·콘솔 명령(`GATE`/`ALERT TRAIL`/`POWER OFF`) 없음(`grep -ci "PB0|PC9|alarm|buzzer|gate"` = 0). `MX_GPIO_Init`은 PA0(DHT11)만 설정 → PC9 MOSFET 게이트가 리셋 상태(부유)로 남아 Jetson 전원이 불확정.
   - 이 스냅샷을 실장 펌웨어로 쓰면 "Jetson이 꺼져 있어도 CO 경보"라는 작품 핵심 주장이 성립하지 않는다. 1번 (a) 경로로 정본 모듈 위에 UART4 링크만 얹는 편이 안전하다.
3. **정본 `Core/Src/air530_gps.c:80-84` 체크섬 없는 NMEA 문장을 수용** — `if (star == NULL) { /* accept a sentence with no checksum field */ return 1u; }`. `README.md:51` "무체크섬 문장 거부"·`:205` "체크섬을 검증한 문장만 받아들입니다"와 반대. 노이즈로 `*` 1바이트가 깨지면 손상된 좌표가 검증 없이 fix로 채택될 수 있다. `uart4_integration/sensor_hub.c:305`는 거부하므로 두 계열 동작도 다르다. 코드를 문서대로(거부) 고치고 호스트 테스트 추가.
4. **DWT CYCCNT 잠금 해제 없음 → DHT11 판독 무한 스핀 가능 `[미검증]`** — 정본 `Core/Src/dht11.c:54-58`은 `DEMCR.TRCENA`·`CYCCNTENA`만 켜고 `DWT->LAR = 0xC5ACCE55`가 없다. `delay_us`(:5-14)와 `DHT11_WaitWhile`(:38-52)의 타임아웃이 모두 CYCCNT에만 의존하므로 카운터가 돌지 않으면 첫 `DHT11_Read`(부팅 2 s 후)에서 영구 정지 → `CoAlarm_Update`·부저까지 멈춘다. `uart4_integration/sensor_hub.c:583-585`는 `#if defined(DWT_LAR)`로 조건부 해제하지만 CMSIS에는 그 매크로가 없어(구조체 멤버 `DWT->LAR`만 존재) 빠질 가능성이 크다. Cortex-M7 실보드에서 확인 필요. 최소한 `HAL_GetTick` 기반 2차 상한을 둔다.

**P2 — 문서/품질**

5. `uart4_integration/README.md:39-51`이 존재하지 않는 `drop_in/` 폴더를 가리킨다 — 실제 경로는 `uart4_integration/Core/Inc`·`Core/Src`.
6. Jetson 포트 기본값 불일치 — `jetson/uart_receiver.py:143`·`uart4_integration/README.md:91` `/dev/ttyTHS1` vs `VERIFICATION.md:116,133`·`jetson/README.md:9` `/dev/ttyTHS0`. 장치명은 보드에서 확정하되 문서 기본값은 하나로.
7. PA0 핀 충돌 예약 — `uart4_integration/Core/Inc/main.h:60` `DHT11_DATA_Pin = PA0` vs `OGTECH-frontend/MAP/README.md:92` 물리 버튼 계약 `PA0=power`(호스트 mock `tests/host/main.h:103`은 PA4). 버튼 미구현이라 지금은 무해, 버튼 실장 시 핀 재배치.
8. DHT11 판독 중 인터럽트 차단 문서-코드 불일치 — `OGTECH-embedded/README.md:50` "(판독 구간 인터럽트 차단)"이나 `Core/Src/dht11.c`에 `__disable_irq`/PRIMASK 없음(저장소 전체에서 `main.c:301` Error_Handler만). `sensor_hub.c` `DhtRead`도 동일. UART×3+SysTick ISR이 26/70 µs 비트 판별에 끼어들 수 있다(단일 ISR은 허용 오차 내, 중첩 시 체크섬 실패 증가 — 오값은 아님). 문서를 고치거나 판독 구간만 마스킹.
9. `OGTECH-embedded/README.md:166-173` 빌드 절차에 USART1/2/3 NVIC 활성화 단계가 없다 — 정본은 세 UART 모두 `HAL_UART_Receive_IT`인데 `uart4_integration/README.md:21-24`가 확인한 실제 `.ioc`는 USART1/2 global interrupt Disabled. 절차대로 만들면 GPS·CO·콘솔 수신이 무음(오류도 없음).
10. 정본 `Core/Src/console.c:5` 수신 링 32 B(실효 31 B) + `sensor_app.c:225-234` 텔레메트리(~330자, 115200에서 ≈29 ms) 블로킹 송신 — 송신 중 Jetson 명령이 31 B를 넘으면 `Console_RingPush`(:18-27)가 바이트를 버려 줄이 깨진 채 조립될 수 있다. 링을 128 B 이상으로 키우거나 넘침 시 해당 줄 폐기.
11. `uart4_integration/Core/Src/sensor_hub.c:538-539` 디버그 출력 음수 온도 표기 — `T=%d.%dC`에 `temperature_x10/10`·`abs(%10)`을 넣어 -0.1~-0.9°C가 `0.x`로 찍힌다. USART3 사람용 출력 한정.
12. `uart4_integration/Core/Src/sensor_hub.c:648,654` ErrorCallback(ISR)이 non-volatile 메인루프 변수 `gps_line_length`/`co_frame_length`를 0으로 쓴다 — 오버플로는 없으나 리셋이 유실되거나 최적화로 캐시될 수 있다. 플래그로 넘기거나 제거.
13. 두 계열을 한 프로젝트에 같이 넣으면 링크 오류 — `Core/Src/main.c:265,270`과 `sensor_hub.c:630,644`가 `HAL_UART_RxCpltCallback`/`HAL_UART_ErrorCallback`을 중복 정의. 루트 README:171 "모듈 전부"와 `uart4_integration/README.md:41-45` "복사"를 동시에 따르면 발생. 어느 한쪽만 쓴다고 문서에 명시.
14. DEMO 배지 문구·캡처 정합 — 2026-08-29 키오스크 `video.html` CO 칸 `DEMO` 문구를 팀 지시로 제거. `.github/profile/README.md` 안전 경계 "모의 값이 하나라도 섞이면 화면의 DEMO 배지를 숨기지 않습니다"와 조직 프로필 `assets/01~08`·`OGTECH-frontend/MAP/TEST_images/` 8장은 제거 전 화면. `/product/`(`index.html`) 화면의 `DEMO` 태그는 유지 중. 캡처 재촬영·문구 범위 조정 필요(frontend README는 갱신함).
15. 운영 규칙 vs 실제 — `docs/GITHUB_OPERATIONS.md:24` "main에 직접 push하지 않는다"와 달리 최근 커밋(이 세션 포함)은 전부 main 직접 푸시. 규칙을 현행화하거나 브랜치·PR로 되돌릴지 결정.

**검토했으나 결함 아님(임베디드)** — 링 버퍼 head/tail `volatile uint16_t` SPSC로 M7에서 원자적 · tick 산술 전부 `(uint32_t)(now - x)`로 wrap 안전 · ZE16B 프레임/체크섬(`~sum+1`) 정확 · NMEA ddmm.mmmm→E7 변환·S/W 부호·60분 검사 정확(두 계열 동일) · snprintf 서식/캐스트 일치 · `JetsonLink_Send` 페이로드 최대 ≈85 B < 192 B · v1 CRC 범위가 `gps_service.crc16_ccitt`와 일치(계약 테스트 통과) · ORE 후 ErrorCallback 재무장 정상.

**OGTECH-frontend**

16. **[P1] keep-alive 연결에서 두 번째 요청부터 오류 응답이 사라짐** — `MAP/app.py:850-858` `end_headers()`가 `_response_started=True`를 세우고 어디서도 리셋하지 않는데 `BaseHTTPRequestHandler`는 HTTP/1.1 keep-alive에서 같은 핸들러로 요청을 반복 처리 → 첫 정상 응답 뒤 오류가 나면 `_error_json`이 조용히 `return`. 재현(2026-08-29, `app.py --gps-mode off`): 같은 소켓에서 `GET /api/health`(200) 후 `POST /api/waypoints {"action":"save_current","kind":"checkpoint"}`(fix 없음) → **3초 내 무응답**, 새 소켓이면 422. Chromium은 연결을 재사용하므로 `/product/`에서 fix 없이 체크포인트·베이스캠프 버튼을 누르면 토스트 없이 멈춘다. 테스트는 요청마다 새 연결을 열어 못 잡음. 수정: `do_GET/do_POST` 진입 시 `_response_started=False`.
17. [P2] `MAP/app.py:186-205` `/api/route` 응답의 `on_trail/offset_m`가 `result.start_snap_m`(최근접 **노드** 거리)을 쓴다 — `/api/device`(선분 거리)와 다르고 `MAP/README.md:31` "노드가 아니라 선분"과 모순. 긴 직선 보행로 중간에서 offset 과장.
18. [P2] `MAP/map_engine.py:751` `write_runtime` 임시 파일명이 고정(`active_map.json.tmp`) — `/api/maps/import` 2건이 겹치면 두 번째가 첫 번째 tmp를 덮어 `replace` 실패 또는 부분 기록 가능.
19. [P2] `server.py:151-157` `Content-Length: -1`이 상한 검사를 통과하고 `rfile.read(-1)`이 EOF까지 블록 → 클라이언트가 안 닫으면 핸들러 스레드 영구 대기(127.0.0.1 바인딩이라 P2). `length < 0`이면 400.
20. [P2] `README.md`의 `python server.py --root MAP/static --index index.html` 안내 — `static/app.js`는 `/api/map`·`/api/maps/import`·`/api/gps/*`를 호출하는데 `server.py:90-98`는 `/backend/*`만 프록시 → 전부 404. 개발자 도구 화면은 `MAP/app.py`(:8790)로 띄워야 한다.
21. [P2] 루트 `README.md` 구성 절은 `video.html`을 "최신 화면", `index.html·live_app.js`를 "이전 세대"로 적지만 `MAP/jetson/start-kiosk.sh:19`는 `/product/`(live_app.js)를 띄우고 `MAP/README.md:11-19`는 `/video/`를 "촬영용 합성 DEMO"로 정의. 어느 화면이 제품 화면인지 문서 통일 필요(실센서 SSE를 받는 쪽은 `/product/`).
22. [P2] `MAP/README.md:96`·`STM32_JETSON_SETUP.md:513` "`systemctl poweroff --no-block`" vs `MAP/jetson/power_control.py:153` `[systemctl, "poweroff"]` — 문서와 코드 불일치(동작은 logind 경유로 동일).
23. [P2] 루트 `README.md` "예상 도착 시각… 최근 보행 속도로 역산" — 실제는 상수(`navigation_service.py:45` `NAVIGATION_SPEED_MPS` 기본 1.0, `video_app.js` `speedMps: 1.4`). **08-29 README 문구 정정함**(해소된 "남은 실패 1건" 문구도 삭제).

**OGTECH-llm / OGTECH-backend**

24. **[P0] `Co-LLM/eval/run_video_scenario.py:44-51` 문자열 리터럴 안에 실제 개행 → `SyntaxError`, 하네스 실행 불가** — `python3 -m py_compile`로 재현("unterminated string literal (detected at line 44)"). `eval/results/video_scenario_20.json`(20/20)과 `docs2/11:440`·`docs2/12:15,126`이 이를 `[실측: 2026-08-19]` 근거로 인용하므로 현재 코드로는 재현 불가한 증거. 개행을 `\n`으로 고치고 재실행해 결과 파일 갱신.
25. **[P1] refuse 정규식 과매칭** — `config/keyword_rules.yaml:8` `(약|진통제|…).*(복용|먹|용량|몇|얼마)`의 `약`이 음절 단위로 걸린다. 재현(`RuleRouter().decide`): "약수터까지 얼마나 걸려"→`refuse`, "일몰까지 약 몇 분 남았어"→`refuse`(daylight여야 함). `keyword_rules.yaml:4` `(…풀|…야생동물|…).*(먹|식용|섭취|독|괜찮)`: "야생동물이 근처에 있는데 괜찮아"·"풀숲에서 독사 봤어"·"배낭 풀고 쉬어도 괜찮아"→`refuse`(wildlife/gear여야 함). refuse가 최우선(`ogtech_core.py:124`)이라 복구 경로 없음 — 야생동물 위험 상황에 약물·버섯 거부 카드가 나간다. 단어 경계·전용 명사(약물/약품)로 좁히고 회귀 케이스 추가. **→ 2026-08-30 조치(위 08-30 항목).**
26. [P1] `ogtech_core.py:152-163` map_rules가 scenario_rules보다 먼저 적용 + `keyword_rules.yaml:157` `(지금|현재|여기).*(춥|추워…)` → "지금 너무 추워 저체온증 같아"→`weather/status`(센서 온도 카드). 생명 B 카드 warmth에 도달 불가("너무 추워서 계속 떨려"는 warmth로 통과해 테스트가 못 잡음). **→ 2026-08-30 조치.**
27. [P1] `keyword_rules.yaml:16` yes 패턴 `(설정|지정).*(해|진행)` + 확인 판정이 map_rules보다 앞(`ogtech_core.py:127-135`) → 수원 POI 확인 대기 중 "야영지는 여기로 설정해 줘"(voice_cases 기대 `save_basecamp`)·"야간 모드 설정해"가 `confirm_destination`으로 판정(재현: `decide(text, pending_confirmation=True)`). 수원이 목적지로 확정되고 베이스캠프는 저장 안 됨. **→ 2026-08-30 조치.**
28. [P1] `scripts/device_monitor.py:157-173` except가 URLError/TimeoutError/JSONDecodeError/MapApiError뿐 — `E.play()`(`engines.py:119`, `check=True`)의 CalledProcessError, `exclusive_pipeline()` 30초 초과 RuntimeError(`pipeline_gate.py`), ppm 변환 ValueError가 데몬을 죽이고 `ogtech-device-monitor.service:14` `Restart=on-failure`(2 s)로 재시작 → 새 `AlertDetector`가 활성 경보를 매번 재발화(크래시-재발화 루프). 트리거: CO 경보 재생 중 스피커 탈락.
29. [P1] `scripts/engines.py:113,119,208,314,337` arecord/aplay/whisper-cli/espeak/piper `subprocess.run`에 `timeout` 없음 — 타임아웃은 LLM 2.0 s·MAP 2.0 s뿐. `docs2/11:88` "경로 A/B와 타임아웃을 코드로 강제"와 불일치. whisper/aplay가 멈추면 physical_voice가 락을 쥔 채 영구 대기 → 28번 루프.
30. [P1] `scripts/physical_voice.py:157-158` 버튼 *press* 시점에 `exclusive_pipeline().__enter__()` — device_monitor가 경보를 재생 중이면 최대 30 s 락 대기 뒤에야 arecord 시작. 사용자는 press 직후부터 말하므로 발화 앞부분/전체가 녹음에서 빠진다. 트리거: 경보 음성 중 버튼 누르기.
31. [P2] `physical_voice.py:78-90` `stop()`: SIGINT→3 s→terminate→2 s 뒤 `kill()` 없음 → arecord 고아가 마이크 점유 가능. `product_voice.py:140` `Queue(maxsize=2)`: 소비 루프가 예외로 빠지면 생산 스레드가 `put`에서 영구 블록. `engines.py:292,383` `make_stt/make_tts`의 `SystemExit`을 `tts_pipeline.py:288`(`Exception`만)이 못 잡아 엔진 이름 오타가 첫 발화 시점에 데몬 종료. `engines.py:420-425` `classify_scenario` except에 TypeError(`content: null`)·IncompleteRead 없음(RuleRouter.resolve가 막아 누출은 없으나 `invalid_llm_label`로 위장).
32. [P2] `device_monitor.py:46` 데모 접두 "데모 값 기준으로, "가 붙은 도착 문장이 `config/fixed_audio.json:5` 키("목적지에 도착하였습니다.")와 달라 데모 지도에서는 고정 WAV 대신 합성 TTS로 감. `run_video_scenario.py`는 접두 없는 문장을 검사해 통과.
33. [P2] 경로·사용자 가정 불일치 — `jetson/*.service` `User=safeaid`·`/opt/ogtech/Co-LLM/.venv` vs `config.py:64-90` 기본 모델 경로 `~/ogtech_ai/…`(→`/home/safeaid`), 설치 문서는 개발 사용자 홈. 락·캐시는 코드 트리 `scripts/test_rec`에 두어 `safeaid` 쓰기 권한 필요. 트리거: 첫 버튼 press마다 "whisper.cpp 바이너리가 없습니다"/PermissionError.
34. [P2] `tts_pipeline.py:98-107,139-146` 합성 WAV마다 순수 파이썬 샘플 루프(inspect 2회+normalize) — MeloTTS 44.1 kHz×5 s ≈ 22만 샘플×5패스가 Xavier CPU에서 경로 B 2.0 s 예산을 잠식(numpy 이미 의존성).

**교차 확인 결과 문제 없음(프런트·LLM)** — live_app.js/static/app.js ↔ app.py 엔드포인트·필드명 일치 · solar 공식 정확 · SSE 큐 drop·락 순서 교착 없음 · `_serve_static` 경로 탈출 차단 · backend `core/`·`config/` 3파일이 Co-LLM 정본과 바이트 일치 · 14 라벨 집합(코드·카드·yaml)과 카드 path↔LIFE_PATH_B 일치 · LLM 출력이 TTS에 닿는 경로 없음(라벨만 검증 후 카드 선택).
