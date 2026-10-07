"use client";

import { useState } from "react";
import { ClockPanel } from "./ClockPanel";
import { DownloadsPanel } from "./DownloadsPanel";
import { LocalLibrary } from "./LocalLibrary";
import { MyPlaylists } from "./MyPlaylists";
import { PlaybackDetails } from "./PlaybackDetails";
import { RecentPlays } from "./RecentPlays";
import { SmartPlaylistPanel } from "./SmartPlaylistPanel";

type WorkspaceView = "library" | "recent" | "playlists" | "smart" | "downloads" | "playback";

const views: Array<{ id: WorkspaceView; label: string }> = [
  { id: "library", label: "本地曲库" },
  { id: "recent", label: "最近播放" },
  { id: "playlists", label: "我的歌单" },
  { id: "smart", label: "智能歌单" },
  { id: "downloads", label: "下载任务" },
  { id: "playback", label: "播放详情" },
];

export function MusicWorkspace() {
  const [view, setView] = useState<WorkspaceView>("playback");

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <nav aria-label="音乐库" className="grid shrink-0 grid-cols-6 overflow-hidden rounded-xl border border-[var(--glass-border)] bg-[rgba(255,255,255,0.04)]">
        {views.map((item) => (
          <button
            key={item.id}
            type="button"
            aria-current={view === item.id ? "page" : undefined}
            onClick={() => setView(item.id)}
            className={`border-r border-[var(--glass-border)] px-2 py-2.5 text-[11px] transition-colors last:border-r-0 ${view === item.id ? "bg-[rgba(129,140,248,0.16)] text-[var(--color-primary)]" : "text-[var(--color-on-surface-muted)] hover:bg-[rgba(255,255,255,0.04)]"}`}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div className="flex min-h-0 flex-1 flex-col gap-4">
        {view === "library" && <LocalLibrary />}
        {view === "recent" && <RecentPlays />}
        {view === "playlists" && <MyPlaylists />}
        {view === "smart" && <SmartPlaylistPanel />}
        {view === "downloads" && <DownloadsPanel />}
        {view === "playback" && (
          <>
            <ClockPanel />
            <PlaybackDetails />
          </>
        )}
      </div>
    </div>
  );
}
