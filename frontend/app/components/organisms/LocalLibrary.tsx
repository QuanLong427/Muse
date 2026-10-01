"use client";

import { useEffect, useMemo, useState } from "react";
import { Label } from "@/app/components/atoms/Label";
import { TrackActionButtons } from "@/app/components/molecules/TrackActionButtons";
import { apiUrl } from "@/app/lib/api";
import type { Track } from "@/app/lib/types";

export function LocalLibrary() {
  const [tracks, setTracks] = useState<Track[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(apiUrl("/api/search?limit=500"), { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("无法扫描本地曲库");
        return (await response.json()) as { tracks?: Track[] };
      })
      .then((payload) => {
        if (!cancelled) {
          setTracks(payload.tracks ?? []);
          setError("");
        }
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "无法扫描本地曲库");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return tracks;
    return tracks.filter((track) =>
      `${track.title} ${track.author} ${track.filename}`.toLowerCase().includes(needle)
    );
  }, [query, tracks]);

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-[var(--glass-border)] bg-[rgba(255,255,255,0.04)]">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--glass-border)] px-4 py-3">
        <div className="flex items-baseline gap-2">
          <Label size="md">LOCAL LIBRARY</Label>
          <span className="text-[11px] text-[var(--color-on-surface-muted)]">[{filtered.length}/{tracks.length}]</span>
        </div>
        <button type="button" onClick={() => setReloadKey((value) => value + 1)} className="text-[10px] uppercase text-[var(--color-primary)]">REFRESH</button>
      </div>
      <div className="border-b border-[var(--glass-border)] p-3">
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索歌曲、歌手或文件名…"
          className="w-full rounded-lg border border-[var(--glass-border)] bg-[rgba(255,255,255,0.04)] px-3 py-2 text-sm outline-none"
        />
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {loading ? (
          <p className="p-4 text-sm text-[var(--color-on-surface-muted)]">正在扫描本地曲库…</p>
        ) : error ? (
          <p className="p-4 text-sm text-[var(--color-error)]">{error}</p>
        ) : filtered.length === 0 ? (
          <p className="p-4 text-sm text-[var(--color-on-surface-muted)]">本地曲库中没有匹配歌曲</p>
        ) : (
          filtered.map((track) => (
            <div key={track.id} className="flex flex-wrap items-center gap-3 border-b border-[var(--glass-border)] px-4 py-3 last:border-b-0">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{track.title}</p>
                <p className="truncate text-xs text-[var(--color-on-surface-muted)]">{track.author || "未知歌手"}</p>
              </div>
              <TrackActionButtons track={track} />
            </div>
          ))
        )}
      </div>
    </section>
  );
}
