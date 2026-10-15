"""Синтез речи: голосовые профили (по умолчанию «Джарвис»), нейроголос edge-tts с обработкой
и запасной системный голос."""

import asyncio
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from ..config import Settings
from ..paths import CACHE_DIR
from ..tools._platform import IS_MAC, IS_WIN, ensure_com
from .profiles import VoiceProfile, get_profile

SAMPLE_RATE = 24000  # edge-tts отдаёт 24 кГц моно
TTS_CACHE = CACHE_DIR / "tts"
CACHE_MAX_CHARS = 80  # короткие фразы («Да, сэр?», «Открываю Блокнот») озвучиваем один раз и храним
CACHE_MAX_FILES = 400

log = logging.getLogger("jarvis.voice")

_CLEANUP = [
    (re.compile(r"```.*?```", re.S), " "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
    (re.compile(r"https?://\S+"), ""),
    (re.compile(r"^\s*[-•]\s+", re.M), ""),
    (re.compile(r"[*_#>~|]"), ""),
]


def clean_for_speech(text: str) -> str:
    for pattern, replacement in _CLEANUP:
        text = pattern.sub(replacement, text)
    return re.sub(r"\s+", " ", text).strip()


class Speaker:
    def __init__(self, settings: Settings):
        self.enabled = settings.tts_engine != "off"
        self.profile: VoiceProfile = get_profile("system" if settings.tts_engine == "system" else settings.voice_profile)
        self.speaking = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._edge_failures = 0
        self._edge_paused_until = 0.0
        self._pygame_ready = False
        self._process: subprocess.Popen | None = None
        self._envelope = None  # громкость текущей фразы по кадрам 40 мс — для анимации эмблемы
        self._started = 0.0

    def set_profile(self, key: str) -> None:
        self.profile = get_profile(key)
        self._edge_failures = 0
        self._edge_paused_until = 0.0

    def level(self) -> float:
        """Громкость произносимой сейчас речи (0..1)."""
        envelope = self._envelope
        if envelope is None:
            return 0.0
        index = int((time.monotonic() - self._started) / 0.04)
        return float(envelope[index]) if 0 <= index < len(envelope) else 0.0

    def say(self, text: str) -> None:
        """Произносит текст (блокирует до конца фразы). Фразы из разных потоков идут по очереди."""
        text = clean_for_speech(text)
        if not text or not self.enabled:
            return
        with self._lock:
            self._stop.clear()
            self.speaking.set()
            try:
                profile = self.profile
                if profile.engine == "edge" and time.monotonic() >= self._edge_paused_until:
                    if self._say_edge(text, profile):
                        return
                self._say_system(text)
            except Exception:
                log.exception("Ошибка синтеза речи")
            finally:
                self._envelope = None
                self.speaking.clear()

    def stop(self) -> None:
        """Прерывает текущую фразу."""
        self._stop.set()
        if self._pygame_ready:
            try:
                import pygame

                pygame.mixer.stop()
            except Exception:
                pass
        if self._process and self._process.poll() is None:
            self._process.terminate()

    # --- edge-tts ----------------------------------------------------------------------

    @staticmethod
    def _cache_path(text: str, profile: VoiceProfile):
        if len(text) > CACHE_MAX_CHARS:
            return None
        key = "|".join([profile.voice, profile.rate, profile.pitch, repr(sorted(profile.effects.items())), text])
        return TTS_CACHE / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.pcm"

    def _say_cached(self, path) -> bool:
        try:
            pcm_bytes = path.read_bytes()
        except OSError:
            return False
        import numpy as np

        os.utime(path)  # отметка «недавно использовано» для очистки старых файлов
        self._play_pcm(np.frombuffer(pcm_bytes, dtype=np.int16))
        return True

    @staticmethod
    def _store_cache(path, pcm) -> None:
        try:
            TTS_CACHE.mkdir(parents=True, exist_ok=True)
            path.write_bytes(pcm.tobytes())
            files = sorted(TTS_CACHE.glob("*.pcm"), key=lambda f: f.stat().st_mtime)
            for old in files[: max(0, len(files) - CACHE_MAX_FILES)]:
                old.unlink(missing_ok=True)
        except OSError as exc:
            log.debug("Кэш озвучки недоступен: %s", exc)

    def _say_edge(self, text: str, profile: VoiceProfile) -> bool:
        cache_path = self._cache_path(text, profile)
        if cache_path is not None and cache_path.exists() and self._say_cached(cache_path):
            return True
        path = os.path.join(tempfile.gettempdir(), f"jarvis_tts_{uuid.uuid4().hex}.mp3")
        try:
            import edge_tts

            communicate = edge_tts.Communicate(text, profile.voice, rate=profile.rate, pitch=profile.pitch)
            asyncio.run(communicate.save(path))
            self._edge_failures = 0
        except Exception as exc:
            self._edge_failures += 1
            log.warning("edge-tts не сработал (%s), использую системный голос", exc)
            if self._edge_failures >= 3:  # сервис недоступен — не ждём его на каждой фразе
                log.warning("edge-tts недоступен, 5 минут говорю системным голосом")
                self._edge_paused_until = time.monotonic() + 300
                self._edge_failures = 0
            self._remove(path)
            return False
        try:
            if not self._stop.is_set():
                pcm = self._decode(path, profile.effects)
                if cache_path is not None:
                    self._store_cache(cache_path, pcm)
                self._play_pcm(pcm)
        finally:
            self._remove(path)
        return True

    def _mixer(self):
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame

        if not self._pygame_ready:
            pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=1)
            self._pygame_ready = True
        return pygame

    def _decode(self, path: str, effects: dict):
        """MP3 от edge-tts -> PCM 24 кГц моно (+ обработка профиля)."""
        import numpy as np

        from .effects import apply_jarvis

        pcm = np.frombuffer(self._mixer().mixer.Sound(path).get_raw(), dtype=np.int16)
        return apply_jarvis(pcm, SAMPLE_RATE, **effects) if effects else pcm

    def _play_pcm(self, pcm) -> None:
        from .effects import envelope

        pygame = self._mixer()
        sound = pygame.mixer.Sound(buffer=pcm.tobytes())
        self._envelope = envelope(pcm, SAMPLE_RATE)
        self._started = time.monotonic()
        channel = sound.play()
        while channel is not None and channel.get_busy() and not self._stop.is_set():
            time.sleep(0.03)
        sound.stop()

    @staticmethod
    def _remove(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    # --- Системный голос ---------------------------------------------------------------

    def _say_system(self, text: str) -> None:
        if IS_WIN:
            self._say_sapi(text)
            return
        command = None
        if IS_MAC:
            command = ["say", text]
        elif shutil.which("spd-say"):
            command = ["spd-say", "-w", "-l", "ru", text]
        elif shutil.which("espeak-ng") or shutil.which("espeak"):
            command = [shutil.which("espeak-ng") or "espeak", "-v", "ru", text]
        if not command:
            log.warning("Нет системного синтезатора речи (установите espeak-ng)")
            return
        self._process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while self._process.poll() is None:
            if self._stop.is_set():
                self._process.terminate()
            time.sleep(0.05)

    def _say_sapi(self, text: str) -> None:
        ensure_com()
        import comtypes.client

        voice = comtypes.client.CreateObject("SAPI.SpVoice")
        voices = voice.GetVoices()
        for i in range(voices.Count):
            token = voices.Item(i)
            try:
                is_russian = token.GetAttribute("Language").startswith("419") or "russian" in token.GetDescription().lower()
            except Exception:
                continue
            if is_russian:  # русский голос (Irina, Pavel), если установлен
                voice.Voice = token
                break
        voice.Speak(text, 1)  # 1 = асинхронно, чтобы можно было прервать
        while not voice.WaitUntilDone(100):
            if self._stop.is_set():
                voice.Speak("", 3)  # очистить очередь и остановить
                break
