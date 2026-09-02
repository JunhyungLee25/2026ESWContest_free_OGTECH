# 실제 Jetson 음성 하드웨어 인수 하네스

`eval/run_hardware_acceptance.py`는 GPIO 버튼, 스피커 loopback, STT/TTS 실행기와
메모리·swap·네트워크 계측기가 수집한 관측값을 판정하는 오프라인 CLI다. 하네스가
GPIO나 스피커를 대신 시뮬레이트하지 않는다. 관측값이 없거나 `simulated=true`,
`evidence_origin=test_fixture`이면 절대 `pass`가 되지 않는다.

## 실제 인수에서 수집할 값

한 run마다 다음을 JSONL 이벤트 또는 JSON의 `runs` 항목으로 기록한다.

- `button_release_monotonic_ns`: 실제 GPIO 해제 edge 또는 현장 operator가 확인한 monotonic 시각
- `first_sound_monotonic_ns`: 스피커 출력이 loopback 마이크에 처음 잡힌 시각
- `first_sound_method=loopback_onset`, `first_sound_source=loopback`
- STT 종료 코드, 기대 키워드 누락 목록, false-positive 키워드 목록
- 별도 `stt_matrix` 관측: 실제 Jetson STT `stt_cases=21`, 누락·false positive `0건`
- `mem_available_min_mb`, `swap_before_mb`, `swap_after_mb`
- STT/TTS 시작·종료 monotonic 시각과 `stt_tts_overlap=false`
- 외부 연결 수, PCM 품질 통과 여부, 실제 TTS 재생 관측 여부, 선택 엔진

`aplay` 프로세스 시작 시각은 첫 소리 시각으로 입력하지 않는다. GPIO edge와 loopback
onset은 같은 monotonic clock 기준이어야 한다.

## 실제 실행 예

실제 수집기는 별도로 실행한다. 아래는 수집된 JSONL을 판정하는 명령이다.

```bash
cd OGTECH-llm/Co-LLM
python3 -B eval/run_hardware_acceptance.py \
  --events /var/local/ogtech/jetson_voice_20.jsonl \
  --runs 20 \
  --output /var/local/ogtech/evidence/jetson_voice_20.json
```

입력 파일이 JSON이면 다음처럼 `{"runs": [...]}` 문서를 읽는다.

```bash
python3 -B eval/run_hardware_acceptance.py \
  --input /var/local/ogtech/jetson_voice_snapshot.json \
  --runs 20 \
  --output /var/local/ogtech/evidence/jetson_voice_20.json
```

JSONL은 다음 이벤트 이름을 사용한다.

```text
run              한 run의 관측값을 한 줄에 모두 기록
button_release   버튼 해제 시각과 출처
first_sound      loopback 첫 onset 시각과 측정법
stt              STT 종료·누락·false positive
resources        MemAvailable·swap
concurrency      STT/TTS 구간과 overlap 플래그
network          외부 연결 수
complete         해당 run의 수집 완료 표시
stt_matrix       run 번호 없이 21문장 STT 전체 평가를 한 번 기록
```

같은 output 파일에 매 이벤트마다 임시 파일을 만들고 `fsync` 후 `os.replace`하므로,
중단되어도 마지막 부분 결과가 남는다. 최종 결과의 `status`는 `pass`, `fail`,
`unverified` 중 하나이며 `pass`일 때만 `pass=true`다.

## 통과 조건

- 요청한 run이 정확히 20개이고 `1..20`이 모두 존재하며 각 run에 `complete=true`
- 경로 B의 버튼 해제→loopback 첫 소리 최댓값 `≤ 2,000 ms`
- 경로 A의 버튼 해제→loopback 첫 소리 최댓값 `≤ 3,500 ms`
- STT 안전 키워드 누락 `0건`, false positive `0건`, STT 종료 코드 `0`
- 별도 STT matrix가 `stt_cases=21`, 누락 `0건`, false positive `0건`, `observed=true`
- 모든 run의 최저 `MemAvailable ≥ 1,024 MB`
- swap 증가 `0 MB 이하`
- STT/TTS timestamp 구간이 겹치지 않음
- 외부 네트워크 연결 `0건`
- PCM 품질 검사·실제 재생·loopback 관측이 모두 존재
- `observed=true`, `simulated=false`, `evidence_origin`이 실제 하드웨어 또는 operator 관측

하나라도 누락되면 `unverified` 또는 `fail`이며 통과로 승격하지 않는다. 테스트용
fixture는 [`tests/fixtures/hardware_acceptance_sample.jsonl`](../tests/fixtures/hardware_acceptance_sample.jsonl)에
있지만 `simulated=true`로 고정되어 의도적으로 통과하지 않는다. 이를 실제 인수 증거로
사용하지 않는다.

## 결과 JSON의 핵심

결과에는 `criteria`, `stt_matrix`, `stt_matrix_verdict`, 원본 `runs`, run별 `run_verdicts`, `summary`, `errors`를 보존한다.
`summary.max_button_release_to_first_sound_ms`, `summary.missing_runs`,
`summary.runs_passed`와 각 run의 `checks`·`reasons`를 먼저 확인한다.

이 문서는 실제 Jetson 청취·GPIO·loopback·STT·MeloTTS/Piper 로드를 이미 완료했다는
뜻이 아니다. 관측 수집기를 실제 장치에서 실행하고 결과 JSON을 보존해야 인수 증거가 된다.
