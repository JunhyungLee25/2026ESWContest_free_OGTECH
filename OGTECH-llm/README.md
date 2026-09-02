# OGTECH-llm — 온디바이스 음성 파이프라인과 평가 하네스

**OGTECH Kit** (2026 임베디드 소프트웨어 경진대회 자유공모 / 팀 OGTECH) 의 LLM 저장소입니다.
[조직 개요](https://github.com/2026-ESW-OGTECH) · [다른 저장소 안내](https://github.com/2026-ESW-OGTECH/.github)

---

## 이 저장소가 하는 일 한 줄

**인터넷 없이, 말로 물으면 말로 답한다. 그리고 위험한 질문에는 모델이 대답하지 못하게 막는다.**

LLM은 판단 주체가 아니라 **정해진 계약 안에서 텍스트만 다루는 부품**입니다.
경로·방위·거리는 **출력 스키마에 숫자 필드 자체가 없어** 환각할 자리가 없습니다.

### LLM이 실제로 하는 일 — 의도 판단 enum 2개(시연 하네스), 문장 생성은 없음

설계 계약([`docs/00_frozen_decisions.md`](Co-LLM/docs/00_frozen_decisions.md) §4)은 역할 3개를 적어 두었습니다.
2026-08-30 시연 하네스([`harness/`](harness/README.md))가 ①②를 한 호출로 합쳐 구현했고, ③은 구현만 있고 시연 프로필에서는 꺼 둡니다.
문서와 코드가 갈라지지 않도록 현재 상태를 그대로 적습니다.

| 구분 | 내용 | 코드 근거 |
|---|---|---|
| **구현됨(2026-08-30) — 의도 판단 `{scenario_id, action}`** | `product_voice.py`가 `DemoAssistant`를 씁니다. STT 보정 사전 → 정본 규칙 → 시연 오버레이 규칙 → (놓친 발화만) LLM이 **enum 2개**(라벨 14 × 지도 동작 14+`none`+`repeat_response`, `max_tokens=32`, 타임아웃 2.0 s, 재시도 없음) → guard가 생명 라벨·비허용 동작·확인 대기 밖 `confirm`을 걸러 고정 카드로 내립니다. 시연 대사 11턴·변형 66개는 LLM 호출 없이 규칙에서 확정됩니다 | [`harness/intent.py`](harness/intent.py) · [`harness/guard.py`](harness/guard.py) · [`harness/demo_router.py`](harness/demo_router.py) · [`config/demo_script.json`](config/demo_script.json) |
| **구 경로 — 14라벨 분류 1개**(`intent.enabled=false`일 때만) | 출력은 **enum 라벨 1개**(`scenario_id`, JSON Schema strict, `max_tokens=16`). 재시도 없이 실패하면 `unknown`. 규칙 게이트가 라벨을 못 고른 발화만 여기로 옵니다. **생명 라벨(`lost·daylight·warmth·sleep_safety·injury·refuse`)은 LLM이 내놓아도 채택하지 않고** 고정 카드로 내립니다 | [`scripts/engines.py`](Co-LLM/scripts/engines.py) `classify_scenario()` · [`scripts/ogtech_core.py`](Co-LLM/scripts/ogtech_core.py) `RuleRouter.resolve()` |
| **문장 생성 — LLM 미사용** | 사용자가 듣는 문장은 **규칙 라우팅 + 검수 카드 템플릿**이 만듭니다. 검수된 고정 문장에, 코드가 계산한 장치값(GPS 정확도·위성 수·트레일 이탈 m·CO ppm·전원 %)만 끼워 넣습니다. 이 경로에 모델은 없습니다 | [`scripts/ogtech_core.py`](Co-LLM/scripts/ogtech_core.py) `CardRenderer.render()` |
| **③ 카드 맞춤 문장 — 구현, 시연에서는 off** | 2~4줄·줄당 40자, 숫자는 카드 원문에 있는 것만, 금지어 guard. `polish.mode=off`(shadow/speak는 llama-server `--parallel 2 -c 4096` 필요 — `--parallel 1`에서는 intent 프리픽스 KV 캐시를 지웁니다) | [`harness/polish.py`](harness/polish.py) |

사용자가 듣는 문장은 여전히 **검수 카드와 코드 계산값**뿐입니다. 두 경로의 차이는
문장을 누가 쓰느냐가 아니라, **라벨과 지도 동작을 규칙이 정했느냐 LLM이 정했느냐**입니다.

## 구성

```text
Co-LLM/                        ★ 실행 파이프라인과 검증 (이 저장소의 본체)
├─ scripts/
│  ├─ product_voice.py         제품 음성 경로 진입점
│  ├─ physical_voice.py        물리 버튼 → 음성 질의
│  ├─ wake_voice.py            "오지야" 호출어 상시 청취 → 인사 → 같은 제품 경로
│  ├─ pipeline_gate.py         키워드 게이트 (모델 도달 전 차단)
│  ├─ tts_pipeline.py          문장 단위 스트리밍 TTS
│  ├─ product_assistant.py     카드 선택과 문장 조립
│  ├─ device_monitor.py        장치 상태 감시 · CO 경보음(비프)+음성
│  ├─ ogtech_core.py          규칙 라우터 · 검수 카드 문장 조립 (LLM 미사용)
│  └─ engines.py · voice_loop.py · stt_prompt.txt
├─ config/
│  ├─ survival_cards.json      검수된 고정 카드
│  ├─ keyword_rules.yaml       키워드 게이트 규칙
│  ├─ wake_voice.json          호출어 변형·단계별 STT 프롬프트·시연 대본 문구
│  └─ fixed_audio.json         사전 합성 음성
├─ eval/                       14 라벨 분류 · refuse 누출 평가, 하드웨어 인수 러너
├─ tests/                      단위 테스트 127개
├─ assets/audio/               검수된 고정 안내 음성 (사전 합성 wav)
└─ jetson/                     systemd 유닛과 오디오 환경 설정

docs2/                         조사·계산 근거 문서 ★ 현재 도메인 정본
config/ · harness/ · eval/ · runner/ · tests/ · results/   시연 하네스 (2026-08-30 주입 — harness/README.md)
```

## 확정된 실행 구성

**작업 크기에 따라 최적 실행 타깃이 다릅니다.** 74M 파라미터(whisper base)에서는 커널 실행
오버헤드가 연산량을 압도해 CPU가 유리하고, 1.5B(Qwen2.5)에서는 GPU가 유리합니다.

```text
STT  → CPU      whisper.cpp
LLM  → GPU      llama.cpp, Qwen2.5 1.5B Q4_K_M
TTS  → CPU
```

메모리 대역폭은 공유하므로 **셋을 동시에 올리지 않고 순차 실행**합니다.
LLM만 상주시키고 STT·TTS는 온디맨드 로드/언로드합니다.

### STT 측정 — 중앙값이 아니라 최댓값으로 고릅니다

whisper는 입력이 5초든 30초든 인코더를 30초 멜 윈도로 돌립니다. 5초 클립에서 25초가 순수 패딩 연산이라
`-ac 450`이 지연의 대부분을 설명합니다. 남은 문제는 평균이 아니라 **꼬리**였습니다 — 무음 구간에서
디코더가 같은 어절을 반복 생성해(디코드 스텝 219회) 한 발화만 3.4초로 튀었고, 입력에서 무음을
제거하는 VAD만이 유효했습니다 `[실측]`.

한국어 6발화(`ko_6utt`) 동일 세트 실측입니다. 전체 표는
[`Co-LLM/docs/decision_matrix.csv`](Co-LLM/docs/decision_matrix.csv) · [`measurements.csv`](Co-LLM/docs/measurements.csv)에 있습니다.

| 구성 | 중앙값 | **최댓값** | 판정 |
|---|---:|---:|---|
| **base CPU, `-ac 450 -nf --vad`** | 1,385 ms | **1,495 ms** | **최종 선정** |
| base CPU, `-ac 450 -nf` | **1,244 ms** | 3,363 ms | 기각 — 중앙값 1위지만 꼬리 |
| base CPU, `-ac 450 -nf --vad -vp 200` | 1,340 ms | 3,446 ms | 기각 — 패딩이 무음을 되돌림 |
| base CPU, `-ac 300` | 983 ms | 12,602 ms | 기각 — 환각 2건 |
| base GPU, `-ac 600` | 3,805 ms | 26,049 ms | 기각 — CUDA ctx init 1.6초 고정비 |
| small CPU, `-ac 600` | 5,857 ms | 6,290 ms | 기각 — base의 3.7배인데 게이트 지표는 동일 |

- **판정은 중앙값이 아니라 최댓값**으로 봅니다. 데모 조건이 연속 20회라 한 번의 이상치가 곧 실패입니다.
  이 기준에서 **경로 B 예산 2.0초를 통과하는 구성은 VAD 구성 하나뿐**입니다.
- **`-ng`는 필수입니다.** 빼면 Xavier 통합 메모리에서 91 MiB cudaMalloc에 실패해 SIGSEGV로 죽습니다 `[실측]`.
- **beam search 기각** — 지연 +33%에 출력이 한 글자도 바뀌지 않았습니다 `[실측]`.
- **`-ac 300` 기각** — 중앙값은 최소지만 환각이 나고 최댓값이 12.6초로 폭주합니다 `[실측]`. 450이 하한선입니다.

**코드 기본 구성이 곧 이 최종 선정 구성입니다.** `Co-LLM/config.py`의 `WHISPER_CPP_FLAGS`와
`Co-LLM/scripts/stt_prompt.sh`의 `OGTECH_STT_FLAGS`가 `--vad -vm ggml-silero-v5.1.2.bin`을 포함합니다.
VAD 모델(864 KB)은 whisper.cpp 본체와 따로 내려받습니다.

```bash
cd ~/ogtech_ai/stt/whisper.cpp && bash ./models/download-vad-model.sh silero-v5.1.2

# 경로가 다르면
OGTECH_WHISPER_VAD_MODEL=/path/to/ggml-silero-v5.1.2.bin
```

**모델 파일이 없으면 `--vad`를 자동으로 빼고 경고를 남깁니다** (`engines.py`의 `whisper_flags()`,
`stt_prompt.sh`의 같은 규칙). 없는 채로 넘기면 whisper-cli가 모델 로드에 실패해 그대로 죽기 때문입니다.
이때는 위 표의 2행 구성으로 내려가므로 최댓값이 예산을 넘길 수 있습니다.

> 남은 이견 — [`Co-LLM/docs/01_main.md`](Co-LLM/docs/01_main.md) §6.3은 2행 구성의 꼬리가
> **5초 고정 녹음이라는 측정 하네스의 산물**일 가능성을 열어 두고, E12(3초 녹음 재측정)를 뒤집기 조건으로
> 걸었습니다. **E12는 아직 미측정**이므로 선정은 VAD 구성으로 유지합니다.

### 생성 설정

`temperature = 0`. 취향이 아니라 재현성 문제입니다 — 리허설 20회 연속 동일 출력을 보장해야 합니다.
구조화 출력은 JSON Schema 제약(llama.cpp GBNF)으로 강제하며, 문법 실패가 구조적으로 0이므로
**재시도 단계를 두지 않습니다.**

## 지연 병목은 모델이 아니라 프롬프트 길이입니다

Xavier 실측 prefill이 413 tok/s입니다. **3,300 토큰짜리 프롬프트는 prefill만 8초입니다.**
그래서 프롬프트를 불변 → 준가변 → 가변 순으로 조립해 KV 캐시를 살리고,
장치 상태(`DEVICE_STATE`)에 **60 토큰 상한**을 겁니다.

```text
[SYSTEM + 출력 규칙]   ← 완전 고정, KV 캐시에 남음
[SURVIVAL_CARD]        ← 선택된 카드 1장, 세션 내 준고정
[DEVICE_STATE]         ← 요청마다 변함. 60 tok 상한
[USER]                 ← 마지막
```

이 조립 순서는 **아직 구현되지 않은 카드 다듬기 경로의 계약**입니다. 현재 구현된 분류 호출은
고정 시스템 문장과 사용자 발화(240자 상한)만 보내므로 프롬프트가 애초에 짧습니다.

## 지도 엔진은 이 저장소에 없습니다

경로·방위·거리를 계산하는 지도 엔진의 정본은
**[OGTECH-frontend/MAP](https://github.com/2026-ESW-OGTECH/OGTECH-frontend/tree/main/MAP)** 하나뿐입니다.
이 저장소에는 사본을 두지 않습니다. 같은 모듈이 두 곳에 있으면 어느 쪽이 정본인지 알 수 없고,
한쪽만 고쳤을 때 조용히 갈라지기 때문입니다.

`Co-LLM/eval/run_video_scenario.py`는 지도 엔진과 음성 경로를 함께 도는 통합 검증 하네스라
두 저장소가 모두 필요합니다. 같은 상위 폴더에 나란히 clone하면 자동으로 찾고,
다른 곳에 있으면 `OGTECH_MAP_ROOT`로 지정합니다.

```bash
git clone https://github.com/2026-ESW-OGTECH/OGTECH-llm.git
git clone https://github.com/2026-ESW-OGTECH/OGTECH-frontend.git

# 경로가 다르면
OGTECH_MAP_ROOT=/path/to/OGTECH-frontend/MAP python Co-LLM/eval/run_video_scenario.py
```

## 검증

```bash
cd Co-LLM && python -B -m unittest discover -s tests
```

| 대상 | 결과 |
|---|---|
| `Co-LLM/tests/` | 63 tests, OK `[실측: 2026-08-30, PC·Jetson Python 3.8]` |
| `tests/` (시연 하네스) | 54 tests, OK `[실측: 2026-08-30, PC]` |
| Jetson `eval/latency_bench.py --runs 20` | 프리픽스 1,549 tok(5판), cold 워밍업 0.748 s, warm 최댓값 0.751 s · 중앙값 0.631 s, 예산 2.0 s 통과, 오류 0 `[실측 2026-08-30, Xavier NX]` |
| Jetson `eval/run_demo_script.py --llm <llama-server> --runs 20` | 11턴 · 변형 66 · 20회 동일 · 통과 `[실측 2026-08-30]` |
| Jetson `eval/run_intent_eval.py --llm <llama-server>` | 라벨 241/280 = **86.07%**(게이트 90% 미달), 오류 0, max 0.875 s, refuse 모델 단독 38/50 `[실측 2026-08-30]`. 프롬프트 5판 비교 78.6→81.7→82.8→78.2→82.9→**86.1%**(5판 채택: 물 정수·음용은 water, 야간 모드는 gear, 텐트 자리는 shelter 경계 명시, few-shot 14턴·평가 문장과 겹침 0). 잔여 혼동: unknown 평서문→주제 라벨(10), lost→unknown/route(6), water→food(6), sleep_safety→gear(4). 제품 경로에서 refuse·생명 라벨은 정본 규칙과 고정 카드가 먼저 잡으므로 LLM 단독 정확도는 규칙 미매칭 발화에만 적용된다 |

지도 엔진 테스트(87건)는 [OGTECH-frontend](https://github.com/2026-ESW-OGTECH/OGTECH-frontend)에서 돕니다.
의존성이 준비되지 않아 실행하지 못한 테스트는 통과로 간주하지 않습니다.

## 안전 경계

- **생명 관련 질문은 모델에 도달하지 않습니다.** `lost / daylight / warmth / sleep_safety / injury / refuse`는
  키워드 게이트가 잡아 검수된 고정 카드로 직행합니다.
- **모호하면 키워드가 결정하지 않습니다.** 두 라벨이 동시에 잡히면 LLM 분류로 강등하되,
  **그중 하나라도 생명 라벨이면 LLM에 보내지 않고** 고정 카드로 되묻습니다.
  `refuse` 키워드가 있으면 다른 매칭을 무시하고 무조건 `refuse`입니다.
- LLM은 경로·방위·거리·진단·처치·**야생 동식물 식용 판정**을 생성하지 않습니다.
- 실제 GPS 트랙과 내부 검토 자료는 커밋하지 않습니다.

## 문서

`docs2/`가 현재 오지 생존 도메인의 정본입니다. 조사 근거, 전력 예산, 부품 선정(BOM),
하네스 재설계, 첨부 기능 명세가 들어 있습니다.
