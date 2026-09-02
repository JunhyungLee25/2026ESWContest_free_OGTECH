# OGTECH Kit GitHub 운영 절차

이 문서는 2026-ESW-OGTECH 워크스페이스의 커밋·브랜치·PR·저장소 메타데이터·공개 전환 절차 정본입니다.
앞으로 GitHub 업로드 작업은 긴 일회성 프롬프트를 다시 작성하지 않고 이 문서와 `AGENTS.md`를 따릅니다.

## 1. 적용 범위

| 로컬 폴더 | 원격 저장소 | 공개 후보 |
|---|---|---|
| `OGTECH-org/` | `2026-ESW-OGTECH/.github` | 예 |
| `OGTECH-backend/` | `2026-ESW-OGTECH/OGTECH-backend` | 예 |
| `OGTECH-embedded/` | `2026-ESW-OGTECH/OGTECH-embedded` | 예 |
| `OGTECH-frontend/` | `2026-ESW-OGTECH/OGTECH-frontend` | 예 |
| `OGTECH-llm/` | `2026-ESW-OGTECH/OGTECH-llm` | 예 |
| `Vscode-Workspace-Setting/` | 동명 저장소 | 아니요. 계속 private 유지 |

워크스페이스 루트는 Git 저장소가 아닙니다. 루트의 `.venv/`, `legacy/`, 제출 서류, 작업 로그는 컴포넌트 저장소의 스테이징 대상이 아닙니다. 다만 과거 원격 이력의 공개 위험은 별도로 검사합니다.

## 2. 절대 규칙

- 기본은 `main` 직접 push 금지이며 작업 브랜치와 draft PR을 사용합니다. 단, 저장소 관리자가 이번 작업처럼
  **통합과 GitHub push를 명시적으로 지시한 경우**에는 테스트·원격 ahead/behind·staging 검사를 기록한 뒤
  non-force fast-forward push할 수 있습니다.
- 사용자 승인 없이 PR을 병합하거나 저장소를 public으로 바꾸지 않습니다.
- `git push --force`, 히스토리 재작성, 저장소·조직 이름 변경을 하지 않습니다.
- 이슈와 저장소는 삭제하지 않습니다. 폐기 이슈는 근거를 남기고 닫습니다.
- 사용자 변경을 `reset`, `checkout`, `stash`로 숨기거나 폐기하지 않습니다.
- 토큰·Wi-Fi 자격 증명·개인정보·실제 GPS 트랙·비공개 Notion 내보내기를 커밋하지 않습니다.
- 비밀값 탐지 결과는 값이 아니라 저장소·경로·규칙만 기록합니다.
- 테스트하지 않은 항목을 통과라고 보고하지 않습니다.
- 수치 주장은 `AGENTS.md`의 `[실측]`·`[출처]`·`[추정]`·`[미검증]` 규칙을 따릅니다. 모델명·버전·포트·이슈 번호·날짜 같은 식별자는 예외입니다.

## 3. `.github` 저장소 파일 구분

로컬 `OGTECH-org/`는 원격 `2026-ESW-OGTECH/.github`입니다.

- `profile/README.md`: `github.com/2026-ESW-OGTECH`에 렌더링되는 조직 프로필입니다.
- 루트 `README.md`: `.github` 저장소 자체 안내입니다. 조직 프로필이 아닙니다.
- `PLAN.md`: 작품 정의와 P0 작업 정본입니다.
- 에이전트 작업 규칙(`AGENTS.md`·`CLAUDE.md`)은 2026-08-20부터 **저장소에 두지 않고 워크스페이스 로컬에만 둡니다.** 심사 대상 저장소에는 코드와 설명 문서만 남깁니다.
- `docs/`: 공통 설계·안전·제출·GitHub 운영 문서입니다.

루트 README를 만들거나 고칠 때 `profile/README.md`를 덮어쓰지 않습니다. 루트 README에는 4개 코드 저장소 안내, 문서 목록, 안전 경계 요약을 둡니다. 심사위원이 조직에서 처음 여는 문서라고 보고 씁니다.

## 4. 공개 파일 경계

### 4.1 `OGTECH-llm/docs2/`

공개 커밋 대상은 공개 인덱스에 등재된 조사 근거·계산·BOM입니다. 내부 검토 자료는 로컬에만 유지하며 공개 인덱스·README·PLAN에서 언급하지 않습니다.

내부 자료는 `OGTECH-llm/.gitignore`의 `docs2` 제외 규칙으로 차단합니다. 과거에 이미 추적된 상태가 발견되면 임의로 히스토리를 고치지 말고 중단하여 보고합니다.

