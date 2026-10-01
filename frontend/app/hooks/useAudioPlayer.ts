"use client";

import type { Track } from "@/app/lib/types";
import { apiUrl } from "@/app/lib/api";
import { useCallback, useEffect, useRef, useState } from "react";

export type AudioPlaybackEvent = {
  track: Track | null;
  currentTime: number;
  duration: number;
};

type LoadTrackOptions = {
  startAt?: number;
  autoplay?: boolean;
};

type AudioPlayerOptions = {
  onPlay?: (event: AudioPlaybackEvent) => void;
  onPause?: (event: AudioPlaybackEvent) => void;
  onEnded?: (event: AudioPlaybackEvent) => void;
};

export function useAudioPlayer(options?: AudioPlayerOptions) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const activeTrackRef = useRef<Track | null>(null);
  const loadGenerationRef = useRef(0);
  const optionsRef = useRef(options);
  useEffect(() => {
    optionsRef.current = options;
  }, [options]);

  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolumeState] = useState(0.8);

  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;

    const syncDuration = () => setDuration(Number.isFinite(el.duration) ? el.duration : 0);
    let lastProgressUpdate = 0;
    const syncProgress = () => {
      const now = performance.now();
      if (now - lastProgressUpdate < 1000) return; // throttle to once per second
      lastProgressUpdate = now;
      setProgress(Number.isFinite(el.currentTime) ? el.currentTime : 0);
    };
    const eventSnapshot = (): AudioPlaybackEvent => ({
      track: activeTrackRef.current,
      currentTime: Number.isFinite(el.currentTime) ? el.currentTime : 0,
      duration: Number.isFinite(el.duration) ? el.duration : 0,
    });
    const syncPlayFlags = () => {
      setPlaying(true);
      optionsRef.current?.onPlay?.(eventSnapshot());
    };
    const syncPauseFlags = () => {
      setPlaying(false);
      optionsRef.current?.onPause?.(eventSnapshot());
    };
    const syncEnded = () => {
      setPlaying(false);
      optionsRef.current?.onEnded?.(eventSnapshot());
    };
    const syncVol = () =>
      setVolumeState(Number.isFinite(el.volume) ? el.volume : 1);

    el.addEventListener("loadedmetadata", syncDuration);
    el.addEventListener("durationchange", syncDuration);
    el.addEventListener("timeupdate", syncProgress);
    el.addEventListener("play", syncPlayFlags);
    el.addEventListener("pause", syncPauseFlags);
    el.addEventListener("ended", syncEnded);
    el.addEventListener("volumechange", syncVol);

    setVolumeState(el.volume);
    syncDuration();
    syncProgress();

    return () => {
      el.removeEventListener("loadedmetadata", syncDuration);
      el.removeEventListener("durationchange", syncDuration);
      el.removeEventListener("timeupdate", syncProgress);
      el.removeEventListener("play", syncPlayFlags);
      el.removeEventListener("pause", syncPauseFlags);
      el.removeEventListener("ended", syncEnded);
      el.removeEventListener("volumechange", syncVol);
    };
  }, []);

  useEffect(() => {
    if (audioRef.current) audioRef.current.volume = volume;
  }, [volume]);

  const play = useCallback(async () => {
    const el = audioRef.current;
    if (!el) return;
    try {
      await el.play();
    } catch {
      /* autoplay blocked or no source */
    }
  }, []);

  const pause = useCallback(() => {
    audioRef.current?.pause();
  }, []);

  const toggle = useCallback(async () => {
    const el = audioRef.current;
    if (!el) return;
    if (el.paused) await play();
    else pause();
  }, [pause, play]);

  const seek = useCallback((t: number) => {
    const el = audioRef.current;
    if (!el || !Number.isFinite(t)) return;
    el.currentTime = Math.max(0, Math.min(t, el.duration || t));
    setProgress(el.currentTime);
  }, []);

  const setVolume = useCallback((n: number) => {
    const v = Math.max(0, Math.min(1, n));
    setVolumeState(v);
    if (audioRef.current) audioRef.current.volume = v;
  }, []);

  const playTrack = useCallback((track: Track, options: LoadTrackOptions = {}) => {
    const el = audioRef.current;
    if (!el) return;
    const generation = ++loadGenerationRef.current;
    const startAt = Math.max(0, options.startAt ?? 0);
    const autoplay = options.autoplay ?? true;
    activeTrackRef.current = track;
    el.src = apiUrl(track.url);
    el.load();
    setProgress(startAt);
    setDuration(0);
    const prepare = () => {
      if (
        loadGenerationRef.current !== generation ||
        activeTrackRef.current?.id !== track.id
      ) {
        return;
      }
      if (startAt > 0) {
        el.currentTime = Math.min(startAt, Number.isFinite(el.duration) ? el.duration : startAt);
        setProgress(el.currentTime);
      }
      if (autoplay) {
        void el.play().catch(() => {
          /* ignore */
        });
      }
    };
    if (el.readyState >= 1) prepare();
    else el.addEventListener("loadedmetadata", prepare, { once: true });
  }, []);

  return {
    audioRef,
    playing,
    progress,
    duration,
    volume,
    play,
    pause,
    toggle,
    seek,
    setVolume,
    playTrack,
  };
}
