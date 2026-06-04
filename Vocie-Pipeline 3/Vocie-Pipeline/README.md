# Voice Pipeline

A real-time voice agent in the browser — Whisper STT → GPT-4o-mini → Supertonic3 TTS, connected via LiveKit.

---

## Stack

| Layer | What |
|---|---|
| STT | OpenAI Whisper (`whisper-1`) |
| LLM | OpenAI GPT-4o-mini |
| TTS | Supertonic3 (local, runs on CPU) |
| Transport | LiveKit Cloud |
| Agent framework | `livekit-agents ~= 1.0` |
| Browser client | Vanilla JS + `livekit-client` UMD |
| Web server | `server.py` (Python built-in `http.server`) |

---

## Setup

```bash
# 1. Install dependencies
uv sync

# 2. Download Supertonic model weights
uv run python app.py download-files

# 3. Copy and fill in your keys
cp .env.example .env
```

`.env` needs:
```
# uvicorn src.main:app --host 0.0.0.0 --port 8080


LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
OPENAI_API_KEY=...
```

---

## Run

```bash
# Terminal 1 — agent worker (hot-reloads on file save)
python app.py dev

# Terminal 2 — web server
python server.py

# Browser
open http://localhost:8080/index.html
```

Click the bubble → **Connect & Talk** → speak.

---

## How it works

```
Browser mic → LiveKit Cloud → agent worker (Whisper STT → GPT-4o-mini → Supertonic TTS) → LiveKit Cloud → Browser speaker
```

`server.py` does two things on every `/token` request:
1. Calls the LiveKit API to **explicitly dispatch** the agent into the room
2. Returns a signed JWT so the browser can join the same room

---

## Fixes that were non-obvious

### 1. Explicit agent dispatch required
LiveKit Cloud projects default to explicit-dispatch mode — the agent worker does **not** auto-join when a browser participant connects. `server.py` must call:
```python
await lk.agent_dispatch.create_dispatch(
    lkapi.CreateAgentDispatchRequest(agent_name="", room=ROOM_NAME)
)
```
Note: the proto field is `room=`, **not** `room_name=`.

### 2. Mic permission needs a separate call
`room.connect(url, token)` does not trigger the browser mic permission dialog.
You must call this after connecting:
```javascript
await room.localParticipant.setMicrophoneEnabled(true);
```

### 3. Audio autoplay is blocked by browsers
The agent's voice plays through an `<audio>` element. Browsers block autoplay unless there has been a user gesture. Call this inside the button click handler before connecting:
```javascript
await new AudioContext().resume();
```
Also call `.play()` explicitly on the audio element after `track.attach()`.

### 4. Greeting confirms TTS is alive
In `on_enter`, use `say()` to speak directly (no LLM round-trip):
```python
async def on_enter(self):
    await self.session.say("Hello! I'm your voice assistant. Go ahead and speak.")
```

### 5. STT/TTS terminal output
Use `print(..., flush=True)` in the agent hooks so transcripts appear immediately:
```python
async def on_user_turn_completed(self, turn_ctx, new_message):
    print(f"\n[STT] {new_message.text_content}\n", flush=True)
    ...

async def on_agent_turn_completed(self, turn_ctx, new_message):
    print(f"[TTS] {new_message.text_content}\n", flush=True)
    ...
```
