"use client";

import { Player } from "./Player";
import { UpNext } from "./UpNext";

export function PlaybackDetails() {
  return (
    <section aria-label="播放详情" className="flex min-h-0 flex-1 flex-col gap-4">
      <Player />
      <UpNext />
    </section>
  );
}
