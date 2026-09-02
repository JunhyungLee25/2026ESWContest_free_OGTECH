# OGTECH-backend — 안전 분기 규칙 엔진 서비스

**OGTECH Kit** (2026 임베디드 소프트웨어 경진대회 자유공모 / 팀 OGTECH) 의 백엔드 저장소입니다.
[조직 개요](https://github.com/2026-ESW-OGTECH) · [다른 저장소 안내](https://github.com/2026-ESW-OGTECH/.github)

---

## 이 저장소가 하는 일 한 줄

**사용자 발화를 검수된 고정 카드로 직행시킬지 판정하는 규칙 엔진을 HTTP로 노출한다.**

정본 분기 엔진은 [OGTECH-llm](https://github.com/2026-ESW-OGTECH/OGTECH-llm)의
`Co-LLM/scripts/ogtech_core.py`이며, 이 저장소는 그 **vendored 사본**(`core/`)을
`http.server` 표준 라이브러리만으로 `:8765`에 서비스합니다.
사본과 정본의 바이트 단위 일치는 `tests/test_vendor_sync.py`가 강제합니다 —
동명 파일이 서로 다른 내용으로 갈라지는 사고를 테스트가 막습니다.

**이 서비스에 LLM은 없습니다.**

- 생명 관련 라벨(lost / daylight / warmth / sleep_safety / injury / refuse)은
  규칙이 확정하고 고정 카드로 직행합니다 (경로 B).
- 규칙이 확정하지 못한 발화는 LLM 없이 추측하지 않고 `unknown` 고정 카드로
  폴백합니다 (`classifier_unavailable`). LLM 다듬기(경로 A)는 Co-LLM 파이프라인의 몫입니다.
- 식용·약물·진단 질의는 다른 어떤 일치보다 먼저 `refuse` 고정 거부 카드로 갑니다.

## 구성

```text
app.py                       HTTP 서비스 (:8765) — 라우팅·에러 처리만 담당
core/ogtech_core.py         정본 미러(vendored). 직접 수정 금지 — 정본에서 고치고 복사
config/keyword_rules.yaml    키워드 게이트 규칙 (정본 미러)
config/survival_cards.json   검수된 고정 카드 (정본 미러)
tests/test_app.py            HTTP 계층 단위 테스트 8개
tests/test_vendor_sync.py    사본=정본 바이트 일치 강제
```

외부 의존성이 없습니다 (표준 라이브러리만).

## 실행

```bash
python3 app.py                # 127.0.0.1:8765 (기본 — 키오스크 로컬 전용)
python3 app.py --host 0.0.0.0 # 외부 노출이 필요할 때만 명시적으로
```

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/health` | 엔진 상태 |
| POST | `/api/classify` | `{"text": ...}` → RouteDecision (scenario_id · path · source · reason) |
| POST | `/api/respond` | `{"text": ..., "device": {...}}` → 분기 결정 + 렌더된 고정 카드 |
| GET | `/api/card/<scenario_id>` | 14개 시나리오 고정 카드 렌더 |

- 모든 오류는 JSON으로 응답합니다 (400 / 404 / 422 / 500). 연결을 끊고 죽지 않습니다.
- 잘못된 JSON, 비정상 Content-Length, text 누락은 각각 400 / 400 / 422 입니다.

## 검증

```bash
python3 -B -m unittest discover -s tests -v
```

| 항목 | 결과 |
|---|---|
| 단위 테스트 9개 (HTTP 8 + 정본 동기화 1) | 통과 `[실측: 2026-08-20]` |
| "버섯 먹어도 되나요" | `refuse` · 경로 B (`refuse_priority`) |
| 규칙 무매칭 발화 | `unknown` · 경로 B (고정 카드 폴백 — 의료 카드로 배정하지 않음) |
| 깨진 JSON | 400 JSON 응답 (연결 절단 없음) |

분기 규칙 자체의 회귀 테스트 55개는 정본 저장소([OGTECH-llm](https://github.com/2026-ESW-OGTECH/OGTECH-llm) `Co-LLM/tests/`)에서 돕니다.
이 저장소로 옮기는 것이 남은 과제입니다.

## 이력

구 도메인(의료 응급처치 구급함) 코드는 2026-08-20 제거했고 git 히스토리에 남아 있습니다.
같은 날 현 도메인 규칙 엔진 서비스로 재구축했습니다.
