"use client";

import { useEffect, useState } from "react";
import { Label } from "@/app/components/atoms/Label";
import { TrackActionButtons } from "@/app/components/molecules/TrackActionButtons";
import { apiUrl } from "@/app/lib/api";
import type { RecentTrack } from "@/app/lib/types";

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function RecentPlays() {
  const [tracks, setTracks] = useState<RecentTrack[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(apiUrl("/api/playback-events/recent?user_id=local&limit=100"), {
      cache: "no-store",
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("无法读取最近播放");
        return (await response.json()) as { tracks?: RecentTrack[] };
      })
      .then((payload) => {
        if (!cancelled) {
          setTracks(payload.tracks ?? []);
          setError("");
        }
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "无法读取最近播放");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-[var(--glass-border)] bg-[rgba(255,255,255,0.04)]">
      <div className="flex items-center justify-between border-b border-[var(--glass-border)] px-4 py-3">
        <div className="flex items-baseline gap-2">
          <Label size="md">RECENTLY PLAYED</Label>
          <span className="text-[11px] text-[var(--color-on-surface-muted)]">[{tracks.length}]</span>
        </div>
        <button type="button" onClick={() => setReloadKey((value) => value + 1)} className="text-[10px] uppercase text-[var(--color-primary)]">REFRESH</button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {loading ? (
          <p className="p-4 text-sm text-[var(--color-on-surface-muted)]">正在读取播放记录…</p>
        ) : error ? (
          <p className="p-4 text-sm text-[var(--color-error)]">{error}</p>
        ) : tracks.length === 0 ? (
          <p className="p-4 text-sm text-[var(--color-on-surface-muted)]">播放一首本地歌曲后会出现在这里</p>
        ) : (
          tracks.map((entry) => (
            <div key={entry.track.id} className="flex flex-wrap items-center gap-3 border-b border-[var(--glass-border)] px-4 py-3 last:border-b-0">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{entry.track.title}</p>
                <p className="truncate text-xs text-[var(--color-on-surface-muted)]">
                  {entry.track.author || "未知歌手"} · {formatTime(entry.last_played_at)}
                </p>
              </div>
              <TrackActionButtons track={entry.track} />
            </div>
          ))
        )}
      </div>
    </section>
  );
}
