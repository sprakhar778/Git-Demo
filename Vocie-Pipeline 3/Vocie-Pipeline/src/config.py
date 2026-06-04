from typing import Literal

from pydantic import BaseModel, field_validator

# ----------------------------- Valid Options ----------------------------------

SARVAM_SPEAKERS: list[str] = [
    "anushka", "abhilash", "manisha", "vidya", "arya", "karun", "hitesh", "aditya",
    "ritu", "priya", "neha", "rahul", "pooja", "rohan", "simran", "kavya", "amit",
    "dev", "ishita", "shreya", "ratan", "varun", "manan", "sumit", "roopa", "kabir",
    "aayan", "shubh", "ashutosh", "advait", "anand", "tanya", "tarun", "sunny", "mani",
    "gokul", "vijay", "shruti", "suhani", "mohit", "kavitha", "rehan", "soham", "rupali",
]

SUPERTONIC_VOICES: list[str] = [
    "M1", "M2", "M3", "M4", "M5",
    "F1", "F2", "F3", "F4", "F5",
]

# ----------------------------- Config Model -----------------------------------

class VoiceConfig(BaseModel):
    use_cloud: bool = False
    language: Literal["en", "hi"] = "en"

    # Sarvam (cloud)
    sarvam_speaker_en: str = "ritu"
    sarvam_speaker_hi: str = "ritu"
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_stt_model: str = "saaras:v3"

    # Supertonic (local)
    supertonic_voice: str = "M1"
    supertonic_steps: int = 8
    supertonic_speed: float = 1.1

    @field_validator("sarvam_speaker_en", "sarvam_speaker_hi")
    @classmethod
    def validate_sarvam_speaker(cls, v: str) -> str:
        if v.lower() not in SARVAM_SPEAKERS:
            raise ValueError(
                f"'{v}' is not a valid Sarvam speaker. "
                f"Available: {', '.join(SARVAM_SPEAKERS)}"
            )
        return v.lower()

    @field_validator("supertonic_voice")
    @classmethod
    def validate_supertonic_voice(cls, v: str) -> str:
        if v not in SUPERTONIC_VOICES:
            raise ValueError(
                f"'{v}' is not a valid Supertonic voice. "
                f"Available: {', '.join(SUPERTONIC_VOICES)}"
            )
        return v

# ----------------------------- Singleton -------------------------------------

_config = VoiceConfig()


def get_config() -> VoiceConfig:
    return _config


def update_config(patch: dict) -> VoiceConfig:
    global _config
    _config = VoiceConfig(**{**_config.model_dump(), **patch})
    return _config
