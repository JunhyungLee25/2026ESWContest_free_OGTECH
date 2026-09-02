# config — 시연용 LLM 하네스 설정

동결된 LLM 역할 3개(분류 · 대상/동작 추출 · 카드 다듬기)에 맞춘 설정. 설계는 `docs2/13_demo_harness_design.md`, 코드는 `harness/`.

| 파일 | 내용 | 상태 |
|---|---|---|
| `harness_policy.json` | 주소·타임아웃·허용 동작·polish 모드(off). 시연 프로필 | 적용(2026-08-30) |
| `system_prompt_ko.txt` | 역할 1+2 시스템 프롬프트. 완전 고정, KV 캐시 대상 | 검토용 |
| `fewshot_intent.jsonl` | few-shot 16턴(시연 대사 중심). 고정 프리픽스 | 검토용 |
| `schema_intent.json` | `{"scenario_id": enum14, "action": enum16}` strict | 검토용 |
| `schema_classify.json` | `{"scenario_id": enum14}` — 기존 `classify_scenario` 호환 | 검토용 |
| `system_prompt_polish_ko.txt` `schema_polish.json` `polish_forbidden.json` | 역할 3. `lines[2..4]` 각 40자, 금지어 | 구현·시연 off |
| `stt_lexicon.json` | STT 오인식 보정(라우팅 입력 전용) | 검토용 |
| `keyword_rules_demo.yaml` | 시연 변형 발화 오버레이(정본이 놓친 것만, refuse 없음) | 검토용 |
| `demo_script.json` | 시연 대사 정본 — 발화·변형·기대 라벨/동작/문장 | 검토용 |
| `llama_server.args` | llama-server 옵션(동결 §5) | 검토용 |

정본 `keyword_rules.yaml`·`survival_cards.json`은 `../Co-LLM/config/`에 있고 backend와 바이트 일치가 강제된다. 여기 파일은 그 사본이 아니다.

## 절대 규칙 (변경 없음)

- 좌표·방위·거리 숫자 필드를 스키마에 넣지 않는다. `confidence` 없음.
- `refuse`·생명 라벨은 정본 규칙과 guard가 결정한다. LLM 출력으로 승격하지 않는다.
- `temperature = 0`, `seed` 고정, 프리픽스 고정.
