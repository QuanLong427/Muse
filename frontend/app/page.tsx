"use client";

import { useState } from "react";
import { DanmakuOverlay, Logo, ModeSwitch } from "@/app/components/atoms";
import {
  AgentChat,
  MusicVisualizer,
  MusicWorkspace,
  SettingsModal,
  StatusBar,
} from "@/app/components/organisms";

export default function Home() {
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <div className="flex min-h-[100dvh] items-center justify-center p-3 md:p-6 lg:p-8">
      <MusicVisualizer />
      <div className="relative z-10 flex h-[min(94dvh,56rem)] w-full max-w-7xl flex-col overflow-hidden rounded-xl border shadow-lg"
        style={{
          borderColor: "var(--glass-border)",
          backgroundColor: "rgba(255, 255, 255, 0.04)",
          backdropFilter: "blur(24px)",
          WebkitBackdropFilter: "blur(24px)",
        }}
      >
        <header
          className="flex shrink-0 flex-wrap items-center justify-between gap-4 border-b px-4 py-3 md:px-6"
          style={{
            borderColor: "var(--glass-border)",
            backgroundColor: "rgba(255, 255, 255, 0.03)",
          }}
        >
          <Logo onClick={() => setSettingsOpen(true)} />
          <nav aria-label="Main" className="flex flex-wrap items-center gap-4 md:gap-6">
            <ModeSwitch />
          </nav>
        </header>

        <main className="flex min-h-0 flex-1 flex-col gap-4 overflow-hidden p-4 md:grid md:grid-cols-2 md:grid-rows-[1fr] md:gap-6 md:p-6">
          <div className="relative flex min-h-0 min-w-0 flex-1 flex-col gap-4 overflow-hidden">
            <DanmakuOverlay />
            <MusicWorkspace />
          </div>

          <div className="flex min-h-0 min-w-0 flex-1">
            <AgentChat />
          </div>
        </main>

        <StatusBar />
      </div>

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
