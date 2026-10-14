"""Синтез речи: нейроголос Microsoft Edge (edge-tts) с запасным системным голосом."""

import asyncio
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
from ..tools._platform import IS_MAC, IS_WIN, ensure_com

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
        self.engine = settings.tts_engine
        self.voice = settings.tts_voice
        self.rate = settings.tts_rate
        self.enabled = self.engine != "off"
        self.speaking = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._edge_failures = 0
        self._pygame_ready = False
        self._process: subprocess.Popen | None = None

    def say(self, text: str) -> None:
        """Произносит текст (блокирует до конца фразы). Фразы из разных потоков идут по очереди."""
        text = clean_for_speech(text)
        if not text or not self.enabled or self.engine == "off":
            return
        with self._lock:
            self._stop.clear()
            self.speaking.set()
            try:
                if self.engine == "edge" and self._say_edge(text):
                    return
                self._say_system(text)
            except Exception:
                log.exception("Ошибка синтеза речи")
            finally:
                self.speaking.clear()

    def stop(self) -> None:
        """Прерывает текущую фразу."""
        self._stop.set()
        if self._pygame_ready:
            try:
                import pygame

                pygame.mixer.music.stop()
            except Exception:
                pass
        if self._process and self._process.poll() is None:
            self._process.terminate()

    # --- edge-tts ----------------------------------------------------------------------

    def _say_edge(self, text: str) -> bool:
        path = os.path.join(tempfile.gettempdir(), f"jarvis_tts_{uuid.uuid4().hex}.mp3")
        try:
            import edge_tts

            asyncio.run(edge_tts.Communicate(text, self.voice, rate=self.rate).save(path))
            self._edge_failures = 0
        except Exception as exc:
            self._edge_failures += 1
            log.warning("edge-tts не сработал (%s), использую системный голос", exc)
            if self._edge_failures >= 3:
                log.warning("edge-tts недоступен, окончательно переключаюсь на системный голос")
                self.engine = "system"
            self._remove(path)
            return False
        try:
            if not self._stop.is_set():
                self._play(path)
        finally:
            self._remove(path)
        return True

    def _play(self, path: str) -> None:
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame

        if not self._pygame_ready:
            pygame.mixer.init()
            self._pygame_ready = True
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy() and not self._stop.is_set():
            time.sleep(0.05)
        pygame.mixer.music.stop()
        pygame.mixer.music.unload()

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
