"use client";

import { Label } from "@/app/components/atoms/Label";
import type { PlaybackSessionItem, Track } from "@/app/lib/types";
import { usePlayer } from "@/app/context/PlayerContext";
import { apiUrl } from "@/app/lib/api";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

function fmtSec(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return `${minutes}:${remainder.toString().padStart(2, "0")}`;
}

function useDurationMap(tracks: Track[]) {
  const [durations, setDurations] = useState<Record<string, number>>({});
  const pending = useRef(new Set<string>());

  useEffect(() => {
    for (const track of tracks) {
      if (durations[track.id] != null || pending.current.has(track.id)) continue;
      pending.current.add(track.id);
      const audio = new Audio();
      audio.preload = "metadata";
      audio.addEventListener(
        "loadedmetadata",
        () => {
          if (Number.isFinite(audio.duration) && audio.duration > 0) {
            setDurations((previous) => ({ ...previous, [track.id]: audio.duration }));
          }
          pending.current.delete(track.id);
          audio.src = "";
        },
        { once: true }
      );
      audio.addEventListener(
        "error",
        () => {
          pending.current.delete(track.id);
          audio.src = "";
        },
        { once: true }
      );
      audio.src = apiUrl(track.url);
    }
  }, [durations, tracks]);
  return durations;
}

export function UpNext() {
  const { state, playTrack, removeSessionItem } = usePlayer();
  const [filter, setFilter] = useState("");
  const currentIndex = state.items.findIndex((item) => item.id === state.currentItemId);
  const upcoming = useMemo(
    () => (currentIndex >= 0 ? state.items.slice(currentIndex + 1) : state.items),
    [currentIndex, state.items]
  );
  const upcomingTracks = useMemo(
    () => upcoming.map((item) => item.track),
    [upcoming]
  );
  const durations = useDurationMap(upcomingTracks);
  const query = filter.trim().toLowerCase();
  const rows = useMemo(
    () =>
      query
        ? upcoming.filter((item) => {
            const track = item.track;
            return `${track.title} ${track.author} ${track.filename}`
              .toLowerCase()
              .includes(query);
          })
        : upcoming,
    [query, upcoming]
  );

  const playItem = useCallback(
    (item: PlaybackSessionItem) => playTrack(item.track),
    [playTrack]
  );

  return (
    <section
      aria-label="接下来播放"
      className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border transition-all duration-200"
      style={{
        borderColor: "var(--glass-border)",
        backgroundColor: "rgba(255, 255, 255, 0.04)",
        backdropFilter: "blur(16px)",
        WebkitBackdropFilter: "blur(16px)",
      }}
    >
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-[var(--glass-border)] px-3 py-2.5 md:px-4">
        <div className="flex flex-wrap items-baseline gap-2">
          <Label size="md">UP NEXT</Label>
          <span className="text-[11px] tabular-nums text-[color:var(--color-on-surface-muted)] opacity-70">
            [{rows.length}/{upcoming.length}]
          </span>
        </div>
      </div>

      <div className="shrink-0 px-3 py-2 md:px-4">
        <label className="sr-only" htmlFor="up-next-search">
          搜索接下来播放
        </label>
        <input
          id="up-next-search"
          type="search"
          placeholder="Search up next…"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          className="w-full rounded-lg border border-[var(--glass-border)] bg-[rgba(255,255,255,0.04)] px-3 py-2 text-sm outline-none transition-all duration-200 placeholder:text-[color:var(--color-on-surface-muted)] focus:border-[rgba(129,140,248,0.3)] focus:bg-[rgba(255,255,255,0.06)]"
          style={{ color: "var(--color-on-surface)" }}
        />
      </div>

      {upcoming.length === 0 ? (
        <div className="flex flex-1 items-center justify-center px-4 py-6">
          <p className="text-center text-xs text-[color:var(--color-on-surface-muted)] opacity-60">
            暂无接下来播放的歌曲
          </p>
        </div>
      ) : rows.length === 0 ? (
        <p className="px-3 py-4 text-sm text-[color:var(--color-on-surface-muted)] opacity-60 md:px-4">
          没有匹配的歌曲
        </p>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto scrollbar-thin">
          <table className="w-full border-collapse text-left text-sm">
            <thead className="sticky top-0 z-[1]">
              <tr style={{ borderBottom: "1px solid var(--glass-border)" }}>
                <th className="w-10 px-2 py-2.5 text-[10px] font-medium uppercase tracking-wider text-[color:var(--color-on-surface-muted)]">#</th>
                <th className="px-2 py-2.5 text-[10px] font-medium uppercase tracking-wider text-[color:var(--color-on-surface-muted)]">TITLE</th>
                <th className="w-20 px-2 py-2.5 text-right text-[10px] font-medium uppercase tracking-wider text-[color:var(--color-on-surface-muted)]">DUR</th>
                <th className="w-10 px-1 py-2.5"><span className="sr-only">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item, index) => (
                <tr
                  key={item.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => playItem(item)}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter" && event.key !== " ") return;
                    event.preventDefault();
                    playItem(item);
                  }}
                  className="group relative cursor-pointer transition-all duration-200 hover:bg-[rgba(255,255,255,0.04)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--color-primary)] focus-visible:ring-inset"
                >
                  <td className="w-10 px-2 py-2.5 tabular-nums text-[color:var(--color-on-surface-muted)]">{index + 1}</td>
                  <td className="max-w-0 truncate px-2 py-2.5 text-[color:var(--color-on-surface)]">{item.track.title}</td>
                  <td className="w-20 px-2 py-2.5 text-right tabular-nums text-[color:var(--color-on-surface-muted)]">{fmtSec(durations[item.track.id])}</td>
                  <td className="w-10 px-1 py-2.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                    <button
                      type="button"
                      aria-label={`从接下来播放中移除 ${item.track.title}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        removeSessionItem(item.id);
                      }}
                      className="flex h-7 w-7 items-center justify-center rounded-full border border-[var(--glass-border)] bg-transparent text-[color:var(--color-on-surface-muted)] transition-all hover:border-[var(--color-error)] hover:bg-[rgba(251,113,133,0.1)] hover:text-[var(--color-error)]"
                    >
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                        <line x1="18" y1="6" x2="6" y2="18" />
                        <line x1="6" y1="6" x2="18" y2="18" />
                      </svg>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
