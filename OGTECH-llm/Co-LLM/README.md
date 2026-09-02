# Co-LLM — OGTECH 제품 음성 경로와 오디오 벤치

이 폴더에는 세 경로가 함께 있다.

1. `scripts/product_voice.py`: 실제 제품 경로. STT → 안전 라우터 → 검수 카드·지도 명령 → 고품질 TTS.
2. `scripts/voice_loop.py`와 `00`~`06` 스크립트: 엔진별 지연·정확도·오디오 장치 벤치.
3. `scripts/physical_voice.py`와 `09_physical_voice.sh`: STM32 물리 음성 버튼 edge만 받는 push-to-talk 실행기.
4. `scripts/wake_voice.py`와 `10_wake_voice.sh`: "오지야" 호출어 상시 청취 데몬. 호출어 뒤 발화만 1번 경로에 넘긴다. [아래](#호출어-데몬-오지야)

제품 실행기는 LLM이 경로·방위·거리·생존 절차를 만들지 못하게 한다. 지도 제어는 숫자 필드가 없는
열거형 `action`만 로컬 지도 서버로 보내며, 최종 음성은 고정 카드 또는 지도·센서 코드가 계산한 값으로만
만든다.

## 제품 동작

```text
물리 음성 버튼 해제
  → whisper.cpp CPU 인식 (-ng -ac 450 -bo 1 -bs 1 -nf -t 6)
  → refuse 최우선 키워드 게이트
  → 확실한 지도 명령은 열거형 action으로 즉시 실행
  → 확실한 생명 관련 라벨은 LLM 없이 고정 카드
  → 나머지만 Qwen2.5 1.5B가 JSON Schema로 라벨 1개 분류
  → 검수 카드 또는 코드 계산 장치값으로 발화문 확정
  → 고정 녹음 / sherpa-onnx KSS(여성) / MeloTTS / Piper / espeak-ng 순차 폴백
  → 동적 엔진이 모두 실패하면 고정 실패 안내 WAV를 한 번만 재생
  → WAV 품질 검사·음량 정규화·캐시
  → 첫 문장 WAV 즉시 재생 + 재생 중 다음 문장 합성 큐
  → USB 또는 I2S ALSA 스피커 재생
```

STT와 TTS는 동시에 로드하지 않는다. LLM 분류에 실패하거나 2초를 넘기면 재시도 없이 `unknown` 고정
카드로 간다. LLM이 규칙에서 빠진 `lost`, `injury` 같은 생명 관련 라벨을 반환해도 그 판단을 채택하지 않고
한 가지씩 다시 말해 달라는 고정 카드로 전환한다.

## 지도 음성 명령

지원 명령은 다음 열거형으로 고정되어 있다.

| 사용자 동작 | 지도 API action | 동작 |
|---|---|---|
| 베이스캠프 저장 | `save_basecamp` | 현재 GPS fix만 저장 |
| 체크포인트 저장 | `save_checkpoint` | 현재 GPS fix만 저장 |
| 베이스캠프 경로 | `route_basecamp` | 저장 ID만 선택, 경로값은 지도 코드 계산 |
| 목적지 경로 | `route_destination` | 저장 ID만 선택 |
| 최근 체크포인트 | `route_last_checkpoint` | 서버가 마지막 저장 ID를 선택 |
| 3분 전 확정 위치 경로 | `route_recent_trace` | MAP이 같은 부팅의 확정 위치 이력만 골라 경로 계산 |
| 가까운 수원 표식 | `find_nearest_water` | 로컬 POI 중 코드가 최근접 항목 선택 |
| 목적지 확인·취소 | `confirm_destination` / `reject_destination` | 확인 전에는 목적지 미변경 |
| 목적지와 경로 삭제 | `clear_destination` | 현재 목적지와 목적지 경로 제거 |
| 현재 음성 작업 취소 | `cancel` | 대기 중인 목적지 후보를 비우고 다른 지도 상태는 유지 |
| 야간 화면 | `night_on` / `night_off` | SSE로 제품 화면에 즉시 반영 |
| 상태 읽기 | `status` | GPS·경로·일조·환경·전원 코드값을 카드 템플릿으로 읽음 |

음성 API는 `action`과 선택적 `request_id` 외 필드를 거부한다. 좌표를 넣으면 HTTP 422가 반환된다. 수원
표식은 위치 정보일 뿐이며, 응답마다 수질이 확인되지 않았음을 말한다. 포함된 일감호 표식은 시연
카탈로그이므로 목적지로 확정되는 순간 화면의 전역 `DEMO` 상태에도 포함된다.

`repeat_response`는 MAP enum이 아니다. Co-LLM repeat store v2는 `scenario`·`map_action`·`map_status`·`source_id`
provenance만 저장하고, 그 provenance로 검수된 고정 문장을 재구성해 다시 재생한다. `speech` 원문은 저장하지
않으며 지도 상태나 목적지를 바꾸지 않는다. SAFE prefix 위조·extra speech·비계약 MAP action·악성 `message`는
거부하고, MAP 자유 `message`는 TTS로 승격하지 않고 `action`+`status` 고정 문구만 사용한다.

촬영용 `/video/`는 합성 이동과 자동 장면 전환을 사용하는 DEMO이고, 실제 사용자 계약은 MAP
`/product/` 화면이다. 실제 계약에서는 수원 후보를 사용자 음성으로 확인하기 전 목적지로 저장하지 않는다.

## 실행

먼저 지도 서버를 실행한다.

```bash
cd ../MAP
. .venv/bin/activate
python app.py --gps-mode stm32 --gps-port /dev/ttyACM0 --gps-baud 115200
```

마이크 없이 텍스트로 전체 분기와 지도 연결을 확인한다.

```bash
cd ../Co-LLM
python3 scripts/product_voice.py --text "현재 위치 알려 줘" --no-tts --json
python3 scripts/product_voice.py --text "근처 수원 찾아 줘" --no-tts
python3 scripts/product_voice.py --text "네" --no-tts
```

Jetson에서 마이크와 스피커를 포함한 1회 실행은 다음과 같다.

```bash
bash scripts/07_product_voice.sh
```

CO·트레일 이탈·귀환 권고·목적지 도착을 SSE로 계속 감시하고 상태가 새로 시작될 때 먼저 말하게 하려면
지도 서버 다음에 별도 데몬을 실행한다. 같은 파일 잠금을 사용하므로 질문용 STT와 경보용 TTS가 동시에
메모리를 점유하지 않는다.

**CO 경보음은 이 데몬이 낸다**(2026-08-31 STM32 부저 PB0 제거). 경보음(비프) 한 번 뒤에 음성 안내가
붙고, 경보가 지속되는 동안 반복한다 — ALARM 20초, WARN 60초. 경보음 WAV는 첫 재생 때 만들어
`RESULT_DIR`에 두고 다시 쓴다. 키오스크 화면은 배너만 띄우고 읽지 않는다(중복 발화 방지).

```bash
bash scripts/08_device_monitor.sh
```

STM32의 `voice` 버튼을 누르고 말한 뒤 놓는 실제 push-to-talk 경로는 다음 실행기를 쓴다. 버튼 SSE에는
좌표가 없고, 녹음·STT·TTS·Jetson 버튼 동작은 모두 실제 장비에서 아직 `[미검증]`이다.

```bash
bash scripts/09_physical_voice.sh
```

스피커 없이 이벤트와 문장만 확인할 때는 `--no-tts`, 합성만 하고 재생하지 않을 때는 `--no-play`를 쓴다.

MeloTTS가 아직 설치되지 않은 장치에서 배관만 확인하려면 폴백 순서를 명시한다.

```bash
bash scripts/07_product_voice.sh --tts-order espeak
```

`espeak-ng` 한국어는 합성 약 60 ms지만 청취자가 내용을 알아듣지 못했다 `[실측]`. 따라서 위 명령은
배관 확인 전용이다. 제품 기본은 `sherpa,melotts,piper,espeak`(2026-08-30 Jetson 실기는 `sherpa,espeak`)이며,
`sherpa`는 sherpa-onnx VITS `vits-mimic3-ko_KO-kss_low`(KSS 여성 단일 화자, CPU ONNX Runtime)다. Jetson 실측:
모델 로드 3.0 s, 문장당 합성 0.6~1.6 s(2~6 s 오디오), 22,050 Hz 모노 `[실측 2026-08-30]`. 잡음 스케일 0.4/0.6,
길이 스케일 1.22(오전 1.1에서 사용자 청취 "너무 빠르다" 지적으로 0.9배속, 발화 길이 약 +10% `[실측 2026-08-30 오후]`)로 야외 명료도 쪽으로 튜닝했고, `config.py`의 `SHERPA_TTS_*`와 `OGTECH_SHERPA_TTS_*`
환경변수로 바꾼다. 속도는 `SHERPA_TTS_LENGTH_SCALE` 한 곳으로만 조절한다 — sherpa-onnx는 `speed≠1.0`이면 length_scale을 `1/speed`로 덮어쓰므로(실측: ls 1.1에서 speed 0.9 → +4%, 0.8 → +14%) `SHERPA_TTS_SPEED`는 1.0을 유지한다. 속도·화자 파라미터는 TTS 캐시 키에 들어가므로 바꾸면 옛 클립이 재생되지 않고, 고정 클립(`assets/audio/*.wav`)은 같은 설정으로 다시 렌더링해 교체한다(2026-08-30 1.22로 교체). 첫 엔진 실패 이유와 `DEGRADED` 상태를
숨기지 않는다. Piper 한국어 모델은 배포 라이선스를 확인한 파일만 설치한다.

## TTS 품질 계약

- 영상에서 이미 검증한 “네, 목적지로 설정되었습니다.”와 도착 문장은 고정 WAV를 우선 사용한다. 확인
  문장은 질문·3초 침묵이 포함된 촬영 합본이 아니라 답변 구간만 분리한 `destination_confirmed.wav`다.
- 동적 문장은 `GPS`, `CO`, `ppm`, `%`, `m`, 시각 표기를 한국어 발음용으로 정규화한다.
- 생성 결과는 16비트 PCM, 채널·샘플레이트, 길이, 무음, 클리핑 비율을 검사한다.
- 통과한 생성 WAV는 피크를 0.82로 정규화하되 최대 4배 이상 증폭하지 않는다.
- 같은 문장은 SHA-256 키로 캐시해 모델 재로딩 지연을 줄인다.
- 검수 카드의 소수점은 보존하면서 문장 경계만 나누고, 첫 문장이 준비되면 바로 재생한다. 첫 문장 재생
  중 다음 문장을 합성해 전체 답변 생성 완료를 기다리는 지연을 없앤다.
- 선호 엔진이 실패하면 각 후보를 한 번씩만 시도한다. LLM 재시도 금지 계약과 별개로, TTS 엔진 폴백은
  텍스트를 바꾸지 않고 오디오 구현만 바꾸는 동작이다.
- 모든 동적 엔진이 실패하면 `tts_unavailable.wav`로 전환하고 뒤 문장 합성을 중단해 같은 실패 안내가
  문장 수만큼 반복되지 않게 한다.

## Jetson ALSA·systemd 설치

기본 ALSA 이름은 Adafruit 3367 마이크 `Device`, Adafruit 3369 스피커 `UACDemoV10`이다. 카드 번호는
부팅마다 바뀌므로 쓰지 않는다. `/etc/ogtech/audio.env`에 `jetson/audio.env.example`을 복사해
`OGTECH_MIC_DEVICE`, `OGTECH_SPK_DEVICE`, `OGTECH_TTS_ORDER`로만 환경별 장치를 덮어쓴다.

sherpa-onnx 여성 음성 설치(실기 `kit`, 온라인 1회):

```bash
pip3 install --user sherpa-onnx
mkdir -p ~/ogtech_ai/tts/sherpa && cd ~/ogtech_ai/tts/sherpa   # 구 설치는 ~/safeaid_ai/tts/sherpa
curl -L -o kss.tar.bz2 https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-mimic3-ko_KO-kss_low.tar.bz2
tar xjf kss.tar.bz2 && rm kss.tar.bz2
```

KSS 데이터셋 계열 음성은 비상업 조건(CC BY-NC-SA)이 붙을 수 있으므로 상업 배포 전 라이선스를 확인한다.

2026-08-30 실기(Jetson, 사용자 `kit`)에서 확인한 사항:

- 마이크는 `AB13X USB Audio`(카드 id `Audio`), 스피커는 Jieli `UACDemoV1.0`(카드 id `UACDemoV10`)으로 잡혔다.
  `arecord`/`aplay`는 두 장치 모두 정상이었고, 스피커 PCM 믹서가 0%로 잡혀 있어 85%로 올리고 `alsactl store`로 저장했다.
- 키오스크 Firefox가 PulseAudio로 같은 USB 스피커를 열고 있으면 `plughw:` 직접 접근은 `Device or resource busy`로
  실패한다(재현됨). 그래서 실기 `~/.config/ogtech/audio.env`는 `OGTECH_MIC_DEVICE=pulse`, `OGTECH_SPK_DEVICE=pulse`이며
  `pactl set-default-sink/-source`로 기본 장치를 USB 스피커/마이크에 고정했다.
- STT는 `whisper-cli -ng`(CPU)만 쓴다. llama-server가 GPU를 점유한 상태에서 GPU 경로는 `cudaMalloc out of memory`로 죽는다.
- 스피커→마이크 음향 루프백으로 고정 안내 클립을 녹음하면 음량은 충분했지만(RMS 732) base 모델 전사는 틀렸다.
  원본 클립 직접 전사는 정확하므로 소형 스피커 경로의 한계이며, 사람 발화로 하는 마이크 STT 검수는 `[미검증]`이다.

```bash
cd /home/kit/ogtech/OGTECH-llm/Co-LLM
sudo install -d /etc/ogtech
sudo install -m 0644 jetson/audio.env.example /etc/ogtech/audio.env
sudo install -m 0644 jetson/ogtech-physical-voice.service /etc/systemd/system/
sudo install -m 0644 jetson/ogtech-device-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ogtech-physical-voice.service
sudo systemctl enable --now ogtech-device-monitor.service
```

`sudo` 설치 권한이 없는 `kit` 실기 환경에서는 `jetson/user/*.service`와 사용자 환경 파일을 쓴다.

```bash
mkdir -p ~/.config/ogtech ~/.config/systemd/user
cp jetson/audio.env.example ~/.config/ogtech/audio.env
cp jetson/user/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ogtech-physical-voice.service ogtech-device-monitor.service
```

두 서비스는 `ogtech-map.service` 뒤에 실행되며, 실제 Jetson·ALSA·STM32 버튼 서비스 설치와 청취 결과는
`[미검증]`이다.

## 표현 경우의 수

`config/keyword_rules.yaml`은 JSON과 호환되는 제한형 YAML이다. 별도 PyYAML 없이 읽힌다. `refuse` 우선,
확정 지도 명령, 단일 시나리오, 다중 매칭 순서로 판정한다.

`eval/voice_cases.json`에는 지도 명령·확인 대화·14개 시나리오·거짓 양성 경계 표현 `183개`가 있다 `[실측]`.
`cases_classify.jsonl`은 라벨마다 `20개`씩 `280개`, `cases_refuse.jsonl`은 식용·약물·진단·침습·프롬프트
추출 유도 `50개`다 `[실측]`. 합계 `513개(183+280+50)` 표현을 검증한다 `[실측]`. 많은 예문을 LLM 런타임
프롬프트에 넣지 않는다. 검수 예문이 판단 규칙·고정 카드·JSON Schema 안전 계약을 바꾸는 지시문처럼
작동하지 않게 분리하고, Xavier의 prefill 지연과 KV 캐시 무효화를 피하기 위해서다. 런타임 프롬프트에는
14개 라벨 정의와 JSON Schema만 두고, 표현 확장은 결정 규칙과 회귀 평가로 검증한다.

```bash
python3 -B -m unittest discover -s tests -v
python3 eval/run_eval.py
python3 eval/run_eval.py --llm   # Jetson의 로컬 llama-server까지 포함
```

현재 계약 테스트 결과는 `59/59` 통과 `[실측: 2026-08-30, PC·Jetson Python 3.8]`이다. repeat store v2와 MAP 음성 경계는
`test_repeat_uses_only_previous_verified_safe_response`, `test_forged_safe_prefix_store_is_not_replayed`,
`test_store_with_extra_speech_field_is_rejected_even_with_valid_provenance`,
`test_store_rejects_impossible_map_action_status_pair`,
`test_repeat_preserves_synthetic_map_error_provenance`,
`test_mismatched_map_contract_uses_fixed_contract_notice`, `test_map_message_is_never_promoted_to_safe_speech`로
검증했다 `[실측]`. 실제 Jetson에서의
MeloTTS/Piper 청취 품질과 STT `21문장` 거짓 양성 `0건`은 별도 하드웨어 인수 전까지 `[미검증]`이다.

영상 10단계 종단 검증은 다음 명령으로 실행하며, 결과는
[`eval/results/video_scenario_20.json`](eval/results/video_scenario_20.json)에 저장한다.

```bash
python3 -B eval/run_video_scenario.py --runs 20 --output eval/results/video_scenario_20.json
```

확정 결과는 10단계 `20/20`, 외부 네트워크 `0`, 최악 `258.69 ms`, 고정 도착 TTS `20/20`,
`configured_engine_failure_fallback_runs` 기준 **하네스에 구성된 테스트 엔진 전부 실패 고정 fallback `20/20`**이다 `[실측]`.
이는 실제 MeloTTS/Piper/espeak 로드·청취 실증이 아니다 `[미검증]`.

## 실제 하드웨어 인수 하네스

20회 실제 버튼·STT·TTS·스피커 loopback·메모리·swap·네트워크 관측은
[`docs/07_hardware_acceptance_harness.md`](docs/07_hardware_acceptance_harness.md)의 입력 스키마와
`eval/run_hardware_acceptance.py`로 판정한다. 하네스는 장치를 흉내 내지 않으며, 관측값이 없거나
`simulated=true`이면 통과로 올리지 않는다.

```bash
python3 -B eval/run_hardware_acceptance.py \
  --events /var/local/ogtech/jetson_voice_20.jsonl \
  --runs 20 \
  --output eval/results/hardware_acceptance_20.json
```

이 하네스의 문서·코드·fixture는 준비됐지만 실제 STT/TTS/Jetson/버튼 인수 관측은 `[미검증]`이다.

## 오디오 벤치

제품 경로와 별개로 기존 벤치는 유지한다.

```text
0단  00_check_audio.sh : 녹음 → 재생
1단  voice_loop.py -b  : STT → 고정 문장 → TTS
2단  voice_loop.py -a  : STT → llama-server 라벨 분류 → 검수 카드 → TTS 지연 분해
평가 04/05             : 21문장 게이트 재현율·거짓 양성·최악 지연·전력
```

USB 검증 장치는 Adafruit 3367 마이크와 Adafruit 3369 스피커다. 최종 I2S 전환 여부는 미결 항목 #7이며,
ALSA 장치 이름만 바꾸면 상위 파이프라인은 유지된다. 카드 번호는 부팅마다 바뀌므로 사용하지 않고
`/proc/asound/card*/usbid`가 있는 USB 카드만 자동 탐지한다.

## 주요 파일

```text
Co-LLM/
├── config.py                         엔진·모델·품질·지도 API 설정
├── config/
│   ├── keyword_rules.yaml            키워드·지도 명령·다중 매칭 규칙
│   ├── survival_cards.json           14개 안전 카드
│   └── fixed_audio.json              검증된 고정 WAV 연결
├── eval/voice_cases.json             표현 변형 회귀 세트
├── eval/cases_classify.jsonl          14라벨 × 20문장 평가 세트
├── eval/cases_refuse.jsonl            금지 유도 50문장 평가 세트
├── eval/run_eval.py                   정확도·혼동·refuse 누출 평가기
├── eval/run_hardware_acceptance.py    실제 Jetson 20회 인수 관측 판정기
├── docs/07_hardware_acceptance_harness.md      인수 입력 스키마·실행 절차
├── jetson/
│   ├── audio.env.example              ALSA 이름·TTS 순서 환경 변수 예시
│   ├── ogtech-physical-voice.service STM32 음성 버튼 push-to-talk 서비스
│   └── ogtech-device-monitor.service 선제 음성 알림 서비스
├── scripts/
│   ├── ogtech_core.py               규칙 라우터·카드 렌더러
│   ├── product_assistant.py          지도 API 연결·최종 문장 확정
│   ├── product_voice.py              제품 1회 실행기
│   ├── physical_voice.py             STM32 물리 음성 버튼 push-to-talk 실행기
│   ├── wake_voice.py                 "오지야" 호출어 상시 청취 데몬 (VAD + whisper 호출어 판별)
│   ├── device_monitor.py              선제 경보 SSE 감시·CO 경보음(비프)+음성 출력
│   ├── pipeline_gate.py               STT·TTS 순차 실행 잠금
│   ├── tts_pipeline.py               고품질 TTS·품질 게이트·캐시
│   ├── engines.py                    STT/TTS/LLM 분류 어댑터
│   ├── 07_product_voice.sh            질문용 Jetson 실행 래퍼
│   ├── 08_device_monitor.sh           선제 경보 Jetson 실행 래퍼
│   ├── 09_physical_voice.sh           물리 음성 버튼 Jetson 실행 래퍼
│   └── 10_wake_voice.sh               호출어 데몬 Jetson 실행 래퍼
└── tests/                             외부 모델 없이 실행되는 계약 테스트
```

실제 음성 WAV와 캐시는 `scripts/test_rec/`에만 만들며 Git에 올리지 않는다.

## 호출어 데몬 (오지야)

`scripts/wake_voice.py`. 물리 음성 버튼 없이 시리처럼 "오지야"를 부르면 "네, 무엇을 도와드릴까요?"로 답하고,
이어지는 발화를 1번 제품 경로(`product_voice._build_assistant`, DemoAssistant)에 그대로 넘긴다. 화면(`/video/`)은 건드리지
않는다. 지도 명령 결과는 `/api/voice` 이벤트로 이미 화면에 간다.

```text
마이크(arecord pulse, 16 kHz raw) → Silero VAD(sherpa-onnx, 32 ms 창) → 발화 WAV(앞 0.25 s·뒤 0.15 s 여유, 1.6 s 미만은 무음 채움)
  idle          : 0.25~4 s 발화만 whisper → 정규화 문장이 호출어 변형으로 시작하면 세션 시작 (긴 발화는 whisper 를 돌리지 않는다)
  await_command : 인사말이 끝난 뒤 10 s 안의 발화 → 제품 경로 → 응답. 후보 확인 질문이면 await_confirm(10 s), 아니면 followup(6 s)
  followup      : 호출어 없이 한 번 더 물을 수 있다. 시간이 지나면 idle
  재생 중·직후 0.3 s 에 잡힌 발화는 버린다(자기 소리 반응 방지). STT·지도·TTS 는 pipeline_gate 잠금 안에서만 돈다
```

설정은 `config/wake_voice.json` 하나다.

| 항목 | 내용 |
|---|---|
| `wake.*_variants` | Jetson whisper base 가 "오지야"를 받아 적은 변형(2026-09-02 합성음 실측). `exact` 는 발화 전체일 때만, `command_only` 는 뒤에 명령이 붙을 때만 |
| `stt_prompt_extra_wake` / `_command` | 정본 `stt_prompt.txt` 뒤에 이 프로세스에서만 덧붙이는 프롬프트. 대기 중엔 "오지야.", 세션 중엔 시연 명령 문장. 둘을 합치면 반복 생성이 늘었다 |
| `lexicon` | 데몬 전용 STT 보정(포스/포수/호술이→호수, 운동화습도→온도와 습도, 내 설정→네 설정). 라우팅 전에만 쓴다 |
| `confirmation` | 후보 대기 중 긍정·부정 어휘. 지도 서버 후보면 정본 라우터에 "네"/"아니"로 넘기고, 대본 후보면 데몬이 답한다 |
| `script.lake` | "네, {N}미터 이내에 호수가 있습니다. 목적지로 지정해 드릴까요?" · N 은 기준점→가장 가까운 `water_source` 표식(지도 서버와 같은 `poi_catalog.json`) haversine 을 100 m 올림, `distance_floor_m`(500) 이하면 500. 기준점은 GPS fix, 없으면 `no_fix_reference`(촬영 화면 1·2번 장면의 현재 위치). **GPS 미수신이면 지도 서버가 수원 검색을 거부하므로** 데몬이 대본대로 답하고, 확인("어/네") 시 화면 터치와 같은 `/api/waypoints set` 으로 표식 좌표를 목적지로 등록한 뒤 "목적지가 설정되었습니다." GPS 가 있으면 지도 서버 후보 → `confirm_destination` 경로이며 문구만 같다 |
| `script.weather` | 온·습도는 `/api/device` `environment`(화면과 같은 값)로 "네, 온도는 24점 1도입니다. 습도는 77퍼센트입니다." 두 절로 말한다. 한 절에 이어 붙이면 "도"가 뭉개지고, 소수점 "24.1"은 sherpa 가 못 읽어 "24점 1"로 적는다(2026-09-02 실기·whisper 되읽기) |
| `strip_demo_prefix` | 데몬이 읽는 모든 문장에서 "데모 값 기준으로," 를 뺀다(2026-09-02 지시). 제품 경로는 그대로 |
| `tts.*` | 1.36 = 정본 1.22 의 0.9배속. 응답은 쉼표·문장 단위 구절로 따로 합성해 사이에 0.1 s(문장 사이 0.3 s) 무음을 넣고 **한 WAV 로 이어 한 번에** 재생한다(긴 문장 발음 뭉개짐·짧은 클립이 pulse 시작 지연에 먹히는 문제, 2026-09-02 실기). 머리의 "네"는 정본 속도(1.22). sherpa VITS 는 `speed=1/length_scale` 로 같은 효과(2026-09-02 실측 2.04 s 일치) |
| `confirmation.short_utterance_max_s` | 확인 질문 뒤 1 s 이하 발화가 빈 문자열로 받아 적히면 긍정으로 본다("어"·"응"은 whisper 가 못 적음, 2026-09-02 실기). 부정은 "아니"·"취소"처럼 말한다 |
| `timeouts.*` | 대기 창(명령 10 s·확인 10 s·후속 6 s)은 **데몬이 말을 끝낸 시점**부터 잰다. 처리 시각부터 재면 인사말 합성·재생(약 5 s)이 포함돼 사용자가 말을 끝내기 전에 만료된다(2026-09-02 실기 로그) |

제품 화면(`/product/`)의 야간 모드 버튼은 서버 `interface.night` 를 토글한다(화면만 바꾸면 2 s 뒤 스냅샷이 되돌린다 — 2026-09-02 실기).
화면(`/video/?live=1`)은 좌표·경로가 촬영 시나리오 상수라 서버 웨이포인트를 그리지 않았다. `video_app.js` 의
`applyServerDestination` 이 페이지가 뜬 뒤 서버 목적지 `saved_at` 이 바뀌면 2번 장면(음성 요청 → 일감호 설정)으로 넘겨
목적지·경로를 그린다(첫 스냅샷은 기준값). 소리는 데몬만 낸다.

실행·검증:

```bash
bash scripts/10_wake_voice.sh                    # 마이크 대기, 스피커 출력
bash scripts/10_wake_voice.sh --no-play          # 응답 WAV 만 만들고 소리는 내지 않음 (OGTECH_WAKE_NO_PLAY=1 과 같음)
bash scripts/10_wake_voice.sh --input-wavs 오지야.wav 호수.wav 네.wav --once --no-play   # 16 kHz WAV 로 대화 재현
python3 -m unittest tests.test_wake_voice        # 53개: 호출어 판정·분절·대본 오버레이·상태기계·구절 합성·짧은 확인
```

이벤트는 `scripts/test_rec/wake_events.jsonl`(`wake / no_wake / command / timeout / ignored_*`)에 남고, 마지막 발화 WAV 는
`wake_seg_N.wav`, 응답은 `wake_say*.wav` 다. 30 s 마다 `[MIC ] peak` 줄이 찍히며 0 이 계속되면 장치·게인을 의심한다.
Jetson 사용자 서비스는 `jetson/user/ogtech-wake-voice.service`(`~/.config/ogtech/wake.env` 의 `OGTECH_WAKE_NO_PLAY=1` 이면 무음).
Silero 모델은 `config.VAD_ONNX_MODEL`(`~/ogtech_ai/stt/silero_vad.onnx`, 구 설치 `~/safeaid_ai/stt/`)이며 없으면 에너지 분절로 내려간다.

2026-09-02 Jetson 검증(합성음 KSS·espeak, `--no-play`): 대본 #1(오지야 → 근처 호수 → 확인 → 목적지 등록·화면 2번 장면 전환) 4/4,
대본 #2(온·습도) 5/5 + 표현 변형 2종, 한 호흡("오지야 근처에…") 1/1, 부정 발화 3종 무반응, 실마이크 90 s 대기 오작동 0.
사람 목소리는 사용자가 실기에서 "오지야" 인식을 확인했다(20:21, 마이크 peak 32767 클리핑 — 20~30 cm 거리 권장).
알려진 한계: whisper 가 같은 어절을 반복 생성하면(합성음 2건) 접기(`collapse_repeats`)로 문장은 살리지만 6 s 가량 늦어진다.
