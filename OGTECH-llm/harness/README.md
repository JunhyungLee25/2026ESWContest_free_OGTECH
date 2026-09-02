# harness — 시연용 LLM 하네스

설계·계획: [`../docs2/13_demo_harness_design.md`](../docs2/13_demo_harness_design.md). 기존 `Co-LLM/` 파이프라인은 무수정. 주입은 검토 승인 뒤 §2.

## 1. 한 눈에

```text
STT 텍스트 → normalize(stt_lexicon) → RuleRouter(정본 규칙) → keyword_rules_demo(오버레이) → IntentResolver(LLM, enum 2개) → guard
          → RouteDecision → ProductAssistant.handle_text(무수정: MAP enum 명령 · CardRenderer) → [polish shadow] → TTS
```

| 파일 | 역할 |
|---|---|
| `__init__.py` `build_harness()` | `config/harness_policy.json`을 읽어 라우터·LLM 클라이언트·의도 분류기·다듬기를 조립 |
| `demo_router.py` `DemoRouter` | `RuleRouter` 상속. 사전 → 정본 규칙 → 오버레이 → LLM 순. `last_trace`에 확정 단계 기록 |
| `intent.py` `IntentResolver` | system + few-shot(고정 프리픽스) + user 1줄 → `{"scenario_id","action"}` → `guard` |
| `guard.py` | enum·생명 라벨·pending·허용 목록·금지어·숫자 검증. 실패는 전부 `unknown` 고정 카드 |
| `demo_assistant.py` `DemoAssistant` | `ProductAssistant` 상속. path A 카드에만 polish 후처리 |
| `polish.py` | 역할 3. `off / shadow / speak`. 기본 shadow(`results/polish_shadow.jsonl`) |
| `llm_client.py` | llama-server 클라이언트. 127.0.0.1만, 재시도 없음, `cache_prompt`, `seed`, `id_slot` |
| `normalize.py` `device_state.py` | STT 보정 사전 · DEVICE_STATE 60 tok 직렬화 |
| `mock_llm_server.py` | llama-server 대역(`ok/timeout/http500/garbage/empty/slow`). PC 검증·장애 리허설용 |
| `preflight.py` | health → 프리픽스 토큰 수 → 워밍업 → 샘플 의도. Jetson 기동 직후 실행 |
| `fake_map.py` | MAP API 대역(평가·테스트) |
| `classify.py` | 기존 `engines.classify_scenario` 자리에 꽂는 라벨 1개 호환 진입점 |

## 2. 주입 — `Co-LLM/scripts/product_voice.py` (✅ 2026-08-30 적용)

적용된 형태는 `product_voice.py`의 `_build_assistant()`다 — 하네스 구성 실패 시 `[WARN]`을 찍고 기존 `ProductAssistant`로 폴백한다. `physical_voice.py`는 `run_once`를 import하므로 버튼 경로도 같이 바뀐다. `device_monitor.py`는 그대로. 아래는 최소 형태.

```python
# product_voice.py 상단 import 뒤
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # OGTECH-llm 루트
from harness import DemoAssistant, build_harness                 # noqa: E402
HARNESS = build_harness()                                        # config/harness_policy.json

# run_once() 안, 기존 ProductAssistant(...) 생성부를 교체
assistant = DemoAssistant(
    client,
    router=HARNESS.router,
    polisher=HARNESS.polisher,
    response_store=VerifiedResponseStore(C.LAST_VERIFIED_RESPONSE_PATH),
)
```

라벨 1개만 쓰던 기존 계약을 유지하려면 대신 `from harness.classify import classify_scenario` 를 `E.classify_scenario` 자리에 넣는다(지도 동작 추출은 빠진다).

## 3. 실행

```bash
cd OGTECH-llm
python3 -B -m unittest discover -s tests            # 하네스 단위 테스트
python3 eval/run_demo_script.py                      # 시연 대사·변형, LLM 없이
python3 eval/run_demo_script.py --llm mock --runs 20 # mock LLM, 20회 동일성
python3 eval/run_demo_script.py --llm mock --mock-mode timeout   # 장애 리허설(http500|garbage|empty)
python3 -m harness.mock_llm_server --port 8080       # 수동 리허설용 대역 서버
```

Jetson(실제 모델):

```bash
cp runner/llm.env.example /etc/ogtech/llm.env   # 경로 확인
bash runner/start_llama_server.sh                # 기동 → health → preflight(워밍업·토큰 수)
python3 eval/latency_bench.py --llm http://127.0.0.1:8080/v1/chat/completions --runs 20 --output results/latency_intent_jetson.json
python3 eval/run_intent_eval.py --llm http://127.0.0.1:8080/v1/chat/completions --output results/intent_eval_jetson.json
python3 eval/run_demo_script.py --llm http://127.0.0.1:8080/v1/chat/completions --runs 20 --output results/demo_script_jetson.json
```

## 4. 정책 손잡이 (`config/harness_policy.json`)

| 키 | 기본 | 뜻 |
|---|---|---|
| `intent.enabled` | true | 규칙이 놓친 발화를 LLM 의도(enum 2개)로 보낸다 |
| `intent.allow_actions` | 14개 | LLM이 고를 수 있는 지도 동작. `night_toggle`은 제외 |
| `intent.allow_life_status_readout` | false | lost/daylight/sleep_safety `status` 읽기를 LLM 판단으로 허용할지 |
| `polish.mode` | off | `off` 호출 안 함(시연) / `shadow` 기록만 / `speak` 스피커 문장 교체(guard 통과 시). shadow·speak는 `llama_server.args` `--parallel 2 -c 4096` 필요 |
| `llm.timeout_s` | 2.0 | 의도 호출 타임아웃. 초과 시 재시도 없이 `unknown` |
| `llm.warmup_on_start` | true | 기동 시 프리픽스 KV 캐시 적재 |
| `stt_lexicon.enabled` `demo_rules.enabled` | true | STT 보정 사전 · 오버레이 규칙 |

## 5. 검토 체크리스트

- [ ] `config/demo_script.json` 대사·기대 문장이 촬영 대본과 같은가 (D06/D07 중 2:00 장면 질문 확정)
- [ ] `config/system_prompt_ko.txt` 라벨·동작 정의 문구
- [ ] `config/fewshot_intent.jsonl` 16턴 — 시연 대사 그대로 넣었음
- [ ] `config/keyword_rules_demo.yaml` 오버레이 패턴 — 정본에 합칠지
- [ ] `config/stt_lexicon.json` 보정 항목 근거
- [ ] `allow_life_status_readout` · `polish.mode` 결정
- [ ] 설계 문서 §9 정본 규칙 결함 3건(WORKLOG #25·#26·#27) 시연 전 수정 여부
