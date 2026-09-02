#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""마이크 -> STT -> 안전 분류 -> 검수 카드 -> TTS 배관 테스트.

    python scripts/voice_loop.py --path b            # 경로 B: LLM 건너뜀 (목표 <= 2.0s)
    python scripts/voice_loop.py --path a            # 경로 A: LLM 라벨 분류 포함 (목표 <= 3.5s)
    python scripts/voice_loop.py --path b --repeat 10
    python scripts/voice_loop.py --path a --text "물 마셔도 되는지 알고 싶어"   # 마이크 없이
    python scripts/voice_loop.py --path b --stt sherpa_onnx --tts piper

측정 결과는 scripts/test_rec/latency.csv 에 append 됩니다.

주의: 이 스크립트는 벤치입니다. 지도 API를 조작하지 않으며 오디오 배관과 단계별 지연만
잽니다. 다만 벤치에서도 LLM 자유 생성문을 재생하지 않습니다. LLM은 라벨 하나만 고르고,
실제 발화는 제품과 같은 검수 카드에서만 가져옵니다.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C          # noqa: E402
import engines as E         # noqa: E402
from ogtech_core import CardRenderer, RuleRouter  # noqa: E402


BAR = "-" * 56
ROUTER = RuleRouter()
CARDS = CardRenderer()


def safe_bench_response(text, *, use_classifier, classifier_fn=E.classify_scenario):
    """자유 생성 없이 라벨 판정과 검수 카드 발화문을 확정한다."""
    classify_s = 0.0
    classify_note = "키워드 규칙에서 확정"

    def measured_classifier(utterance):
        nonlocal classify_s, classify_note
        started = time.time()
        result = classifier_fn(utterance)
        classify_s = time.time() - started
        if isinstance(result, tuple):
            label, classify_note = result
            return label
        classify_note = "분류기 라벨 반환"
        return result

    decision = ROUTER.resolve(
        text or "지금 상황을 어떻게 해야 하나요",
        classifier=measured_classifier if use_classifier else None,
    )
    card = CARDS.render(decision.scenario_id)
    return card.text, decision, classify_s, classify_note


def parse_args():
    ap = argparse.ArgumentParser(description="Co-LLM 음성 배관 테스트")
    ap.add_argument("--path", choices=["a", "b"], default="b",
                    help="b = LLM 건너뜀, a = 필요할 때 LLM 라벨 분류")
    ap.add_argument("--repeat", type=int, default=1, help="반복 횟수")
    ap.add_argument("--seconds", type=float, default=None, help="녹음 초")
    ap.add_argument("--text", default=None, help="마이크 대신 이 텍스트를 STT 결과로 사용")
    ap.add_argument("--stt", default=None, help="config.py 의 STT_ENGINE 덮어쓰기")
    ap.add_argument("--tts", default=None, help="config.py 의 TTS_ENGINE 덮어쓰기")
    ap.add_argument("--no-play", action="store_true", help="합성만 하고 재생하지 않음")
    return ap.parse_args()


