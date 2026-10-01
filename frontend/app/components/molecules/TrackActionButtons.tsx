"use client";

import { useState } from "react";
import { usePlayer } from "@/app/context/PlayerContext";
import { usePlaylists } from "@/app/context/PlaylistContext";
import type { Track } from "@/app/lib/types";

export function TrackActionButtons({ track }: { track: Track }) {
  const { state, playTrack, play, addTracks } = usePlayer();
  const { playlists, addTrack } = usePlaylists();
  const [playlistId, setPlaylistId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inSession = state.items.some(
    (item) =>
      item.track.id === track.id ||
      Boolean(track.bvid && item.track.bvid === track.bvid)
  );
  const isCurrent = Boolean(
    state.current?.id === track.id ||
      (track.bvid && state.current?.bvid === track.bvid)
  );

  const handlePlay = () => {
    if (isCurrent && !state.playing) void play();
    else if (!isCurrent) playTrack(track);
  };

  const handlePlaylist = async (nextId: string) => {
    setPlaylistId(nextId);
    if (!nextId) return;
    setBusy(true);
    setError("");
    try {
      await addTrack(nextId, track);
      setPlaylistId("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加入歌单失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center justify-end gap-1.5">
      <button
        type="button"
        disabled={isCurrent && state.playing}
        onClick={handlePlay}
        className="rounded-full border border-[rgba(129,140,248,0.3)] px-2.5 py-1 text-[10px] font-medium uppercase text-[var(--color-primary)] disabled:opacity-40"
      >
        {isCurrent && state.playing ? "PLAYING" : isCurrent ? "RESUME" : "PLAY"}
      </button>
      <button
        type="button"
        disabled={inSession}
        onClick={() => addTracks([track], "manual")}
        className="rounded-full border border-[var(--glass-border)] px-2.5 py-1 text-[10px] font-medium uppercase text-[var(--color-on-surface-muted)] disabled:opacity-40"
      >
        {inSession ? "ADDED" : "+ ADD"}
      </button>
      <label className="sr-only" htmlFor={`playlist-${encodeURIComponent(track.id)}`}>
        加入歌单
      </label>
      <select
        id={`playlist-${encodeURIComponent(track.id)}`}
        value={playlistId}
        disabled={busy || playlists.length === 0}
        onChange={(event) => void handlePlaylist(event.target.value)}
        title={error || (playlists.length ? "加入歌单" : "请先创建歌单")}
        className="max-w-28 rounded-full border border-[var(--glass-border)] bg-[var(--color-surface)] px-2 py-1 text-[10px] text-[var(--color-on-surface-muted)] disabled:opacity-40"
      >
        <option value="">{busy ? "ADDING…" : "PLAYLIST +"}</option>
        {playlists.map((playlist) => (
          <option key={playlist.id} value={playlist.id}>
            {playlist.name}
          </option>
        ))}
      </select>
    </div>
  );
}
