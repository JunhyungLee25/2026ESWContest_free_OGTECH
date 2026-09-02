# 부록 A — 재현 절차

본문 [`01_main.md`](01_main.md)의 모든 수치를 다시 만들어 내는 절차입니다.
명령은 ASCII로만 썼습니다. 실행은 전부 Jetson입니다.

---

## A.0 전제

| 항목 | 값 |
|---|---|
| 보드 | Jetson Xavier NX 8 GB, JetPack 5.1.x |
| 사용자 | `kit` (홈 `/home/kit`) |
| 작업 폴더 | `~/ogtech_ai/stt/whisper.cpp` (빌드), `~/scripts` (측정) |
| USB 장치 | 마이크 Adafruit 3367, 스피커 Adafruit 3369 |

`Co-LLM/scripts/` 폴더를 `~/scripts`로 복사합니다. Windows에서 옮겼다면 줄바꿈부터 정리합니다.

```bash
sed -i 's/\r$//' ~/scripts/*.sh ~/scripts/*.py
```

---

## A.1 시스템 패키지

```bash
sudo apt update && sudo apt install -y alsa-utils ffmpeg espeak-ng build-essential cmake git
```

---

## A.2 CUDA PATH — JetPack의 함정

JetPack은 `nvcc`를 `/usr/local/cuda/bin`에 설치하지만 PATH에 넣지 않습니다.
이대로 CMake를 돌리면 `No CMAKE_CUDA_COMPILER could be found`가 납니다.

```bash
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc && echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc && source ~/.bashrc
```

```bash
nvcc --version
```

---

## A.3 whisper.cpp 빌드

```bash
mkdir -p ~/ogtech_ai/stt && cd ~/ogtech_ai/stt && git clone https://github.com/ggml-org/whisper.cpp && cd whisper.cpp
```

**두 플래그를 반드시 함께** 줍니다. `GGML_NATIVE`(기본 ON)가 켜져 있으면
`GGML_CPU_ARM_ARCH`는 무시됩니다.

```bash
cd ~/ogtech_ai/stt/whisper.cpp && rm -rf build && cmake -B build -DGGML_CUDA=1 -DCMAKE_CUDA_ARCHITECTURES=72 -DGGML_NATIVE=OFF -DGGML_CPU_ARM_ARCH=armv8.2-a+fp16+dotprod
```

구성 출력에서 아래 네 줄을 확인합니다.

```
-- Performing Test HAVE_DOTPROD - Success
-- Performing Test HAVE_FP16_VECTOR_ARITHMETIC - Success
-- Using CMAKE_CUDA_ARCHITECTURES=72 CMAKE_CUDA_ARCHITECTURES_NATIVE=72-real
-- Including CUDA backend
```

```bash
cd ~/ogtech_ai/stt/whisper.cpp && time cmake --build build -j4 --config Release
```

`-j4`인 이유는 `-j$(nproc)`로 하면 8 GB에서 OOM이 나기 때문입니다. 죽으면 `-j2`로 낮추면
기존 오브젝트를 재사용해 이어서 빌드합니다.

---

## A.4 모델 내려받기

Ubuntu 20.04의 curl 7.68에는 `--retry-all-errors`(7.71+)가 없어 스크립트가 실패합니다.
해당 옵션만 제거합니다.

```bash
cd ~/ogtech_ai/stt/whisper.cpp && sed -i 's/ --retry-all-errors//' models/download-ggml-model.sh
```

```bash
cd ~/ogtech_ai/stt/whisper.cpp && bash ./models/download-ggml-model.sh base && bash ./models/download-ggml-model.sh small
```

VAD 모델:

```bash
cd ~/ogtech_ai/stt/whisper.cpp && bash ./models/download-vad-model.sh silero-v5.1.2
```

크기 확인 — base 141 MB, small 465 MB, VAD 864 KB입니다.

```bash
ls -lh ~/ogtech_ai/stt/whisper.cpp/models/
```

---

## A.5 오디오 장치 확인