def run_once(args, idx):
    stt_name = args.stt or C.STT_ENGINE
    tts_name = args.tts or C.TTS_ENGINE
    budget = C.BUDGET_PATH_B_S if args.path == "b" else C.BUDGET_PATH_A_S

    C.RESULT_DIR.mkdir(parents=True, exist_ok=True)
    rec_wav = C.RESULT_DIR / ("rec_%04d.wav" % idx)
    out_wav = C.RESULT_DIR / ("say_%04d.wav" % idx)

    mem0 = E.mem_available_mb()
    print(BAR)
    print("[#%d  ] 경로 %s  |  STT %s  |  TTS %s" % (idx, args.path.upper(), stt_name, tts_name))
    print("[MEM ] MemAvailable %d MB" % mem0)
    if 0 <= mem0 < C.MEM_GATE_MB:
        print("       ! 게이트 미달(%d MB). 모델을 줄이세요. swap 증설은 해법이 아닙니다."
              % C.MEM_GATE_MB)

    # ---- 1. 녹음 -------------------------------------------------
    rec_s = 0.0
    if args.text is None:
        secs = args.seconds if args.seconds is not None else C.REC_SECONDS
        print("[REC ] %.1f초 녹음 - 지금 말하세요" % secs)
        t = time.time()
        E.record(rec_wav, seconds=secs)
        rec_s = time.time() - t
        print("[REC ] %.2f s -> %s" % (rec_s, rec_wav.name))
    else:
        print("[REC ] (건너뜀 - --text 사용)")

    t_rec_end = time.time()

    # ---- 2. STT --------------------------------------------------
    if args.text is None:
        t = time.time()
        with E.make_stt(stt_name) as stt:
            heard = stt.transcribe(rec_wav)
        stt_s = time.time() - t
    else:
        heard, stt_s = args.text, 0.0
    print('[STT ] %-14s %5.2f s  "%s"' % (stt_name, stt_s, heard or "(빈 결과)"))

    if not heard:
        print("       ! 빈 결과입니다. 녹음 wav 를 직접 들어 보세요:")
        print("         aplay -D %s %s" % (C.SPK_DEVICE, rec_wav))

    # ---- 3. 안전 분류 + 검수 카드 -------------------------------
    speak, decision, llm_s, llm_note = safe_bench_response(
        heard, use_classifier=args.path == "a"
    )
    if llm_s > 0:
        print("[LLM ] %-14s %5.2f s  라벨=%s (%s)"
              % (C.LLM_MODEL, llm_s, decision.scenario_id, llm_note))
    elif args.path == "a":
        print("[LLM ] (규칙에서 확정되어 분류 호출 없음)")
    else:
        print("[LLM ] (건너뜀 - 경로 B)")
    print("[CARD] %-14s source=%s reason=%s"
          % (decision.scenario_id, decision.source, decision.reason))
    print("       %s" % speak.replace("\n", "\n       "))

    # ---- 4. TTS --------------------------------------------------
    t = time.time()
    with E.make_tts(tts_name) as tts:
        tts.synth(speak, out_wav)
    tts_s = time.time() - t
    try:
        dur = E.wav_duration_s(out_wav)
    except Exception:  # noqa: BLE001
        dur = 0.0
    print("[TTS ] %-14s %5.2f s  (음성 길이 %.1f s)" % (tts_name, tts_s, dur))

    total_to_audio = time.time() - t_rec_end

    # ---- 5. 재생 -------------------------------------------------
    play_s = 0.0
    if not args.no_play:
        t = time.time()
        E.play(out_wav)
        play_s = time.time() - t
        print("[PLAY] %.2f s" % play_s)

    mem1 = E.mem_available_mb()

    # ---- 6. 판정 -------------------------------------------------
    verdict = "OK  " if total_to_audio <= budget else "OVER"
    print(BAR)
    print("[%s] 녹음 종료 -> 재생 시작(첫 소리) : %.2f s   (목표 <= %.2f s)"
          % (verdict, total_to_audio, budget))
    print("[MEM ] MemAvailable %d MB  (변화 %+d MB)" % (mem1, mem1 - mem0))
    if verdict == "OVER":
        big = max(("STT", stt_s), ("LLM", llm_s), ("TTS", tts_s), key=lambda kv: kv[1])
        print("       가장 큰 단계: %s %.2f s -> 03_stt_candidates.md / 04_tts_candidates.md" % big)

    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "path": args.path,
        "stt_engine": stt_name,
        "tts_engine": tts_name,
        "rec_s": round(rec_s, 3),
        "stt_s": round(stt_s, 3),
        "llm_s": round(llm_s, 3),
        "tts_s": round(tts_s, 3),
        "play_s": round(play_s, 3),
        "total_to_audio_s": round(total_to_audio, 3),
        "budget_s": budget,
        "verdict": verdict.strip(),
        "mem_before_mb": mem0,
        "mem_after_mb": mem1,
        "heard": heard,
        "spoken": speak.replace("\n", " "),
    }


def append_csv(rows):
    path = C.RESULT_DIR / "latency.csv"
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if new:
            w.writeheader()
        w.writerows(rows)
    return path


def main():
    args = parse_args()
    rows = []
    try:
        for i in range(1, args.repeat + 1):
            rows.append(run_once(args, i))
            if i < args.repeat:
                input("\nEnter 를 누르면 다음 회차...")
    except KeyboardInterrupt:
        print("\n중단했습니다.")
    except FileNotFoundError as e:
        print("\n실행 파일을 찾을 수 없습니다: %s" % e)
        print("02_install_a_to_z.md 의 0-3 단계(apt install)를 확인하세요.")
    except RuntimeError as e:
        print("\n%s" % e)

    if not rows:
        return 1

    path = append_csv(rows)
    print(BAR)
    print("기록: %s  (%d 회)" % (path, len(rows)))

    tot = [r["total_to_audio_s"] for r in rows]
    tot_sorted = sorted(tot)
    print("총지연  최소 %.2f  중앙 %.2f  최대 %.2f  (목표 <= %.2f)"
          % (tot_sorted[0], tot_sorted[len(tot_sorted) // 2], tot_sorted[-1],
             rows[0]["budget_s"]))
    print("단계평균 STT %.2f  LLM %.2f  TTS %.2f"
          % (sum(r["stt_s"] for r in rows) / len(rows),
             sum(r["llm_s"] for r in rows) / len(rows),
             sum(r["tts_s"] for r in rows) / len(rows)))
    print("\n05_test_log.md 를 채워서 공유하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
