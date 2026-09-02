# 13. 시연용 LLM 하네스 — 설계·계획 (2026-08-30)

상태: **검토용 초안.** 코드는 `config/` `harness/` `eval/` `runner/` `tests/`에 새 파일로만 추가했다. 기존 `Co-LLM/` 파이프라인·`keyword_rules.yaml`·`survival_cards.json`·backend 사본은 건드리지 않았다. 주입(§8)은 검토 승인 뒤에 한다.
우선순위: **시연 대사가 연속 20회 동일하게 동작** > 상용 일반화. 동결 계약(`Co-LLM/docs/00_frozen_decisions.md` §3·§4·§5)은 그대로 지킨다.

---

## 0. 한 줄 결론

LLM은 시연 대사의 **주 경로가 아니라 안전망**이다. 시연 대사 11개는 전부 규칙(0 ms)으로 확정되게 고정하고, LLM은 (1) 규칙이 놓친 발화의 **의도(라벨+장치 동작 enum)** 분류, (2) 모델 사망 시 **고정 카드 폴백** 두 장면에서만 개입한다. 카드 문장 다듬기(역할 3)는 구현하되 시연 프로필에서는 **off**로 둔다(2026-08-30 결정 — `--parallel 1`에서는 다듬기 호출이 intent 프리픽스 KV 캐시를 지워 다음 질문이 콜드가 된다. shadow/speak는 `--parallel 2 -c 4096`에서만).

---

## 1. 현재 상태 진단 (코드 기준)

| 항목 | 현재 | 근거 |
|---|---|---|
| LLM 호출 | `classify_scenario()` 1개. 시스템 프롬프트 3문장(`Co-LLM/config.py` `CLASSIFIER_SYSTEM`), 스키마 `{"scenario_id": enum14}`, 240자 절단, 2.0 s, 재시도 없음 | `Co-LLM/scripts/engines.py:395` |
| 호출 조건 | 규칙 무매칭(`no_rule_match`) 또는 비생명 다중 매칭(`ambiguous`)일 때만 | `ogtech_core.py` `RuleRouter.decide/resolve` |
| LLM 출력의 쓰임 | 라벨 1개 → 카드 선택. **map_action은 절대 안 나옴** → LLM 경로로 가면 지도 명령 불가 | `RuleRouter.resolve` 반환 `map_action=None` |
| 역할 2(대상 추출)·3(카드 다듬기) | 계약만 있고 미구현 | `00_frozen_decisions.md` §4 정합화 주석 |
| `config/ harness/ eval/ runner/` | `.gitkeep`·README만 | 조직 프로필 File Architecture "하네스 자리(비어 있음)" |
| 프롬프트·스키마·few-shot·워밍업·지연 측정 | 없음 | — |

**시연에 실제로 위험한 지점 3개**

