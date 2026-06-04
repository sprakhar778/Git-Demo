import asyncio
import json
import os
import traceback
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from livekit import api as lkapi
from pydantic import ValidationError

from src.app import _active_sessions, agent_server
from src.config import SARVAM_SPEAKERS, SUPERTONIC_VOICES, get_config, update_config

load_dotenv()

# ----------------------------- Environment ------------------------------------

API_KEY = os.environ["LIVEKIT_API_KEY"]
API_SECRET = os.environ["LIVEKIT_API_SECRET"]
LIVEKIT_URL = os.environ["LIVEKIT_URL"]

# ----------------------------- App Lifespan ----------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[startup] starting agent worker…", flush=True)
    task = asyncio.create_task(agent_server.run(devmode=True))
    print("[startup] agent worker running", flush=True)
    yield
    print("[shutdown] stopping agent worker…", flush=True)
    task.cancel()
    await agent_server.aclose()


app = FastAPI(lifespan=lifespan)

# ----------------------------- Helpers ---------------------------------------

def make_token(room_name: str) -> str:
    return (
        lkapi.AccessToken(api_key=API_KEY, api_secret=API_SECRET)
        .with_identity("user1")
        .with_name("User")
        .with_grants(lkapi.VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )


async def dispatch_agent(room_name: str, project_id: str) -> None:
    async with lkapi.LiveKitAPI(url=LIVEKIT_URL, api_key=API_KEY, api_secret=API_SECRET) as lk:
        try:
            resp = await lk.room.list_participants(lkapi.ListParticipantsRequest(room=room_name))
            if any(p.kind == 4 for p in resp.participants):
                print("[dispatch] agent already in room, skipping")
                return
        except Exception as e:
            print(f"[dispatch] list_participants: {e}")

        try:
            existing = await lk.agent_dispatch.list_dispatch(lkapi.ListAgentDispatchRequest(room=room_name))
            if existing.agent_dispatches:
                print("[dispatch] pending dispatch found, skipping")
                return
        except Exception as e:
            print(f"[dispatch] list_dispatch: {e}")

        await lk.agent_dispatch.create_dispatch(
            lkapi.CreateAgentDispatchRequest(
                agent_name="voice-agent",
                room=room_name,
                metadata=json.dumps({"project_id": project_id}),
            )
        )
        print(f"[dispatch] agent dispatched to room={room_name!r} project_id={project_id!r}")

# ----------------------------- Routes ----------------------------------------

@app.get("/token")
async def get_token(project_id: str):
    """Frontend calls /token?project_id=<mongo_doc_id> to start a session."""
    project_id = project_id.strip().rstrip("/")
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")

    room_name = f"room-{project_id}"
    print(f"\n[/token] project={project_id!r} → room={room_name!r}", flush=True)

    try:
        await dispatch_agent(room_name, project_id)
    except Exception as e:
        print(f"[/token] dispatch failed: {e}", flush=True)
        traceback.print_exc()

    token = make_token(room_name)
    print(f"[/token] token issued for room={room_name!r}", flush=True)
    return {"url": LIVEKIT_URL, "token": token}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/config")
async def get_current_config():
    """Return active configuration plus available options."""
    cfg = get_config()
    return {
        **cfg.model_dump(),
        "_meta": {
            "sarvam_speakers": SARVAM_SPEAKERS,
            "supertonic_voices": SUPERTONIC_VOICES,
        },
    }


@app.post("/config")
async def set_config(
    patch: dict = Body(
        openapi_examples={
            "cloud_hindi": {
                "summary": "Cloud · Hindi (Sarvam)",
                "value": {"use_cloud": True, "language": "hi", "sarvam_speaker_hi": "shubh"},
            },
            "cloud_english": {
                "summary": "Cloud · English (Sarvam)",
                "value": {"use_cloud": True, "language": "en", "sarvam_speaker_en": "ritu"},
            },
            "local_male": {
                "summary": "Local · Male voice (Supertonic M1–M5)",
                "value": {"use_cloud": False, "supertonic_voice": "M1", "supertonic_speed": 1.1},
            },
            "local_female": {
                "summary": "Local · Female voice (Supertonic F1–F5)",
                "value": {"use_cloud": False, "supertonic_voice": "F2", "supertonic_speed": 1.0},
            },
        }
    ),
):
    """Update one or more config fields. Active sessions are restarted immediately."""
    try:
        cfg = update_config(patch)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    print(f"[/config] updated → {cfg.model_dump()}", flush=True)

    # Stop background audio first, then close session to avoid PyAV EOF errors
    restarted: list[str] = []
    for room_name, (session, bg_audio) in list(_active_sessions.items()):
        try:
            await bg_audio.aclose()
        except Exception:
            pass
        try:
            await session.aclose()
            restarted.append(room_name)
            print(f"[/config] restarted room={room_name!r}", flush=True)
        except Exception as e:
            print(f"[/config] failed to close room={room_name!r}: {e}", flush=True)

    return {"status": "ok", "config": cfg.model_dump(), "restarted_rooms": restarted}

# ----------------------------- Static Files ----------------------------------

# Serve index.html from the src/ directory (same folder as this file)
static_dir = os.path.dirname(os.path.abspath(__file__))
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=False)
