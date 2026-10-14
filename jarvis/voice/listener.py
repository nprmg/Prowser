"""Распознавание речи с микрофона и поиск слова-активатора («Джарвис»)."""

import logging
import re
import threading

import speech_recognition as sr

from ..config import Settings

log = logging.getLogger("jarvis.voice")


class ListenerError(RuntimeError):
    pass


class _StreamReader:
    def __init__(self, stream):
        self._stream = stream

    def read(self, frames: int) -> bytes:
        data, _overflowed = self._stream.read(frames)
        return bytes(data)


class SoundDeviceMicrophone(sr.AudioSource):
    """Микрофон на базе sounddevice — не требует PyAudio, который сложно ставить на Windows."""

    def __init__(self, device: int | None = None, sample_rate: int = 16000, chunk_size: int = 1024):
        self.device = device
        self.SAMPLE_RATE = sample_rate
        self.SAMPLE_WIDTH = 2  # int16
        self.CHUNK = chunk_size
        self.stream = None
        self._raw = None

    def __enter__(self):
        import sounddevice as sd

        try:
            self._raw = sd.RawInputStream(
                samplerate=self.SAMPLE_RATE, blocksize=self.CHUNK, device=self.device, channels=1, dtype="int16"
            )
        except sd.PortAudioError:
            # Некоторые устройства не умеют 16 кГц — берём их родную частоту.
            info = sd.query_devices(self.device, "input")
            self.SAMPLE_RATE = int(info["default_samplerate"])
            self._raw = sd.RawInputStream(
                samplerate=self.SAMPLE_RATE, blocksize=self.CHUNK, device=self.device, channels=1, dtype="int16"
            )
        self._raw.start()
        self.stream = _StreamReader(self._raw)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self._raw.stop()
            self._raw.close()
        finally:
            self._raw = None
            self.stream = None


def list_microphones() -> list[str]:
    import sounddevice as sd

    return [f"{i}: {d['name']}" for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]


class Listener:
    def __init__(self, settings: Settings):
        self.language = settings.language
        self.phrase_time_limit = settings.phrase_time_limit
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.8
        self.microphone = SoundDeviceMicrophone(device=settings.mic_device)
        self._calibrated = False
        self._lock = threading.Lock()

    def listen_once(self, timeout: float | None = 5, phrase_time_limit: float | None = None) -> str | None:
        """Ждёт фразу (до timeout секунд тишины) и возвращает распознанный текст или None."""
        with self._lock:
            try:
                with self.microphone as source:
                    if not self._calibrated:
                        self.recognizer.adjust_for_ambient_noise(source, duration=1.0)
                        self._calibrated = True
                    audio = self.recognizer.listen(
                        source, timeout=timeout, phrase_time_limit=phrase_time_limit or self.phrase_time_limit
                    )
            except sr.WaitTimeoutError:
                return None
            except Exception as exc:
                raise ListenerError(f"Микрофон недоступен: {exc}") from exc
        try:
            text = self.recognizer.recognize_google(audio, language=self.language)
        except sr.UnknownValueError:
            return None
        except sr.RequestError as exc:
            raise ListenerError(f"Сервис распознавания речи недоступен: {exc}") from exc
        text = text.strip() if isinstance(text, str) else ""
        log.info("Распознано: %s", text)
        return text or None


_FILLERS = {"эй", "окей", "ок", "ok", "hey", "привет", "слушай", "ну", "алло"}


def split_wake_word(text: str, wake_words: list[str]) -> tuple[bool, str]:
    """Ищет слово-активатор. Возвращает (найдено ли, команда без него)."""
    normalized = text.lower().replace("ё", "е")
    for word in sorted(wake_words, key=len, reverse=True):
        match = re.search(rf"\b{re.escape(word)}\w*", normalized)  # \w* — падежи: «джарвиса», «джарвис,»
        if match:
            command = f"{text[:match.start()]} {text[match.end():]}"
            command = re.sub(r"\s+", " ", command).strip(" ,.!?:;-")
            if command.lower() in _FILLERS:
                command = ""
            return True, command
    return False, text


_YES = {"да", "ага", "угу", "конечно", "подтверждаю", "выполняй", "давай", "делай", "согласен", "yes", "можно"}
_NO = {"нет", "отмена", "отмени", "отменить", "стоп", "no", "нельзя", "не"}


def parse_yes_no(text: str) -> bool | None:
    words = set(re.findall(r"\w+", text.lower().replace("ё", "е")))
    if words & _NO:
        return False
    if words & _YES:
        return True
    return None
