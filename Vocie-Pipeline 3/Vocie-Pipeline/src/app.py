import asyncio
import json
import logging
import time

from dotenv import load_dotenv

from livekit.agents import AudioConfig, BackgroundAudioPlayer, BuiltinAudioClip, JobContext
from livekit.agents.voice import Agent, AgentSession, room_io
from livekit.agents.worker import AgentServer, JobExecutorType
from livekit.plugins import ai_coustics, langchain, openai, sarvam, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from src.config import get_config
from src.project_agent import agent_graph, load_cache_for_project
from src.prompt import get_prompt
from src.tts_engine import SupertonicTTS

load_dotenv()

# ----------------------------- Setup -----------------------------------------

logger = logging.getLogger("voice-agent")
logger.setLevel(logging.INFO)

# room_name → (AgentSession, BackgroundAudioPlayer); used by POST /config to restart sessions
_active_sessions: dict[str, tuple[AgentSession, BackgroundAudioPlayer]] = {}

# ----------------------------- Voice Agent -----------------------------------

class VoiceAgent(Agent):

    def __init__(self) -> None:
        cfg = get_config()

        if cfg.use_cloud:
            lang_code = "hi-IN" if cfg.language == "hi" else "en-IN"
            speaker = cfg.sarvam_speaker_hi if cfg.language == "hi" else cfg.sarvam_speaker_en
            stt = sarvam.STT(language=lang_code, model=cfg.sarvam_stt_model)
            tts = sarvam.TTS(
                target_language_code=lang_code,
                model=cfg.sarvam_tts_model,
                speaker=speaker,
            )
        else:
            stt = openai.STT(model="whisper-1")
            tts = SupertonicTTS(
                voice_name=cfg.supertonic_voice,
                lang="hi" if cfg.language == "hi" else "en",
                total_steps=cfg.supertonic_steps,
                speed=cfg.supertonic_speed,
            )

        super().__init__(
            instructions=get_prompt(cfg.language),
            stt=stt,
            llm=langchain.LLMAdapter(agent_graph),
            tts=tts,
        )
        self._turn_start: float = 0.0

    async def on_enter(self):
        await self.session.say("Hello! I'm your voice assistant. Go ahead and speak.")

    async def on_user_turn_completed(self, turn_ctx, new_message):
        self._turn_start = time.perf_counter()
        print(f"\n[STT] {new_message.text_content}", flush=True)
        await super().on_user_turn_completed(turn_ctx, new_message)

    async def on_agent_turn_completed(self, turn_ctx, new_message):
        elapsed = (time.perf_counter() - self._turn_start) * 1000
        print(f"[TIMER] STT→LLM→TTS: {elapsed:.0f}ms", flush=True)
        print(f"[LLM]  {new_message.text_content}\n", flush=True)
        await super().on_agent_turn_completed(turn_ctx, new_message)

# ----------------------------- Entrypoint ------------------------------------

async def entrypoint(ctx: JobContext):
    logger.info("User connected to room: %s", ctx.room.name)

    try:
        meta = json.loads(ctx.job.metadata or "{}")
        project_id = meta.get("project_id")
        if project_id:
            load_cache_for_project(project_id)
        else:
            logger.warning("No project_id in job metadata — cache not loaded")
    except Exception as e:
        logger.error("Failed to load project cache: %s", e)

    vad = silero.VAD.load(
        activation_threshold=0.3,
        deactivation_threshold=0.2,
        min_speech_duration=0.1,
        min_silence_duration=1.0,
        prefix_padding_duration=0.3,
    )

    session = AgentSession(
        vad=vad,
        turn_handling={
            "turn_detection": MultilingualModel(),
            "endpointing": {
                "mode": "dynamic",
                "min_delay": 0.3,
                "max_delay": 4.0,
            },
            "interruption": {
                "enabled": True,
                "mode": "adaptive",
                "min_duration": 0.4,
                "resume_false_interruption": True,
            },
            "preemptive_generation": {
                "enabled": True,
            },
        },
    )

    bg_audio = BackgroundAudioPlayer(
        thinking_sound=[
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.8),
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.7),
        ],
    )

    room_name = ctx.room.name
    _active_sessions[room_name] = (session, bg_audio)
    try:
        await session.start(
            agent=VoiceAgent(),
            room=ctx.room,
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(
                    noise_cancellation=ai_coustics.audio_enhancement(
                        model=ai_coustics.EnhancerModel.QUAIL_VF_S,
                    ),
                ),
            ),
        )
        await bg_audio.start(room=ctx.room, agent_session=session)
    finally:
        _active_sessions.pop(room_name, None)

# ----------------------------- Server ----------------------------------------

agent_server = AgentServer(job_executor_type=JobExecutorType.THREAD)
agent_server.rtc_session(entrypoint, agent_name="voice-agent")

if __name__ == "__main__":
    asyncio.run(agent_server.run(devmode=True))
