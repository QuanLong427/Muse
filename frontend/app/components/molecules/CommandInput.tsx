"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import type { VoiceInputStatus } from "@/app/hooks/useVoiceRecorder";

const SLASH_COMMANDS = [
  { command: "/reset-wiki", label: "重置知识库" },
  { command: "/reset-memory", label: "重置用户记忆" },
  { command: "/clear", label: "清屏" },
];

type Props = {
  onSubmit: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
  voice?: {
    supported: boolean;
    status: VoiceInputStatus;
    toggle: () => void;
  };
};

export function CommandInput({
  onSubmit,
  disabled = false,
  placeholder = "Hi, 告诉我你想听什么…",
  voice,
}: Props) {
  const [value, setValue] = useState("");
  const [cursorLeft, setCursorLeft] = useState(0);
  const [showMenu, setShowMenu] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const measureRef = useRef<HTMLSpanElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const valueRef = useRef(value);
  valueRef.current = value;

  const syncCursor = useCallback(() => {
    const input = inputRef.current;
    const measure = measureRef.current;
    if (!input || !measure) return;
    const pos = input.selectionStart ?? valueRef.current.length;
    measure.textContent = valueRef.current.slice(0, pos);
    setCursorLeft(measure.offsetWidth);
  }, []);

  useEffect(() => {
    syncCursor();
  }, [value, syncCursor]);

  // Show menu when input starts with /
  useEffect(() => {
    setShowMenu(value === "/");
  }, [value]);

  // Close menu on outside click
  useEffect(() => {
    if (!showMenu) return;
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowMenu(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [showMenu]);

  const submit = useCallback(() => {
    const t = valueRef.current.trim();
    if (!t || disabled) return;
    onSubmit(t);
    setValue("");
    setShowMenu(false);
  }, [disabled, onSubmit]);

  const selectCommand = useCallback((command: string) => {
    setValue(command);
    setShowMenu(false);
    inputRef.current?.focus();
  }, []);

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
      return;
    }
    if (e.key === "Escape" && showMenu) {
      setShowMenu(false);
      return;
    }
    requestAnimationFrame(syncCursor);
  };

  const voiceBusy = voice?.status === "transcribing";
  const recording = voice?.status === "recording";
  const voiceDisabled =
    !voice?.supported || voiceBusy || (disabled && !recording);
  const resolvedPlaceholder = recording
    ? "正在录音，再次点击麦克风结束…"
    : voiceBusy
      ? "正在识别语音…"
      : placeholder;

  return (
    <div className="relative w-full rounded-xl border border-[var(--glass-border)] bg-[rgba(255,255,255,0.04)] transition-all duration-200 focus-within:border-[rgba(129,140,248,0.3)] focus-within:bg-[rgba(255,255,255,0.06)]">
      {showMenu && (
        <div
          ref={menuRef}
          className="absolute bottom-full left-0 right-0 mb-1 rounded-lg border border-[var(--glass-border)] bg-[rgba(30,30,40,0.95)] shadow-lg backdrop-blur-sm z-50 overflow-hidden"
        >
          {SLASH_COMMANDS.map((cmd) => (
            <button
              key={cmd.command}
              type="button"
              onClick={() => selectCommand(cmd.command)}
              className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm transition-colors hover:bg-[rgba(129,140,248,0.1)]"
            >
              <span className="font-mono text-[color:var(--color-primary)]">{cmd.command}</span>
              <span className="text-[color:var(--color-on-surface-muted)]">{cmd.label}</span>
            </button>
          ))}
        </div>
      )}
      <div className="flex items-start gap-2">
        <span className="shrink-0 select-none py-3 pl-3 text-[color:var(--color-primary)]" aria-hidden>
          ▸
        </span>
        <div className="relative min-w-0 flex-1 py-3 pr-3" style={{ fontFamily: "var(--font-body)" }}>
          <input
            ref={inputRef}
            type="text"
            disabled={disabled}
            value={value}
            placeholder={resolvedPlaceholder}
            onChange={(e) => {
              setValue(e.target.value);
              requestAnimationFrame(syncCursor);
            }}
            onKeyDown={onKeyDown}
            onKeyUp={syncCursor}
            onSelect={syncCursor}
            onClick={syncCursor}
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            className="w-full border-0 bg-transparent p-0 text-sm text-[color:var(--color-on-surface)] outline-none placeholder:text-[color:var(--color-on-surface-muted)]"
            style={{
              caretColor: "transparent",
              letterSpacing: "0.02em",
            }}
          />
          <span
            ref={measureRef}
            className="pointer-events-none invisible absolute top-3 left-0 whitespace-pre text-sm"
            aria-hidden
            style={{ letterSpacing: "0.02em" }}
          />
          <span
            className="pointer-events-none absolute top-3 h-4 w-0.5 rounded-full bg-[var(--color-primary)] animate-pulse"
            aria-hidden
            style={{
              left: `${cursorLeft}px`,
              display: disabled ? "none" : "inline-block",
            }}
          />
        </div>
        {voice && (
          <button
            type="button"
            aria-label={recording ? "结束录音" : "开始语音输入"}
            aria-pressed={recording}
            title={
              !voice.supported
                ? "当前浏览器不支持录音"
                : voiceBusy
                  ? "正在识别语音"
                  : recording
                    ? "结束录音"
                    : "语音输入"
            }
            disabled={voiceDisabled}
            onClick={voice.toggle}
            className={`mr-2 mt-1.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border transition-all disabled:cursor-not-allowed disabled:opacity-35 ${
              recording
                ? "animate-pulse border-red-400/60 bg-red-400/15 text-red-300"
                : "border-[var(--glass-border)] bg-white/5 text-[color:var(--color-on-surface-muted)] hover:border-[rgba(129,140,248,0.45)] hover:text-[color:var(--color-primary)]"
            }`}
          >
            {voiceBusy ? (
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
            ) : recording ? (
              <span className="h-3 w-3 rounded-sm bg-current" />
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
                <path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 1 0 6 0V6a3 3 0 0 0-3-3Z" stroke="currentColor" strokeWidth="1.8" />
                <path d="M6.5 11.5V12a5.5 5.5 0 0 0 11 0v-.5M12 17.5V21M9.5 21h5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            )}
          </button>
        )}
      </div>
    </div>
  );
}
