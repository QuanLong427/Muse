"use client";

import type { Track, PlayerState } from "@/app/lib/types";
import { useAudioPlayer } from "@/app/hooks/useAudioPlayer";
import { apiUrl } from "@/app/lib/api";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

type PlayerCtx = {
  state: PlayerState;
  playTrack: (track: Track, playlist?: Track[]) => void;
  addTracks: (tracks: Track[]) => void;
  removeTrack: (trackId: string) => void;
  play: () => void | Promise<void>;
  pause: () => void;
  next: () => void;
  prev: () => void;
  togglePlay: () => void | Promise<void>;
  seek: (n: number) => void;
  setVolume: (n: number) => void;
  stop: () => void;
  audioRef: React.RefObject<HTMLAudioElement | null>;
  trackRemoved: boolean;
  clearTrackRemoved: () => void;
};

const PlayerContext = createContext<PlayerCtx | null>(null);

export function PlayerProvider({ children }: { children: ReactNode }) {
  const [playlist, setPlaylist] = useState<Track[]>([]);
  const [index, setIndex] = useState(-1);

  const playlistRef = useRef<Track[]>([]);
  const indexRef = useRef(-1);
  const playTrackInternalRef = useRef<(track: Track) => void>(() => {});
  const shouldPauseRef = useRef(false);
  const [trackRemoved, setTrackRemoved] = useState(false);

  // ── Playlist persistence ──────────────────────────────────────────────
  const syncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const syncToBackend = useCallback((tracks: Track[]) => {
    if (syncTimerRef.current) clearTimeout(syncTimerRef.current);
    syncTimerRef.current = setTimeout(() => {
      fetch(apiUrl("/api/playlist"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tracks }),
      }).catch(() => {});
    }, 500);
  }, []);

  // Load playlist from backend on mount
  useEffect(() => {
    fetch(apiUrl("/api/playlist"))
      .then((r) => r.json())
      .then((data) => {
        if (data.tracks && Array.isArray(data.tracks) && data.tracks.length > 0) {
          setPlaylist(data.tracks);
          playlistRef.current = data.tracks;
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    playlistRef.current = playlist;
  }, [playlist]);

  useEffect(() => {
    indexRef.current = index;
  }, [index]);

  const handleEnded = useCallback(() => {
    const pl = playlistRef.current;
    if (!pl.length) return;
    const ni = (indexRef.current + 1) % pl.length;
    const t = pl[ni];
    setIndex(ni);
    indexRef.current = ni;
    if (t) playTrackInternalRef.current(t);
  }, []);

  const {
    audioRef,
    playing,
    progress,
    duration,
    volume,
    toggle,
    seek,
    setVolume,
    play,
    playTrack,
    pause,
  } = useAudioPlayer({ onEnded: handleEnded });

  useEffect(() => {
    if (shouldPauseRef.current) {
      shouldPauseRef.current = false;
      pause();
    }
  }, [pause]);

  useEffect(() => {
    playTrackInternalRef.current = playTrack;
  }, [playTrack]);

  const current =
    index >= 0 && index < playlist.length ? playlist[index] ?? null : null;

  const addTracks = useCallback((tracks: Track[]) => {
    setPlaylist((prev) => {
      const ids = new Set(prev.map((t) => t.id));
      const bvids = new Set(prev.map((t) => t.bvid).filter(Boolean));
      const fresh = tracks.filter((t) => !ids.has(t.id) && !(t.bvid && bvids.has(t.bvid)));
      if (!fresh.length) return prev;
      const next = [...prev, ...fresh];
      playlistRef.current = next;
      syncToBackend(next);
      return next;
    });

    if (indexRef.current < 0) {
      const cur = playlistRef.current;
      const first = cur[0];
      if (first) {
        setIndex(0);
        indexRef.current = 0;
        playTrack(first);
      }
    }
  }, [playTrack, syncToBackend]);

  const removeTrack = useCallback(
    (trackId: string) => {
      const curIdx = indexRef.current;
      let newIndex = curIdx;
      let shouldPlayNext = false;
      let nextTrack: Track | undefined;

      const nextPlaylist = playlistRef.current.filter((t) => t.id !== trackId);
      if (nextPlaylist.length === playlistRef.current.length) return; // track not found

      const rmIdx = playlistRef.current.findIndex((t) => t.id === trackId);
      playlistRef.current = nextPlaylist;
      syncToBackend(nextPlaylist);

      if (rmIdx === curIdx) {
        // removing the currently playing track
        setTrackRemoved(true);
        if (nextPlaylist.length === 0) {
          newIndex = -1;
          // Directly reset audio element
          const el = audioRef.current;
          if (el) {
            el.pause();
            el.removeAttribute("src");
            el.load();
          }
        } else if (!playing) {
          // paused — just move index, don't auto-play
          newIndex = Math.min(rmIdx, nextPlaylist.length - 1);
          shouldPauseRef.current = true;
        } else {
          newIndex = Math.min(rmIdx, nextPlaylist.length - 1);
          shouldPlayNext = true;
          nextTrack = nextPlaylist[newIndex];
        }
      } else if (rmIdx < curIdx) {
        // removed a track before current — shift index back
        newIndex = curIdx - 1;
      }
      // rmIdx > curIdx: index unchanged

      indexRef.current = newIndex;
      setPlaylist(nextPlaylist);
      setIndex(newIndex);
      if (shouldPlayNext && nextTrack) playTrack(nextTrack);
    },
    [audioRef, playTrack, playing, syncToBackend]
  );

  const playTrackWrapped = useCallback(
    (track: Track, pl?: Track[]) => {
      if (pl?.length) {
        const nextPl = [...pl];
        const i = Math.max(nextPl.findIndex((t) => t.id === track.id), 0);
        setPlaylist(nextPl);
        playlistRef.current = nextPl;
        syncToBackend(nextPl);
        setIndex(i);
        indexRef.current = i;
        playTrack(track);
      } else {
        const cur = playlistRef.current;
        const i = cur.findIndex((t) => t.id === track.id);
        if (i >= 0) {
          setIndex(i);
          indexRef.current = i;
          playTrack(track);
        } else {
          const single = [track];
          setPlaylist(single);
          playlistRef.current = single;
          setIndex(0);
          indexRef.current = 0;
          playTrack(track);
        }
      }
    },
    [playTrack, syncToBackend]
  );

  const next = useCallback(() => {
    const i = indexRef.current;
    const pl = playlistRef.current;
    if (!pl.length) return;
    const ni = Math.min(pl.length - 1, Math.max(i + 1, 0));
    if (ni === i && i >= 0) return;
    setIndex(ni);
    indexRef.current = ni;
    const t = pl[ni];
    if (t) playTrack(t);
  }, [playTrack]);

  const prev = useCallback(() => {
    const i = indexRef.current;
    const pl = playlistRef.current;
    if (!pl.length || i <= 0) return;
    const ni = Math.max(0, i - 1);
    setIndex(ni);
    indexRef.current = ni;
    const t = pl[ni];
    if (t) playTrack(t);
  }, [playTrack]);

  const togglePlayWrapped = useCallback(() => {
    if (!playlistRef.current.length) return;
    if (indexRef.current < 0 || !playlistRef.current[indexRef.current]) {
      const first = playlistRef.current[0];
      if (first) {
        setIndex(0);
        indexRef.current = 0;
        playTrack(first);
        return;
      }
    }
    return toggle();
  }, [toggle, playTrack]);

  const stop = useCallback(() => {
    pause();
    seek(0);
  }, [pause, seek]);

  const playWrapped = useCallback(() => {
    const pl = playlistRef.current;
    if (!pl.length) return;
    if (indexRef.current < 0 || !pl[indexRef.current]) {
      const first = pl[0];
      if (first) {
        setIndex(0);
        indexRef.current = 0;
        playTrack(first);
      }
      return;
    }
    return play();
  }, [play, playTrack]);

  const clearTrackRemoved = useCallback(() => {
    setTrackRemoved(false);
  }, []);

  const state: PlayerState = useMemo(
    () => ({
      current,
      playlist,
      index: index < 0 ? 0 : index,
      playing,
      progress,
      duration,
      volume,
    }),
    [current, playlist, index, playing, progress, duration, volume]
  );

  const ctx: PlayerCtx = useMemo(
    () => ({
      state,
      playTrack: playTrackWrapped,
      addTracks,
      removeTrack,
      play: playWrapped,
      pause,
      next,
      prev,
      togglePlay: togglePlayWrapped,
      seek,
      setVolume,
      stop,
      audioRef,
      trackRemoved,
      clearTrackRemoved,
    }),
    [state, playTrackWrapped, addTracks, removeTrack, playWrapped, pause, next, prev, togglePlayWrapped, seek, setVolume, stop, audioRef, trackRemoved, clearTrackRemoved]
  );

  return (
    <PlayerContext.Provider value={ctx}>
      <audio ref={audioRef} className="hidden" preload="metadata" aria-hidden />
      {children}
    </PlayerContext.Provider>
  );
}

export function usePlayer() {
  const v = useContext(PlayerContext);
  if (!v) throw new Error("usePlayer must be used within PlayerProvider");
  return v;
}
