# -*- coding: utf-8 -*-
"""
학교 수업 종소리 알리미 (School Bell Notifier) v1.1
Windows 데스크톱 프로그램 (Python Tkinter + 내장 차임벨 합성 엔진 + 사용자 MP3/WAV 오디오 지원)

- 외부 라이브러리(pip install) 설치 없이 Windows 기본 API(winmm MCI / winsound)만으로
  사용자가 직접 준비한 MP3, WAV, WMA, M4A 음원 파일을 완벽하게 재생 및 볼륨 조절합니다.
- 자체 물리 모델링 배음(Harmonics) + ADSR 엔벨로프 차임벨 합성 엔진도 함께 내장되어 있어
  별도 음원 파일이 없어도 고품질 학교 종소리를 기본 제공합니다.
"""

import sys
import io
import os
import math
import wave
import struct
import json
import time
import datetime
import tempfile
import threading
import shutil
import ctypes
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# pythonw 실행 시 stdout/stderr None 방어
for _n in ("stdout", "stderr"):
    _s = getattr(sys, _n, None)
    if _s is None:
        setattr(sys, _n, open(os.devnull, "w", encoding="utf-8"))
    else:
        try:
            setattr(sys, _n, io.TextIOWrapper(_s.buffer, encoding="utf-8"))
        except Exception:
            pass

try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

# High-DPI 배율 대응 (Windows)
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

VERSION = "1.2 (일괄 벨소리 설정)"

if getattr(sys, "frozen", False):
    HERE = os.path.dirname(sys.executable)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(HERE, "bell_config.json")
CUSTOM_SOUNDS_DIR = os.path.join(HERE, "custom_sounds")
os.makedirs(CUSTOM_SOUNDS_DIR, exist_ok=True)

TEMP_WAV_DIR = os.path.join(tempfile.gettempdir(), "school_bell_wavs")
os.makedirs(TEMP_WAV_DIR, exist_ok=True)

SUPPORTED_AUDIO_EXTS = (".mp3", ".wav", ".wma", ".m4a", ".aac", ".ogg", ".mid")

MCI_ALIAS = "SchoolBell_MCI"
_playback_timer = None


# ==============================================================================
# 1. 고성능 Windows 오디오 엔진 (MCI / winsound 기반 MP3 & WAV 비동기 재생)
# ==============================================================================

def stop_bell():
    """현재 재생 중인 벨소리 및 사용자 MP3를 즉시 정지합니다."""
    global _playback_timer
    if _playback_timer is not None:
        try:
            _playback_timer.cancel()
        except Exception:
            pass
        _playback_timer = None

    # 1) winsound 정지
    if HAS_WINSOUND:
        try:
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    # 2) Windows MCI 정지 및 장치 닫기
    try:
        if hasattr(ctypes, "windll") and hasattr(ctypes.windll, "winmm"):
            ctypes.windll.winmm.mciSendStringW(f"stop {MCI_ALIAS}", None, 0, 0)
            ctypes.windll.winmm.mciSendStringW(f"close {MCI_ALIAS}", None, 0, 0)
    except Exception:
        pass


def play_audio_file(file_path: str, volume_pct: int = 85, max_seconds: int = 0) -> bool:
    """
    MP3, WAV, WMA 등 오디오 파일을 Windows MCI API를 통해 비동기로 재생합니다.
    - volume_pct: 볼륨 (0 ~ 100)
    - max_seconds: 0이면 파일 끝까지 재생, >0이면 지정된 초 후 자동 정지
    """
    stop_bell()

    if not os.path.exists(file_path):
        print(f"[오디오 오류] 파일이 존재하지 않습니다: {file_path}")
        return False

    ext = os.path.splitext(file_path)[1].lower()

    # 경로에 한글/공백/특수문자가 있을 때 MCI 호환을 위한 8.3 Short Path 변환 시도
    target_path = file_path
    try:
        buf = ctypes.create_unicode_buffer(1024)
        res = ctypes.windll.kernel32.GetShortPathNameW(file_path, buf, 1024)
        if res > 0 and buf.value:
            target_path = buf.value
    except Exception:
        pass

    played = False

    # 1) Windows winmm.mciSendStringW 시도 (Windows 기본 내장 MP3 코덱 사용)
    try:
        winmm = ctypes.windll.winmm
        winmm.mciSendStringW(f"close {MCI_ALIAS}", None, 0, 0)

        # mpegvideo 드라이버로 열기 (MP3, WAV, WMA 등 지원)
        open_cmd = f'open "{target_path}" type mpegvideo alias {MCI_ALIAS}'
        err = winmm.mciSendStringW(open_cmd, None, 0, 0)
        if err != 0:
            # 기본 형식으로 재시도
            open_cmd = f'open "{target_path}" alias {MCI_ALIAS}'
            err = winmm.mciSendStringW(open_cmd, None, 0, 0)

        if err == 0:
            # 볼륨 조절 (MCI 볼륨 범위: 0 ~ 1000)
            vol_mci = max(0, min(1000, int(volume_pct * 10)))
            winmm.mciSendStringW(f"setaudio {MCI_ALIAS} volume to {vol_mci}", None, 0, 0)
            # 처음부터 비동기 재생
            winmm.mciSendStringW(f"play {MCI_ALIAS} from 0", None, 0, 0)
            played = True
        else:
            print(f"[MCI 열기 실패 코드: {err}]: {file_path}")
    except Exception as e:
        print(f"[MCI 재생 예외 발생]: {e}")

    # 2) WAV 파일인데 MCI가 실패한 경우 winsound 폴백
    if not played and ext == ".wav" and HAS_WINSOUND:
        try:
            winsound.PlaySound(None, winsound.SND_PURGE)
            winsound.PlaySound(file_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            played = True
        except Exception as e:
            print(f"[winsound 재생 오류]: {e}")

    # 재생 시간 제한(초)이 설정되어 있다면 백그라운드 타이머로 자동 정지
    if played and max_seconds > 0:
        global _playback_timer
        _playback_timer = threading.Timer(float(max_seconds), stop_bell)
        _playback_timer.daemon = True
        _playback_timer.start()

    return played


# ==============================================================================
# 2. 내장 학교 차임벨 오디오 물리 합성 엔진 (44.1kHz, 16-bit PCM WAV)
# ==============================================================================

NOTE_FREQS = {
    "G3": 196.00, "A3": 220.00, "B3": 246.94,
    "C4": 261.63, "D4": 293.66, "E4": 329.63, "F4": 349.23, "F#4": 369.99, "G4": 392.00, "A4": 440.00, "B4": 493.88,
    "C5": 523.25, "D5": 587.33, "E5": 659.25, "F5": 698.46, "G5": 783.99, "A5": 880.00, "C6": 1046.50,
}

BELL_MELODIES = {
    "westminster": {
        "name": "웨스트민스터 차임벨 (기본 학교종)",
        "desc": "가장 친숙한 표준 학교 차임벨 멜로디",
        "events": [
            (0.00, ["E4", "E5"], 0.75, 0.90),
            (0.75, ["C4", "C5"], 0.75, 0.85),
            (1.50, ["D4", "D5"], 0.75, 0.90),
            (2.25, ["G3", "G4"], 1.50, 0.95),
            (4.00, ["G3", "G4"], 0.75, 0.85),
            (4.75, ["D4", "D5"], 0.75, 0.90),
            (5.50, ["E4", "E5"], 0.75, 0.95),
            (6.25, ["C4", "G4", "C5", "E5"], 2.40, 1.00),
        ],
    },
    "class_start": {
        "name": "수업 시작 차임 (밝은 상행 화음)",
        "desc": "수업 시작을 산뜻하게 알리는 상행 차임벨",
        "events": [
            (0.00, ["C4", "C5"], 0.45, 0.80),
            (0.45, ["E4", "E5"], 0.45, 0.85),
            (0.90, ["G4", "G5"], 0.45, 0.90),
            (1.35, ["C5", "E5", "G5", "C6"], 1.60, 1.00),
            (3.15, ["G4", "B4", "D5"], 0.55, 0.85),
            (3.70, ["C4", "E4", "G4", "C5"], 2.20, 1.00),
        ],
    },
    "class_end": {
        "name": "수업 종료 / 쉬는 시간 차임 (부드러운 종)",
        "desc": "수업 마무리를 편안하게 알리는 하행 차임벨",
        "events": [
            (0.00, ["E5", "G5"], 0.50, 0.85),
            (0.50, ["D5", "F5"], 0.50, 0.85),
            (1.00, ["C5", "E5"], 0.50, 0.85),
            (1.50, ["G4", "B4"], 0.85, 0.90),
            (2.50, ["A4", "C5"], 0.55, 0.85),
            (3.05, ["G4", "B4"], 0.55, 0.85),
            (3.60, ["C4", "E4", "G4", "C5"], 2.30, 1.00),
        ],
    },
    "prep_chime": {
        "name": "예비종 (짧은 딩동댕동 4음)",
        "desc": "수업 시작 1~2분 전 착석을 알리는 짧은 차임벨",
        "events": [
            (0.00, ["C5"], 0.45, 0.85),
            (0.45, ["E5"], 0.45, 0.90),
            (0.90, ["G5"], 0.45, 0.95),
            (1.35, ["C5", "E5", "G5", "C6"], 1.80, 1.00),
        ],
    },
    "lunch_bell": {
        "name": "점심시간 멜로디 (즐거운 차임)",
        "desc": "점심시간 및 휴식 시간을 알리는 밝고 경쾌한 멜로디",
        "events": [
            (0.00, ["G4"], 0.35, 0.85),
            (0.35, ["C5"], 0.35, 0.90),
            (0.70, ["E5"], 0.35, 0.90),
            (1.05, ["G5"], 0.70, 0.95),
            (1.75, ["E5"], 0.35, 0.85),
            (2.10, ["G5"], 0.95, 1.00),
            (3.20, ["F5"], 0.40, 0.85),
            (3.60, ["D5"], 0.40, 0.85),
            (4.00, ["B4"], 0.40, 0.85),
            (4.40, ["G4"], 0.70, 0.90),
            (5.10, ["C4", "E4", "G4", "C5", "E5"], 2.30, 1.00),
        ],
    },
    "school_song": {
        "name": "학교종이 땡땡땡 (오르골 차임)",
        "desc": "전통적인 '학교종이 땡땡땡' 오르골 스타일 차임벨",
        "events": [
            (0.00, ["G4", "C4"], 0.45, 0.90),
            (0.45, ["G4"], 0.45, 0.85),
            (0.90, ["A4", "F4"], 0.45, 0.90),
            (1.35, ["A4"], 0.45, 0.85),
            (1.80, ["G4", "C4"], 0.45, 0.90),
            (2.25, ["G4"], 0.45, 0.85),
            (2.70, ["E4", "C4"], 0.90, 0.95),
            (3.75, ["G4", "C4"], 0.45, 0.90),
            (4.20, ["G4"], 0.45, 0.85),
            (4.65, ["E4"], 0.45, 0.85),
            (5.10, ["E4"], 0.45, 0.85),
            (5.55, ["D4", "G3", "B3"], 1.80, 1.00),
        ],
    },
}


def synthesize_bell_wav(melody_key: str, volume_pct: int = 85) -> str:
    """내장 차임벨 합성 WAV를 생성하고 캐시 파일 경로를 반환합니다."""
    if melody_key not in BELL_MELODIES:
        melody_key = "westminster"
    vol_clamped = max(5, min(100, int(volume_pct)))
    wav_path = os.path.join(TEMP_WAV_DIR, f"bell_{melody_key}_v{vol_clamped}.wav")
    if os.path.exists(wav_path) and os.path.getsize(wav_path) > 1000:
        return wav_path

    events = BELL_MELODIES[melody_key]["events"]
    sample_rate = 44100
    total_duration = max(ev[0] + ev[2] for ev in events) + 1.2
    num_samples = int(sample_rate * total_duration)
    buf = [0.0] * num_samples

    harmonics = [
        (1.0, 1.00, 1.0),
        (2.0, 0.55, 1.5),
        (3.0, 0.30, 2.2),
        (4.0, 0.15, 3.0),
        (5.0, 0.08, 4.0),
        (2.76, 0.12, 2.8),
    ]

    for start_sec, notes, dur_sec, rel_vol in events:
        start_idx = int(start_sec * sample_rate)
        ring_sec = dur_sec + 0.9
        ring_samples = min(int(ring_sec * sample_rate), num_samples - start_idx)
        if ring_samples <= 0:
            continue

        note_scale = (rel_vol / max(1, len(notes) ** 0.65)) * 0.45
        attack_samples = int(0.012 * sample_rate)

        for n_name in notes:
            freq = NOTE_FREQS.get(n_name, 440.0)
            two_pi_f = 2.0 * math.pi * freq / sample_rate

            for i in range(ring_samples):
                t = i / sample_rate
                env_attack = (i / attack_samples) if i < attack_samples else 1.0
                remain = (ring_samples - i) / sample_rate
                env_release = min(1.0, remain / 0.15) if remain < 0.15 else 1.0

                sample_val = 0.0
                for h_mult, h_amp, h_decay in harmonics:
                    decay = math.exp(-t * (1.65 * h_decay) / max(0.6, dur_sec * 0.75))
                    sample_val += h_amp * decay * math.sin(two_pi_f * h_mult * i)

                buf[start_idx + i] += sample_val * env_attack * env_release * note_scale

    peak = max(abs(x) for x in buf) or 1.0
    master_gain = (vol_clamped / 100.0) * 30000.0 / max(peak, 0.85)

    pcm_bytes = bytearray(num_samples * 2)
    for i, val in enumerate(buf):
        s = int(val * master_gain)
        if s > 32767:
            s = 32767
        elif s < -32768:
            s = -32768
        struct.pack_into("<h", pcm_bytes, i * 2, s)

    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)

    return wav_path


