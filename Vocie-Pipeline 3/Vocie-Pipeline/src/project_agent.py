import json
import os
import threading

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import ToolMessage
from langchain_openai import ChatOpenAI
from pymongo import MongoClient

from src.prompt import PROMPT

load_dotenv()

# ----------------------------- Cache -----------------------------------------

_cache: dict = {}
_lock = threading.Lock()


def load_cache_for_project(project_id: str) -> None:
    client = MongoClient(os.getenv("MONGO_URI"))
    doc = (
        client[os.getenv("MONGO_DB")][os.getenv("MONGO_COLLECTION")]
        .find_one({"_id": project_id}, {"_id": 0})
    )
    if not doc:
        print(f"  [CACHE] no document found for project_id={project_id!r}", flush=True)
        return
    data = doc.get("state", {})
    with _lock:
        _cache.clear()
        _cache.update(data)
    print(f"  [CACHE] loaded project {project_id!r} — {len(data)} sections", flush=True)


def _find_sections(node: dict | list, keyword: str, path: str = "") -> dict:
    results = {}
    if isinstance(node, dict):
        for k, v in node.items():
            current_path = f"{path}.{k}" if path else k
            if keyword.lower() in k.lower():
                results[current_path] = v
            else:
                results.update(_find_sections(v, keyword, current_path))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            results.update(_find_sections(item, keyword, f"{path}[{i}]"))
    return results

# ----------------------------- Tool ------------------------------------------

@tool
def get_project_data(topic: str) -> str:
    """
    Search the in-memory project document for a topic. Returns all matching
    sections including cross-references across design_input, design_output,
    and design_verification. No MongoDB call is made — data is read from RAM.

    Use specific terms like: sterilization, tibial plate, functional performance,
    biological safety, packaging, manufacturing, stability, labelling,
    user patient, statutory regulatory, device details, gspr,
    design input, design output, design verification.

    For cross-referenced queries pass comma-separated topics:
    e.g. "design_input, sterilization"
    """
    with _lock:
        snapshot = dict(_cache)

    if not snapshot:
        return "Project data not loaded yet. Please try again in a moment."

    keywords = [kw.strip() for kw in topic.replace(" and ", ",").split(",") if kw.strip()]

    combined: dict = {}
    for kw in keywords:
        combined.update(_find_sections(snapshot, kw))

    if not combined:
        return f"No data found for: {topic}"

    return json.dumps(combined, default=str)

# ----------------------------- Agent -----------------------------------------

agent_graph = create_agent(
    system_prompt=PROMPT,
    model=ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=400),
    tools=[get_project_data],
)

# Wrap astream to filter out raw ToolMessages so they never reach TTS
_orig_astream = agent_graph.astream


async def _filtered_astream(*args, **kwargs):
    async for item in _orig_astream(*args, **kwargs):
        msg = item[0] if isinstance(item, tuple) else item
        if isinstance(msg, ToolMessage):
            print(f"  [FILTER] blocked ToolMessage: {str(msg.content)[:60]!r}", flush=True)
            continue
        yield item


agent_graph.astream = _filtered_astream
