"""Звук фронта аларма: короткий программный бип через QSoundEffect.

QApplication.beep() на macOS играет системный alert sound, который у
пользователя может быть выключен или не слышен, поэтому вместо него
генерируется короткий WAV (синус 880 Гц, ~180 мс, огибающая attack/release,
16-bit mono PCM) и проигрывается QtMultimedia QSoundEffect. QSoundEffect
читает источник только по URL, поэтому байты пишутся во временный файл,
живущий до конца процесса. Без QtMultimedia (сборка без Addons) — откат на
QApplication.beep().
"""

import math
import struct

from PySide6.QtCore import QTemporaryFile, QUrl
from PySide6.QtWidgets import QApplication

_SAMPLE_RATE = 44100
_FREQ_HZ = 880.0
_DURATION_S = 0.18
_AMPLITUDE = 0.6


def _alarm_wav_bytes() -> bytes:
    """Синус 880 Гц с огибающей (attack 10 мс, release 50 мс) в WAV-контейнере."""
    count = int(_SAMPLE_RATE * _DURATION_S)
    frames = bytearray()
    for i in range(count):
        t = i / _SAMPLE_RATE
        envelope = min(1.0, t / 0.01, (_DURATION_S - t) / 0.05)
        sample = int(
            _AMPLITUDE * 32767 * envelope * math.sin(2 * math.pi * _FREQ_HZ * t)
        )
        frames += struct.pack("<h", sample)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(frames),
        b"WAVE",
        b"fmt ",
        16,  # PCM chunk size
        1,  # PCM format
        1,  # mono
        _SAMPLE_RATE,
        _SAMPLE_RATE * 2,  # byte rate
        2,  # block align
        16,  # bits per sample
        b"data",
        len(frames),
    )
    return header + frames


class AlarmSound:
    """Неблокирующее проигрывание бипа; play() на ходу просто переигрывает."""

    def __init__(self) -> None:
        self._effect: object | None = None  # QSoundEffect, лениво на первом play()
        self._temp: QTemporaryFile | None = None  # держит WAV-файл живым
        self._unavailable = False  # QtMultimedia отсутствует: откат на beep

    def play(self) -> None:
        if self._effect is None and not self._unavailable:
            try:
                from PySide6.QtMultimedia import QSoundEffect
            except ImportError:
                self._unavailable = True
            else:
                temp = QTemporaryFile()
                if temp.open():
                    temp.write(_alarm_wav_bytes())
                    temp.flush()
                    effect = QSoundEffect()
                    effect.setSource(QUrl.fromLocalFile(temp.fileName()))
                    effect.setVolume(0.9)
                    self._temp = temp
                    self._effect = effect
        if self._effect is not None:
            self._effect.play()  # type: ignore[attr-defined]
        else:
            QApplication.beep()
