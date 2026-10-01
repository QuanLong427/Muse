"use client";

import { useEffect } from "react";
import { ControlBar } from "@/app/components/molecules/ControlBar";
import { SeekBar } from "@/app/components/molecules/SeekBar";
import { TrackInfo } from "@/app/components/molecules/TrackInfo";
import { VolumeControl } from "@/app/components/molecules/VolumeControl";
import { PlaybackModeControl } from "@/app/components/molecules/PlaybackModeControl";
import { TrackFeedbackControls } from "@/app/components/molecules/TrackFeedbackControls";
import { usePlayer } from "@/app/context/PlayerContext";

export function Player() {
  const {
    state,
    next,
    prev,
    togglePlay,
    stop,
    seek,
    setVolume,
    setPlaybackMode,
  } = usePlayer();

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || (e.target as HTMLElement)?.isContentEditable) return;
      if (e.code === "Space") {
        e.preventDefault();
        void togglePlay();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [togglePlay]);

  return (
    <div
      className="flex flex-col gap-5 rounded-xl border p-4 md:p-5 transition-all duration-200"
      style={{
        borderColor: "var(--glass-border)",
        backgroundColor: "rgba(255, 255, 255, 0.06)",
      }}
    >
      <TrackInfo track={state.current} playing={state.playing} />
      <div className="flex flex-wrap items-center justify-between gap-4">
        <ControlBar
          playing={state.playing}
          onPrev={prev}
          onToggle={togglePlay}
          onNext={next}
          onStop={stop}
        />
        <VolumeControl volume={state.volume} onChange={setVolume} />
      </div>
      <PlaybackModeControl
        orderMode={state.orderMode}
        repeatMode={state.repeatMode}
        onChange={setPlaybackMode}
      />
      <TrackFeedbackControls track={state.current} onNegative={next} />
      <SeekBar progress={state.progress} duration={state.duration} playing={state.playing} onSeek={seek} />
    </div>
  );
}