`OGTECH-llm/docs/`(이전 의료 도메인 아카이브)는 2026-08-20에 저장소에서 제거하고 워크스페이스 `legacy/`로 옮겼습니다. 현재 도메인 정본은 `OGTECH-llm/docs2/`입니다.

### 4.2 `OGTECH-llm/MAP/`

`MAP/`은 오프라인 지도 변환·경로 계산 검증 앱입니다. 다음은 공개 커밋 대상입니다.

- Python 소스와 `requirements.txt`
- 정적 UI
- 테스트
- `README.md`, `SCRIPT_REVIEW.md`
- `sample_data/konkuk_walk.graphml`
- `sample_data/ATTRIBUTION.md`
- `runtime/.gitkeep`

다음은 런타임 산출물이므로 커밋하지 않습니다.

- `MAP/runtime/active_map.json`
- `MAP/runtime/uploads/`
- `.venv/`, `__pycache__/`

MAP 내부 `.gitignore`가 위 산출물을 실제로 제외하는지 매 PR 전에 `git check-ignore -v`로 확인합니다. 샘플 GraphML은 OSM 파생 데이터와 고정 DEMO 좌표만 허용합니다. Air530 실측 좌표나 사용자 이동 기록이 섞이면 커밋을 중단합니다.

현재 ESP32 캡처 코드는 저장소 이력 보존을 위해 즉시 삭제하지 않습니다. 카메라는 현 도메인 범위 밖이며 현재 미사용이라는 사실을 `OGTECH-embedded/README.md`에 명시합니다. 새 기능으로 소개하지 않습니다.

## 5. 반복 업로드 절차

### 5.1 단계 로그

작업별 로그를 워크스페이스 루트의 다음 경로에 둡니다.

`worklogs/github/<YYYY-MM-DD>-<작업명>/`

권장 파일:

- `00-preflight.md`
- `01-scope.md`
- `02-validation.md`
- `03-pull-request.md`
- `04-merge-and-metadata.md`
- `05-public-audit.md`

로그는 어느 저장소에도 커밋하지 않습니다. 명령, 대상, 변경 파일, 검사 결과, 커밋 해시, PR URL, 성공·실패만 기록하고 인증 정보와 비밀값 원문은 남기지 않습니다.

### 5.2 사전 점검

1. `gh auth status`가 성공하는지 확인합니다.
2. 각 저장소에서 `git status`, 현재 브랜치, origin URL을 확인합니다.
3. `dubious ownership`이 난 정확한 저장소만 `safe.directory`에 추가합니다. `safe.directory=*`와 워크스페이스 루트 등록은 금지합니다.
4. `git fetch --prune origin` 후 `HEAD...origin/main`을 확인합니다.
5. `behind > 0`이면 중단합니다. `ahead > 0`은 미푸시 커밋을 검토해 작업 범위가 맞으면 진행할 수 있습니다. ahead와 behind가 모두 있으면 중단합니다.
6. 열린 PR과 같은 목적의 기존 브랜치가 있는지 확인합니다.

줄바꿈 대량 변경을 구분하기 위해 다음 두 결과를 함께 비교합니다.

```powershell
git diff --stat
git diff --stat --ignore-all-space
```

차이가 크면 `.gitattributes`와 CRLF/LF 변환 상태를 확인하고 작업을 중단합니다.

### 5.3 브랜치와 스테이징

브랜치 이름은 `<종류>/<간단한-목적>-<날짜>` 형식을 사용합니다.

예: `docs/wilderness-domain-transition-20260803`

무검토 `git add -A`를 사용하지 않습니다. 파일을 명시적으로 stage한 뒤 다음을 확인합니다.

```powershell
git status --short
git diff --cached --stat
git diff --cached --check
```

`.venv/`, `runtime/`, `__pycache__/`, 실제 GPS 기록, 비공개 문서, 단계 로그, 예상 밖 대용량 파일이 하나라도 stage되면 중단합니다.

### 5.4 검사와 테스트

현재 워킹트리와 stage 파일을 대상으로 비밀값·개인정보·실제 GPS 트랙을 검사합니다. 공개 전환 전에는 모든 원격 브랜치와 태그를 fetch한 후 전체 이력을 별도로 검사합니다.

기본 테스트:

```powershell
# frontend
python -B -m unittest discover -s tests -v

# backend
python -c "import app; print('import ok')"
python -B -m unittest discover -s tests -v

# MAP: OGTECH-frontend/MAP에서 실행
python -B -m pytest tests/ -q
```

embedded는 STM32CubeIDE(NUCLEO-H7A3ZI-Q, `-Q` 디바이스)로 빌드한 뒤 플래시/RAM 사용량과 로그를 기록합니다. LLM 평가는 실행 코드가 있을 때만 수행합니다.

