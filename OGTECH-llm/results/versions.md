# 실행 버전 기록

측정 결과를 추가할 때 모델 파일과 실행 환경을 함께 기록한다. 확인하지 못한 값은 추정하지 않고 `미기록`으로 둔다.

| 항목 | 값 |
|---|---|
| 모델 | Qwen2.5 1.5B Q4_K_M |
| 모델 파일명 | 미기록 |
| 모델 SHA-256 | 미기록 |
| llama.cpp commit | 미기록 |
| JetPack | 5.1.x |
| 장치 | Jetson Xavier NX 8GB |
| OS 인식 RAM | 6.8GB |

## 실행 옵션

```text
-fa
--cache-type-k q8_0
--cache-type-v q8_0
--cache-reuse 256
--mlock
-b 512 -ub 512
--threads 4~6
--parallel 1
```

## 측정 조건

| 날짜 | 입력 토큰 | 출력 토큰 | prefill | 생성 속도 | 최대 메모리 | 비고 |
|---|---:|---:|---:|---:|---:|---|
| 미기록 | 미기록 | 미기록 | 미기록 | 미기록 | 미기록 | 기준선 스크린샷 미제공 |

## 하네스 결과 파일 (2026-08-30, 이 PC — 실제 모델 아님)

| 파일 | 내용 | 증거 등급 |
|---|---|---|
| `demo_script_rules_only_20.json` | 시연 대사 11턴·변형 66, LLM 없이 규칙·오버레이만, 20회 동일 | 배관 검증 `[실측: 규칙 경로]` |
| `demo_script_mock_ok_20.json` | mock LLM(정상) 20회 동일, D10만 LLM 경로 | mock — 모델 증거 아님 |
| `demo_script_mock_{timeout,http500,garbage,empty}.json` | LLM 장애 4종에서 전 턴 고정 카드/규칙 유지 | mock — 폴백 배관 검증 |
| `intent_eval_mock.json` `latency_intent_mock.json` | 러너 동작 확인용 | mock — 수치 무의미 |

실제 Qwen2.5 1.5B 수치는 Jetson에서 `eval/run_intent_eval.py`·`eval/latency_bench.py`·`eval/run_demo_script.py --llm http://127.0.0.1:8080/...`로 채운다(`harness/README.md` §3).

### 정본 규칙 수정·주입 후 재실행 (2026-08-30, 이 PC)

| 검사 | 결과 |
|---|---|
| `Co-LLM/eval/run_eval.py` | 14라벨 280/280 · refuse 50/50 · 누출 0 |
| `Co-LLM/tests` · 하네스 `tests` · backend `tests` | 55 OK · 54 OK · 9 OK |
| `run_demo_script.py --llm none --runs 20` | 11턴·변형 66 · 20회 동일 · 정본 규칙 회귀 5건 OK (`demo_script_rules_only_20.json` 갱신) |
| `--llm mock --runs 20` · `--mock-mode timeout/http500/garbage/empty` | 통과 (파일 갱신) |
| `product_voice.py --text … --no-tts` × MAP `app.py --gps-mode replay` | 베이스캠프 저장→수원 탐색(pending)→"그래 거기로 해 줘" 확정→일몰 361분/18:32→버너 카드→복귀 경로(오버레이)→체크포인트. LLM 호출 0 |
