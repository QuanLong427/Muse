"use client";

import { AgentProvider } from "@/app/context/AgentContext";
import { DanmakuProvider } from "@/app/context/DanmakuContext";
import { DownloadProvider } from "@/app/context/DownloadContext";
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
              <DownloadProvider>
                <AgentProvider>{children}</AgentProvider>
              </DownloadProvider>
            </DanmakuProvider>
          </PlaylistProvider>
        </PlayerProvider>
      </ScenarioProvider>
    </ModeProvider>
  );
}
