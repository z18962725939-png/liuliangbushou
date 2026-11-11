# -*- coding: utf-8 -*-
"""生成官网背景音乐（原创、无版权风险）。

产出：官网素材/bgm/bgm.mp3
构建官网时 tools/build_site.py 会把它拷到 standalone/assets/bgm.mp3。

★ 想换成自己的音乐，不用改任何代码：把任意 mp3 覆盖到
  官网素材/bgm/bgm.mp3，再重新生成官网即可。播放器同时带纯 Web Audio
  合成曲兜底 —— 就算某个发布渠道漏传了这个文件，用户点按钮依然有声音。

曲子说明：22.05kHz / 单声道 / 24 秒无缝循环。四个柔和七和弦循环两轮，
垫底 pad + 电钢琶音 + 低音 + 极轻的 hi-hat，音量压得很低，适合当网页
背景而不盖住内容。全部用标准库直接算样本，不依赖任何第三方音频库。
"""
from __future__ import annotations

import math
import random
import subprocess
import sys
import wave
from array import array
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
OUT_DIR = ROOT / "官网素材" / "bgm"
WAV_PATH = OUT_DIR / "bgm.wav"
MP3_PATH = OUT_DIR / "bgm.mp3"
FFMPEG = ROOT / "assets" / "ffmpeg" / "ffmpeg.exe"

SR = 22050
BARS = 4                      # 四个和弦
BAR_SEC = 3.0                 # 每和弦 3 秒
LOOPS = 2                     # 循环两轮
DUR = BARS * BAR_SEC * LOOPS  # 24 秒
N = int(SR * DUR)

# Dm9 → Bbmaj7 → Fmaj7 → Cadd9，柔和、略带科技感，和官网「信号台」调性合拍
CHORDS = [
    [50, 53, 57, 60, 64],
    [46, 50, 53, 57],
    [41, 45, 48, 52],
    [48, 52, 55, 62],
]
BASS = [38, 34, 29, 36]


def freq(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def add_tone(buf: list[float], start: float, dur: float, midi: int,
             amp: float, kind: str, attack: float, release: float) -> None:
    """把一个带 ADSR 简化包络的音符叠加进混音缓冲。"""
    n0 = int(start * SR)
    n1 = min(N, int((start + dur) * SR))
    if n0 >= N or n1 <= n0:
        return
    f = freq(midi)
    w = 2.0 * math.pi * f / SR
    for i in range(n0, n1):
        t = (i - n0) / SR
        env = min(1.0, t / attack) * min(1.0, (dur - t) / release)
        if env <= 0.0:
            continue
        ph = w * (i - n0)
        if kind == "pad":
            # 轻微失谐让垫子有宽度，不至于像蜂鸣器
            v = math.sin(ph) + 0.48 * math.sin(ph * 1.0031) + 0.20 * math.sin(2 * ph)
        elif kind == "epiano":
            v = math.sin(ph) + 0.30 * math.sin(2 * ph) + 0.09 * math.sin(3 * ph)
        elif kind == "bass":
            v = math.sin(ph) + 0.16 * math.sin(2 * ph)
        else:
            v = math.sin(ph)
        buf[i] += amp * env * v


def add_hat(buf: list[float], start: float, amp: float = 0.016,
            dur: float = 0.045) -> None:
    """一阶差分白噪声 ≈ 高通，做极轻的 hi-hat。"""
    rng = random.Random(int(start * 1000))
    n0 = int(start * SR)
    n1 = min(N, int((start + dur) * SR))
    prev = 0.0
    for i in range(n0, n1):
        t = (i - n0) / SR
        cur = rng.uniform(-1.0, 1.0)
        hp = cur - prev
        prev = cur
        buf[i] += amp * math.exp(-t * 52.0) * hp


def add_kick(buf: list[float], start: float, amp: float = 0.085) -> None:
    """低频扫频小底鼓，只给一点脉搏，不做成舞曲。"""
    dur = 0.22
    n0 = int(start * SR)
    n1 = min(N, int((start + dur) * SR))
    phase = 0.0
    for i in range(n0, n1):
        t = (i - n0) / SR
        f = 72.0 - 28.0 * min(1.0, t / dur)
        phase += 2.0 * math.pi * f / SR
        buf[i] += amp * math.exp(-t * 16.0) * math.sin(phase)


def render() -> list[float]:
    buf = [0.0] * N
    for loop in range(LOOPS):
        for bar, chord in enumerate(CHORDS):
            t0 = (loop * BARS + bar) * BAR_SEC

            # 垫底和弦：慢起慢落，音头不抢戏
            for m in chord:
                add_tone(buf, t0, BAR_SEC, m, 0.050, "pad", 0.65, 1.05)

            # 电钢琶音：每 0.375 秒一个音，高八度穿插
            for k in range(8):
                m = chord[k % len(chord)] + (12 if k in (3, 7) else 0)
                add_tone(buf, t0 + k * 0.375, 0.62, m, 0.095,
                         "epiano", 0.008, 0.50)

            # 低音与节拍
            for half in (0.0, 1.5):
                add_tone(buf, t0 + half, 1.30, BASS[bar], 0.145,
                         "bass", 0.02, 0.55)
                add_kick(buf, t0 + half)
            for k in range(4):
                add_hat(buf, t0 + k * 0.75 + 0.375)

    # 软限幅 + 循环点淡入淡出（24 秒处干净归零，接缝听感是乐句换气）
    fade_in = int(SR * 0.08)
    fade_out = int(SR * 0.14)
    for i in range(N):
        v = math.tanh(buf[i] * 1.25) * 0.82
        if i < fade_in:
            v *= i / fade_in
        if i > N - fade_out:
            v *= (N - i) / fade_out
        buf[i] = v
    return buf


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"合成 {DUR:.0f} 秒循环曲（{SR} Hz，{N:,} 采样点）…")
    pcm = render()

    with wave.open(str(WAV_PATH), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(array("h", (int(max(-1.0, min(1.0, x)) * 32767)
                                  for x in pcm)).tobytes())
    print(f"  WAV：{WAV_PATH.name}  {WAV_PATH.stat().st_size / 1024:.0f} KB")

    if not FFMPEG.is_file():
        print(f"[错误] 找不到 {FFMPEG}，无法转 MP3")
        return 1
    cmd = [str(FFMPEG), "-y", "-loglevel", "error", "-i", str(WAV_PATH),
           "-codec:a", "libmp3lame", "-b:a", "64k", "-ar", str(SR),
           "-ac", "1", str(MP3_PATH)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not MP3_PATH.is_file():
        print(f"[错误] MP3 转换失败：{r.stderr.strip()[:300]}")
        return 1
    print(f"  MP3：{MP3_PATH.name}  {MP3_PATH.stat().st_size / 1024:.0f} KB")
    WAV_PATH.unlink(missing_ok=True)
    print("完成。构建官网时会自动拷到 standalone/assets/bgm.mp3")
    return 0


if __name__ == "__main__":
    sys.exit(main())
