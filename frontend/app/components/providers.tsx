"use client";

import { AgentProvider } from "@/app/context/AgentContext";
import { DanmakuProvider } from "@/app/context/DanmakuContext";
import { ModeProvider } from "@/app/context/ModeContext";
import { PlayerProvider } from "@/app/context/PlayerContext";
import { PlaylistProvider } from "@/app/context/PlaylistContext";
import type { ReactNode } from "react";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ModeProvider>
      <PlayerProvider>
        <PlaylistProvider>
          <DanmakuProvider>
            <AgentProvider>{children}</AgentProvider>
          </DanmakuProvider>
        </PlaylistProvider>
      </PlayerProvider>
    </ModeProvider>
  );
}
