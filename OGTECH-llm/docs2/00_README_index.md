# docs2 — 오지 생존 도메인 전환 문서 묶음

작성 시작: 2026-08-02
상태: **공개 조사·계산 근거** — 결정과 작업 우선순위는 `PLAN.md`와 동결 결정 기록 `Co-LLM/docs/00_frozen_decisions.md`가 우선합니다.

---

## 이 폴더가 왜 따로 있나

`docs/`는 이전 도메인 전제로 쓰인 아카이브입니다.
`docs2/`는 오지 생존 도메인의 조사·계산 근거입니다.

두 폴더는 전제가 충돌합니다. 섞어서 인용하지 마세요. 현재 결정은 `PLAN.md`와 `Co-LLM/docs/00_frozen_decisions.md`, 상세 근거는 `docs2/`를 따릅니다.

---

## 작품 정의

> 생존 전문가가 아닌 **일반적인 지식만 가진 초보자**가 **혼자서도** 오지·조난 상황에서
> 생존 가능성을 최대한 높이고 사망하지 않도록 돕는, 인터넷 없이 작동하는 착용형 생존 보조 장치.

---

## 읽는 순서

| 파일 | 내용 | 누가 읽나 |
|---|---|---|
| [01_domain_transition_overview.md](01_domain_transition_overview.md) | 무엇이 바뀌고 무엇이 살아남나. 동결 결정 표 개정안 | 전원 (**먼저 읽을 것**) |
| [02_wilderness_case_studies.md](02_wilderness_case_studies.md) | 오지 탐험에서 실제로 뭘 하는지 + 실제 조난·사망 사례 8건 | 전원 |
| [03_beginner_risk_taxonomy.md](03_beginner_risk_taxonomy.md) | 숙련되지 않은 사람이 죽는 경로. 통계 기반 위험 taxonomy | 기획·LLM |
| [04_feature_spec_draft.md](04_feature_spec_draft.md) | 3.1~3.7 요구사항에 대한 설계 답변. 수면 안전 기능 제안 포함 | 전원 |
| [05_llm_harness_redesign.md](05_llm_harness_redesign.md) | Qwen2.5 1.5B로 충분한가. 새 도메인 하네스·스키마·안전 계약 | LLM |
| [06_power_budget_battery.md](06_power_budget_battery.md) | 2주~1달 요구사항 실측 계산. 태양광·발전기 판정 | 하드웨어 |
| [07_hardware_feasibility.md](07_hardware_feasibility.md) | "현재 보드로 다 돌아가나"에 대한 정면 답변 | 전원 |
| [10_parts_selection_bom.md](10_parts_selection_bom.md) | 전 부품 선정·원가·중량·**즉시 발주 목록**. 탈락 부품 사유 포함 | 하드웨어 (**발주 급함**) |
| [11_attachment_pdf_demo_video_spec.md](11_attachment_pdf_demo_video_spec.md) | 첨부 PDF·영상의 기능, 시간축, MAP·음성 동작, 안전 충돌, 구현·검증 기준 | 전원 (**구현 추적 기준**) |
| [12_attachment_feature_verification_log.md](12_attachment_feature_verification_log.md) | 계획→수행→검증→피드백 반복, 구현 추적표, 자동·실화면 증거, 하드웨어 잔여 게이트 | 전원 (**현재 구현 결과**) |

---

## 이 묶음의 역할

이 폴더는 구현 완료를 주장하지 않습니다. P0 범위와 현재 결정은 `PLAN.md`를 따르며, 전력과 하드웨어 타당성의 상세 근거는 [06](06_power_budget_battery.md), [07](07_hardware_feasibility.md)에 있습니다.

---

## 수치 표기 규칙

이 폴더의 모든 수치에는 아래 태그가 붙습니다. 태그 없는 수치는 신뢰하지 마세요.

- `[실측]` — 우리가 직접 측정함
- `[출처]` — 외부 문헌·통계. 링크 병기
- `[추정]` — 계산으로 유도. 가정을 같이 씀
- `[미검증]` — 근거 없음. 확인 필요
