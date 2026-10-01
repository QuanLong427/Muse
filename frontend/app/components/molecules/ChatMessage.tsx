"use client";

import type { CSSProperties } from "react";
import { useEffect, useMemo, useState } from "react";
import type { Track, TrackCardData } from "@/app/lib/types";
import type { ChatMessage as ChatMessageModel } from "@/app/lib/types";
import { usePlayer } from "@/app/context/PlayerContext";
import { useAgent } from "@/app/context/AgentContext";
import { useDanmaku } from "@/app/context/DanmakuContext";
import { usePlaylists } from "@/app/context/PlaylistContext";
import { MarkdownContent } from "@/app/components/molecules/MarkdownContent";
import { apiUrl } from "@/app/lib/api";

type Props = { message: ChatMessageModel };

type ContentPart =
  | { type: "text"; text: string }
  | { type: "tracks"; tracks: Track[] };

const FENCED_RE = /```(?:tracks|json|added)?\s*\n([\s\S]*?)```/g;

function looksLikeTracks(arr: unknown[]): arr is Track[] {
  if (arr.length === 0) return false;
  const first = arr[0] as Record<string, unknown>;
  return typeof first === "object" && first !== null && "title" in first;
}

function tryParseTrackArray(raw: string): Track[] | null {
  try {
    const trimmed = raw.trim();
    const parsed = JSON.parse(trimmed);
    if (Array.isArray(parsed) && looksLikeTracks(parsed)) return parsed;
    if (parsed?.tracks && Array.isArray(parsed.tracks) && looksLikeTracks(parsed.tracks))
      return parsed.tracks;
  } catch { /* not valid JSON */ }
  return tryExtractTracksFromRaw(raw);
}

const OBJ_RE = /\{([^}]*)\}/g;

function tryExtractTracksFromRaw(raw: string): Track[] | null {
  const tracks: Track[] = [];
  OBJ_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = OBJ_RE.exec(raw)) !== null) {
    const obj = m[1];
    const bvid = obj.match(/"bvid"\s*:\s*"([^"]+)"/)?.[1];
    const id = obj.match(/"id"\s*:\s*"([^"]+)"/)?.[1];
    const title = obj.match(/"title"\s*:\s*"([\s\S]+?)"\s*,\s*"(?:author|duration|url|bvid|id)"/)?.[1];
    const author = obj.match(/"author"\s*:\s*"([\s\S]+?)"\s*,\s*"(?:duration|url|bvid)"/)?.[1];
    const duration = obj.match(/"duration"\s*:\s*"([^"]+)"/)?.[1];
    const url = obj.match(/"url"\s*:\s*"([^"]+)"/)?.[1];

    const trackId = bvid || id;
    if (trackId && title) {
      tracks.push({
        id: trackId,
        ...(bvid ? { bvid } : {}),
        title,
        author: author ?? "",
        ...(duration ? { duration } : {}),
        url: url ?? "",
        date: "",
        filename: "",
        subDir: "",
        size: 0,
      } as Track);
    }
  }
  OBJ_RE.lastIndex = 0;
  return tracks.length > 0 ? tracks : null;
}

function parseContent(content: string): ContentPart[] {
  const parts: ContentPart[] = [];
  let last = 0;

  FENCED_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = FENCED_RE.exec(content)) !== null) {
    const tracks = tryParseTrackArray(match[1]);
    if (match.index > last) {
      parts.push({ type: "text", text: content.slice(last, match.index) });
    }
    if (tracks) {
      parts.push({ type: "tracks", tracks });
    } else {
      // Preserve non-Track code fences so the Markdown renderer can display
      // ordinary JSON and code blocks correctly.
      parts.push({ type: "text", text: match[0] });
    }
    last = match.index + match[0].length;
  }

  if (last < content.length) {
    const remainder = content.slice(last);
    const bare = remainder.match(/(\[[\s\n]*\{[\s\S]*?\}[\s\n]*\])/);
    if (bare) {
      const tracks = tryParseTrackArray(bare[1]);
      if (tracks) {
        const idx = remainder.indexOf(bare[1]);
        if (idx > 0) parts.push({ type: "text", text: remainder.slice(0, idx) });
        parts.push({ type: "tracks", tracks });
        const end = idx + bare[1].length;
        if (end < remainder.length) parts.push({ type: "text", text: remainder.slice(end) });
        return parts;
      }
    }
    parts.push({ type: "text", text: remainder });
  }
  return parts;
}

type TrackExt = Track & { bvid?: string; duration?: string };

