"""
capacities-sync plugin for Hermes.

Registers a model-callable tool `sync_conversation_to_capacities` that exports
a Hermes session and ingests it into Capacities using the existing
capacities_tool pipeline (RootAIChat first, RootPage fallback, deduplication).

Usage from a Hermes chat:
  "Sync this conversation to Capacities with title 'My Chat'"

The tool calls the underlying `capacities_tool.session_ingest_bot` logic
directly (no subprocess), so it runs inside the Hermes process.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

# Make the capacities_tool package importable from this plugin.
_CAPACITIES_TOOL_DIR = Path("C:/Temp/Stuff/capacities_tool")
if str(_CAPACITIES_TOOL_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_CAPACITIES_TOOL_DIR.parent))

from capacities_tool.config import CapacitiesConfig
from capacities_tool.session_ingest_bot import (
    export_session_to_jsonl,
    extract_messages_from_export,
    messages_to_conversation_dict,
    ingest_conversation as sb_ingest_conversation,
)
from tools.registry import tool_error, tool_result

logger = logging.getLogger(__name__)

SYNC_TOOL_SCHEMA = {
    "description": (
        "Export a Hermes session and ingest it into Capacities. "
        "Use this when the user wants to back up a conversation to Capacities. "
        "The tool tries RootAIChat first, falls back to RootPage on failure, "
        "and deduplicates by title."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Hermes session ID to sync. If omitted, uses the current session.",
            },
            "title": {
                "type": "string",
                "description": "Title for the Capacities object (required).",
            },
        },
        "required": ["title"],
        "additionalProperties": False,
    },
}


def _sync_conversation(args: dict, **kwargs: Any) -> str:
    """Handler: export a Hermes session and ingest into Capacities."""
    del kwargs

    title = args.get("title")
    session_id = args.get("session_id")

    if not isinstance(title, str) or not title.strip():
        return tool_error("title must be a non-empty string")

    # Load Capacities config from environment.
    try:
        config = CapacitiesConfig.load()
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"Failed to load Capacities config: {exc}")

    try:
        # Export the session to a temp file.
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            export_path = Path(tmpdir) / f"{session_id or 'current'}.jsonl"

            try:
                export_session_to_jsonl(session_id, export_path)
            except RuntimeError as exc:
                return tool_error(f"Failed to export session: {exc}")

            # Extract messages.
            messages = extract_messages_from_export(
                export_path,
                include_tool_messages=False,
                min_content_length=0,
            )

            if not messages:
                return tool_error("No messages extracted from session.")

            # Build conversation dict for ingestion.
            conversation = messages_to_conversation_dict(
                title=title,
                messages=messages,
                source="hermes-session",
                participants="user,assistant",
            )

            # Ingest into Capacities (uses RootPage fallback on RootAIChat failure).
            result = sb_ingest_conversation(conversation, config)

            if result is None:
                return tool_result({
                    "success": True,
                    "deduplicated": True,
                    "message": "Conversation already exists (deduplicated).",
                })

            return tool_result({
                "success": True,
                "object_id": result.get("id"),
                "deduplicated": False,
                "message": f"Ingested conversation -> object {result.get('id')}",
            })

    except Exception as exc:  # noqa: BLE001
        logger.exception("Sync to Capacities failed")
        return tool_error(f"Sync failed: {exc}")


def register(ctx: Any) -> None:
    """Plugin entry point - expose the sync tool."""
    ctx.register_tool(
        name="sync_conversation_to_capacities",
        toolset="capacities_sync",
        schema=SYNC_TOOL_SCHEMA,
        handler=_sync_conversation,
        description=(
            "Export a Hermes session and ingest it into Capacities. "
            "Tries RootAIChat first, falls back to RootPage, deduplicates by title."
        ),
    )
    logger.debug("capacities-sync: registered sync_conversation_to_capacities")
