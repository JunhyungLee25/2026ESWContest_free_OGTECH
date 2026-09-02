# 02. 설치 A to Z

Xavier NX(JetPack 5.1.x, Ubuntu 20.04) 기준입니다. 모든 명령은 Jetson에서 실행합니다.
명령은 ASCII로만 썼습니다 — 터미널 인코딩 사고를 피하기 위해서입니다.

작업 폴더는 기존 문서와 같은 `~/ogtech_ai/`를 그대로 씁니다.

---

## 0단계 — 준비 (10분)

### 0-1. 폴더와 가상환경

```bash
mkdir -p ~/ogtech_ai/{stt,tts,llm}   # 모델 설치 위치. 녹음/측정 결과는 scripts/test_rec/ 에 남습니다
cd ~/ogtech_ai
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
```

### 0-2. 이 저장소의 Co-LLM 폴더를 Jetson으로

```bash
cd ~/ogtech_ai
git clone https://github.com/2026-ESW-OGTECH/OGTECH-llm.git
ln -sfn ~/ogtech_ai/OGTECH-llm/Co-LLM ~/ogtech_ai/colm
```

Windows에서 파일을 복사해 왔다면 줄바꿈부터 정리합니다. 안 하면 `bash: \r: command not found`가 납니다.

```bash
cd ~/ogtech_ai/colm
sed -i 's/\r$//' scripts/*.sh scripts/*.py config.py
chmod +x scripts/00_check_audio.sh
```

### 0-3. 시스템 패키지

```bash
sudo apt update
sudo apt install -y alsa-utils ffmpeg espeak-ng build-essential cmake git
```

- `alsa-utils` — `arecord` / `aplay` / `alsamixer`
- `ffmpeg` — 샘플레이트 변환
- `espeak-ng` — TTS 1안 (배관 뚫기용)

### 0-4. 상태 모니터

```bash
sudo pip3 install -U jetson-stats   # venv 밖에 설치
sudo reboot
```

재부팅 후 별도 터미널에서 `jtop`을 띄워 두고 테스트합니다. GPU 사용률과 RAM 피크를 봐야 합니다.

---

## 1단계 — 오디오 루프백 (5분) ★ 여기서 막히면 아래로 내려가지 마세요

마이크와 스피커를 **서로 다른 USB 포트**에 꽂고:

```bash
cd ~/ogtech_ai/colm
bash scripts/00_check_audio.sh
```

스크립트가 하는 일:

1. `lsusb` / `arecord -l` / `aplay -l`로 장치 존재 확인
2. `arecord -L` / `aplay -L`에서 **쓸 수 있는 이름 후보**를 뽑아 출력
3. 5초 녹음 → 즉시 재생
4. `config.py`에 넣을 문자열을 그대로 찍어 줌

### 통과 판정

- 자기 목소리가 스피커로 되돌아 나온다 → **통과**
- 소리가 아예 안 난다 → `alsamixer` → `F6`으로 스피커 카드 선택 → `Master`/`PCM` 볼륨 확인, `M`으로 음소거 해제
- `Invalid argument` → `hw:`를 `plughw:`로 바꿨는지 확인
- `Device or resource busy` → PulseAudio가 물고 있음. `pulseaudio -k` 또는 `systemctl --user stop pulseaudio.socket pulseaudio.service`
- 재생 중 Jetson 재부팅 → 전원 문제. [`01_hardware_check.md`](01_hardware_check.md) 4절

### 확정한 장치 이름을 `config.py`에 기록

```bash
nano ~/ogtech_ai/colm/config.py
```

```python
MIC_DEVICE = "plughw:CARD=Device,DEV=0"     # 스크립트가 찍어 준 값으로
SPK_DEVICE = "plughw:CARD=Device_1,DEV=0"
```

---

## 2단계 — STT 1안 설치: whisper.cpp + CUDA (20분)

1안으로 whisper.cpp를 고른 이유는 정확도가 아니라 **aarch64에서 확실히 빌드되고,
이미 쓰는 llama.cpp와 같은 ggml 생태계**라서입니다. 파이썬 휠 호환 문제가 아예 없습니다.

```bash
cd ~/ogtech_ai/stt
git clone https://github.com/ggml-org/whisper.cpp
cd whisper.cpp
cmake -B build -DGGML_CUDA=1
cmake --build build -j4 --config Release
```

빌드에 10~20분 걸립니다. `-j4`인 이유는 `-j$(nproc)`로 하면 Xavier NX 8GB에서 OOM이 나기 때문입니다.

### 모델 받기

```bash
bash ./models/download-ggml-model.sh small
```

