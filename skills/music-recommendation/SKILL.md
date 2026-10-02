---
name: music-recommendation
description: Generate personalized, source-aware music recommendations from the current request, scenario, preference evidence, LLM-Wiki relations, local availability, and Bilibili discovery. Use for song recommendation, similar-music discovery, or scene-based listening suggestions; do not use for exact-title playback or plain catalog lookup.
---

# Music Recommendation

Use `recommend_music` for conversational recommendation and discovery. The
backend owns preference scoring, Wiki expansion, source quotas, fallback and
audit records; do not recreate those decisions with ad-hoc `local_search` and
`bili_search` calls.

## Inputs

- Preserve the user's explicit count, artist, genre and source constraint.
- If the user does not specify a count, use `count=4`.
- If the user does not specify a source, use `source_policy="balanced"`.
- Use `local` only for an explicit local/offline/immediately-playable request.
- Use `cloud` only for an explicit B站/online/cloud request.
- Pass a concise copy of the recommendation intent in `query`; extract an
  explicit artist or genre into its dedicated field instead of hiding it in
  prose.
- For an artist or genre request that includes cloud discovery, always pass
  2-8 concrete candidate song titles as one comma-separated `seed_songs`
  string, for example `"青花瓷, 稻香, 晴天, 七里香"`. Prefer
  verified/inferred LLM-Wiki relations; when the Wiki
  has no usable evidence, model knowledge may supply discovery hints. A hint is
  not a verified fact: only the records returned by local or B站 search may be
  presented as results.

## Source policy

Balanced recommendations plan half local and half cloud. For odd counts, the
extra slot is local. The backend dynamically reallocates a slot when one source
is unavailable or lacks relevant candidates. Do not issue extra searches merely
to force the original ratio after `recommend_music` returns.

## Present the result

1. Call `recommend_music` once with the resolved constraints.
   If it returns a retryable parameter error, correct the parameters and retry
   once; do not present the error response as a recommendation.
2. Use the exact returned `track_ids` in one `present_tracks` call.
3. Summarize the returned `source_plan.actual`, recommendation reasons and
   `user_notice`. Do not invent preference evidence or Wiki relationships.
4. Local cards may be played or added. Online cards require the user's DOWNLOAD
   action; recommendation never authorizes an automatic download.
5. `Hi-Res`, `无损`, `原唱` and similar wording in an online title is a source
   claim. Say “标题标注为……” unless separately verified.

If `cloud_errors` caused source reallocation, tell the user briefly that online
search was unavailable and that local songs were used instead. Recovered
internal details that did not change the result do not need to be exposed.

## Boundaries

- Exact song playback belongs to `local-search` and player tools.
- Plain local or B站 lookup belongs to `local-search` or `cloud-search`.
- Radio auto-fill belongs to `recommend_next` and remains local-only.
- LLM-Wiki contributes only `verified` or `inferred` relations. A
  `needs_review` entity is not an automatic recommendation fact.
- Never claim that a B站 uploader is the performer unless verified evidence
  says so.