# ==============================================================================
# 3. 사용자 음원(MP3/WAV) 및 통합 재생 관리 함수
# ==============================================================================

def scan_custom_sounds() -> list:
    """custom_sounds 폴더를 검사하여 지원되는 오디오 파일 목록을 반환합니다."""
    sounds = []
    if not os.path.exists(CUSTOM_SOUNDS_DIR):
        return sounds
    try:
        for fname in sorted(os.listdir(CUSTOM_SOUNDS_DIR)):
            ext = os.path.splitext(fname)[1].lower()
            if ext in SUPPORTED_AUDIO_EXTS:
                full_p = os.path.join(CUSTOM_SOUNDS_DIR, fname)
                if os.path.isfile(full_p):
                    tag = ext[1:].upper()
                    sounds.append({
                        "key": f"custom:{fname}",
                        "name": f"[{tag}] {fname}",
                        "filename": fname,
                        "path": full_p,
                    })
    except Exception as e:
        print("사용자 음원 스캔 오류:", e)
    return sounds


def resolve_melody_sound(melody_key: str):
    """
    melody_key 문자열을 분석하여:
    (is_custom_file, file_path_or_key, display_name) 형태로 반환합니다.
    """
    if not melody_key:
        melody_key = "westminster"

    # 1) custom: 접두사가 붙은 사용자 파일
    if melody_key.startswith("custom:"):
        fname = melody_key[7:]
        local_p = os.path.join(CUSTOM_SOUNDS_DIR, fname)
        if os.path.exists(local_p):
            ext = os.path.splitext(fname)[1].lstrip(".").upper() or "MP3"
            return True, local_p, f"[{ext}] {fname}"
        elif os.path.exists(fname):
            return True, fname, f"[MP3] {os.path.basename(fname)}"
        else:
            return True, local_p, f"[파일없음] {fname}"

    # 2) 파일 시스템 경로 직접 전달된 경우
    if os.path.exists(melody_key) and os.path.isfile(melody_key):
        fname = os.path.basename(melody_key)
        ext = os.path.splitext(fname)[1].lstrip(".").upper() or "AUDIO"
        return True, melody_key, f"[{ext}] {fname}"

    # 3) 내장 차임벨
    if melody_key in BELL_MELODIES:
        return False, melody_key, BELL_MELODIES[melody_key]["name"]

    # 4) 매칭되지 않는 경우 기본 벨소리
    return False, "westminster", BELL_MELODIES["westminster"]["name"]


def play_bell_async(melody_key: str, volume_pct: int = 85, max_seconds: int = 0):
    """지정된 벨소리(내장 차임벨 또는 사용자 MP3)를 백그라운드 스레드에서 재생합니다."""
    def _worker():
        try:
            is_file, target, name = resolve_melody_sound(melody_key)
            if is_file:
                if os.path.exists(target):
                    play_audio_file(target, volume_pct, max_seconds)
                else:
                    print(f"음원 파일을 찾을 수 없습니다: {target}")
            else:
                wav_path = synthesize_bell_wav(target, volume_pct)
                play_audio_file(wav_path, volume_pct, max_seconds)
        except Exception as e:
            print("종소리 재생 오류:", e)

    threading.Thread(target=_worker, daemon=True).start()


# ==============================================================================
# 4. 초·중·고 표준 시간표 프리셋 생성기
# ==============================================================================

CATEGORIES = ["수업 시작", "수업 종료", "예비종", "점심시간", "조·종례", "기타"]

CATEGORY_DEFAULT_MELODY = {
    "수업 시작": "westminster",
    "수업 종료": "class_end",
    "예비종": "prep_chime",
    "점심시간": "lunch_bell",
    "조·종례": "school_song",
    "기타": "class_start",
}


def build_standard_schedule(
    start_hm: str,
    class_min: int,
    break_min: int,
    total_periods: int,
    lunch_after_period: int,
    lunch_min: int,
    include_homeroom: bool = True,
    include_prep_bell: bool = False,
):
    sh, sm = map(int, start_hm.split(":"))
    cur = datetime.datetime(2026, 1, 1, sh, sm)
    items = []

    def fmt(dt):
        return dt.strftime("%H:%M")

    if include_homeroom:
        hr_time = cur - datetime.timedelta(minutes=10)
        items.append({
            "time": fmt(hr_time),
            "name": "아침 조례 / 등교 타종",
            "category": "조·종례",
            "melody": "school_song",
            "duration": 0,
            "enabled": True,
        })

    for p in range(1, total_periods + 1):
        if include_prep_bell:
            prep_t = cur - datetime.timedelta(minutes=2)
            items.append({
                "time": fmt(prep_t),
                "name": f"{p}교시 예비종 (2분 전)",
                "category": "예비종",
                "melody": "prep_chime",
                "duration": 0,
                "enabled": True,
            })

        # 교시 시작
        items.append({
            "time": fmt(cur),
            "name": f"{p}교시 시작",
            "category": "수업 시작",
            "melody": "westminster",
            "duration": 0,
            "enabled": True,
        })

        # 교시 종료
        cur += datetime.timedelta(minutes=class_min)
        if p == lunch_after_period:
            items.append({
                "time": fmt(cur),
                "name": f"{p}교시 종료 (점심시간 시작)",
                "category": "점심시간",
                "melody": "lunch_bell",
                "duration": 0,
                "enabled": True,
            })
            cur += datetime.timedelta(minutes=lunch_min)
        else:
            items.append({
                "time": fmt(cur),
                "name": f"{p}교시 종료" + (" (쉬는 시간)" if p < total_periods else ""),
                "category": "수업 종료",
                "melody": "class_end",
                "duration": 0,
                "enabled": True,
            })
            if p < total_periods:
                cur += datetime.timedelta(minutes=break_min)

    if include_homeroom:
        hr_end = cur + datetime.timedelta(minutes=10)
        items.append({
            "time": fmt(hr_end),
            "name": "종례 및 하교 타종",
            "category": "조·종례",
            "melody": "school_song",
            "duration": 0,
            "enabled": True,
        })

    items.sort(key=lambda x: x["time"])
    return items


