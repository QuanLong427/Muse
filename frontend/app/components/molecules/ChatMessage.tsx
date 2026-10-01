"use client";

import type { CSSProperties } from "react";
import { useMemo, useState } from "react";
import type { Track, TrackCardData } from "@/app/lib/types";
import type { ChatMessage as ChatMessageModel } from "@/app/lib/types";
import { usePlayer } from "@/app/context/PlayerContext";
import { useAgent } from "@/app/context/AgentContext";
import { useDanmaku } from "@/app/context/DanmakuContext";
import { MarkdownContent } from "@/app/components/molecules/MarkdownContent";

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

function legacyTrackCard(track: TrackExt): TrackCardData {
  const local = Boolean(track.filename);
  return {
    track_id: local ? track.id : `bilibili:${track.bvid || track.id}`,
    source_type: local ? "local" : "bilibili",
    availability: local ? "local" : "remote",
    title: track.title,
    author: track.author,
    duration: track.duration,
    bvid: track.bvid,
    url: track.url,
    download_status: "idle",
    allowed_actions: local ? ["play", "add_to_queue"] : ["download"],
    local_track: local ? track : null,
  };
}

type ButtonState = "add" | "adding" | "added" | "downloaded";

function getButtonState(
  track: TrackExt,
  inPlaylist: Set<string>,
  convertingSet: Set<string>,
  convertedSet: Set<string>,
): ButtonState {
  if (inPlaylist.has(track.id) || (track.bvid && inPlaylist.has(track.bvid))) return "added";
  if (track.bvid && convertingSet.has(track.bvid)) return "adding";
  if (track.bvid && convertedSet.has(track.bvid)) return "downloaded";
  return "add";
}

function TrackCards({ tracks, cards }: { tracks?: TrackExt[]; cards?: TrackCardData[] }) {
  const { state, addTracks, playTrack } = usePlayer();
  const { queueConvert, convertingSet, convertedSet } = useAgent();
  const { fetchDanmaku } = useDanmaku();
  const inPlaylist = new Set([
    ...state.playlist.map((t) => t.id),
    ...state.playlist
      .map((t) => t.bvid)
      .filter((bvid): bvid is string => Boolean(bvid)),
  ]);

  // Deduplicate by bvid to avoid duplicate React keys
  const normalizedCards = useMemo(
    () => cards ?? (tracks ?? []).map(legacyTrackCard),
    [cards, tracks]
  );
  const uniqueTracks = useMemo(() => {
    const seen = new Set<string>();
    return normalizedCards.filter((t) => {
      const key = t.bvid || t.track_id;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [normalizedCards]);

  const handleDownload = (track: TrackCardData) => {
    if (!track.bvid) return;
    queueConvert([{ bvid: track.bvid, title: track.title, author: track.author }]);
    fetchDanmaku(track.bvid);
  };

  const handleQueue = (track: TrackCardData) => {
    if (!track.local_track) return;
    addTracks([track.local_track]);
    if (track.bvid) fetchDanmaku(track.bvid);
  };

  const handlePlay = (track: TrackCardData) => {
    if (!track.local_track) return;
    playTrack(track.local_track);
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
          const legacyForState = localTrack ?? ({ id: t.track_id, bvid: t.bvid } as TrackExt);
          const btnState = getButtonState(legacyForState, inPlaylist, convertingSet, convertedSet);
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
                {t.allowed_actions.includes("play") && (
                  <button onClick={() => handlePlay(t)} className="rounded-full border border-[rgba(129,140,248,0.3)] px-2.5 py-0.5 text-[10px] font-medium uppercase" style={{ color: "var(--color-primary)" }}>
                    ▶ PLAY
                  </button>
                )}
                {t.allowed_actions.includes("add_to_queue") && (
                  <button onClick={() => handleQueue(t)} disabled={btnState === "added"} className="rounded-full border border-[rgba(129,140,248,0.3)] px-2.5 py-0.5 text-[10px] font-medium uppercase disabled:opacity-40" style={{ color: "var(--color-primary)" }}>
                    {btnState === "added" ? "ADDED" : "+ ADD"}
                  </button>
                )}
                {t.allowed_actions.includes("download") && (
                  <button onClick={() => handleDownload(t)} disabled={btnState === "adding" || btnState === "downloaded"} className="rounded-full border border-[rgba(129,140,248,0.3)] px-2.5 py-0.5 text-[10px] font-medium uppercase disabled:opacity-40" style={{ color: "var(--color-primary)" }}>
                    {btnState === "adding" ? "DOWNLOADING..." : btnState === "downloaded" ? "DOWNLOADED" : "⇩ DOWNLOAD"}
                  </button>
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