const LOCAL_TRACK_ID_RE = /(?:^|[/\\])[^/\\]+\.(?:mp3|flac|wav|m4a|aac|ogg|opus)$/i;

function legacyTrackCard(track: TrackExt): TrackCardData {
  const completeLocal = Boolean(track.filename && track.url);
  const localHint = completeLocal || LOCAL_TRACK_ID_RE.test(track.id);
  return {
    track_id: localHint ? track.id : `bilibili:${track.bvid || track.id}`,
    source_type: localHint ? "local" : "bilibili",
    availability: completeLocal ? "local" : localHint ? "failed" : "remote",
    title: track.title,
    author: track.author,
    duration: track.duration,
    bvid: track.bvid,
    url: track.url,
    download_status: completeLocal ? "downloaded" : "idle",
    allowed_actions: completeLocal ? ["play", "add_to_session"] : localHint ? [] : ["download"],
    local_track: completeLocal ? track : null,
  };
}

function TrackCards({ tracks, cards }: { tracks?: TrackExt[]; cards?: TrackCardData[] }) {
  const { state, addTracks, playTrack, play } = usePlayer();
  const { queueConvert, convertingSet, convertedSet } = useAgent();
  const { fetchDanmaku } = useDanmaku();
  const { playlists, addTrack: addTrackToPlaylist } = usePlaylists();
  const inSession = new Set([
    ...state.items.map((item) => item.track.id),
    ...state.items
      .map((item) => item.track.bvid)
      .filter((bvid): bvid is string => Boolean(bvid)),
  ]);

  // Deduplicate by bvid to avoid duplicate React keys
  const normalizedCards = useMemo(
    () => cards ?? (tracks ?? []).map(legacyTrackCard),
    [cards, tracks]
  );
  const [resolvedLocal, setResolvedLocal] = useState<Record<string, Track>>({});
  const [resolutionComplete, setResolutionComplete] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    const unresolved = normalizedCards.filter(
      (card) =>
        !card.local_track &&
        (card.source_type === "local" ||
          Boolean(
            card.bvid &&
              (convertedSet.has(card.bvid) || card.download_status === "downloaded")
          ))
    );
    for (const card of unresolved) {
      if (resolvedLocal[card.track_id] || resolutionComplete.has(card.track_id)) continue;
      const lookupPath =
        card.source_type === "local"
          ? `/api/tracks/by-id?track_id=${encodeURIComponent(card.track_id)}`
          : `/api/tracks/by-bvid?bvid=${encodeURIComponent(card.bvid ?? "")}`;
      fetch(apiUrl(lookupPath), {
        cache: "no-store",
      })
        .then(async (response) => {
          if (!response.ok) return null;
          return (await response.json()) as Track;
        })
        .then((track) => {
          if (!cancelled && track) {
            setResolvedLocal((previous) => ({ ...previous, [card.track_id]: track }));
          }
        })
        .catch(() => null)
        .finally(() => {
          if (!cancelled) {
            setResolutionComplete((previous) => {
              const next = new Set(previous);
              next.add(card.track_id);
              return next;
            });
          }
        });
    }
    return () => {
      cancelled = true;
    };
  }, [convertedSet, normalizedCards, resolutionComplete, resolvedLocal]);

  const effectiveCards = useMemo(
    () =>
      normalizedCards.map((card) => {
        const localTrack = resolvedLocal[card.track_id];
        if (!localTrack) return card;
        return {
          ...card,
          source_type: "local" as const,
          availability: "local" as const,
          title: localTrack.title,
          author: localTrack.author,
          bvid: localTrack.bvid,
          url: localTrack.url,
          download_status: "downloaded" as const,
          allowed_actions: ["play", "add_to_session"] as TrackCardData["allowed_actions"],
          local_track: localTrack,
        };
      }),
    [normalizedCards, resolvedLocal]
  );
  const uniqueTracks = useMemo(() => {
    const seen = new Set<string>();
    return effectiveCards.filter((t) => {
      const key = t.bvid || t.track_id;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [effectiveCards]);

  const handleDownload = (track: TrackCardData) => {
    if (!track.bvid) return;
    queueConvert([{ bvid: track.bvid, title: track.title, author: track.author }]);
    fetchDanmaku(track.bvid);
  };

  const handleAdd = (track: TrackCardData) => {
    if (!track.local_track) return;
    addTracks([track.local_track], "agent");
    if (track.bvid) fetchDanmaku(track.bvid);
  };

  const handlePlay = (track: TrackCardData) => {
    if (!track.local_track) return;
    const isCurrent =
      state.current?.id === track.local_track.id ||
      Boolean(track.bvid && state.current?.bvid === track.bvid);
    if (isCurrent && !state.playing) void play();
    else if (!isCurrent) playTrack(track.local_track);
    if (track.bvid) fetchDanmaku(track.bvid);
  };

  return (
    <div
      className="my-2 overflow-hidden rounded-xl border border-[var(--glass-border)] bg-[rgba(255,255,255,0.04)]"
    >
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-[var(--glass-border)]">
        <span className="text-[11px] font-medium uppercase tracking-wider text-[color:var(--color-on-surface-muted)]">
          [{uniqueTracks.length} TRACKS]
        </span>
      </div>
      <div className="max-h-[16rem] overflow-y-auto">
        {uniqueTracks.map((t) => {
          const localTrack = t.local_track;
          const inCurrentSession = Boolean(
            (localTrack && inSession.has(localTrack.id)) ||
              (t.bvid && inSession.has(t.bvid))
          );
          const isCurrent = Boolean(
            localTrack &&
              (state.current?.id === localTrack.id ||
                Boolean(t.bvid && state.current?.bvid === t.bvid))
          );
          const isDownloading = Boolean(t.bvid && convertingSet.has(t.bvid));
          const wasDownloaded = Boolean(
            t.download_status === "downloaded" ||
              (t.bvid && convertedSet.has(t.bvid))
          );
          const isLocal = t.availability === "local" && Boolean(localTrack);
          const isResolving =
            t.source_type === "local" &&
            !localTrack &&
            !resolutionComplete.has(t.track_id);
          return (
            <div
              key={t.bvid || t.track_id}
              className="flex items-center gap-2 border-b border-[var(--glass-border)] last:border-b-0 px-3 py-2"
            >
              <div className="min-w-0 flex-1">
                <p className="m-0 truncate text-sm">
                  {t.bvid ? (
                    <a
                      href={`https://www.bilibili.com/video/${t.bvid}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="transition-colors duration-200 hover:underline"
                      style={{ color: "var(--color-primary)" }}
                      title={t.title}
                    >
                      {t.title}
                    </a>
                  ) : (
                    <span style={{ color: "var(--color-on-surface)" }}>{t.title}</span>
                  )}
                </p>
                <p className="m-0 truncate text-xs text-[color:var(--color-on-surface-muted)]">
                  {t.author}
                  {t.duration && <span className="ml-2 opacity-70">{t.duration}</span>}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                {isLocal && (
                  <button disabled={isCurrent && state.playing} onClick={() => handlePlay(t)} className="rounded-full border border-[rgba(129,140,248,0.3)] px-2.5 py-0.5 text-[10px] font-medium uppercase disabled:opacity-40" style={{ color: "var(--color-primary)" }}>
                    {isCurrent && state.playing ? "PLAYING" : isCurrent ? "▶ RESUME" : "▶ PLAY"}
                  </button>
                )}
                {isLocal && (
                  <button title="加入接下来播放" onClick={() => handleAdd(t)} disabled={inCurrentSession} className="rounded-full border border-[rgba(129,140,248,0.3)] px-2.5 py-0.5 text-[10px] font-medium uppercase disabled:opacity-40" style={{ color: "var(--color-primary)" }}>
                    {inCurrentSession ? "ADDED" : "+ ADD"}
                  </button>
                )}
                {isLocal && localTrack && (
                  <select
                    defaultValue=""
                    disabled={playlists.length === 0}
                    title={playlists.length ? "加入命名歌单" : "请先创建歌单"}
                    onChange={(event) => {
                      const targetPlaylistId = event.target.value;
                      event.target.value = "";
                      if (targetPlaylistId) void addTrackToPlaylist(targetPlaylistId, localTrack);
                    }}
                    className="max-w-24 rounded-full border border-[var(--glass-border)] bg-[var(--color-surface)] px-2 py-0.5 text-[10px] text-[var(--color-on-surface-muted)] disabled:opacity-40"
                  >
                    <option value="">PLAYLIST +</option>
                    {playlists.map((playlist) => (
                      <option key={playlist.id} value={playlist.id}>{playlist.name}</option>
                    ))}
                  </select>
                )}
                {!isLocal && t.source_type === "bilibili" && t.bvid && (
                  <button onClick={() => handleDownload(t)} disabled={isDownloading || wasDownloaded} className="rounded-full border border-[rgba(129,140,248,0.3)] px-2.5 py-0.5 text-[10px] font-medium uppercase disabled:opacity-40" style={{ color: "var(--color-primary)" }}>
                    {isDownloading ? "DOWNLOADING..." : wasDownloaded ? "DOWNLOADED" : "⇩ DOWNLOAD"}
                  </button>
                )}
                {!isLocal && t.source_type === "local" && (
                  <span className="px-2 py-0.5 text-[10px] uppercase text-[color:var(--color-on-surface-muted)]">
                    {isResolving ? "VERIFYING…" : "UNAVAILABLE"}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function labelFor(role: ChatMessageModel["role"]) {
  if (role === "agent") return "AGENT";
  if (role === "operator") return "YOU";
  if (role === "tool") return "TOOL";
  return "SYS";
}

function formatTs(ts: number) {
  try {
    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(ts);
  } catch {
    return String(ts);
  }
}

function ToolMessage({ message: m }: Props) {
  const [open, setOpen] = useState(false);
  const firstLine = m.content.split("\n")[0] ?? "";
  const rest = m.content.slice(firstLine.length + 1);
  const toolLabel = m.toolName || firstLine.split(/\s/)[0] || "Tool";

  return (
    <article className="mb-2 flex w-full justify-start animate-fade-in">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex max-w-[min(100%,38rem)] cursor-pointer items-start gap-1.5 rounded-xl border-l-2 border-l-[var(--color-secondary)] py-1 pl-4 pr-4 text-left transition-all duration-200 hover:bg-[rgba(255,255,255,0.03)]"
        style={{ opacity: open ? 0.85 : 0.55 }}
      >
        <span className="mt-px shrink-0 text-[10px]" style={{ color: "var(--color-on-surface-muted)" }}>
          {open ? "▾" : "▸"}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline min-w-0">
            <span
              className="shrink-0 text-[10px] font-medium uppercase tracking-wider"
              style={{ color: "var(--color-secondary)" }}
            >
              [{toolLabel}]
            </span>
            {!open && rest && (
              <span className="ml-1.5 truncate text-[11px] text-[color:var(--color-on-surface-muted)]">
                {rest.slice(0, 80)}
              </span>
            )}
          </div>
          {open && rest && (
            <pre className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-[color:var(--color-on-surface-muted)]">
              {rest}
            </pre>
          )}
        </div>
      </button>
    </article>
  );
}

export function ChatMessage({ message: m }: Props) {
  if (m.role === "tool") return <ToolMessage message={m} />;

  const isOp = m.role === "operator";
  const label = labelFor(m.role);

  const parts = m.role === "agent" && m.content ? parseContent(m.content) : null;

  const wrapperClass = isOp
    ? "justify-end"
    : "justify-start";

  const bubbleStyle: CSSProperties = isOp
    ? {
        backgroundColor: "rgba(129, 140, 248, 0.12)",
        border: "1px solid rgba(129, 140, 248, 0.2)",
        borderRadius: "16px 16px 4px 16px",
      }
    : {
        backgroundColor: "rgba(255, 255, 255, 0.05)",
        border: "1px solid var(--glass-border)",
        borderRadius: "16px 16px 16px 4px",
      };

  return (
    <article className={`mb-4 flex w-full animate-fade-in ${wrapperClass}`}>
      <div
        className="max-w-[min(100%,38rem)] overflow-hidden px-4 py-3"
        style={bubbleStyle}
      >
        <div
          className={`mb-2 flex flex-wrap items-baseline gap-2 ${isOp ? "justify-end" : ""}`}
        >
          <span className="text-[11px] font-medium uppercase tracking-wider text-[color:var(--color-on-surface-muted)]">
            {labelFor(m.role)}
          </span>
          <span className="text-[10px] text-[color:var(--color-on-surface-muted)] opacity-60">{formatTs(m.timestamp)}</span>
        </div>
        {m.trackCards?.length ? <TrackCards cards={m.trackCards} /> : null}
        {parts ? (
          <div className={isOp ? "text-right" : "text-left"}>
            {parts.map((part, i) => {
              if (part.type === "tracks") return <TrackCards key={i} tracks={part.tracks} />;
              return <MarkdownContent key={i} content={part.text} />;
            })}
          </div>
        ) : m.content ? (
          <pre
            className={`m-0 whitespace-pre-wrap break-words text-sm leading-relaxed ${isOp ? "text-right" : "text-left"}`}
            style={{ fontFamily: "var(--font-body)", color: "var(--color-on-surface)" }}
          >
            {m.content}
          </pre>
        ) : null}
      </div>
    </article>
  );
}