PRESET_TEMPLATES = {
    "초등학교 표준 (40분 수업 / 10분 휴식 / 6교시)": lambda: build_standard_schedule(
        start_hm="09:00", class_min=40, break_min=10, total_periods=6,
        lunch_after_period=4, lunch_min=50, include_homeroom=True, include_prep_bell=False
    ),
    "중학교 표준 (45분 수업 / 10분 휴식 / 7교시)": lambda: build_standard_schedule(
        start_hm="08:50", class_min=45, break_min=10, total_periods=7,
        lunch_after_period=4, lunch_min=55, include_homeroom=True, include_prep_bell=False
    ),
    "고등학교 표준 (50분 수업 / 10분 휴식 / 7교시)": lambda: build_standard_schedule(
        start_hm="08:40", class_min=50, break_min=10, total_periods=7,
        lunch_after_period=4, lunch_min=60, include_homeroom=True, include_prep_bell=True
    ),
    "단축수업 (35분 수업 / 10분 휴식 / 6교시)": lambda: build_standard_schedule(
        start_hm="09:00", class_min=35, break_min=10, total_periods=6,
        lunch_after_period=4, lunch_min=50, include_homeroom=True, include_prep_bell=False
    ),
}


# ==============================================================================
# 5. 메인 GUI 애플리케이션
# ==============================================================================

class SchoolBellApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"학교 수업 종소리 알리미 v{VERSION}")
        self.root.geometry("1060x720")
        self.root.minsize(960, 640)
        self.root.configure(bg="#f1f5f9")

        # 설정 데이터 로드
        self.config = self.load_config()
        self.last_triggered_key = None

        self._setup_styles()
        self._build_ui()
        self._refresh_melody_choices()
        self._refresh_table()
        self._tick()

        # 안내 파일 생성 (custom_sounds 폴더에 설명서)
        self._ensure_custom_sounds_readme()

        # 시작 시 기본 벨소리 캐싱
        threading.Thread(
            target=lambda: synthesize_bell_wav("westminster", self.var_volume.get()),
            daemon=True,
        ).start()

    def _ensure_custom_sounds_readme(self):
        readme_path = os.path.join(CUSTOM_SOUNDS_DIR, "음원 넣는 곳 안내.txt")
        if not os.path.exists(readme_path):
            try:
                with open(readme_path, "w", encoding="utf-8") as f:
                    f.write(
                        "이 폴더(custom_sounds)에 학교 교가, 클래식, 팝송 등\n"
                        "원하시는 오디오 파일(.mp3, .wav, .wma, .m4a)을 넣어두시면\n"
                        "프로그램의 '벨소리 선택' 목록에 자동으로 나타납니다.\n\n"
                        "프로그램 화면 우측의 [➕ 내 MP3 파일 추가] 버튼을 눌러\n"
                        "파일을 바로 등록하셔도 자동으로 이 폴더에 복사됩니다.\n"
                    )
            except Exception:
                pass

    # --------------------------------------------------------------------------
    # 설정 입출력
    # --------------------------------------------------------------------------
    def load_config(self) -> dict:
        default_profiles = {name: fn() for name, fn in PRESET_TEMPLATES.items()}
        default_cfg = {
            "master_enabled": True,
            "mute_weekends": True,
            "volume": 85,
            "active_profile": "중학교 표준 (45분 수업 / 10분 휴식 / 7교시)",
            "profiles": default_profiles,
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "profiles" in data and data["profiles"]:
                    for k, v in default_cfg.items():
                        data.setdefault(k, v)
                    if data["active_profile"] not in data["profiles"]:
                        data["active_profile"] = next(iter(data["profiles"].keys()))
                    return data
            except Exception as e:
                print("설정 파일 로드 실패, 기본값 사용:", e)
        return default_cfg

    def save_config(self):
        self.config["master_enabled"] = bool(self.var_master.get())
        self.config["mute_weekends"] = bool(self.var_mute_weekend.get())
        self.config["volume"] = int(self.var_volume.get())
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("설정 저장 실패:", e)

    @property
    def current_schedule(self) -> list:
        prof = self.config["active_profile"]
        return self.config["profiles"].setdefault(prof, [])

    # --------------------------------------------------------------------------
    # UI 및 스타일 빌드
    # --------------------------------------------------------------------------
    def _setup_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(
            "Bell.Treeview",
            font=("맑은 고딕", 10),
            rowheight=32,
            background="white",
            fieldbackground="white",
            foreground="#1e293b",
            borderwidth=0,
        )
        style.configure(
            "Bell.Treeview.Heading",
            font=("맑은 고딕", 10, "bold"),
            background="#e2e8f0",
            foreground="#0f172a",
            padding=(8, 6),
        )
        style.map(
            "Bell.Treeview",
            background=[("selected", "#dbeafe")],
            foreground=[("selected", "#1e3a8a")],
        )

    def _build_ui(self):
        # 1) 상단 대시보드 헤더
        header = tk.Frame(self.root, bg="#1e293b", padx=22, pady=16)
        header.pack(fill="x")

        clock_box = tk.Frame(header, bg="#1e293b")
        clock_box.pack(side="left")

        self.lbl_date = tk.Label(
            clock_box,
            text="2026년 11월 06일 (금)",
            font=("맑은 고딕", 11),
            fg="#94a3b8",
            bg="#1e293b",
        )
        self.lbl_date.pack(anchor="w")

        self.lbl_clock = tk.Label(
            clock_box,
            text="00:00:00",
            font=("Consolas", 34, "bold"),
            fg="#f8fafc",
            bg="#1e293b",
        )
        self.lbl_clock.pack(anchor="w")

        next_box = tk.Frame(
            header, bg="#334155", padx=20, pady=10, highlightbackground="#475569", highlightthickness=1
        )
        next_box.pack(side="left", padx=28, fill="y")

        self.lbl_next_title = tk.Label(
            next_box,
            text="다음 예정 타종",
            font=("맑은 고딕", 10, "bold"),
            fg="#38bdf8",
            bg="#334155",
        )
        self.lbl_next_title.pack(anchor="w")

        self.lbl_next_info = tk.Label(
            next_box,
            text="계산 중...",
            font=("맑은 고딕", 14, "bold"),
            fg="#ffffff",
            bg="#334155",
        )
        self.lbl_next_info.pack(anchor="w", pady=(2, 2))

        self.lbl_countdown = tk.Label(
            next_box,
            text="남은 시간: --분 --초",
            font=("맑은 고딕", 11),
            fg="#cbd5e1",
            bg="#334155",
        )
        self.lbl_countdown.pack(anchor="w")

        ctrl_box = tk.Frame(header, bg="#1e293b")
        ctrl_box.pack(side="right", fill="y")

        self.var_master = tk.BooleanVar(value=self.config.get("master_enabled", True))
        self.var_mute_weekend = tk.BooleanVar(value=self.config.get("mute_weekends", True))
        self.var_volume = tk.IntVar(value=self.config.get("volume", 85))

        sw_row = tk.Frame(ctrl_box, bg="#1e293b")
        sw_row.pack(anchor="e", pady=(0, 6))

        self.btn_master = tk.Checkbutton(
            sw_row,
            text="자동 타종 활성화 (ON)",
            variable=self.var_master,
            command=self._on_master_toggle,
            font=("맑은 고딕", 10, "bold"),
            fg="#4ade80",
            bg="#1e293b",
            selectcolor="#0f172a",
            activebackground="#1e293b",
            activeforeground="#4ade80",
        )
        self.btn_master.pack(side="left", padx=(0, 12))

        tk.Checkbutton(
            sw_row,
            text="주말(토·일) 음소거",
            variable=self.var_mute_weekend,
            command=self.save_config,
            font=("맑은 고딕", 9),
            fg="#e2e8f0",
            bg="#1e293b",
            selectcolor="#0f172a",
            activebackground="#1e293b",
            activeforeground="#e2e8f0",
        ).pack(side="left")

        vol_row = tk.Frame(ctrl_box, bg="#1e293b")
        vol_row.pack(anchor="e", pady=(2, 6))

        tk.Label(vol_row, text="🔊 음량:", font=("맑은 고딕", 9), fg="#cbd5e1", bg="#1e293b").pack(side="left")

        vol_scale = tk.Scale(
            vol_row,
            from_=10,
            to=100,
            orient="horizontal",
            variable=self.var_volume,
            command=lambda _: self.save_config(),
            bg="#1e293b",
            fg="#f8fafc",
            highlightthickness=0,
            troughcolor="#475569",
            length=120,
            showvalue=False,
        )
        vol_scale.pack(side="left", padx=6)

        self.lbl_vol_val = tk.Label(
            vol_row,
            textvariable=self.var_volume,
            font=("맑은 고딕", 9, "bold"),
            fg="#38bdf8",
            bg="#1e293b",
            width=3,
        )
        self.lbl_vol_val.pack(side="left")
        tk.Label(vol_row, text="%", font=("맑은 고딕", 9), fg="#cbd5e1", bg="#1e293b").pack(side="left")

        manual_row = tk.Frame(ctrl_box, bg="#1e293b")
        manual_row.pack(anchor="e")

        tk.Button(
            manual_row,
            text="지금 종 울리기 (수동 타종)",
            command=self.ring_manual_now,
            font=("맑은 고딕", 9, "bold"),
            bg="#2f5f9e",
            fg="white",
            activebackground="#234b80",
            activeforeground="white",
            relief="flat",
            padx=12,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            manual_row,
            text="소리 멈춤",
            command=stop_bell,
            font=("맑은 고딕", 9),
            bg="#9e4747",
            fg="white",
            activebackground="#7f3535",
            activeforeground="white",
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left")

        # 2) 프리셋 / 프로필 선택 바
        preset_bar = tk.Frame(self.root, bg="#eef3f9", padx=18, pady=10)
        preset_bar.pack(fill="x")

        tk.Label(
            preset_bar,
            text="시간표 프리셋:",
            font=("맑은 고딕", 10, "bold"),
            bg="#eef3f9",
            fg="#1c2b3b",
        ).pack(side="left")

        self.var_profile = tk.StringVar(value=self.config["active_profile"])
        self.cmb_profile = ttk.Combobox(
            preset_bar,
            textvariable=self.var_profile,
            values=list(self.config["profiles"].keys()),
            state="readonly",
            font=("맑은 고딕", 10),
            width=36,
        )
        self.cmb_profile.pack(side="left", padx=8)
        self.cmb_profile.bind("<<ComboboxSelected>>", self._on_profile_change)

        tk.Button(
            preset_bar,
            text="시작/종료 벨소리 일괄 설정",
            command=self.open_batch_melody_dialog,
            font=("맑은 고딕", 9, "bold"),
            bg="#2f5f9e",
            fg="white",
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=4)

        tk.Button(
            preset_bar,
            text="맞춤 시간표 자동 생성",
            command=self.open_generator_dialog,
            font=("맑은 고딕", 9, "bold"),
            bg="#2b6777",
            fg="white",
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=4)

        tk.Button(
            preset_bar,
            text="기본값 초기화",
            command=self.reset_current_profile,
            font=("맑은 고딕", 9),
            bg="#ffffff",
            fg="#495d73",
            relief="solid",
            bd=1,
            padx=9,
            pady=3,
            cursor="hand2",
        ).pack(side="left", padx=4)

        tk.Button(
            preset_bar,
            text="시간표 불러오기",
            command=self.import_schedule_json,
            font=("맑은 고딕", 9),
            bg="#ffffff",
            fg="#495d73",
            relief="solid",
            bd=1,
            padx=9,
            pady=3,
            cursor="hand2",
        ).pack(side="right", padx=(4, 0))

        tk.Button(
            preset_bar,
            text="시간표 파일 저장",
            command=self.export_schedule_json,
            font=("맑은 고딕", 9),
            bg="#ffffff",
            fg="#495d73",
            relief="solid",
            bd=1,
            padx=9,
            pady=3,
            cursor="hand2",
        ).pack(side="right", padx=4)

        # 3) 중앙 바디 영역 (좌: 시간표 목록 / 우: 편집 & MP3 관리 패널)
        body = tk.Frame(self.root, bg="#f1f5f9", padx=18, pady=14)
        body.pack(fill="both", expand=True)

        table_card = tk.Frame(body, bg="white", highlightbackground="#cbd5e1", highlightthickness=1)
        table_card.pack(side="left", fill="both", expand=True, padx=(0, 14))

        table_top = tk.Frame(table_card, bg="#f8fafc", padx=14, pady=10)
        table_top.pack(fill="x")

        tk.Label(
            table_top,
            text="시간표 타종 목록 (더블클릭: ON/OFF | 클릭: 우측에서 수정)",
            font=("맑은 고딕", 10, "bold"),
            fg="#334155",
            bg="#f8fafc",
        ).pack(side="left")

        self.lbl_count = tk.Label(table_top, text="총 0개", font=("맑은 고딕", 9), fg="#64748b", bg="#f8fafc")
        self.lbl_count.pack(side="right")

        cols = ("status", "time", "category", "name", "melody", "duration")
        self.tree = ttk.Treeview(
            table_card, columns=cols, show="headings", style="Bell.Treeview", selectmode="browse"
        )
        self.tree.heading("status", text="상태")
        self.tree.heading("time", text="타종 시각")
        self.tree.heading("category", text="구분")
        self.tree.heading("name", text="일정 / 교시 이름")
        self.tree.heading("melody", text="지정 벨소리 / MP3")
        self.tree.heading("duration", text="재생 시간")

        self.tree.column("status", width=65, anchor="center")
        self.tree.column("time", width=80, anchor="center")
        self.tree.column("category", width=90, anchor="center")
        self.tree.column("name", width=180, anchor="w")
        self.tree.column("melody", width=190, anchor="w")
        self.tree.column("duration", width=85, anchor="center")

        self.tree.tag_configure("next_up", background="#fef9c3", foreground="#854d0e")
        self.tree.tag_configure("disabled", foreground="#94a3b8")
        self.tree.tag_configure("past", foreground="#64748b")

        vsb = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        # 우측 편집 & MP3 관리 카드
        edit_card = tk.Frame(
            body, bg="white", width=340, padx=16, pady=14,
            highlightbackground="#cbd5e1", highlightthickness=1
        )
        edit_card.pack(side="right", fill="y")
        edit_card.pack_propagate(False)

        tk.Label(
            edit_card,
            text="✏️ 타종 시간 추가 / 편집",
            font=("맑은 고딕", 12, "bold"),
            fg="#0f172a",
            bg="white",
        ).pack(anchor="w", pady=(0, 10))

        # 시각
        tk.Label(edit_card, text="타종 시각 (24시간제)", font=("맑은 고딕", 9, "bold"), fg="#334155", bg="white").pack(anchor="w")
        time_row = tk.Frame(edit_card, bg="white")
        time_row.pack(fill="x", pady=(3, 8))

        self.var_hour = tk.StringVar(value="09")
        self.var_min = tk.StringVar(value="00")

        sp_h = ttk.Spinbox(
            time_row, from_=0, to=23, wrap=True, textvariable=self.var_hour,
            format="%02.0f", font=("Consolas", 12, "bold"), width=4, justify="center"
        )
        sp_h.pack(side="left")
        tk.Label(time_row, text="시", font=("맑은 고딕", 9), bg="white", fg="#475569").pack(side="left", padx=(4, 8))

        sp_m = ttk.Spinbox(
            time_row, from_=0, to=59, wrap=True, textvariable=self.var_min,
            format="%02.0f", font=("Consolas", 12, "bold"), width=4, justify="center"
        )
        sp_m.pack(side="left")
        tk.Label(time_row, text="분", font=("맑은 고딕", 9), bg="white", fg="#475569").pack(side="left", padx=4)

        # 구분
        tk.Label(edit_card, text="구분", font=("맑은 고딕", 9, "bold"), fg="#334155", bg="white").pack(anchor="w")
        self.var_cat = tk.StringVar(value="수업 시작")
        cmb_cat = ttk.Combobox(edit_card, textvariable=self.var_cat, values=CATEGORIES, state="readonly", font=("맑은 고딕", 9))
        cmb_cat.pack(fill="x", pady=(3, 8))
        cmb_cat.bind("<<ComboboxSelected>>", self._on_category_change)

        # 일정 이름
        tk.Label(edit_card, text="일정 / 교시 이름", font=("맑은 고딕", 9, "bold"), fg="#334155", bg="white").pack(anchor="w")
        self.var_name = tk.StringVar(value="1교시 시작")
        ent_name = ttk.Entry(edit_card, textvariable=self.var_name, font=("맑은 고딕", 9))
        ent_name.pack(fill="x", pady=(3, 8))

        # 벨소리 선택 (내장 차임벨 + 사용자 MP3)
        lbl_sound_box = tk.Frame(edit_card, bg="white")
        lbl_sound_box.pack(fill="x")
        tk.Label(lbl_sound_box, text="벨소리 / MP3 선택", font=("맑은 고딕", 9, "bold"), fg="#334155", bg="white").pack(side="left")

        self.var_melody_label = tk.StringVar(value=BELL_MELODIES["westminster"]["name"])
        self.cmb_melody = ttk.Combobox(
            edit_card,
            textvariable=self.var_melody_label,
            values=[],
            state="readonly",
            font=("맑은 고딕", 9),
        )
        self.cmb_melody.pack(fill="x", pady=(3, 4))
        self.cmb_melody.bind("<<ComboboxSelected>>", self._on_melody_select)

        # 사용자 MP3 추가 / 폴더 열기 버튼 그룹
        mp3_btn_row = tk.Frame(edit_card, bg="white")
        mp3_btn_row.pack(fill="x", pady=(2, 6))

        tk.Button(
            mp3_btn_row,
            text="내 MP3 추가",
            command=self.import_custom_audio,
            font=("맑은 고딕", 9, "bold"),
            bg="#e4eef9",
            fg="#2f5f9e",
            relief="flat",
            pady=3,
            cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=(0, 2))

        tk.Button(
            mp3_btn_row,
            text="음원 폴더 열기",
            command=self.open_custom_sounds_folder,
            font=("맑은 고딕", 8),
            bg="#f1f5f9",
            fg="#334155",
            relief="solid",
            bd=1,
            pady=3,
            cursor="hand2",
        ).pack(side="left", padx=2)

        tk.Button(
            mp3_btn_row,
            text="새로고침",
            command=lambda: self._refresh_melody_choices(),
            font=("맑은 고딕", 8),
            bg="#f1f5f9",
            fg="#475569",
            relief="solid",
            bd=1,
            pady=3,
            cursor="hand2",
        ).pack(side="right", padx=(2, 0))

        self.lbl_melody_desc = tk.Label(
            edit_card,
            text=BELL_MELODIES["westminster"]["desc"],
            font=("맑은 고딕", 8),
            fg="#64748b",
            bg="white",
            wraplength=300,
            justify="left",
        )
        self.lbl_melody_desc.pack(anchor="w", pady=(0, 6))

        # 재생 시간 제한 설정 (MP3를 일정 시간만 울리고 자동 정지할 때 유용)
        dur_row = tk.Frame(edit_card, bg="white")
        dur_row.pack(fill="x", pady=(0, 8))

        tk.Label(dur_row, text="재생 시간 제한:", font=("맑은 고딕", 9), fg="#475569", bg="white").pack(side="left")

        self.var_duration = tk.StringVar(value="전체 재생")
        self.cmb_duration = ttk.Combobox(
            dur_row,
            textvariable=self.var_duration,
            values=["전체 재생", "10초 후 멈춤", "15초 후 멈춤", "20초 후 멈춤", "30초 후 멈춤", "45초 후 멈춤", "60초 후 멈춤"],
            state="readonly",
            width=13,
            font=("맑은 고딕", 9),
        )
        self.cmb_duration.pack(side="right")

        # 미리듣기 및 정지
        preview_row = tk.Frame(edit_card, bg="white")
        preview_row.pack(fill="x", pady=(0, 10))

        tk.Button(
            preview_row,
            text="🔊 선택한 소리 미리듣기",
            command=self.preview_selected_melody,
            font=("맑은 고딕", 9),
            bg="#f8fafc",
            fg="#1e293b",
            relief="solid",
            bd=1,
            pady=4,
            cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        tk.Button(
            preview_row,
            text="⏹ 멈춤",
            command=stop_bell,
            font=("맑은 고딕", 9),
            bg="#fee2e2",
            fg="#dc2626",
            relief="solid",
            bd=1,
            pady=4,
            cursor="hand2",
        ).pack(side="right", padx=(4, 0))

        # 사용 여부
        self.var_item_enabled = tk.BooleanVar(value=True)
        tk.Checkbutton(
            edit_card,
            text="이 시간 타종 사용 (ON)",
            variable=self.var_item_enabled,
            font=("맑은 고딕", 9, "bold"),
            bg="white",
            fg="#0f172a",
        ).pack(anchor="w", pady=(0, 10))

        # 추가 / 수정 / 삭제 버튼 그룹
        btn_grid = tk.Frame(edit_card, bg="white")
        btn_grid.pack(fill="x")

        tk.Button(
            btn_grid,
            text="새 시간 추가",
            command=self.add_schedule_item,
            font=("맑은 고딕", 10, "bold"),
            bg="#2f5f9e",
            fg="white",
            relief="flat",
            pady=5,
            cursor="hand2",
        ).pack(fill="x", pady=(0, 5))

        tk.Button(
            btn_grid,
            text="선택 항목 수정 저장",
            command=self.update_selected_item,
            font=("맑은 고딕", 10, "bold"),
            bg="#ffffff",
            fg="#1c2b3b",
            relief="solid",
            bd=1,
            pady=5,
            cursor="hand2",
        ).pack(fill="x", pady=(0, 5))

        sub_btns = tk.Frame(btn_grid, bg="white")
        sub_btns.pack(fill="x")

        tk.Button(
            sub_btns,
            text="ON/OFF 전환",
            command=self.toggle_selected_item,
            font=("맑은 고딕", 9),
            bg="#f8fafc",
            fg="#334155",
            relief="solid",
            bd=1,
            pady=4,
            cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=(0, 3))

        tk.Button(
            sub_btns,
            text="선택 삭제",
            command=self.delete_selected_item,
            font=("맑은 고딕", 9),
            bg="#fef2f2",
            fg="#9e4747",
            relief="solid",
            bd=1,
            pady=4,
            cursor="hand2",
        ).pack(side="right", fill="x", expand=True, padx=(3, 0))

        # 카테고리별 일괄 적용 버튼 영역
        batch_frame = tk.LabelFrame(
            edit_card,
            text="수업별 벨소리 일괄 적용",
            font=("맑은 고딕", 9, "bold"),
            bg="white",
            fg="#1c2b3b",
            padx=8,
            pady=6,
        )
        batch_frame.pack(fill="x", pady=(10, 0))

        tk.Button(
            batch_frame,
            text="모든 '수업 시작'에 현재 소리 적용",
            command=lambda: self.apply_current_sound_to_category("수업 시작"),
            font=("맑은 고딕", 9, "bold"),
            bg="#e5f2f5",
            fg="#2b6777",
            activebackground="#d1e8ed",
            activeforeground="#2b6777",
            relief="solid",
            bd=1,
            pady=4,
            cursor="hand2",
        ).pack(fill="x", pady=(0, 4))

        tk.Button(
            batch_frame,
            text="모든 '수업 종료'에 현재 소리 적용",
            command=lambda: self.apply_current_sound_to_category("수업 종료"),
            font=("맑은 고딕", 9, "bold"),
            bg="#f8ebeb",
            fg="#9e4747",
            activebackground="#f2d7d7",
            activeforeground="#9e4747",
            relief="solid",
            bd=1,
            pady=4,
            cursor="hand2",
        ).pack(fill="x", pady=(0, 2))
            relief="solid",
            bd=1,
            pady=4,
            cursor="hand2",
        ).pack(fill="x", pady=(0, 2))

        # 하단 상태바
        self.status_bar = tk.Label(
            self.root,
            text="준비 완료 — 모든 시간표 및 MP3 설정은 자동 저장됩니다.",
            font=("맑은 고딕", 9),
            fg="#475569",
            bg="#e2e8f0",
            anchor="w",
            padx=18,
            pady=5,
        )
        self.status_bar.pack(fill="x", side="bottom")

    # --------------------------------------------------------------------------
    # 벨소리/MP3 선택 목록 동기화
    # --------------------------------------------------------------------------
    def _refresh_melody_choices(self, select_key: str = None):
        """내장 벨소리 6종과 custom_sounds 폴더의 MP3 목록을 읽어 드롭다운을 갱신합니다."""
        self.melody_options = []  # list of (key, display_name, desc)

        # 1) 기본 내장 차임벨
        for k, v in BELL_MELODIES.items():
            self.melody_options.append((k, v["name"], v["desc"]))

        # 2) 사용자 MP3/오디오 파일
        custom_list = scan_custom_sounds()
        for c in custom_list:
            self.melody_options.append(
                (c["key"], c["name"], f"사용자 MP3 파일: {c['filename']} ({c['path']})")
            )

        display_values = [item[1] for item in self.melody_options]
        self.cmb_melody["values"] = display_values

        # 선택할 키가 주어지면 해당 항목 선택
        if select_key:
            for k, dname, _ in self.melody_options:
                if k == select_key:
                    self.var_melody_label.set(dname)
                    self._on_melody_select()
                    return

        # 현재 콤보박스 텍스트가 목록에 없으면 첫 번째 선택
        cur_label = self.var_melody_label.get()
        if cur_label not in display_values:
            self.var_melody_label.set(display_values[0])
            self._on_melody_select()

    def _get_selected_melody_key(self) -> str:
        label = self.var_melody_label.get()
        for k, dname, _ in self.melody_options:
            if dname == label:
                return k
        # 매칭 안 될 경우 접두사 또는 기본값
        for k, dname, _ in self.melody_options:
            if label in dname:
                return k
        return "westminster"

    def _set_melody_selection_by_key(self, key: str):
        for k, dname, _ in self.melody_options:
            if k == key:
                self.var_melody_label.set(dname)
                self._on_melody_select()
                return
        # 만약 목록에 아직 없는 custom 파일이라면 목록 갱신 후 재시도
        self._refresh_melody_choices()
        for k, dname, _ in self.melody_options:
            if k == key:
                self.var_melody_label.set(dname)
                self._on_melody_select()
                return
        self.var_melody_label.set(BELL_MELODIES["westminster"]["name"])
        self._on_melody_select()

    def _get_duration_seconds(self) -> int:
        val = self.var_duration.get()
        if "10초" in val:
            return 10
        if "15초" in val:
            return 15
        if "20초" in val:
            return 20
        if "30초" in val:
            return 30
        if "45초" in val:
            return 45
        if "60초" in val or "1분" in val:
            return 60
        return 0  # 전체 재생

    def _set_duration_seconds(self, sec: int):
        if sec == 10:
            self.var_duration.set("10초 후 멈춤")
        elif sec == 15:
            self.var_duration.set("15초 후 멈춤")
        elif sec == 20:
            self.var_duration.set("20초 후 멈춤")
        elif sec == 30:
            self.var_duration.set("30초 후 멈춤")
        elif sec == 45:
            self.var_duration.set("45초 후 멈춤")
        elif sec == 60:
            self.var_duration.set("60초 후 멈춤")
        else:
            self.var_duration.set("전체 재생")

    def _on_melody_select(self, _event=None):
        label = self.var_melody_label.get()
        for k, dname, desc in self.melody_options:
            if dname == label:
                self.lbl_melody_desc.configure(text=desc)
                return
        self.lbl_melody_desc.configure(text="")

    def _on_category_change(self, _event=None):
        cat = self.var_cat.get()
        # 현재 선택된 것이 이미 사용자 MP3라면 굳이 덮어쓰지 않음
        cur_key = self._get_selected_melody_key()
        if not cur_key.startswith("custom:"):
            default_key = CATEGORY_DEFAULT_MELODY.get(cat, "westminster")
            self._set_melody_selection_by_key(default_key)

    def import_custom_audio(self):
        """PC에 있는 MP3 / WAV 파일을 선택해 custom_sounds 폴더로 복사하고 즉시 등록합니다."""
        path = filedialog.askopenfilename(
            parent=self.root,
            title="추가할 학교 종소리 / MP3 파일 선택",
            filetypes=[
                ("오디오 파일 (*.mp3, *.wav, *.wma, *.m4a)", "*.mp3;*.wav;*.wma;*.m4a;*.aac;*.ogg"),
                ("MP3 파일 (*.mp3)", "*.mp3"),
                ("WAV 파일 (*.wav)", "*.wav"),
                ("모든 파일 (*.*)", "*.*"),
            ],
        )
        if not path:
            return

        fname = os.path.basename(path)
        dest_path = os.path.join(CUSTOM_SOUNDS_DIR, fname)

        # 원본과 대상이 다르면 custom_sounds 폴더로 복사
        if os.path.abspath(path) != os.path.abspath(dest_path):
            try:
                shutil.copy2(path, dest_path)
            except Exception as e:
                messagebox.showerror("복사 실패", f"음원 파일을 custom_sounds 폴더로 복사하지 못했습니다:\n{e}", parent=self.root)
                return

        new_key = f"custom:{fname}"
        self._refresh_melody_choices(select_key=new_key)
        self.set_status(f"🎵 새 MP3 등록 완료: {fname}")
        messagebox.showinfo(
            "MP3 등록 완료",
            f"'{fname}' 음원이 성공적으로 등록되었습니다!\n우측 [선택한 소리 미리듣기]로 바로 들어보실 수 있습니다.",
            parent=self.root,
        )

    def open_custom_sounds_folder(self):
        """custom_sounds 폴더를 윈도우 파일 탐색기로 엽니다."""
        try:
            os.startfile(CUSTOM_SOUNDS_DIR)
            self.set_status(f"음원 폴더 열림: {CUSTOM_SOUNDS_DIR}")
        except Exception as e:
            messagebox.showerror("폴더 열기 실패", f"폴더를 열지 못했습니다:\n{e}", parent=self.root)

    def preview_selected_melody(self):
        key = self._get_selected_melody_key()
        dur = self._get_duration_seconds()
        _, _, name = resolve_melody_sound(key)
        dur_msg = f" ({dur}초 제한)" if dur > 0 else ""
        self.set_status(f"미리듣기 재생: {name}{dur_msg}")
        play_bell_async(key, self.var_volume.get(), max_seconds=dur)

    def ring_manual_now(self):
        key = self._get_selected_melody_key()
        dur = self._get_duration_seconds()
        _, _, name = resolve_melody_sound(key)
        self.set_status(f"수동 타종 실행: {name}")
        play_bell_async(key, self.var_volume.get(), max_seconds=dur)

    def _on_master_toggle(self):
        on = self.var_master.get()
        if on:
            self.btn_master.configure(text="자동 타종 활성화 (ON)", fg="#4ade80")
            self.set_status("자동 타종이 활성화되었습니다.")
        else:
            self.btn_master.configure(text="자동 타종 일시중지 (OFF)", fg="#f87171")
            self.set_status("자동 타종이 일시중지되었습니다. (수동 타종만 가능)")
        self.save_config()

    def set_status(self, msg: str):
        now_s = datetime.datetime.now().strftime("%H:%M:%S")
        self.status_bar.configure(text=f"[{now_s}] {msg}")

    # --------------------------------------------------------------------------
    # 테이블 렌더링 및 항목 CRUD
    # --------------------------------------------------------------------------
    def _refresh_table(self, select_idx=None):
        self.current_schedule.sort(key=lambda x: x.get("time", "00:00"))
        for item in self.tree.get_children():
            self.tree.delete(item)

        now_hm = datetime.datetime.now().strftime("%H:%M")
        next_idx = self._find_next_index(now_hm)

        enabled_cnt = 0
        for idx, item in enumerate(self.current_schedule):
            on = item.get("enabled", True)
            if on:
                enabled_cnt += 1
            status_txt = "켜짐" if on else "꺼짐"
            m_key = item.get("melody", "westminster")
            _, _, m_name = resolve_melody_sound(m_key)

            dur_sec = item.get("duration", 0)
            dur_str = f"{dur_sec}초" if dur_sec > 0 else "전체"

            tags = []
            if not on:
                tags.append("disabled")
            elif idx == next_idx:
                tags.append("next_up")
            elif item.get("time", "") < now_hm:
                tags.append("past")

            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    status_txt,
                    item.get("time", "00:00"),
                    item.get("category", "수업 시작"),
                    item.get("name", ""),
                    m_name,
                    dur_str,
                ),
                tags=tuple(tags),
            )

        total = len(self.current_schedule)
        self.lbl_count.configure(text=f"총 {total}개 (활성 {enabled_cnt}개)")

        if select_idx is not None and str(select_idx) in self.tree.get_children():
            self.tree.selection_set(str(select_idx))
            self.tree.see(str(select_idx))

    def _find_next_index(self, now_hm: str):
        for idx, item in enumerate(self.current_schedule):
            if item.get("enabled", True) and item.get("time", "00:00") > now_hm:
                return idx
        for idx, item in enumerate(self.current_schedule):
            if item.get("enabled", True):
                return idx
        return None

    def _on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx < 0 or idx >= len(self.current_schedule):
            return
        item = self.current_schedule[idx]
        hm = item.get("time", "09:00").split(":")
        if len(hm) == 2:
            self.var_hour.set(hm[0])
            self.var_min.set(hm[1])
        self.var_cat.set(item.get("category", "수업 시작"))
        self.var_name.set(item.get("name", ""))
        self._set_melody_selection_by_key(item.get("melody", "westminster"))
        self._set_duration_seconds(item.get("duration", 0))
        self.var_item_enabled.set(bool(item.get("enabled", True)))

    def _on_tree_double_click(self, _event=None):
        self.toggle_selected_item()

    def _validate_form_item(self):
        try:
            h = int(self.var_hour.get())
            m = int(self.var_min.get())
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except ValueError:
            messagebox.showwarning("입력 오류", "시각을 00:00 ~ 23:59 범위로 입력해주세요.", parent=self.root)
            return None

        name = self.var_name.get().strip()
        if not name:
            messagebox.showwarning("입력 오류", "일정 / 교시 이름을 입력해주세요.", parent=self.root)
            return None

        return {
            "time": f"{h:02d}:{m:02d}",
            "category": self.var_cat.get(),
            "name": name,
            "melody": self._get_selected_melody_key(),
            "duration": self._get_duration_seconds(),
            "enabled": bool(self.var_item_enabled.get()),
        }

    def add_schedule_item(self):
        new_item = self._validate_form_item()
        if not new_item:
            return
        self.current_schedule.append(new_item)
        self.save_config()
        self._refresh_table()
        _, _, s_name = resolve_melody_sound(new_item["melody"])
        self.set_status(f"➕ 타종 추가: {new_item['time']} ({new_item['name']} - {s_name})")

    def update_selected_item(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("안내", "좌측 목록에서 수정할 항목을 먼저 선택해주세요.", parent=self.root)
            return
        idx = int(sel[0])
        updated = self._validate_form_item()
        if not updated:
            return
        self.current_schedule[idx] = updated
        self.save_config()
        self._refresh_table(select_idx=idx)
        _, _, s_name = resolve_melody_sound(updated["melody"])
        self.set_status(f"✔️ 타종 수정 저장: {updated['time']} ({updated['name']} - {s_name})")

    def toggle_selected_item(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        cur = self.current_schedule[idx].get("enabled", True)
        self.current_schedule[idx]["enabled"] = not cur
        self.var_item_enabled.set(not cur)
        self.save_config()
        self._refresh_table(select_idx=idx)
        state_str = "켜짐(ON)" if not cur else "꺼짐(OFF)"
        self.set_status(f"🔁 '{self.current_schedule[idx]['name']}' ({self.current_schedule[idx]['time']}) → {state_str}")

    def delete_selected_item(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("안내", "삭제할 항목을 먼저 선택해주세요.", parent=self.root)
            return
        idx = int(sel[0])
        removed = self.current_schedule.pop(idx)
        self.save_config()
        self._refresh_table()
        self.set_status(f"🗑️ 삭제됨: {removed.get('time')} ({removed.get('name')})")

    def apply_current_sound_to_category(self, target_cat: str):
        """현재 우측에서 선택된 벨소리 및 재생 시간을 대상 카테고리(예: '수업 시작', '수업 종료') 전체에 일괄 적용합니다."""
        key = self._get_selected_melody_key()
        dur = self._get_duration_seconds()
        _, _, sound_name = resolve_melody_sound(key)

        matching = [it for it in self.current_schedule if it.get("category") == target_cat]
        cnt = len(matching)
        if cnt == 0:
            messagebox.showinfo("안내", f"현재 시간표에 '{target_cat}' 항목이 없습니다.", parent=self.root)
            return

        dur_str = f" ({dur}초 재생)" if dur > 0 else " (전체 재생)"
        msg = (
            f"현재 시간표의 모든 [{target_cat}] 항목 (총 {cnt}개 교시)의 벨소리를\n\n"
            f"▶ {sound_name}{dur_str}\n\n"
            f"(으)로 한 번에 일괄 변경하시겠습니까?"
        )
        if not messagebox.askyesno("일괄 적용 확인", msg, parent=self.root):
            return

        for it in matching:
            it["melody"] = key
            it["duration"] = dur

        self.save_config()
        self._refresh_table()
        self.set_status(f"⚡ 모든 '{target_cat}'({cnt}개 교시) 벨소리 일괄 변경 완료: {sound_name}")
        messagebox.showinfo(
            "일괄 적용 완료",
            f"모든 '{target_cat}' (총 {cnt}개 교시)의 벨소리가\n'{sound_name}'{dur_str}(으)로 일괄 변경되었습니다!",
            parent=self.root,
        )

    # --------------------------------------------------------------------------
    # 프로필 전환 / 초기화 / 자동 생성기 / 파일 입출력
    # --------------------------------------------------------------------------
    def _on_profile_change(self, _event=None):
        chosen = self.var_profile.get()
        if chosen in self.config["profiles"]:
            self.config["active_profile"] = chosen
            self.save_config()
            self._refresh_table()
            self.set_status(f"📋 프리셋 전환: {chosen}")

    def reset_current_profile(self):
        prof = self.config["active_profile"]
        if prof not in PRESET_TEMPLATES:
            messagebox.showinfo("안내", "기본 제공 프리셋만 초기값으로 되돌릴 수 있습니다.", parent=self.root)
            return
        if not messagebox.askyesno(
            "기본값 초기화",
            f"'{prof}' 시간표를 처음 기본 상태로 되돌리시겠습니까?\n(직접 수정한 내역이 초기화됩니다)",
            parent=self.root,
        ):
            return
        self.config["profiles"][prof] = PRESET_TEMPLATES[prof]()
        self.save_config()
        self._refresh_table()
        self.set_status(f"🔄 '{prof}' 기본값으로 초기화 완료")

    def open_generator_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("맞춤 학교 시간표 자동 생성기")
        win.geometry("430x430")
        win.resizable(False, False)
        win.configure(bg="#f8fafc")
        win.transient(self.root)
        win.grab_set()

        pad = tk.Frame(win, bg="#f8fafc", padx=22, pady=18)
        pad.pack(fill="both", expand=True)

        tk.Label(pad, text="⚡ 맞춤 시간표 자동 생성", font=("맑은 고딕", 13, "bold"), bg="#f8fafc", fg="#0f172a").pack(anchor="w")
        tk.Label(
            pad, text="1교시 시작 시각과 수업/휴식 시간을 입력하면 하루 타종표를 자동으로 만듭니다.",
            font=("맑은 고딕", 9), bg="#f8fafc", fg="#475569", wraplength=380, justify="left"
        ).pack(anchor="w", pady=(2, 14))

        form = tk.Frame(pad, bg="#f8fafc")
        form.pack(fill="x")

        fields = [
            ("1교시 시작 시각 (HH:MM)", "09:00"),
            ("수업 시간 (분)", "45"),
            ("쉬는 시간 (분)", "10"),
            ("총 교시 수 (교시)", "7"),
            ("점심시간 위치 (몇 교시 후)", "4"),
            ("점심시간 길이 (분)", "55"),
        ]
        vars_map = {}
        for r, (label_txt, default_val) in enumerate(fields):
            tk.Label(form, text=label_txt, font=("맑은 고딕", 10), bg="#f8fafc", fg="#1e293b").grid(row=r, column=0, sticky="w", pady=5)
            v = tk.StringVar(value=default_val)
            ttk.Entry(form, textvariable=v, width=12, font=("Consolas", 11), justify="center").grid(
                row=r, column=1, sticky="e", pady=5, padx=(16, 0)
            )
            vars_map[label_txt] = v

        var_hr = tk.BooleanVar(value=True)
        var_prep = tk.BooleanVar(value=False)

        tk.Checkbutton(pad, text="아침 조례(10분 전) 및 종례(10분 후) 타종 포함", variable=var_hr, font=("맑은 고딕", 9), bg="#f8fafc").pack(anchor="w", pady=(10, 2))
        tk.Checkbutton(pad, text="매 교시 시작 2분 전 예비종 포함", variable=var_prep, font=("맑은 고딕", 9), bg="#f8fafc").pack(anchor="w")

        def do_generate():
            try:
                start_hm = vars_map["1교시 시작 시각 (HH:MM)"].get().strip()
                sh, sm = map(int, start_hm.split(":"))
                if not (0 <= sh <= 23 and 0 <= sm <= 59):
                    raise ValueError
                c_min = int(vars_map["수업 시간 (분)"].get())
                b_min = int(vars_map["쉬는 시간 (분)"].get())
                periods = int(vars_map["총 교시 수 (교시)"].get())
                l_after = int(vars_map["점심시간 위치 (몇 교시 후)"].get())
                l_min = int(vars_map["점심시간 길이 (분)"].get())
                if not (5 <= c_min <= 120 and 1 <= b_min <= 60 and 1 <= periods <= 12):
                    raise ValueError
            except ValueError:
                messagebox.showwarning("입력 확인", "시각(HH:MM)과 숫자 항목을 올바르게 입력해주세요.", parent=win)
                return

            generated = build_standard_schedule(
                start_hm=f"{sh:02d}:{sm:02d}", class_min=c_min, break_min=b_min,
                total_periods=periods, lunch_after_period=l_after, lunch_min=l_min,
                include_homeroom=var_hr.get(), include_prep_bell=var_prep.get(),
            )
            prof = self.config["active_profile"]
            self.config["profiles"][prof] = generated
            self.save_config()
            self._refresh_table()
            self.set_status(f"⚡ 맞춤 시간표 생성 완료 ({c_min}분 수업 / {b_min}분 휴식 / {periods}교시)")
            win.destroy()

        btn_row = tk.Frame(pad, bg="#f8fafc")
        btn_row.pack(fill="x", pady=(16, 0))

        tk.Button(btn_row, text="현재 시간표에 적용", command=do_generate, font=("맑은 고딕", 10, "bold"), bg="#0d9488", fg="white", relief="flat", padx=16, pady=6, cursor="hand2").pack(side="right")
        tk.Button(btn_row, text="취소", command=win.destroy, font=("맑은 고딕", 9), bg="#e2e8f0", fg="#334155", relief="flat", padx=12, pady=6).pack(side="right", padx=8)

    def open_batch_melody_dialog(self):
        """수업 시작 및 수업 종료 벨소리를 한 번에 설정하는 대화상자"""
        win = tk.Toplevel(self.root)
        win.title("수업 시작 / 종료 벨소리 일괄 설정")
        win.geometry("540x580")
        win.resizable(False, False)
        win.configure(bg="#f8fafc")
        win.transient(self.root)
        win.grab_set()

        def on_close():
            stop_bell()
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        pad = tk.Frame(win, bg="#f8fafc", padx=22, pady=16)
        pad.pack(fill="both", expand=True)

        tk.Label(pad, text="🔔 수업 시작 / 종료 벨소리 일괄 설정", font=("맑은 고딕", 13, "bold"), bg="#f8fafc", fg="#0f172a").pack(anchor="w")
        tk.Label(
            pad,
            text="시간마다 하나씩 지정할 필요 없이, 하루 전체 시간표의\n수업 시작종과 수업 종료종을 한 번에 원하는 벨소리(또는 MP3)로 변경합니다.",
            font=("맑은 고딕", 9),
            bg="#f8fafc",
            fg="#475569",
            justify="left",
        ).pack(anchor="w", pady=(2, 12))

        start_cnt = sum(1 for it in self.current_schedule if it.get("category") == "수업 시작")
        end_cnt = sum(1 for it in self.current_schedule if it.get("category") == "수업 종료")
        prep_cnt = sum(1 for it in self.current_schedule if it.get("category") == "예비종")

        display_melodies = [item[1] for item in self.melody_options]
        dur_options = ["전체 재생", "10초 후 멈춤", "15초 후 멈춤", "20초 후 멈춤", "30초 후 멈춤", "45초 후 멈춤", "60초 후 멈춤"]

        def _dur_to_sec(val):
            for s in (10, 15, 20, 30, 45, 60):
                if f"{s}초" in val:
                    return s
            return 0

        def _find_key_by_dname(dname):
            for k, dn, _ in self.melody_options:
                if dn == dname:
                    return k
            return "westminster"

        # 1) 수업 시작 벨소리 카드
        box_start = tk.LabelFrame(
            pad,
            text=f" 🟢 수업 시작 벨소리 (현재 총 {start_cnt}개 교시) ",
            font=("맑은 고딕", 10, "bold"),
            bg="white",
            fg="#15803d",
            padx=14,
            pady=10,
        )
        box_start.pack(fill="x", pady=(0, 10))

        var_apply_start = tk.BooleanVar(value=True)
        tk.Checkbutton(
            box_start,
            text=f"모든 '수업 시작' 시간({start_cnt}개)에 적용하기",
            variable=var_apply_start,
            font=("맑은 고딕", 9, "bold"),
            bg="white",
            fg="#1e293b",
        ).pack(anchor="w", pady=(0, 6))

        row_s1 = tk.Frame(box_start, bg="white")
        row_s1.pack(fill="x", pady=2)
        tk.Label(row_s1, text="벨소리/MP3:", font=("맑은 고딕", 9), bg="white", fg="#334155").pack(side="left")

        def_start_key = "westminster"
        for it in self.current_schedule:
            if it.get("category") == "수업 시작":
                def_start_key = it.get("melody", "westminster")
                break
        _, _, def_start_name = resolve_melody_sound(def_start_key)

        var_start_sound = tk.StringVar(value=def_start_name if def_start_name in display_melodies else display_melodies[0])
        cmb_start_sound = ttk.Combobox(
            row_s1, textvariable=var_start_sound, values=display_melodies, state="readonly", font=("맑은 고딕", 9), width=32
        )
        cmb_start_sound.pack(side="left", padx=8, fill="x", expand=True)

        row_s2 = tk.Frame(box_start, bg="white")
        row_s2.pack(fill="x", pady=(4, 0))
        tk.Label(row_s2, text="재생 시간:", font=("맑은 고딕", 9), bg="white", fg="#334155").pack(side="left")
        var_start_dur = tk.StringVar(value="전체 재생")
        ttk.Combobox(row_s2, textvariable=var_start_dur, values=dur_options, state="readonly", font=("맑은 고딕", 9), width=13).pack(side="left", padx=8)

        def preview_start():
            k = _find_key_by_dname(var_start_sound.get())
            d = _dur_to_sec(var_start_dur.get())
            play_bell_async(k, self.var_volume.get(), max_seconds=d)

        tk.Button(row_s2, text="🔊 미리듣기", command=preview_start, font=("맑은 고딕", 8), bg="#f1f5f9", fg="#1e293b", relief="solid", bd=1, padx=6, pady=1, cursor="hand2").pack(side="right")
        tk.Button(row_s2, text="⏹ 멈춤", command=stop_bell, font=("맑은 고딕", 8), bg="#fee2e2", fg="#dc2626", relief="solid", bd=1, padx=6, pady=1, cursor="hand2").pack(side="right", padx=4)

        # 2) 수업 종료 (쉬는 시간) 벨소리 카드
        box_end = tk.LabelFrame(
            pad,
            text=f" 🔴 수업 종료 / 쉬는 시간 벨소리 (현재 총 {end_cnt}개 교시) ",
            font=("맑은 고딕", 10, "bold"),
            bg="white",
            fg="#b91c1c",
            padx=14,
            pady=10,
        )
        box_end.pack(fill="x", pady=(0, 10))

        var_apply_end = tk.BooleanVar(value=True)
        tk.Checkbutton(
            box_end,
            text=f"모든 '수업 종료' 시간({end_cnt}개)에 적용하기",
            variable=var_apply_end,
            font=("맑은 고딕", 9, "bold"),
            bg="white",
            fg="#1e293b",
        ).pack(anchor="w", pady=(0, 6))

        row_e1 = tk.Frame(box_end, bg="white")
        row_e1.pack(fill="x", pady=2)
        tk.Label(row_e1, text="벨소리/MP3:", font=("맑은 고딕", 9), bg="white", fg="#334155").pack(side="left")

        def_end_key = "class_end"
        for it in self.current_schedule:
            if it.get("category") == "수업 종료":
                def_end_key = it.get("melody", "class_end")
                break
        _, _, def_end_name = resolve_melody_sound(def_end_key)

        var_end_sound = tk.StringVar(value=def_end_name if def_end_name in display_melodies else display_melodies[0])
        cmb_end_sound = ttk.Combobox(
            row_e1, textvariable=var_end_sound, values=display_melodies, state="readonly", font=("맑은 고딕", 9), width=32
        )
        cmb_end_sound.pack(side="left", padx=8, fill="x", expand=True)

        row_e2 = tk.Frame(box_end, bg="white")
        row_e2.pack(fill="x", pady=(4, 0))
        tk.Label(row_e2, text="재생 시간:", font=("맑은 고딕", 9), bg="white", fg="#334155").pack(side="left")
        var_end_dur = tk.StringVar(value="전체 재생")
        ttk.Combobox(row_e2, textvariable=var_end_dur, values=dur_options, state="readonly", font=("맑은 고딕", 9), width=13).pack(side="left", padx=8)

        def preview_end():
            k = _find_key_by_dname(var_end_sound.get())
            d = _dur_to_sec(var_end_dur.get())
            play_bell_async(k, self.var_volume.get(), max_seconds=d)

        tk.Button(row_e2, text="🔊 미리듣기", command=preview_end, font=("맑은 고딕", 8), bg="#f1f5f9", fg="#1e293b", relief="solid", bd=1, padx=6, pady=1, cursor="hand2").pack(side="right")
        tk.Button(row_e2, text="⏹ 멈춤", command=stop_bell, font=("맑은 고딕", 8), bg="#fee2e2", fg="#dc2626", relief="solid", bd=1, padx=6, pady=1, cursor="hand2").pack(side="right", padx=4)

        # 3) 예비종 옵션 (해당 항목이 있을 때만 표시)
        var_apply_prep = tk.BooleanVar(value=False)
        if prep_cnt > 0:
            box_prep = tk.Frame(pad, bg="#f8fafc")
            box_prep.pack(fill="x", pady=(0, 10))
            tk.Checkbutton(
                box_prep,
                text=f"🟡 예비종({prep_cnt}개 항목)도 '예비종 차임'으로 함께 맞추기",
                variable=var_apply_prep,
                font=("맑은 고딕", 9),
                bg="#f8fafc",
                fg="#475569",
            ).pack(anchor="w")

        # 4) 일괄 적용 및 취소 버튼
        def do_batch_apply():
            apply_s = var_apply_start.get()
            apply_e = var_apply_end.get()
            apply_p = var_apply_prep.get()

            if not (apply_s or apply_e or apply_p):
                messagebox.showinfo("선택 안내", "적용할 대상을 하나 이상 체크해주세요.", parent=win)
                return

            s_key = _find_key_by_dname(var_start_sound.get())
            s_dur = _dur_to_sec(var_start_dur.get())
            _, _, s_name = resolve_melody_sound(s_key)

            e_key = _find_key_by_dname(var_end_sound.get())
            e_dur = _dur_to_sec(var_end_dur.get())
            _, _, e_name = resolve_melody_sound(e_key)

            updated_start_cnt = 0
            updated_end_cnt = 0
            updated_prep_cnt = 0

            for it in self.current_schedule:
                cat = it.get("category")
                if apply_s and cat == "수업 시작":
                    it["melody"] = s_key
                    it["duration"] = s_dur
                    updated_start_cnt += 1
                elif apply_e and cat == "수업 종료":
                    it["melody"] = e_key
                    it["duration"] = e_dur
                    updated_end_cnt += 1
                elif apply_p and cat == "예비종":
                    it["melody"] = "prep_chime"
                    it["duration"] = 0
                    updated_prep_cnt += 1

            self.save_config()
            self._refresh_table()

            total_cnt = updated_start_cnt + updated_end_cnt + updated_prep_cnt
            summary_lines = []
            if updated_start_cnt:
                dur_text = f" ({s_dur}초)" if s_dur > 0 else ""
                summary_lines.append(f"• 수업 시작 ({updated_start_cnt}개 교시): {s_name}{dur_text}")
            if updated_end_cnt:
                dur_text = f" ({e_dur}초)" if e_dur > 0 else ""
                summary_lines.append(f"• 수업 종료 ({updated_end_cnt}개 교시): {e_name}{dur_text}")
            if updated_prep_cnt:
                summary_lines.append(f"• 예비종 ({updated_prep_cnt}개 교시): 예비종 차임")

            self.set_status(f"⚡ 벨소리 일괄 적용 완료: 총 {total_cnt}개 교시 변경됨")
            on_close()

            messagebox.showinfo(
                "일괄 설정 완료",
                "벨소리가 성공적으로 일괄 변경되었습니다!\n\n" + "\n".join(summary_lines),
                parent=self.root,
            )

        btn_row = tk.Frame(pad, bg="#f8fafc")
        btn_row.pack(fill="x", pady=(12, 0))

        tk.Button(
            btn_row,
            text="🚀 선택한 벨소리 일괄 적용",
            command=do_batch_apply,
            font=("맑은 고딕", 11, "bold"),
            bg="#4f46e5",
            fg="white",
            activebackground="#4338ca",
            activeforeground="white",
            relief="flat",
            padx=18,
            pady=8,
            cursor="hand2",
        ).pack(side="right")

        tk.Button(
            btn_row,
            text="취소",
            command=on_close,
            font=("맑은 고딕", 10),
            bg="#e2e8f0",
            fg="#334155",
            relief="flat",
            padx=14,
            pady=8,
            cursor="hand2",
        ).pack(side="right", padx=8)

    def export_schedule_json(self):
        path = filedialog.asksaveasfilename(
            parent=self.root, title="시간표 파일로 저장", defaultextension=".json",
            initialfile="학교_시간표.json", filetypes=[("JSON 시간표 파일", "*.json"), ("모든 파일", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.current_schedule, f, ensure_ascii=False, indent=2)
            self.set_status(f"💾 시간표 저장 완료: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("저장 실패", f"파일을 저장하지 못했습니다:\n{e}", parent=self.root)

    def import_schedule_json(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="시간표 파일 불러오기", filetypes=[("JSON 시간표 파일", "*.json"), ("모든 파일", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("올바른 시간표 목록 형식이 아닙니다.")
            prof = self.config["active_profile"]
            self.config["profiles"][prof] = data
            self.save_config()
            self._refresh_table()
            self.set_status(f"📂 시간표 불러오기 완료: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("불러오기 실패", f"파일을 읽지 못했습니다:\n{e}", parent=self.root)

    # --------------------------------------------------------------------------
    # 실시간 타이머 루프 (0.5초 간격)
    # --------------------------------------------------------------------------
    def _tick(self):
        now = datetime.datetime.now()
        weekdays = ["월", "화", "수", "목", "금", "토", "일"]
        wd_str = weekdays[now.weekday()]

        self.lbl_date.configure(text=f"{now.strftime('%Y년 %m월 %d일')} ({wd_str}요일)")
        self.lbl_clock.configure(text=now.strftime("%H:%M:%S"))

        now_hm = now.strftime("%H:%M")
        today_key = now.strftime("%Y-%m-%d %H:%M")
        is_weekend = now.weekday() >= 5

        # 1) 타종 조건 체크 (해당 시:분에 도달 시 1회 타종)
        if (
            self.var_master.get()
            and not (self.var_mute_weekend.get() and is_weekend)
            and self.last_triggered_key != today_key
        ):
            for item in self.current_schedule:
                if item.get("enabled", True) and item.get("time") == now_hm:
                    self.last_triggered_key = today_key
                    m_key = item.get("melody", "westminster")
                    dur = item.get("duration", 0)
                    play_bell_async(m_key, self.var_volume.get(), max_seconds=dur)
                    self.set_status(f"🔔 [자동 타종] {item.get('time')} - {item.get('name')}")
                    self._show_ring_toast(item)
                    self._refresh_table()
                    break

        # 2) 다음 타종 카운트다운 업데이트
        self._update_next_bell_banner(now)
        self.root.after(500, self._tick)

    def _update_next_bell_banner(self, now: datetime.datetime):
        if not self.var_master.get():
            self.lbl_next_info.configure(text="자동 타종 꺼짐 (일시중지)")
            self.lbl_countdown.configure(text="우측 상단에서 자동 타종을 켜주세요")
            return

        if self.var_mute_weekend.get() and now.weekday() >= 5:
            self.lbl_next_info.configure(text="주말 음소거 적용 중")
            self.lbl_countdown.configure(text="월요일에 자동 타종이 재개됩니다")
            return

        now_hm = now.strftime("%H:%M")
        upcoming = None
        is_tomorrow = False

        for item in self.current_schedule:
            if item.get("enabled", True) and item.get("time", "00:00") > now_hm:
                upcoming = item
                break

        if upcoming is None:
            for item in self.current_schedule:
                if item.get("enabled", True):
                    upcoming = item
                    is_tomorrow = True
                    break

        if upcoming is None:
            self.lbl_next_info.configure(text="예정된 활성 타종 없음")
            self.lbl_countdown.configure(text="시간표에서 타종을 켜거나 추가해주세요")
            return

        th, tm = map(int, upcoming["time"].split(":"))
        target_dt = now.replace(hour=th, minute=tm, second=0, microsecond=0)
        if is_tomorrow or target_dt <= now:
            target_dt += datetime.timedelta(days=1)

        diff_sec = max(0, int((target_dt - now).total_seconds()))
        rem_h, rem = divmod(diff_sec, 3600)
        rem_m, rem_s = divmod(rem, 60)

        day_prefix = "(내일) " if is_tomorrow else ""
        self.lbl_next_info.configure(text=f"{day_prefix}{upcoming['time']}  {upcoming['name']}")
        if rem_h > 0:
            cd_str = f"남은 시간: {rem_h}시간 {rem_m:02d}분 {rem_s:02d}초"
        else:
            cd_str = f"남은 시간: {rem_m:02d}분 {rem_s:02d}초"
        self.lbl_countdown.configure(text=cd_str)

    def _show_ring_toast(self, item: dict):
        """타종 시 화면 우측 하단에 알림 토스트 표시"""
        try:
            t = tk.Toplevel(self.root)
            t.overrideredirect(True)
            t.attributes("-topmost", True)
            f = tk.Frame(t, bg="#1d4ed8", padx=20, pady=14)
            f.pack()
            tk.Label(
                f, text=f"🔔 {item.get('time')}  {item.get('name')}",
                font=("맑은 고딕", 13, "bold"), fg="white", bg="#1d4ed8",
            ).pack(anchor="w")
            _, _, m_name = resolve_melody_sound(item.get("melody", "westminster"))
            dur = item.get("duration", 0)
            dur_msg = f" ({dur}초 재생)" if dur > 0 else ""
            tk.Label(
                f, text=f"재생 중: {m_name}{dur_msg}",
                font=("맑은 고딕", 9), fg="#bfdbfe", bg="#1d4ed8",
            ).pack(anchor="w", pady=(2, 0))
            t.update_idletasks()
            sw, sh = t.winfo_screenwidth(), t.winfo_screenheight()
            t.geometry(f"+{sw - t.winfo_width() - 28}+{sh - t.winfo_height() - 68}")
            t.after(6500, t.destroy)
        except Exception:
            pass


def main():
    root = tk.Tk()
    app = SchoolBellApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