마이크·스피커를 **서로 다른 USB 포트**에 꽂습니다. 포트가 부족하면 아래처럼 나눠서 씁니다.

| 스크립트 | 하는 일 | 필요한 장치 |
|---|---|---|
| `00_check_audio.sh` | 녹음 → 재생 루프백 | 마이크 + 스피커 |
| `00_check_audio.sh --spk-only` | 재생만 | 스피커 |
| `01_record.sh [초]` | 녹음만 | 마이크 |
| `02_play.sh [파일]` | 재생만 (`--tts`로 한국어 TTS) | 스피커 |
| `03_echo.sh` | STT → TTS | **없음** |
| `04_record_set.sh [초]` | 한국어 6발화 녹음 | 마이크 |
| `05_bench.sh` | 지연·정확도·전력·열 동시 측정 | **없음** |

```bash
cd ~/scripts && bash 00_check_audio.sh --spk-only
```

`plughw:CARD=...` 이름을 그대로 `config.py`에 넣습니다. 카드 번호(`hw:1,0`)는 부팅마다
바뀌므로 쓰지 않습니다. `hw:` 대신 `plughw:`를 쓰는 이유는 16 kHz 모노 리샘플링 때문입니다.

**알려진 간헐 실패**: `plughw:`는 배타 접근이라 핫플러그 직후 `Device or resource busy`가
날 수 있습니다. `02_play.sh`는 `default` → `pulse` → `sysdefault` 순으로 폴백합니다.

---

## A.6 클럭 스냅샷 — `--restore`를 쓰려면 필수

`jetson_clocks --restore`는 사전에 저장된 스냅샷이 필요합니다.
**반드시 재부팅 직후(기본 클럭 상태)에** 저장해야 합니다. 고정된 상태에서 저장하면
복원 지점이 "고정"이 되어 영영 안 풀립니다.

```bash
sudo reboot
```

```bash
sudo jetson_clocks --store
```

현재 클럭 상태 확인 — `schedutil`이면 기본, `performance`면 고정된 상태입니다.

```bash
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
```

---

## A.7 단일 파일 지연 측정 (E1~E7)

영어 샘플로 기준선을 잡습니다.

```bash
cd ~/ogtech_ai/stt/whisper.cpp && time ./build/bin/whisper-cli -m models/ggml-small.bin -f samples/jfk.wav
```

축별 조작:

```bash
cd ~/ogtech_ai/stt/whisper.cpp && ./build/bin/whisper-cli -m models/ggml-small.bin -f samples/jfk.wav -bo 1 -bs 1 2>&1 | grep -E 'load time|encode time|total time'
```

```bash
cd ~/ogtech_ai/stt/whisper.cpp && ./build/bin/whisper-cli -m models/ggml-small.bin -f samples/jfk.wav -bo 1 -bs 1 -nfa 2>&1 | grep -E 'load time|encode time|total time'
```

```bash
cd ~/ogtech_ai/stt/whisper.cpp && ./build/bin/whisper-cli -m models/ggml-base.bin -f samples/jfk.wav -bo 1 -bs 1 -ac 600 -ng 2>&1 | grep -E 'load time|encode time|total time'
```

---

## A.8 한국어 발화 수집 (E10)

Enter를 누른 **직후** 녹음이 시작됩니다. 누르고 0.5초쯤 뒤에 말하면 첫 음절이 안 잘립니다.

```bash
cd ~/scripts && bash 04_record_set.sh 5
```

문장 하나만 다시 녹음하려면 번호를 붙입니다.

```bash
cd ~/scripts && bash 04_record_set.sh 5 3
```

`peak`가 500 미만이면 무음입니다. `alsamixer` → `F6` 마이크 카드 → `F4` → 게인 올리고 `Space`.

---

## A.9 통합 벤치 (E8~E11)

한 실행이 유휴 15초 + 추론 6회로 약 1.5분입니다. `sudo` 비밀번호를 한 번 묻습니다.
**유휴 15초 동안은 기기를 건드리지 않습니다.**