`ggml-small.bin`은 f16 약 488 MB입니다. 메모리 예산(STT 온디맨드 ~0.5 GB)에 맞으므로 그대로 씁니다.
느리면 `base`로 내리고, 못 알아들으면 `medium`으로 올립니다 — 순서는 [`03_stt_candidates.md`](03_stt_candidates.md) 참조.

### 단독 동작 확인

```bash
cd ~/ogtech_ai
arecord -D plughw:CARD=Device,DEV=0 -f S16_LE -r 16000 -c 1 -d 5 samples/mic16k.wav
time ~/ogtech_ai/stt/whisper.cpp/build/bin/whisper-cli \
  -m ~/ogtech_ai/stt/whisper.cpp/models/ggml-small.bin \
  -f samples/mic16k.wav -l ko -nt -np
```

- 한국어 텍스트가 나오면 통과입니다. **걸린 초를 적어 두세요.**
- 실행 중 `jtop`에서 GPU 사용률이 올라가야 합니다. 안 올라가면 CPU로 돈 겁니다 —
  `cmake -B build -DGGML_CUDA=1`을 다시 확인하고 `build/`를 지우고 재빌드하세요.
- 바이너리 이름이 `whisper-cli`가 아니라 `main`인 구버전이면 `config.py`의 `WHISPER_CPP_BIN`을 고칩니다.

---

## 3단계 — TTS 1안 설치: espeak-ng (2분)

espeak-ng는 **음질이 좋아서가 아니라 무조건 되기 때문에** 1안입니다.
포먼트 합성이라 로봇 소리가 나지만, 합성 지연이 사실상 0이라
**"STT가 느린 건가 TTS가 느린 건가"를 분리하는 기준선**이 됩니다.

```bash
espeak-ng -v ko -s 150 -w /tmp/tts_test.wav --stdin <<'EOF'
해가 지기까지 40분 남았습니다.
EOF
aplay -D plughw:CARD=Device_1,DEV=0 /tmp/tts_test.wav
```

한국어가 들리면 통과입니다. 발음이 어색한 건 정상입니다 — 3단계에서 갈아탑니다.

`-v ko` 가 없다는 오류가 나면 설치된 음성 목록을 확인합니다.

```bash
espeak-ng --voices | grep -i ko
```

---

## 4단계 — 1단 배관 테스트: LLM 없이 (10분) ★ 핵심 측정

**여기서 나오는 숫자가 경로 B 예산(≤ 2.0초)입니다.**

```bash
cd ~/ogtech_ai/colm
source ~/ogtech_ai/venv/bin/activate
python scripts/voice_loop.py --path b
```

동작:

```
Enter -> 5초 녹음 -> STT 로드/인식/언로드 -> [고정 문장] -> TTS 로드/합성/언로드 -> 재생
```

출력 예시:

```
[MEM ] MemAvailable 3980 MB
[REC ] 5.00 s  -> /home/.../results/rec_0001.wav
[STT ] whisper_cpp    1.24 s  "해 지기 전에 돌아갈 수 있어?"
[LLM ] (건너뜀 - 경로 B)
[TTS ] espeak         0.06 s  22050 Hz
[PLAY] 2.31 s
------------------------------------------------
[TOTAL] 녹음 종료 -> 첫 소리 : 1.42 s   (목표 <= 2.00 s)
[MEM ] MemAvailable 3960 MB
```

**보는 곳은 `녹음 종료 -> 첫 소리` 한 줄입니다.** 나머지는 어디를 깎을지 정하는 재료입니다.

- 2.0초를 넘으면 → 거의 항상 STT입니다. [`03_stt_candidates.md`](03_stt_candidates.md)로 갑니다
- STT는 빠른데 TTS가 느리면 → [`04_tts_candidates.md`](04_tts_candidates.md)로 갑니다
- `MemAvailable`이 1 GB 아래로 내려가면 → 모델 크기를 줄입니다. **swap을 늘려서 통과시키지 않습니다**

연속 측정:

```bash
python scripts/voice_loop.py --path b --repeat 10
```

10회 결과가 `scripts/test_rec/latency.csv`에 append 됩니다.

---

## 5단계 — llama-server 띄우기 (10분)

이미 Qwen2.5 1.5B Q4_K_M을 준비해 두었다면 이 단계는 확인만 하면 됩니다.

```bash
cd ~/ogtech_ai/llm
ls models/    # qwen2.5-1.5b-instruct-q4_k_m.gguf 가 있어야 함
```

`docs/00_frozen_decisions.md` §5에 동결된 실행 옵션 그대로 띄웁니다.

