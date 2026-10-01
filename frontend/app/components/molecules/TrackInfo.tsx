"use client";

import { useEffect, useRef, useState } from "react";
import type { Track } from "@/app/lib/types";
import { DanmakuToggle, SpectrumBars } from "@/app/components/atoms";

const MARQUEE_KF =
  "@keyframes pcb-mq{0%{transform:translateX(0)}100%{transform:translateX(calc(var(--mq-offset)*-1))}}";
const MQ_GAP = 64;
const MQ_SPEED = 40;

type Props = {
  track: Track | null;
  playing: boolean;
};

export function TrackInfo({ track, playing }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLSpanElement>(null);
  const [textWidth, setTextWidth] = useState(0);

  useEffect(() => {
    const container = containerRef.current;
    const text = textRef.current;
    if (!container || !text) {
      setTextWidth(0);
      return;
    }

    const check = () => {
      setTextWidth(text.scrollWidth > container.clientWidth ? text.scrollWidth : 0);
    };

    check();
    const ro = new ResizeObserver(check);
    ro.observe(container);
    return () => ro.disconnect();
  }, [track?.id, track?.title]);

  const hasTrack = !!track;
  const playbackLabel = !hasTrack ? "IDLE" : playing ? "PLAYING" : "PAUSED";
  const needsMarquee = textWidth > 0;
  const offset = textWidth + MQ_GAP;

  return (
    <div className="flex flex-col gap-3">
      {needsMarquee && <style>{MARQUEE_KF}</style>}
      <div className="flex items-baseline gap-3">
        <div ref={containerRef} className="min-w-0 flex-1 overflow-hidden">
          <div
            className="inline-flex whitespace-nowrap"
            style={
              needsMarquee
                ? ({
                    animation: `pcb-mq ${offset / MQ_SPEED}s linear infinite`,
                    "--mq-offset": `${offset}px`,
                  } as React.CSSProperties)
                : undefined
            }
          >
            <span
              ref={textRef}
              className={
                hasTrack
                  ? "text-lg font-semibold tracking-tight md:text-xl"
                  : "text-sm text-[color:var(--color-on-surface-muted)]"
              }
              style={
                hasTrack
                  ? { fontFamily: "var(--font-headline)", color: "var(--color-on-surface)" }
                  : { fontFamily: "var(--font-headline)" }
              }
            >
              {hasTrack ? track.title : "NO SIGNAL"}
            </span>
            {needsMarquee && (
              <span
                className="pl-16 text-lg font-semibold tracking-tight md:text-xl"
                style={{ fontFamily: "var(--font-headline)", color: "var(--color-on-surface)" }}
              >
                {track!.title}
              </span>
            )}
          </div>
        </div>
        {hasTrack && (
          <span className="shrink-0 min-w-28 text-sm text-right text-[color:var(--color-on-surface-muted)]" style={{ fontFamily: "var(--font-body)" }}>
            {track.author}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2.5">
        <span
          className="w-fit rounded-full border px-3 py-0.5 text-[10px] font-medium uppercase tracking-wider"
          style={{
            borderColor: hasTrack ? "rgba(129,140,248,0.4)" : "var(--glass-border)",
            color: hasTrack ? "var(--color-primary)" : "var(--color-on-surface-muted)",
            backgroundColor: hasTrack ? "rgba(129,140,248,0.1)" : "rgba(255,255,255,0.04)",
            boxShadow: hasTrack ? "0 0 16px rgba(129,140,248,0.15)" : "none",
            opacity: hasTrack ? 1 : 0.5,
          }}
        >
          {playbackLabel}
        </span>
        <SpectrumBars active={playing} muted={!hasTrack} />
        <DanmakuToggle />
      </div>
    </div>
  );
}
