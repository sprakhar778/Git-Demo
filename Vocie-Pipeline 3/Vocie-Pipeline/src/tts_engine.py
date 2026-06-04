import asyncio
import re
import time
import uuid
from dataclasses import dataclass

import numpy as np
from dotenv import load_dotenv
from scipy.signal import resample_poly

from livekit.agents import APIConnectOptions, tts
from supertonic import TTS as SupertonicEngine

load_dotenv()

# ----------------------------- Constants -------------------------------------

NATIVE_RATE = 44_100   # Supertonic output rate
OUTPUT_RATE = 24_000   # LiveKit expected rate
CHANNELS = 1

# ----------------------------- Helpers ---------------------------------------

def split_clauses(text: str) -> list[str]:
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    result = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(s) > 200:
            parts = re.split(r'(?<=[,;])\s+', s)
            result.extend(p.strip() for p in parts if p.strip())
        else:
            result.append(s)
    return result or [text.strip()]


@dataclass
class TTSOptions:
    voice_name: str = "M1"
    lang: str = "en"
    total_steps: int = 8
    speed: float = 1.05

# ----------------------------- Model Load ------------------------------------

print("Loading Supertonic model…", flush=True)
_engine = SupertonicEngine(auto_download=True)
print("Supertonic model loaded", flush=True)

# ----------------------------- Chunked Stream --------------------------------

class SupertonicChunkedStream(tts.ChunkedStream):

    def __init__(self, *, tts_instance: "SupertonicTTS", input_text: str, opts: TTSOptions, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts_instance, input_text=input_text, conn_options=conn_options)
        self._opts = opts
        self._tts = tts_instance

    async def _run(self, output_emitter) -> None:
        loop = asyncio.get_event_loop()
        clauses = split_clauses(self._input_text)
        print(f"  [TTS] {len(clauses)} clause(s): {self._input_text!r}", flush=True)

        segment_id = uuid.uuid4().hex
        output_emitter.initialize(
            request_id=segment_id,
            sample_rate=OUTPUT_RATE,
            num_channels=CHANNELS,
            mime_type="audio/pcm",
            stream=True,
        )
        output_emitter.start_segment(segment_id=segment_id)

        async with self._tts._sem:
            for i, clause in enumerate(clauses, 1):
                t0 = time.perf_counter()
                wav, dur = await loop.run_in_executor(None, self._synthesize, clause)
                elapsed = time.perf_counter() - t0
                print(f"  [TTS] {i}/{len(clauses)} {elapsed:.2f}s → {dur[0]:.2f}s audio", flush=True)
                # resample 44100 Hz → 24000 Hz (ratio 80/147)
                audio = (resample_poly(wav.squeeze(), 80, 147) * 32767).clip(-32768, 32767).astype(np.int16)
                output_emitter.push(audio.tobytes())

        output_emitter.flush()

    def _synthesize(self, text: str):
        style = _engine.get_voice_style(voice_name=self._opts.voice_name)
        wav, duration = _engine.synthesize(
            text=text,
            lang=self._opts.lang,
            voice_style=style,
            total_steps=self._opts.total_steps,
            speed=self._opts.speed,
        )
        return wav, duration

# ----------------------------- TTS Class -------------------------------------

class SupertonicTTS(tts.TTS):

    def __init__(self, *, voice_name: str = "M1", lang: str = "en", total_steps: int = 8, speed: float = 1.05) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=OUTPUT_RATE,
            num_channels=CHANNELS,
        )
        self._opts = TTSOptions(voice_name=voice_name, lang=lang, total_steps=total_steps, speed=speed)
        self._sem = asyncio.Semaphore(1)

    def synthesize(self, text: str, *, conn_options: APIConnectOptions) -> SupertonicChunkedStream:
        return SupertonicChunkedStream(
            tts_instance=self,
            input_text=text,
            opts=self._opts,
            conn_options=conn_options,
        )
