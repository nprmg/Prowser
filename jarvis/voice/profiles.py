"""Голосовые профили. «Джарвис» — голос по умолчанию; остальные можно выбрать в настройках.

Чтобы добавить свой профиль, допишите его в PROFILES: движок edge (любой голос из
`edge-tts --list-voices`) или system (встроенный голос Windows, работает без интернета).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VoiceProfile:
    key: str
    title: str
    engine: str  # edge | system
    voice: str = ""
    rate: str = "+0%"
    pitch: str = "+0Hz"
    effects: dict = field(default_factory=dict)  # параметры jarvis.voice.effects.apply_jarvis


PROFILES: dict[str, VoiceProfile] = {
    profile.key: profile
    for profile in (
        VoiceProfile(
            "jarvis", "Джарвис", "edge", "ru-RU-DmitryNeural", rate="+4%", pitch="-7Hz",
            effects={"chorus": 0.10, "reverb": 0.13, "presence_db": 2.5},
        ),
        VoiceProfile("dmitry", "Дмитрий (без обработки)", "edge", "ru-RU-DmitryNeural", rate="+5%"),
        VoiceProfile("svetlana", "Светлана", "edge", "ru-RU-SvetlanaNeural", rate="+5%"),
        VoiceProfile("system", "Голос Windows (без интернета)", "system"),
    )
}

DEFAULT_PROFILE = "jarvis"


def get_profile(key: str) -> VoiceProfile:
    return PROFILES.get(key) or PROFILES[DEFAULT_PROFILE]
