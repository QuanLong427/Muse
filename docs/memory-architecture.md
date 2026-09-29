# Musicer Memory Architecture v2

## Goals

The memory system keeps conversation continuity without treating every message
as a durable preference. It separates lossless evidence from small, curated
directives and keeps LLM-Wiki as an independent music-knowledge layer.

## Layers

1. **Working context** — the server loads the last 16 user/agent messages from
   the current session. The browser sends only `user_id` and `session_id`; it is
   not the authority for conversation history.
2. **Episodic memory** — SQLite stores complete messages, tool calls and tool
   results with user, session, scenario, timestamps and metadata.
3. **Candidates** — Dream extracts structured proposals from user messages
   only. Every proposal records source message IDs, confidence and evidence
   type.
4. **Durable memory** — only candidates that pass deterministic gates become
   active directives. Global and current-scenario directives are injected into
   the Agent prompt.
5. **Domain knowledge** — LLM-Wiki stores attributable facts about music. Web
   content and Wiki results never become user preferences automatically.

## Storage

`memory/data/memory.db` contains:

- `memory_users`
- `memory_sessions`
- `memory_messages`
- `memory_candidates`
- `memory_items`
- `memory_dream_runs`

The existing `memory/data/history.jsonl` is imported once and left untouched.
Imported rows are marked processed so they do not rewrite the existing profile.
`user_profile.md` remains inspectable and receives a deterministic projection of
active structured memories.

## Promotion policy

- Explicit user preference/correction with confidence >= 0.75: promote.
- Repeated behavior with confidence >= 0.85: require independent evidence.
- Temporary requests, Agent inference and external content: reject.
- Invalid or invented source message IDs: reject.
- Dream batches are marked processed only after parsing and staging succeeds.

## Agent tools

- `search_memory`: retrieve attributable past messages on demand.
- `remember_preference`: direct write only for an explicit user request.
- `forget_preference`: soft-delete a durable preference on explicit request.

## HTTP API

- `GET /api/history?user_id=...&session_id=...`
- `GET /api/memory?user_id=...&scenario=...`
- `GET /api/memory/search?q=...&user_id=...`
- `DELETE /api/memory/items/{memory_key}?user_id=...`
- `POST /api/dream?user_id=...`

## Remaining work

- Add authenticated account IDs before exposing the backend to multiple users.
- Add a UI for new sessions and memory review/correction.
- Add Chinese trigram/hybrid retrieval when the episodic store becomes large.
- Record playback feedback events separately from conversation messages.
- Add retention/export controls and encryption for non-local deployments.
