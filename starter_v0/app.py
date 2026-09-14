"""Streamlit chat UI for the IT Helpdesk Agent.

Thin wrapper around chat.run_model_tool_loop so the UI, the CLI (chat.py)
and eval runs (run_eval.py) all drive the exact same agent loop.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from chat import run_model_tool_loop, write_transcript
from env_loader import load_lab_env
from providers import make_provider
from tools import load_tool_declarations, to_openai_tools
from versioning import artifact_version_dict, build_artifact_version

ROOT = Path(__file__).parent
ARTIFACTS_DIR = ROOT / "artifacts"
TRANSCRIPTS_DIR = ROOT / "transcripts"

PROVIDER_CHOICES = ["openrouter", "openai", "anthropic", "gemini"]
VERSION_CHOICES = ["v0", "v1", "v2", "v3"]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_session_id(version: str, provider: str) -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{version}_{provider}_{stamp}_{uuid.uuid4().hex[:6]}"


def render_tool_trace(tool_events: list[dict[str, Any]]) -> None:
    if not tool_events:
        return
    with st.expander(f"Tool trace ({len(tool_events)} call{'s' if len(tool_events) != 1 else ''})", expanded=False):
        for i, event in enumerate(tool_events, start=1):
            result = event.get("result") or {}
            is_error = isinstance(result, dict) and result.get("error")
            status = "error" if is_error else "ok"
            st.markdown(f"**{i}. `{event.get('tool')}`** — `{status}`")
            col_args, col_result = st.columns(2)
            with col_args:
                st.caption("args")
                st.json(event.get("args", {}))
            with col_result:
                st.caption("result")
                st.json(result)


def init_state(version: str, provider: str) -> None:
    if "turns" not in st.session_state:
        st.session_state.turns = []  # rendered chat turns: {role, text, tool_events}
    if "history" not in st.session_state:
        st.session_state.history = []  # raw user/assistant messages fed back into the model
    if "session_id" not in st.session_state:
        st.session_state.session_id = new_session_id(version, provider)
    if "tool_call_count" not in st.session_state:
        st.session_state.tool_call_count = 0
    if "error_count" not in st.session_state:
        st.session_state.error_count = 0


def reset_conversation(version: str, provider: str) -> None:
    st.session_state.turns = []
    st.session_state.history = []
    st.session_state.tool_call_count = 0
    st.session_state.error_count = 0
    st.session_state.session_id = new_session_id(version, provider)


def main() -> None:
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    load_lab_env(ROOT)

    st.set_page_config(page_title="IT Helpdesk Agent", page_icon=":wrench:", layout="wide")

    with st.sidebar:
        st.header("Session settings")
        version_label = st.selectbox("Artifact version", VERSION_CHOICES, index=len(VERSION_CHOICES) - 1)
        provider_choice = st.selectbox("Model provider", PROVIDER_CHOICES, index=0)
        model_override = st.text_input("Model override (optional)", value="")
        history_window = st.slider("History window (turns)", min_value=1, max_value=10, value=5)
        max_tool_rounds = st.slider("Max tool rounds", min_value=1, max_value=6, value=4)

        init_state(version_label, provider_choice)

        if st.button("Start new conversation", use_container_width=True):
            reset_conversation(version_label, provider_choice)
            st.rerun()

        system_prompt_path = ARTIFACTS_DIR / "system_prompt.md"
        tools_path = ARTIFACTS_DIR / "tools.yaml"
        try:
            artifact_version = build_artifact_version(version_label, system_prompt_path, tools_path)
            version_error = None
        except FileNotFoundError as exc:
            artifact_version = None
            version_error = str(exc)

        st.divider()
        st.caption("Artifact")
        if artifact_version:
            st.code(artifact_version.artifact_version, language="text")
            st.caption(f"prompt_hash: {artifact_version.prompt_hash[:16]}...")
            st.caption(f"tools_hash: {artifact_version.tools_hash[:16]}...")
        else:
            st.error(f"Could not hash artifacts: {version_error}")

        transcript_path = TRANSCRIPTS_DIR / f"{st.session_state.session_id}.transcript.json"
        st.caption("Transcript file")
        st.code(str(transcript_path.relative_to(ROOT)), language="text")

        st.divider()
        st.caption("Session stats")
        stat_a, stat_b = st.columns(2)
        stat_a.metric("Tool calls", st.session_state.tool_call_count)
        stat_b.metric("Errors", st.session_state.error_count)

    st.title("IT Helpdesk Agent")
    st.caption(
        "Structured tool calling with multi-turn context, clarification and confirmation boundaries."
    )

    if artifact_version is None:
        st.stop()

    system_prompt = system_prompt_path.read_text(encoding="utf-8")
    tool_declarations = load_tool_declarations(tools_path)
    openai_tools = to_openai_tools(tool_declarations)

    for turn in st.session_state.turns:
        with st.chat_message(turn["role"]):
            st.markdown(turn["text"])
            render_tool_trace(turn.get("tool_events", []))

    user_text = st.chat_input("Describe the IT issue (e.g. 'VPN production status?', 'Asset LT-204 network check')")
    if not user_text:
        return

    st.session_state.turns.append({"role": "user", "text": user_text, "tool_events": []})
    with st.chat_message("user"):
        st.markdown(user_text)

    recent_history = st.session_state.history[-history_window * 2:]
    messages = [
        {"role": "system", "content": system_prompt},
        *recent_history,
        {"role": "user", "content": user_text},
    ]

    with st.chat_message("assistant"):
        with st.spinner("Routing request and executing tools..."):
            try:
                provider = make_provider(provider_choice)
                result = run_model_tool_loop(
                    provider=provider,
                    messages=messages,
                    tools=openai_tools,
                    model=model_override.strip() or None,
                    max_tool_rounds=max_tool_rounds,
                )
                assistant_text = result.get("assistant_text") or "(no response text)"
                tool_events = result.get("tool_events", [])

                st.markdown(assistant_text)
                render_tool_trace(tool_events)
                if result.get("status") not in ("answered", "waiting_for_user"):
                    st.warning(f"Loop status: {result.get('status')}")

                st.session_state.tool_call_count += len(tool_events)
                st.session_state.error_count += sum(
                    1 for e in tool_events if isinstance(e.get("result"), dict) and e["result"].get("error")
                )

                st.session_state.turns.append(
                    {"role": "assistant", "text": assistant_text, "tool_events": tool_events}
                )
                st.session_state.history.append({"role": "user", "content": user_text})
                st.session_state.history.append({"role": "assistant", "content": assistant_text})

                write_transcript(
                    transcript_path,
                    {
                        "transcript_id": st.session_state.session_id,
                        **artifact_version_dict(artifact_version),
                        "provider": provider_choice,
                        "model": model_override.strip() or getattr(provider, "default_model", None),
                        "created_at": st.session_state.get("_created_at", now_iso()),
                        "turns": st.session_state.turns,
                    },
                )
            except Exception as exc:
                st.session_state.error_count += 1
                error_text = f"{type(exc).__name__}: {exc}"
                st.error(f"Agent loop failed: {error_text}")
                st.session_state.turns.append(
                    {"role": "assistant", "text": f"Error: {error_text}", "tool_events": []}
                )


if __name__ == "__main__":
    main()
