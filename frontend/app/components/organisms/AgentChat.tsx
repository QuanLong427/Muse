"use client";

import { Badge } from "@/app/components/atoms/Badge";
import { GlowDot } from "@/app/components/atoms/GlowDot";
import { Label } from "@/app/components/atoms/Label";
import { ChatMessage } from "@/app/components/molecules/ChatMessage";
import { CommandInput } from "@/app/components/molecules/CommandInput";
import { ScenarioSelect } from "@/app/components/molecules/ScenarioSelect";
import { useAgent } from "@/app/context/AgentContext";
import { useVoiceRecorder } from "@/app/hooks/useVoiceRecorder";
import { useEffect, useMemo, useRef } from "react";

const ThinkingCard = (
  <article className="mb-2 flex w-full justify-start animate-fade-in" key="__thinking__">
    <div className="max-w-[min(100%,38rem)] rounded-xl border-l-2 border-l-[var(--color-outline-variant)] bg-[rgba(255,255,255,0.03)] pl-4 pr-4 pt-3 pb-3">
      <div className="mb-2 flex items-baseline gap-2 opacity-90">
        <span className="text-[11px] font-medium uppercase tracking-wider text-[color:var(--color-on-surface-muted)]">
          AGENT
        </span>
        <span className="text-[10px] uppercase tracking-wider text-[color:var(--color-on-surface-muted)] opacity-60">
          thinking
        </span>
      </div>
      <div className="flex items-center gap-1.5">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="inline-block h-2 w-2 rounded-full"
            style={{
              backgroundColor: "var(--color-primary)",
              animation: `thinking-dot 1.4s ease-in-out ${i * 0.2}s infinite`,
            }}
          />
        ))}
      </div>
    </div>
  </article>
);

export function AgentChat() {
  const {
    messages,
    loading,
    sessionId,
    sendMessage,
    cancel,
    currentScenario,
    setCurrentScenario,
    voiceOutputEnabled,
    voiceSpeaking,
    voiceOutputError,
    toggleVoiceOutput,
  } = useAgent();
  const listRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const voiceInput = useVoiceRecorder((text) => void sendMessage(text));

  useEffect(() => {
    const el = listRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, loading]);

  const showThinking = loading && (messages.length === 0 || messages[messages.length - 1].role !== "agent");

  const rendered = useMemo(() => {
    if (!showThinking || messages.length === 0) {
      return showThinking
        ? [ThinkingCard]
        : messages.map((m) => <ChatMessage key={m.id} message={m} />);
    }

    let insertIdx = messages.length;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role !== "tool") {
        insertIdx = i + 1;
        break;
      }
    }

    const before = messages.slice(0, insertIdx);
    const after = messages.slice(insertIdx);

    return [
      ...before.map((m) => <ChatMessage key={m.id} message={m} />),
      ThinkingCard,
      ...after.map((m) => <ChatMessage key={m.id} message={m} />),
    ];
  }, [messages, showThinking]);

  return (
    <section
      className="flex h-full min-h-[min(420px,70vh)] w-full flex-1 flex-col overflow-hidden rounded-xl border md:min-h-0 transition-all duration-200"
      style={{
        borderColor: "var(--glass-border)",
        backgroundColor: "rgba(255, 255, 255, 0.04)",
        backdropFilter: "blur(16px)",
        WebkitBackdropFilter: "blur(16px)",
      }}
    >
      <header
        className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-[var(--glass-border)] px-3 py-2.5 md:px-4"
      >
        <GlowDot color="primary" />
        <Label size="md" className="text-[color:var(--color-on-surface)]">
          AI ASSISTANT
        </Label>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <ScenarioSelect value={currentScenario} onChange={setCurrentScenario} />
          <button
            type="button"
            onClick={toggleVoiceOutput}
            aria-label={voiceOutputEnabled ? "关闭语音播报" : "开启语音播报"}
            aria-pressed={voiceOutputEnabled}
            title={voiceOutputEnabled ? "语音播报已开启" : "语音播报已关闭"}
            className={`inline-flex h-7 w-7 items-center justify-center rounded-full border transition-all ${
              voiceOutputEnabled
                ? "border-[rgba(129,140,248,0.45)] bg-[rgba(129,140,248,0.14)] text-[color:var(--color-primary)]"
                : "border-[var(--glass-border)] bg-white/5 text-[color:var(--color-on-surface-muted)]"
            }`}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path d="M5 9v6h4l5 4V5L9 9H5Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
              <path d="M17 9a4 4 0 0 1 0 6M19 6.5a7.5 7.5 0 0 1 0 11" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
            </svg>
          </button>
          {voiceSpeaking && <Badge label="SPEAKING" variant="primary" />}
          {loading ? (
            <Badge label="THINKING" variant="primary" />
          ) : (
            <Badge label="READY" variant="default" />
          )}
          {sessionId ? (
            <Badge label="CONNECTED" variant="primary" />
          ) : (
            <Badge label="OFFLINE" variant="default" />
          )}
        </div>
      </header>

      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto px-2 py-3 md:px-3 md:py-4">
        {messages.length === 0 && !loading ? (
          <p className="px-2 text-center text-sm text-[color:var(--color-on-surface-muted)] opacity-55">
            Hi! Tell me what you want to listen to…
          </p>
        ) : (
          rendered
        )}
        <div ref={bottomRef} aria-hidden />
      </div>

      <div className="shrink-0 border-t border-[var(--glass-border)] px-3 py-3 md:px-4">
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">
            <CommandInput
              disabled={loading}
              onSubmit={(t) => void sendMessage(t)}
              voice={voiceInput}
            />
          </div>
          {loading && (
            <button
              type="button"
              aria-label="中断"
              onClick={cancel}
              className="group relative inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-[var(--glass-border)] bg-[rgba(255,255,255,0.06)] text-[color:var(--color-on-surface-muted)] transition-all duration-200 outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-error)] hover:border-[var(--color-error)] hover:text-[var(--color-error)] hover:bg-[rgba(251,113,133,0.1)]"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12" rx="2" stroke="none" />
              </svg>
              <span
                className="pointer-events-none absolute -bottom-8 left-1/2 z-10 -translate-x-1/2 whitespace-nowrap rounded-lg px-2 py-1 text-[10px] font-medium uppercase tracking-wider opacity-0 transition-opacity duration-200 group-hover:opacity-100"
                style={{
                  backgroundColor: "rgba(255, 255, 255, 0.1)",
                  backdropFilter: "blur(8px)",
                  WebkitBackdropFilter: "blur(8px)",
                  color: "var(--color-error)",
                  border: "1px solid rgba(251,113,133,0.2)",
                }}
              >
                STOP
              </span>
            </button>
          )}
        </div>
        {(voiceInput.error || voiceOutputError) && (
          <p className="mt-1.5 px-1 text-[11px] text-red-300/90">
            {voiceInput.error || voiceOutputError}
          </p>
        )}
      </div>
    </section>
  );
}
