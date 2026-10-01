"use client";

import { useEffect, useState } from "react";
import { apiUrl } from "@/app/lib/api";
import type { Track } from "@/app/lib/types";

type FeedbackType = "like" | "dislike" | "dislike_version" | "not_now";

const actions: Array<{ type: FeedbackType; label: string }> = [
  { type: "like", label: "喜欢" },
  { type: "dislike", label: "不喜欢" },
  { type: "dislike_version", label: "不喜欢此版本" },
  { type: "not_now", label: "暂时不听" },
];

export function TrackFeedbackControls({
  track,
  onNegative,
}: {
  track: Track | null;
  onNegative?: () => void;
}) {
  const [latest, setLatest] = useState<string>("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setLatest("");
    if (!track) return;
    let cancelled = false;
    void fetch(
      apiUrl(`/api/track-feedback?user_id=local&track_id=${encodeURIComponent(track.id)}`),
      { cache: "no-store" }
    )
      .then(async (response) => (response.ok ? response.json() : { feedback: [] }))
      .then((payload: { feedback?: Array<{ latest_feedback?: string }> }) => {
        if (!cancelled) setLatest(payload.feedback?.[0]?.latest_feedback ?? "");
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [track]);

  const record = async (feedbackType: FeedbackType) => {
    if (!track || busy) return;
    setBusy(true);
    try {
      const response = await fetch(apiUrl("/api/track-feedback"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: "local",
          track_id: track.id,
          feedback_type: feedbackType,
          source: "player_ui",
        }),
      });
      if (!response.ok) return;
      setLatest(feedbackType);
      if (feedbackType !== "like") onNegative?.();
    } finally {
      setBusy(false);
    }
  };

  if (!track) return null;
  return (
    <div className="flex flex-wrap gap-1.5" aria-label="歌曲反馈">
      {actions.map((action) => (
        <button
          key={action.type}
          type="button"
          disabled={busy}
          aria-pressed={latest === action.type}
          onClick={() => void record(action.type)}
          className={`rounded-full border px-2.5 py-1 text-[10px] transition-colors disabled:opacity-50 ${
            latest === action.type
              ? "border-[rgba(129,140,248,0.55)] bg-[rgba(129,140,248,0.16)] text-[var(--color-primary)]"
              : "border-[var(--glass-border)] text-[var(--color-on-surface-muted)]"
          }`}
        >
          {action.label}
        </button>
      ))}
    </div>
  );
}
