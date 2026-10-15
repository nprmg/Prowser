"""Обработка голоса для профиля «Джарвис»: лёгкий «цифровой» тембр и объём.

Всё векторизовано на numpy (без поэлементных циклов), поэтому обработка фразы занимает
миллисекунды и не задерживает ответ.
"""

import numpy as np

_IR_CACHE: dict[tuple[int, float], np.ndarray] = {}


def _equalize(x: np.ndarray, sr: int, low_cut: float, presence_db: float) -> np.ndarray:
    """Срез гула ниже low_cut и мягкий подъём «присутствия» 3–8 кГц (разборчивость)."""
    n = len(x)
    size = 1 << (n - 1).bit_length()
    spectrum = np.fft.rfft(x, size)
    freqs = np.fft.rfftfreq(size, 1 / sr)
    gain = np.ones_like(freqs)
    gain *= 1 / np.sqrt(1 + (low_cut / np.maximum(freqs, 1.0)) ** 4)  # плавный ФВЧ 2-го порядка
    presence = 10 ** (presence_db / 20) - 1
    gain *= 1 + presence * np.exp(-(((freqs - 5000) / 2200) ** 2))
    return np.fft.irfft(spectrum * gain, size)[:n]


def _chorus(x: np.ndarray, sr: int, depth_ms: float = 2.2, base_ms: float = 11.0, rate_hz: float = 0.6) -> np.ndarray:
    """Копия сигнала с плавно «плавающей» задержкой — даёт синтетический, «цифровой» оттенок."""
    n = np.arange(len(x), dtype=np.float64)
    delay = (base_ms + depth_ms * np.sin(2 * np.pi * rate_hz * n / sr)) * sr / 1000
    return np.interp(n - delay, n, x, left=0.0)


def _impulse_response(sr: int, seconds: float) -> np.ndarray:
    """Синтетический отклик небольшого помещения: затухающий шум + ранние отражения."""
    key = (sr, seconds)
    if key not in _IR_CACHE:
        rng = np.random.default_rng(7)  # фиксированный — чтобы голос звучал одинаково
        length = int(sr * seconds)
        t = np.arange(length) / sr
        ir = rng.standard_normal(length) * np.exp(-t / (seconds / 5.5))
        ir = np.convolve(ir, np.ones(6) / 6, mode="same")  # смягчаем верха хвоста
        for delay_ms, gain in ((7, 0.5), (13, 0.35), (21, 0.25)):
            ir[int(sr * delay_ms / 1000)] += gain
        ir[0] = 0.0
        _IR_CACHE[key] = ir / np.sqrt(np.sum(ir ** 2))
    return _IR_CACHE[key]


def _convolve(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    size = 1 << (len(x) + len(ir) - 2).bit_length()
    return np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]


def apply_jarvis(
    pcm: np.ndarray,
    sr: int,
    chorus: float = 0.10,
    reverb: float = 0.13,
    presence_db: float = 2.5,
    low_cut: float = 90.0,
) -> np.ndarray:
    """Обрабатывает моно int16 и возвращает int16 (с коротким хвостом реверберации)."""
    if pcm.size == 0:
        return pcm
    tail = int(sr * 0.35)
    x = np.concatenate([pcm.astype(np.float64) / 32768.0, np.zeros(tail)])
    peak_in = np.max(np.abs(x)) or 1.0
    x = _equalize(x, sr, low_cut, presence_db)
    if chorus:
        x = (x + chorus * _chorus(x, sr)) / (1 + chorus)
    if reverb:
        x = (1 - reverb) * x + reverb * _convolve(x, _impulse_response(sr, 0.45))
    # Возвращаем исходную громкость и мягко ограничиваем пики.
    x *= peak_in / (np.max(np.abs(x)) or 1.0)
    x = np.tanh(x * 1.1) / np.tanh(1.1)
    return np.clip(x * 32767, -32768, 32767).astype(np.int16)


def envelope(pcm: np.ndarray, sr: int, frame_ms: int = 40) -> np.ndarray:
    """Громкость речи по кадрам (0..1) — для анимации эмблемы, пока ассистент говорит."""
    frame = max(1, int(sr * frame_ms / 1000))
    usable = len(pcm) - len(pcm) % frame
    if usable <= 0:
        return np.zeros(1)
    frames = pcm[:usable].astype(np.float64).reshape(-1, frame) / 32768.0
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    return np.clip(rms / (np.percentile(rms, 95) or 1.0), 0.0, 1.0)