1. **STT 오인식 변형이 규칙을 놓치면 LLM이 라벨만 내고 지도 명령이 없다.** 예: "야간 모드로 바꿔 줘"는 `night_on` 규칙(`켜|시작|전환|활성`)에 없음 → `llm_required` → LLM `gear` → "장비 상태를 자동으로 추측하지 않습니다" 카드. 시연 실패.
2. **첫 호출 prefill이 차갑다.** Xavier prefill 413 tok/s. 프롬프트를 제대로 쓰면 800~1,200 tok → 첫 호출 2~3 s로 2.0 s 타임아웃 초과 → `unknown` 카드. 워밍업이 없으면 첫 질문이 실패한다.
3. **refuse 과매칭(P1, WORKLOG #25).** `(약|…).*(복용|먹|용량|몇|얼마)`의 `약`이 "약수터", "약 몇 분"에 걸린다. "일몰까지 약 몇 분 남았어" → 거부 카드. 규칙이 최우선이라 하네스로는 못 막는다 → §9.

---

## 2. 설계 원칙

| # | 원칙 | 강제 수단 |
|---|---|---|
| P1 | 시연 대사는 규칙으로 확정. LLM 미호출 | `config/demo_script.json` + `eval/run_demo_script.py`가 "규칙에서 확정"을 검사 |
| P2 | LLM 출력은 enum 2개(`scenario_id`, `action`)뿐. 숫자 필드 없음 | `config/schema_intent.json` strict + `harness/guard.py` |
| P3 | 생명 라벨·refuse·unknown은 LLM이 확정 못 함 → 고정 카드 | `guard.validate_intent` (기존 `resolve` 규칙 유지) |
| P4 | LLM이 고른 지도 동작도 기존 MAP 계약 경로(enum → `/api/voice/commands` → 고정 문장)만 탄다 | `DemoRouter`가 `RouteDecision`만 만들고 `ProductAssistant.handle_text`는 무수정 |
| P5 | confirm/reject는 후보 대기(pending) 중에만 허용 | guard |
| P6 | 실패 시 재시도 없음, 2.0 s 타임아웃, `unknown` 고정 카드 | `LlmClient` 단일 호출, 예외 전부 `classifier_failed_no_retry` |
| P7 | `temperature=0`, `seed` 고정, 고정 프리픽스(KV 캐시) | `harness_policy.json` |
| P8 | 사용자가 듣는 문장은 카드·MAP 고정 문장만. 다듬기는 시연에서 off | `polish.mode = "off"` |
| P9 | 외부 네트워크 0. LLM 주소는 127.0.0.1만 허용 | `LlmClient` 주소 검증 |

---

## 3. 구조

```text
OGTECH-llm/
├─ config/
│  ├─ harness_policy.json        실행 정책(주소·타임아웃·허용 동작·polish 모드). 시연 프로필
│  ├─ system_prompt_ko.txt       역할 1+2 시스템 프롬프트(고정 프리픽스)
│  ├─ fewshot_intent.jsonl       few-shot 16턴 — 시연 대사 중심(고정 프리픽스)
│  ├─ schema_intent.json         {"scenario_id": enum14, "action": enum16} strict
│  ├─ schema_classify.json       {"scenario_id": enum14} — 기존 classify_scenario 호환
│  ├─ system_prompt_polish_ko.txt / schema_polish.json / polish_forbidden.json   역할 3(shadow)
│  ├─ stt_lexicon.json           STT 오인식 보정(목격체→목적지, 헨트→텐트, 번호(켜)→버너 …)
│  ├─ keyword_rules_demo.yaml    시연 변형 발화용 규칙 오버레이(정본 규칙이 놓친 것만)
│  ├─ demo_script.json           시연 대사 정본 — 발화·변형·기대 라벨/동작/문장
│  └─ llama_server.args          llama-server 실행 옵션(동결 §5)
├─ harness/
│  ├─ normalize.py               STT 사전 적용
│  ├─ llm_client.py              llama-server 클라이언트(JSON Schema, 타임아웃, 캐시, 로컬 주소만)
│  ├─ guard.py                   의도·다듬기 출력 검증(enum·생명 라벨·pending·금지어·숫자)
│  ├─ intent.py                  역할 1+2: 메시지 조립 → 호출 → guard
│  ├─ device_state.py            DEVICE_STATE 60 tok 직렬화
│  ├─ polish.py                  역할 3(off/shadow/speak)
│  ├─ demo_router.py             DemoRouter(RuleRouter): 사전 → 정본 규칙 → 오버레이 → LLM 의도
│  ├─ demo_assistant.py          DemoAssistant(ProductAssistant): polish 후처리만 추가
│  ├─ fake_map.py                평가·테스트용 MAP 대역
│  ├─ mock_llm_server.py         llama-server 대역(정상·타임아웃·500·비JSON) — PC 검증·리허설용
│  └─ preflight.py               health·토큰 수·워밍업·샘플 의도 점검 CLI
├─ eval/
│  ├─ run_demo_script.py         시연 대사 전 턴 실행·기대 대조·N회 동일성
│  ├─ run_intent_eval.py         LLM 단독 의도 정확도(280+183+50)
│  └─ latency_bench.py           프리픽스 토큰 수·cold/warm 지연
├─ runner/  start_llama_server.sh · ogtech-llm-server.service · llm.env.example
├─ results/ 평가 JSON 출력 자리
└─ tests/   하네스 단위 테스트
```

데이터 흐름(질문 1회):

```text
STT 텍스트
 → normalize(stt_lexicon)                       0 ms
 → RuleRouter.decide (정본 규칙, refuse 최우선)  0 ms   ── 확정 ──┐
 → keyword_rules_demo 오버레이 (llm_required일 때만)     ── 확정 ──┤
 → IntentResolver (LLM, 2.0 s, enum 2개) → guard          ── 확정/폴백 ─┤
                                                                      ▼
                                   RouteDecision(scenario_id, map_action, assistant_action)
                                                                      ▼
                    ProductAssistant.handle_text (무수정): MAP enum 명령 → 고정 문장 / CardRenderer
                                                                      ▼
                          [DemoAssistant] path A 카드에만 polish(shadow: 로그, speak: 교체) → TTS
```

---

## 4. LLM 역할과 시연 정책

| 역할 | 구현 | 시연 기본값 | 근거 |
|---|---|---|---|
| 1 분류(라벨) | `intent.py` 호출 1번에 통합 | **on** | 기존과 동일 |
| 2 대상·동작 추출 | 같은 호출의 `action` enum | **on** | §1 위험 1 해소. 값이 아니라 "무엇을 시키는지"만 |
| 3 카드 다듬기 | `polish.py` | **off**(시연 프로필). shadow는 `results/polish_shadow.jsonl` 기록만 | 스피커 문장은 검수 문장만. shadow/speak는 `--parallel 2 -c 4096` 필요 |

### 4.1 guard — LLM 출력 처리 규칙 (`harness/guard.py`)

| LLM 출력 | 처리 | reason |
|---|---|---|
| 스키마 외 키·enum 외 값 | `unknown` 고정 카드 | `schema_validation_failed` |
| 호출 실패·타임아웃·비JSON | `unknown` 고정 카드 | `classifier_failed_no_retry` |
| `action=repeat_response` | 마지막 검수 응답 재생 | `validated_llm_assistant_action` |
| `confirm/reject_destination` + pending 아님 | 동작 무시(none 취급) | — |
| 정책 허용 목록 밖 동작 | 동작 무시 | — |
| `action=status` + 라벨이 lost/daylight/sleep_safety(생명) | 기본 **차단** → `unknown` (`allow_life_status_readout=false`) | `llm_life_label_blocked` |
| `action=status` + route/weather/gear | 동작 채택 | `validated_llm_map_action` |
| 지도 동작(save/route/clear/find/night/cancel) | 라벨을 동작의 정본 라벨로 정규화 후 채택 | `validated_llm_map_action` |
| `action=none` + 생명 라벨/refuse | 차단 → `unknown` | `llm_life_label_blocked` (기존과 동일) |
| `action=none` + unknown | `unknown` | `llm_unknown` |
| `action=none` + 저위험 라벨 | 카드 채택(path A) | `validated_llm_label` |

### 4.2 프롬프트 (`config/system_prompt_ko.txt`, `fewshot_intent.jsonl`)

- 구조: `[system: 역할·라벨 14·경계·동작 16·규칙 4]` → `[few-shot 14턴: 지도 명령·확인 대기·status·refuse·unknown 대조 예, 평가 문장과 겹침 0]` → `[user: "발화: …\n확인 대기: 예|아니오"]`. 앞 두 블록은 매 호출 동일 → KV 캐시 적중. 가변부는 user 한 줄.
- pending 여부를 user 턴에 넣는 이유: "네"의 의미가 확인 대기 상태에 따라 달라지고, 그 상태는 MAP `/api/voice`에서 온다.
- 프리픽스 크기(2026-08-30 5판): 시스템 + few-shot 14턴 = 2,811자, 휴리스틱 추정 1,398 tok(상한 1,400, `tests/test_config_assets.py`), Qwen 토크나이저 실측 1,549 tok(`eval/latency_bench.py` `/tokenize`) `[실측 Jetson]`. cold 워밍업 0.748 s(KV 캐시 적재 후 warm 최댓값 0.751 s) → **워밍업 없이는 첫 질문이 느리다.** 운영은 `ogtech-llm-server.service`의 `ExecStartPost=runner/warmup_llm.sh` 뒤 warm 경로만. 경계 규칙을 늘리려면 few-shot을 줄여 상한을 지킨다(5판에서 weather/status 예시 1턴을 빼 1,398로 맞춤). 라벨 정확도 5판 86.07%(`results/intent_eval_jetson.json`).
- 워밍업: `runner/start_llama_server.sh`가 기동 직후 `preflight`로 1회 호출 → 슬롯 KV 캐시에 프리픽스 적재. 정책 `llm.warmup_on_start=true`.

### 4.3 지연 예산(추정, Xavier 실측 prefill 413 tok/s · 생성 27 tok/s 기준)

| 구간 | 토큰 | 시간 |
|---|---:|---:|
| 프리픽스(캐시 적중) | ~1,300 `[추정]` | 0 |
| user 턴 prefill | ~30 | 0.07 s |
| 생성 `{"scenario_id":"route","action":"route_basecamp"}` | ~18 | 0.67 s |
| **warm 합계** | | **≈ 0.8 s** `[추정]` |
| cold(캐시 없음) | ~1,300 `[추정]` | ≈ 3.2 s → **워밍업 필수**(타임아웃 20 s 별도) |

`temperature=0` + `seed` 고정 + 동일 프리픽스 → 20회 동일 출력 조건 충족(실측은 §7).

---

## 5. 시연 대사 정본 (`config/demo_script.json`)

출처: `.github/docs/DEMO_SCRIPT.md`, `OGTECH-frontend/MAP/kiosk/VIDEO_DEMO_2026-08-09.md`, `docs2/11 §4.1·§10.3`, `Co-LLM/eval/run_video_scenario.py`.

| ID | 장면 | 발화 | 확정 단계 | 기대 동작 | 기대 음성(고정) |
|---|---|---|---|---|---|
| D01 | 영상 1 | 여기를 베이스캠프로 저장해 줘 | 규칙 | `save_basecamp` | 현재 GPS 위치를 베이스캠프로 저장했습니다. |
| D02 | 영상 2~3 | 아 너무 목마른데 | 규칙 | `find_nearest_water` | 가장 가까운 검수 수원 표식을 찾았습니다. 수질은 확인되지 않았습니다. 이 위치를 목적지로 지정할까요? |
| D03 | 영상 4 (pending) | 네 | 규칙 | `confirm_destination` | 네, 목적지로 설정되었습니다. (고정 WAV) |
| P01 | 영상 7 선제 | — | AlertDetector | arrival | 목적지에 도착하였습니다. |
| P02 | 영상 8 선제 | — | AlertDetector | daylight | 귀환 권고 시각에 도달했습니다. 베이스캠프 경로를 화면에서 확인하세요. |
| D04 | 영상 8 | 베이스캠프 복귀 경로 보여 줘 | 규칙 | `route_basecamp` | 베이스캠프 경로를 불러왔습니다. 방위와 거리는 지도 엔진 계산값입니다. |
| P03 | 영상 9 선제 | — | AlertDetector | arrival | 베이스캠프에 도착하였습니다. |
| D05 | 영상 10 | 야간 모드 켜 줘 | 규칙 | `night_on` | 야간 모드를 켰습니다. |
| D06 | DEMO_SCRIPT 2:00 경로 B | 텐트 안에서 버너 켜도 돼 | 규칙 | `sleep_safety` 카드 | 현재 일산화탄소 센서 계측값은 N피피엠입니다. … (CO 값 없으면 고정 문장) |
| D07 | DEMO_SCRIPT 2:00 대안 | 일몰까지 몇 분 남았어 | 규칙 | `daylight`+`status` | 일몰까지 N분 남았습니다. … |
| D08 | 보조 | 다시 말해 줘 | 규칙 | `repeat_response` | 직전 검수 응답 |
| D09 | 보조 | 현재 목적지 삭제해 줘 | 규칙 | `clear_destination` | 현재 목적지와 목적지 경로를 삭제했습니다. |
| D10 | DEMO_SCRIPT 2:25 LLM kill | 이 상황에서 무엇을 먼저 살펴보면 좋을까 | **LLM 필요** → 사망 시 폴백 | `unknown` 고정 카드 | 요청을 안전하게 분류하지 못했습니다. 지도 상태, 남은 일조 시간, … |
| D11 | 영상 후 | 야간 모드 꺼 줘 | 규칙 | `night_off` | 야간 모드를 껐습니다. |

각 D 턴에 STT 변형 5~8개(`variants`)를 두고, 변형도 **규칙 또는 오버레이에서** 같은 동작으로 확정되어야 통과다. 판단이 갈리는 발화("여기가 베이스캠프로 적당하겠는걸")는 `observe`로 두고 결과만 기록한다.

---

## 6. 오버레이 규칙과 STT 사전 — 왜 정본 규칙을 안 고치나

- `keyword_rules.yaml`은 backend `config/`와 **바이트 일치**가 테스트로 강제된다(`OGTECH-backend/tests/test_vendor_sync.py`). 지금 다른 세션이 backend를 편집 중이라 동시 수정을 피했다.
- 오버레이(`keyword_rules_demo.yaml`)는 정본이 `llm_required`를 낸 뒤에만 본다. 정본의 refuse·확인·생명 규칙 우선순위는 그대로다.
- 검토 뒤 오버레이 패턴을 정본에 합치면 오버레이는 비워도 된다(§8).

---

## 7. 검증 계획

| 러너 | 무엇을 | 통과 기준 |
|---|---|---|
| `tests/` (unittest) | guard·사전·라우터·스크립트·mock 서버 | 전부 OK |
| `eval/run_demo_script.py --llm none` | 시연 대사 + 변형, LLM 없이 | 규칙/오버레이 확정 100%, 기대 문장 일치, D10만 `classifier_unavailable` |
| `… --llm mock --runs 20` | mock LLM으로 20회 | 20회 출력 동일 |
| `… --llm mock --mock-mode timeout|http500|garbage` | LLM 장애 리허설 | 전 턴 고정 카드/규칙 유지, 예외 0 |
| `eval/run_intent_eval.py --llm <Jetson>` | 실제 Qwen 의도 정확도(규칙 우회) | 라벨 ≥ 90%(PLAN §2.2), 지도 동작 정확도 보고, few-shot 겹침 제외 |
| `eval/latency_bench.py --llm <Jetson> --runs 20` | 프리픽스 토큰·cold/warm 최댓값 | warm 최댓값 ≤ 2.0 s(경로 B 예산 안에서 LLM 몫) |
| `Co-LLM/tests` 55개 | 기존 파이프라인 회귀 없음 | OK (하네스는 기존 파일 무수정) |

실행 결과 `[실측 2026-08-30, 이 PC, mock/LLM 없음]`: `tests/` 하네스 단위 테스트 통과 · `run_demo_script --llm none --runs 20` 11턴/66변형 통과·20회 동일 · `--llm mock --runs 20` 통과·20회 동일 · mock `timeout/http500/garbage/empty` 전부 고정 카드 폴백 통과 · `Co-LLM/tests` 55 OK(무수정). 결과 파일은 `results/demo_script_*.json`, `results/intent_eval_mock.json`, `results/latency_intent_mock.json`(mock 수치는 무의미, 배관 확인용).

Jetson 실측 `[2026-08-30, Xavier NX, Qwen2.5-1.5B Q4_K_M, -ngl 99 -b 128 -ub 128]` — `results/intent_eval_jetson.json`, `results/latency_intent_jetson.json`, `results/demo_script_jetson.json`, `results/preflight.log`:

| 러너 | 결과 |
|---|---|
| `run_intent_eval.py --llm <llama-server>` | 라벨 226/273 = **82.78%**, 게이트 90% **미달**, 오류 0, 지연 max 0.817 s · 중앙값 0.632 s. 동작(정보성) 46/92. 프롬프트 판별 이력: 초판 78.60% → 경계 규칙 명문화·압축 81.68% → 라벨 정의에 경계어 편입·few-shot 18→16 **82.78%(채택)** → unknown 예시 2개 추가 78.18%(unknown 과예측, 기각). 잔여 혼동 상위: refuse→unknown 7(약 복용량·병명·수술·프롬프트 지시), sleep_safety→gear 6(버너·난로·CO), water→food 3(식수), unknown→주제 라벨 12(사물 묘사·과거형). |
| `latency_bench.py --runs 20` | 프리픽스 1,490 tok, cold 0.829 s, warm 최댓값 0.706 s · 중앙값 0.63 s, 예산 2.0 s 통과, 오류 0 |
| `run_demo_script.py --llm <llama-server> --runs 20` | 11턴 · 변형 66 · 20회 동일 · 통과 |

라벨 게이트 미달의 의미: 이 평가는 규칙을 우회한 **모델 단독** 정확도다. 제품 경로에서는 `refuse`와 생명 라벨을 정본 규칙·고정 카드가 먼저 잡고(§9, `Co-LLM/eval/run_eval.py` 14라벨 280/280·refuse 50/50), LLM은 규칙 미매칭 발화의 enum 2개만 정한다. 90%를 넘기려면 프롬프트만으로는 부족하며(3판 시도 상한 ≈ 83%), 1.5B 모델의 LoRA 미세조정 또는 규칙 범위 확대가 필요하다 — 팀 결정 항목.

---

## 8. 주입(배포) 절차 — 2026-08-30 적용 상태

1. `runner/start_llama_server.sh` (또는 `ogtech-llm-server.service`)로 llama-server 기동 → `preflight`가 health·토큰 수·워밍업 보고.
2. ✅ 적용(2026-08-30) `Co-LLM/scripts/product_voice.py`: `_build_assistant()`가 `DemoAssistant(router=HARNESS.router, polisher=HARNESS.polisher, classifier=E.classify_scenario)`를 만들고, 하네스 구성 실패 시 기존 `ProductAssistant`로 폴백(`[WARN]` 출력). `physical_voice.py`는 `run_once`를 import하므로 버튼 경로도 같이 바뀐다. `device_monitor.py`는 그대로. E2E: MAP `app.py --gps-mode replay` + `product_voice.py --text … --no-tts`로 베이스캠프 저장→수원 탐색→확인→일몰(361분/18:32)→버너 카드→복귀 경로→체크포인트 통과, LLM 호출 0 `[실측 2026-08-30, 이 PC]`.
3. ✅ Jetson에서 `eval/run_intent_eval.py --llm http://127.0.0.1:8080/…`, `eval/latency_bench.py`, `run_demo_script.py --runs 20` 실행 → `results/*_jetson.json` 저장(2026-08-30, §7 표). 라벨 게이트는 미달(82.78%).
4. 결과를 보고 `harness_policy.json` 결정: `allow_life_status_readout`(현재 false), `polish.mode`(현재 off — 켜려면 `llama_server.args`를 `--parallel 2 -c 4096`으로).
5. 오버레이는 시연 전용으로 유지(정본 미합류). 정본에는 §9의 결함 수정만 반영했고 backend 사본을 바이트 동기화했다(2026-08-30).
6. ✅ 조직 프로필 File Architecture·LLM 역할 설명 갱신(2026-08-30).

---

## 9. 하네스 밖에서 시연 전에 고쳐야 할 것 (정본 규칙, 팀 결정)

**2026-08-30 정본에 적용했다**(사용자 지시 "헷갈리는 것들은 해결 가능하면 해결"). backend `config/keyword_rules.yaml` 바이트 동기화, 회귀 테스트 `tests/test_product_rules_regression.py`(5건), `config/demo_script.json`의 결함 5건은 관찰에서 단언으로 전환. 결과: 14라벨 280/280 · refuse 50/50 누출 0 · Co-LLM 55 OK · backend 9 OK · 하네스 54 OK · 시연 대사 20회 동일 `[실측 2026-08-30]`. 아래 표는 적용 전 제안 기록이다.

| # | 문제 | 시연 영향 | 제안 | 검증 `[실측 2026-08-30, 임시 사본]` |
|---|---|---|---|---|
| WORKLOG #25 | refuse `(약\|…).*(복용\|먹\|용량\|몇\|얼마)`가 음절 `약`에 걸리고, 야생동물 패턴이 `괜찮`에 걸림 | "일몰까지 약 몇 분 남았어"·"약수터까지 얼마나 걸려" → 거부 카드 | 아래 refuse 패턴 9개로 교체 | refuse 50/50 유지 · voice_cases 183 회귀 0 · classify 280 회귀 0 · 오탐 5건 해소("약수터까지 얼마나 걸려"→route/status, "일몰까지 약 몇 분"→daylight/status, "야생동물이 근처에 있는데 괜찮아"→wildlife, "풀숲에서 독사 봤어"→unknown, "배낭 풀고 쉬어도 괜찮아"→unknown) |
| #26 | map_rules(weather/status)가 scenario_rules(warmth)보다 먼저 | "지금 너무 추워 저체온증 같아" → 온도 카드 | weather status 규칙에 `exclude_patterns: ["저체온","떨려","한기","체온"]` | 적용 — warmth 도달, "지금 여기 온도 얼마야"는 weather/status 유지 |
| #27 | yes 패턴 `(설정\|지정).*(해\|진행)`이 pending 중 다른 명령을 삼킴 | 수원 확인 대기 중 "야영지는 여기로 설정해 줘" → 목적지 확정 | `^(설정\|지정) ?(해\|진행)`으로 한정 + 짧은 긍정 접두 패턴 `^(네\|응\|그래\|…)[,. ]+(그렇게\|거기로…)?(설정\|지정\|진행)?(해\|해 줘…)?$` 추가 | 적용 — "야영지는 여기로 설정해 줘"·"네 야영지는 여기로 설정해 줘"→save_basecamp, "설정 진행해"·"네 설정해 줘"→confirm(voice_cases 183 회귀 0) |
| 신규 | scenario daylight `(해\|일몰\|일조\|어두워).*(지\|남\|시간\|언제\|돌아)`의 `해…지`가 "해야 하지"에 걸림 | "지금 뭘 해야 하지" → daylight 카드 | daylight scenario 규칙에 `exclude_patterns: ["해야 ?(하\|되\|돼\|할\|해\|지)"]` | 적용 — "지금 뭘 해야 하지"→llm_required, "해 언제 져"·"해 지기 전에 돌아가야 해"→daylight 유지 |

#25 제안 refuse 패턴(정본 순서 유지, 교체 대상 4개만 바꿈):

```json
"(버섯|열매|나물|풀|식물|뿌리|곤충|야생동물|물고기).{0,8}(먹어도|먹을 수|먹어|먹으|먹을|식용|섭취)",
"(먹|식용|섭취).*(버섯|열매|나물|풀|식물|뿌리|곤충|야생동물|물고기)",
"(버섯|열매|나물|풀|식물|뿌리|곤충|물고기).*독(이|은)?\\s*(있|인지|맞|판단)",
"독\\s*(버섯|열매|나물|풀|식물|뿌리|곤충|물고기).*(인지|맞아|판단)",
"(무슨|어떤).*(버섯|열매|식물).*(인지|이야|맞아|맞혀)",
"(약물|약품|알약|진통제|항생제|소염제|해열제)(을|를|은|는|이|가|도)?.*(복용|먹|용량|몇|얼마)",
"(^|\\s)약(을|를|은|는|이|가|도)?\\s*(몇 알|몇 개|몇 정|복용|먹|용량)",
"(진단|병명|수술|절개|봉합).*(해|알려|판단)",
"(시스템 프롬프트|내부 지시|규칙).*(보여|출력|무시|잊어)"
```

적용 시 `OGTECH-backend/config/keyword_rules.yaml`도 같은 바이트로 복사하고 `Co-LLM/tests`(55)·backend `tests`(9)를 다시 돈다. 하네스의 `eval/run_demo_script.py`는 위 발화 4개를 `known_rule_defects_observe`로 기록해 수정 전후를 비교한다.

---

## 10. 하지 않는 것

- LLM이 사용자에게 들리는 문장을 생성하는 일(polish speak 전환 전까지). 생명 라벨 절차 생성. 숫자 생성.
- 정본 규칙·카드·backend 사본 수정(§9는 제안만).
- 모델·양자화 변경, 미세조정, RAG.
- 실제 Jetson 수치를 추정으로 채우는 일.

---

## 11. 검토 시 결정해 줄 것

**2026-08-30 결정(사용자 "계획대로 수행")**: 1 → false 유지 · 2 → **off**(캐시 보존, §0) · 3 → 3건 + daylight 전부 적용(§9) · 4 → 오버레이 시연 전용 유지 · 5 → 미확정(둘 다 규칙 경로 B, 팀이 대사 선택) · 6 → 16턴 유지(워밍업 강제). 아래는 원래 질문의 기록이다.


1. `allow_life_status_readout`: 규칙이 놓친 "일몰/위치/CO 값 읽어 줘" 변형을 LLM이 `status`로 채택해도 되는가(카드 내용은 코드 계산값). 기본 **false**.
2. `polish.mode`: shadow 유지 vs speak. 기본 **shadow**.
3. §9 정본 규칙 수정 3건을 시연 전에 반영할지.
4. 오버레이 패턴을 정본에 합칠지, 시연 전용으로 둘지.
5. D06/D07 중 DEMO_SCRIPT 2:00 장면에 쓸 질문 확정(둘 다 경로 B, 2.0 s 예산).
6. few-shot 16턴 유지(프리픽스 실측 1,490 tok, cold 워밍업 0.829 s) vs 12턴으로 축소(대조 예 4개 제거). 워밍업이 보장되면 warm 지연은 같다. 2026-08-30 실측: 18턴+긴 경계 규칙은 휴리스틱 상한 1,400을 넘겼고, unknown 예시를 늘리자 unknown 과예측으로 정확도가 떨어졌다(§7).