이번 변경이 원인인 테스트 실패는 중단 조건입니다. 변경 전부터 존재하던 코드 결함이나 누락된 로컬 도구체인은 원인과 재현 명령을 기록하고 PR의 알려진 제한 사항으로 남길 수 있습니다. `Ran 0 tests`는 테스트 없음입니다.

### 5.5 커밋·push·PR 게이트

커밋 메시지와 PR은 한국어로 씁니다. 브랜치 push 후 draft PR을 만들고 다음을 보고합니다.

- 저장소와 브랜치
- 변경 파일·용량
- 검사와 테스트 결과
- 커밋 해시
- draft PR URL
- 알려진 제한 사항

사용자가 `merge해`라고 승인하기 전에는 병합하지 않습니다. 승인 후에도 default branch와 CI 상태를 다시 확인합니다.

### 5.6 메타데이터·이슈

저장소 설명·토픽·라벨·이슈 변경은 관련 문서 PR이 `main`에 병합된 뒤 진행합니다. 그래야 이슈에서 참조하는 `PLAN.md` 링크가 깨지지 않습니다.

- 라벨은 저장소별 자원입니다. 같은 표준 라벨을 필요한 저장소마다 생성·갱신합니다.
- 기존의 관련 없는 라벨은 삭제하지 않습니다.
- P0 체크리스트는 체크박스마다 이슈를 만들지 않고 컴포넌트별 상위 추적 이슈로 묶습니다.
- 기존 이슈를 대체할 때는 새 이슈 링크와 전환 근거를 남기고 `not planned`로 닫습니다.
- 중복 제목이나 같은 PLAN 항목을 참조하는 열린 이슈가 있으면 새로 만들지 않습니다.

## 6. 라이선스와 지도 귀속

공개 전환 전에 코드 라이선스를 팀이 결정해야 합니다. MIT와 Apache-2.0 중 하나를 에이전트가 임의로 선택하지 않습니다. 저장소별 LICENSE 유무와 제3자 라이선스 호환성을 표로 보고하고 사용자 승인을 기다립니다.

OSM 데이터나 파생 타일·그래프를 표시하는 화면에는 사용자가 볼 수 있는 귀속을 유지합니다.

```text
© OpenStreetMap contributors
```

관련 README와 조직 프로필의 제3자 고지에는 최소한 다음을 포함합니다.

- 지도 데이터: © OpenStreetMap contributors, ODbL 1.0
- Qwen2.5 1.5B: Apache-2.0
- llama.cpp: MIT
- 확정된 TTS·STT·지도 렌더러와 데이터셋 라이선스

MAP 샘플의 상세 출처는 `OGTECH-llm/MAP/sample_data/ATTRIBUTION.md`를 정본으로 사용합니다. OSM 귀속을 UI에 표시하는 작업은 backend 또는 frontend P0 이슈의 완료 조건에 포함합니다.

## 7. 공개 전환 게이트

공개 후보는 `.github`, backend, embedded, frontend, llm 5개입니다. `Vscode-Workspace-Setting`은 제외합니다.

다음을 저장소별로 보고합니다.

| 항목 | 판정 기준 |
|---|---|
| 비밀값 | 전체 브랜치·태그·이력에서 0건 |
| 개인정보 | 전체 이력에서 0건 |
| GPS | 실제 사용자 트랙·비공개 좌표 0건 |
| 비공개 문서 | Notion 내보내기·내부 심사 대응 문서 0건 |
| 구 구현 | 현재 default branch와 README의 불일치 명시 |
| 라이선스 | 프로젝트와 제3자 라이선스 결정 완료 |
| OSM 귀속 | README와 실제 지도 UI 모두 표시 |
| 구현 주장 | 실행·실측으로 확인된 범위만 현재형 사용 |

하나라도 미확인 또는 실패면 공개를 권하지 않습니다. private에서 public으로 바꾸는 명령은 별도 사용자 승인 후에만 실행합니다. 다시 private으로 바꿀 수 있어도 이미 clone된 이력은 회수할 수 없습니다.

## 8. 앞으로 사용할 짧은 요청

일반 업로드:

> 현재 변경분을 `AGENTS.md`와 `OGTECH-org/docs/GITHUB_OPERATIONS.md`에 따라 GitHub draft PR로 준비해. 단계 로그를 남기고 각 GATE에서 멈춰.

PR 병합 이후 정리:

> 승인한 PR을 병합한 뒤 GitHub 운영 절차에 따라 메타데이터·라벨·이슈를 정리해. 공개 전환은 하지 마.

공개 준비 점검:

> GitHub 운영 절차의 공개 전환 게이트만 실행해. 전체 이력을 검사하고 저장소별 표를 보고한 뒤 멈춰.