```bash
cd ~/scripts && bash 05_bench.sh base_cpu_vad ~/ogtech_ai/stt/whisper.cpp/models/ggml-base.bin -ng -ac 450 -bo 1 -bs 1 -nf --vad -vm ~/ogtech_ai/stt/whisper.cpp/models/ggml-silero-v5.1.2.bin
```

버스트 클럭 정책으로 재려면 `CLOCKS=1`을 붙입니다 — 스크립트가 `jetson_clocks` → 실행 →
`--restore`를 자동으로 합니다.

```bash
cd ~/scripts && CLOCKS=1 bash 05_bench.sh base_cpu_burst ~/ogtech_ai/stt/whisper.cpp/models/ggml-base.bin -ng -ac 600 -bo 1 -bs 1
```

산출물:

```
~/scripts/test_rec/bench_runs.csv     발화별 인식 결과 + 단계 지연
~/scripts/test_rec/bench_power.csv    구성별 지연·전력·열 요약
```

---

## A.10 반복 루프 진단 (E11)

문제 파일 하나만 단독으로 돌려 `decode ... / N runs`를 봅니다.
정상이면 10~20 runs, 폭주하면 200 runs를 넘습니다.

```bash
cd ~/ogtech_ai/stt/whisper.cpp && ./build/bin/whisper-cli -m models/ggml-base.bin -f ~/scripts/test_rec/ko_1.wav -l ko -nt -ng -ac 450 -bo 1 -bs 1 -nf
```

```bash
cd ~/ogtech_ai/stt/whisper.cpp && ./build/bin/whisper-cli -m models/ggml-base.bin -f ~/scripts/test_rec/ko_1.wav -l ko -nt -ng -ac 450 -bo 1 -bs 1 -nf --vad -vm models/ggml-silero-v5.1.2.bin
```

`fallbacks = 0 p / 0 h`이면 temperature fallback이 아니라 순수 반복 생성입니다.

---

## A.11 측정 오염을 피하는 규칙

실측 중 세 번 겪은 문제입니다. 재현할 때 그대로 밟지 않도록 적어 둡니다.

| 함정 | 증상 | 대책 |
|---|---|---|
| `jetson_clocks`가 재부팅 전까지 유지됨 | "미고정" 라벨인데 실제로는 고정 상태 | 측정 전 `scaling_governor` 확인 |
| `tegrastats --logfile`이 이어쓰기 | 활성 전력이 회차마다 단조 감소 | 실행 전 로그 삭제 |
| `tegrastats`가 root로 로그 생성 | 일반 권한 truncate가 `Permission denied` | `sudo rm -f`로 삭제 |
| `[ -f /root/... ]`를 일반 권한으로 평가 | `jetson_clocks --store`가 Y/N 프롬프트 | `sudo test -f`로 확인 |

지연·정확도는 실행마다 whisper stderr에서 독립적으로 파싱하므로 위 오염에 영향받지 않습니다.
**오염되는 것은 전력 값뿐입니다.**

---

## 부록 C — 스크립트 목록

`Co-LLM/scripts/` 안에 있습니다. `test_rec/`는 자동 생성되며 `.gitignore`로 커밋을 막습니다.

| 파일 | 역할 | 오디오 장치 |
|---|---|---|
| `00_check_audio.sh` | 루프백 확인, 장치 이름 출력, 녹음 레벨 검사 | 마이크+스피커 |
| `01_record.sh` | 녹음 전용 | 마이크 |
| `02_play.sh` | 재생 전용, `--tts`로 한국어 TTS 확인 | 스피커 |
| `03_echo.sh` | STT → 텍스트 → TTS | 불필요 |
| `04_record_set.sh` | 한국어 6발화 수집 + 기대 키워드 표 생성 | 마이크 |
| `05_bench.sh` | 지연·정확도·전력·열 통합 측정 | 불필요 |
| `config.py` | 장치 이름·엔진 선택 (편집 전용) | — |
| `engines.py` | STT/TTS 어댑터. `with` 블록으로 로드/언로드 강제 | — |
| `voice_loop.py` | 마이크→STT→LLM→TTS 전체 + 단계 지연 | 마이크+스피커 |