```bash
~/ogtech_ai/llm/llama.cpp/build/bin/llama-server \
  -m ~/ogtech_ai/llm/models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  --host 127.0.0.1 --port 8080 \
  -ngl 99 \
  -fa \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --cache-reuse 256 \
  --mlock \
  -b 512 -ub 512 \
  --threads 4 \
  --parallel 1
```

별도 터미널에서 살아 있는지 확인:

```bash
curl -s http://127.0.0.1:8080/health
```

> 포트 8080은 llama-server 직결입니다. 제품의 backend(8765)·frontend(8780)와 다릅니다.
> 이 벤치는 backend를 거치지 않습니다.

---

## 6단계 — 2단 전체 테스트 (10분)

```bash
cd ~/ogtech_ai/colm
source ~/ogtech_ai/venv/bin/activate
python scripts/voice_loop.py --path a
```

**여기서 나오는 숫자가 경로 A 예산(≤ 3.5초)입니다.**

```
[STT ] whisper_cpp    1.24 s  "물 마셔도 되는지 알고 싶어"
[LLM ] qwen2.5-1.5b   0.91 s  (프롬프트 74 tok / 생성 38 tok)
[TTS ] espeak         0.07 s
------------------------------------------------
[TOTAL] 녹음 종료 -> 첫 소리 : 2.26 s   (목표 <= 3.50 s)
```

LLM이 1초를 크게 넘으면 프롬프트 길이부터 보세요. `docs/00_frozen_decisions.md` §5 기준 Xavier prefill은
413 tok/s [실측]이므로 **프롬프트 토큰 수가 곧 지연**입니다. 벤치 프롬프트는 60토큰 안에 맞춰 두었습니다.

---

## 7단계 — 기록

[`05_test_log.md`](05_test_log.md)를 복사해서 채웁니다.
`scripts/test_rec/latency.csv`를 같이 첨부하면 어디를 깎을지 바로 판단할 수 있습니다.

---

## 트러블슈팅

| 증상                                 | 원인                       | 조치                                                                                     |
| ------------------------------------ | -------------------------- | ---------------------------------------------------------------------------------------- |
| `arecord: Device or resource busy` | PulseAudio 점유            | `pulseaudio -k`, 안 되면 `systemctl --user stop pulseaudio.socket`                   |
| `Invalid argument` (녹음/재생)     | `hw:`로 미지원 포맷 요청 | `plughw:`로 변경                                                                       |
| 녹음은 되는데 무음                   | 캡처 볼륨 0 또는 음소거    | `alsamixer` → `F6` 마이크 카드 → `F4`(캡처) → 볼륨 올리고 `Space`로 캡처 활성 |
| 재생 중`underrun` 반복             | USB 전류 부족              | 볼륨 낮춤, 다른 포트, 셀프 파워 허브                                                     |
| 부팅마다 카드 번호가 바뀜            | `hw:1,0` 하드코딩        | `plughw:CARD=이름` 사용                                                                |
| whisper가 CPU로만 돔                 | CUDA 미빌드                | `rm -rf build` 후 `-DGGML_CUDA=1`로 재빌드, `jtop`에서 GPU 확인                    |
| whisper 결과가 영어                  | 언어 자동감지              | `-l ko` 명시 확인                                                                      |
| whisper 결과가`[음악]`, `.` 등   | 무음 구간                  | 마이크 게인 확인, 마이크 20 cm 이내                                                      |
| `whisper-cli: not found`           | 구버전 바이너리명          | `config.py`의 `WHISPER_CPP_BIN`을 `main`으로                                       |
| llama-server 연결 거부               | 미기동/포트 다름           | `curl 127.0.0.1:8080/health`                                                           |
| `MemAvailable` 1 GB 미만           | 동시 로드                  | STT·TTS 동시 로드 여부 확인. 모델 축소.**swap 증설 금지**                         |
| `bash: \r: command not found`      | Windows CRLF               | `sed -i 's/\r$//' scripts/*`                                                           |

---

## 이 테스트가 끝나면 확정되는 것

1. USB 오디오 경로에서 **음성 왕복이 성립하는가** (예/아니오)
2. **경로 B 예산 2.0초가 현실적인가** — STT+TTS 실측
3. **경로 A 예산 3.5초가 현실적인가** — STT+LLM+TTS 실측
4. 세 예산 중 어디를 깎아야 하는가 — 단계별 초 단위 분해
5. `docs/00_frozen_decisions.md` §10 미결 항목 #2(**한국어 TTS 엔진 확정**)의 후보 3안 중 하나
