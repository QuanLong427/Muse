"use client";

import { AgentProvider } from "@/app/context/AgentContext";
import { DanmakuProvider } from "@/app/context/DanmakuContext";
import { ModeProvider } from "@/app/context/ModeContext";
import { PlayerProvider } from "@/app/context/PlayerContext";
import { PlaylistProvider } from "@/app/context/PlaylistContext";
import { ScenarioProvider } from "@/app/context/ScenarioContext";
import type { ReactNode } from "react";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ModeProvider>
      <ScenarioProvider>
        <PlayerProvider>
          <PlaylistProvider>
            <DanmakuProvider>
              <AgentProvider>{children}</AgentProvider>
            </DanmakuProvider>
          </PlaylistProvider>
        </PlayerProvider>
      </ScenarioProvider>
    </ModeProvider>
  );
}
