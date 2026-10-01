"use client";

import type { PlaybackOrderMode, PlaybackRepeatMode } from "@/app/lib/types";

type Props = {
  orderMode: PlaybackOrderMode;
  repeatMode: PlaybackRepeatMode;
  onChange: (orderMode?: PlaybackOrderMode, repeatMode?: PlaybackRepeatMode) => void;
};

export function PlaybackModeControl({ orderMode, repeatMode, onChange }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-[10px] text-[var(--color-on-surface-muted)]">
      <label className="flex items-center gap-1.5">
        <span>播放顺序</span>
        <select
          aria-label="播放顺序"
          value={orderMode}
          onChange={(event) => onChange(event.target.value as PlaybackOrderMode)}
          className="rounded border border-[var(--glass-border)] bg-[var(--color-surface)] px-2 py-1 text-[var(--color-on-surface)] outline-none"
        >
          <option value="sequential">顺序播放</option>
          <option value="shuffle">随机播放</option>
          <option value="radio">随机推荐</option>
        </select>
      </label>
      <label className="flex items-center gap-1.5">
        <span>循环</span>
        <select
          aria-label="循环策略"
          value={repeatMode}
          onChange={(event) =>
            onChange(undefined, event.target.value as PlaybackRepeatMode)
          }
          className="rounded border border-[var(--glass-border)] bg-[var(--color-surface)] px-2 py-1 text-[var(--color-on-surface)] outline-none"
        >
          <option value="off">播完停止</option>
          <option value="all">列表循环</option>
          <option value="one">单曲循环</option>
        </select>
      </label>
    </div>
  );
}
