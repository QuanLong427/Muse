"use client";

import type { AgentState, ChatMessage, Track } from "@/app/lib/types";
import { useMode } from "@/app/context/ModeContext";
import { usePlayer } from "@/app/context/PlayerContext";
import { useSSE } from "@/app/hooks/useSSE";
import { apiUrl } from "@/app/lib/api";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type ConvertTrack = { bvid: string; title?: string; author?: string };

function parseTracksFromMessage(content: string): Track[] {
  const match = content.match(/```tracks\s*\n([\s\S]*?)```/);
  if (!match) return [];
  try {
    const parsed = JSON.parse(match[1].trim());
    const arr = Array.isArray(parsed) ? parsed : parsed?.tracks;
    if (Array.isArray(arr)) {
      return arr.filter((t: Record<string, unknown>) => t && t.id && t.title);
    }
  } catch { /* not valid JSON */ }
  return [];
}

type AgentCtxValue = AgentState & {
  sendMessage: (text: string) => Promise<void>;
  clearMessages: () => void;
  queueConvert: (tracks: ConvertTrack[]) => void;
  cancel: () => void;
  convertQueue: ConvertTrack[];
  convertingSet: Set<string>;
  convertedSet: Set<string>;
  currentScenario: string;
  setCurrentScenario: (s: string) => void;
  scenarios: string[];
  addScenario: (name: string) => Promise<void>;
  deleteScenario: (name: string) => Promise<void>;
  onConvertedTracksRef: React.RefObject<((tracks: Track[]) => void) | null>;
};

const AgentContext = createContext<AgentCtxValue | null>(null);

function newId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `m-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function appendFromSdkPayload(
  data: unknown,
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>,
  setSessionId: React.Dispatch<React.SetStateAction<string | null>>,
  streamingIdRef: React.MutableRefObject<string | null>
) {
  if (!data || typeof data !== "object") return;
  const d = data as Record<string, unknown>;

  const sid = d.session_id;
  if (typeof sid === "string" && sid) {
    setSessionId((prev) => prev ?? sid);
  }

  const t = d.type;
  const ts = Date.now();

  if (t === "assistant") {
    const message = d.message as Record<string, unknown> | undefined;
    const content = message?.content;
    if (!Array.isArray(content)) return;
    const blocks = content as Array<Record<string, unknown>>;
    for (const block of blocks) {
      if (block.type === "text") {
        const text = block.text;
        if (typeof text === "string" && text.trim()) {
          const currentId = streamingIdRef.current;
          if (currentId) {
            // Append to existing streaming message
            setMessages((m) =>
              m.map((msg) =>
                msg.id === currentId
                  ? { ...msg, content: msg.content + text }
                  : msg
              )
            );
          } else {
            // Create new streaming message
            const id = newId();
            streamingIdRef.current = id;
            setMessages((m) => [
              ...m,
              { id, role: "agent" as const, content: text, timestamp: ts },
            ]);
          }
        }
      } else if (block.type === "tool_use") {
        const tool = block.name;
        if (typeof tool === "string") {
          let summary = `Tool: ${tool}`;
          if (block.input !== undefined) {
            try {
              summary += `\n${JSON.stringify(block.input).slice(0, 480)}`;
            } catch {
              summary += `\n[input]`;
            }
          }
          setMessages((m) => [
            ...m,
            {
              id: newId(),
              role: "tool" as const,
              content: summary,
              timestamp: ts,
              toolName: tool,
            },
          ]);
        }
      }
    }
    return;
  }

  if (t === "tool_call") {
    const name =
      (typeof d.name === "string" && d.name) ||
      (typeof d.tool === "string" && d.tool) ||
      "tool";
    let body =
      typeof d.arguments === "string"
        ? d.arguments
        : d.input !== undefined
          ? JSON.stringify(d.input)
          : "";
    if (!body.trim()) body = "{}";
    setMessages((m) => [
      ...m,
      {
        id: newId(),
        role: "tool" as const,
        content: `${name}\n${body.slice(0, 512)}`,
        timestamp: ts,
        toolName: name,
      },
    ]);
    return;
  }

  if (t === "result" && d.subtype === "success" && typeof d.result === "string") {
    const text = d.result.trim();
    const currentId = streamingIdRef.current;
    if (text.length) {
      if (currentId) {
        // Replace streaming message content with final result
        setMessages((m) =>
          m.map((msg) =>
            msg.id === currentId ? { ...msg, content: text } : msg
          )
        );
        streamingIdRef.current = null;
      } else {
        // No streaming message, create new
        setMessages((m) => [
          ...m,
          { id: newId(), role: "agent" as const, content: text, timestamp: ts },
        ]);
      }
    }
    return;
  }

  if (t === "done") {
    streamingIdRef.current = null;
  }
}

export function AgentProvider({
  children,
  chatApiPath = "/api/chat",
}: {
  children: ReactNode;
  chatApiPath?: string;
}) {
  const { mode } = useMode();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const messagesRef = useRef(messages);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [userId, setUserId] = useState<string | null>(null);
  const streamingIdRef = useRef<string | null>(null);

  const [currentScenario, setCurrentScenario] = useState("默认");
  const [scenarios, setScenarios] = useState<string[]>([]);
  const [convertQueue, setConvertQueue] = useState<ConvertTrack[]>([]);
  const [convertingSet, setConvertingSet] = useState<Set<string>>(new Set());
  const [convertedSet, setConvertedSet] = useState<Set<string>>(new Set());
  const onConvertedTracksRef = useRef<((tracks: Track[]) => void) | null>(null);

  const {
    state: playerState,
    addTracks,
    play,
    pause,
    next: playNext,
    prev: playPrevious,
    stop,
    seek,
    setVolume,
  } = usePlayer();
  const playerStateRef = useRef(playerState);
  useEffect(() => {
    playerStateRef.current = playerState;
  }, [playerState]);

  // Register auto-add callback from PlayerContext
  useEffect(() => {
    onConvertedTracksRef.current = (tracks: Track[]) => {
      addTracks(tracks);
    };
  }, [addTracks]);

  useEffect(() => {
    const getOrCreateId = (key: string, legacyDefault?: string) => {
      const existing = window.localStorage.getItem(key);
      if (existing) return existing;
      // Keep the pre-v2 single-user data reachable on first migration. A
      // future account/new-chat UI can replace these with explicit identities.
      const value = legacyDefault ?? crypto.randomUUID();
      window.localStorage.setItem(key, value);
      return value;
    };
    setUserId(getOrCreateId("musicer.user-id", "local"));
    setSessionId(getOrCreateId("musicer.session-id", "default"));
  }, []);

  const executePlayerAction = useCallback((data: unknown) => {
    if (!data || typeof data !== "object") return false;
    const actionData = data as Record<string, unknown>;
    if (actionData.type !== "client_action" || actionData.target !== "player") {
      return false;
    }

    const value = actionData.value;
    switch (actionData.action) {
      case "play":
        void play();
        break;
      case "pause":
        pause();
        break;
      case "next":
        playNext();
        break;
      case "previous":
        playPrevious();
        break;
      case "stop":
        stop();
        break;
      case "seek":
        if (typeof value === "number" && Number.isFinite(value)) seek(value);
        break;
      case "set_volume":
        if (typeof value === "number" && Number.isFinite(value)) setVolume(value);
        break;
      default:
        return false;
    }
    return true;
  }, [pause, play, playNext, playPrevious, seek, setVolume, stop]);

  const { send, loading, cancel: sseCancel } = useSSE({
    url: apiUrl(chatApiPath),
    body: { mode },
    onMessage: (msg) => {
      if (msg.event === "status" && msg.data && typeof msg.data === "object") {
        const sid = (msg.data as Record<string, unknown>).session_id;
        if (typeof sid === "string" && sid) {
          window.localStorage.setItem("musicer.session-id", sid);
          setSessionId(sid);
        }
        return;
      }
      if (msg.event === "output") {
        if (executePlayerAction(msg.data)) return;
        appendFromSdkPayload(msg.data, setMessages, setSessionId, streamingIdRef);
        return;
      }
      if (msg.event === "error") {
        const err =
          typeof msg.data === "string"
            ? msg.data
            : JSON.stringify(msg.data ?? "error");
        setMessages((m) => [
          ...m,
          { id: newId(), role: "system", content: err, timestamp: Date.now() },
        ]);
      }
    },
  });

  const loadingRef = useRef(loading);
  const convertQueueRef = useRef(convertQueue);
  useEffect(() => {
    loadingRef.current = loading;
  }, [loading]);
  useEffect(() => {
    convertQueueRef.current = convertQueue;
  }, [convertQueue]);

  const flush = useCallback(() => {
    const queue = convertQueueRef.current;
    if (!queue.length) return;

    setConvertQueue([]);
    setConvertingSet((prev) => {
      const next = new Set(prev);
      for (const t of queue) next.add(t.bvid);
      return next;
    });

    const items = queue.map((t) => ({
      url: `https://www.bilibili.com/video/${t.bvid}`,
      title: t.title || "",
      artist: t.author || "",
      bvid: t.bvid,
    }));
    const msg = `请将以下B站视频转为音频并加入播放列表:\n${JSON.stringify(items)}`;
    send(msg, {
      user_id: userId ?? "local",
      session_id: sessionId ?? "default",
      scenario: currentScenario,
    });
  }, [send, userId, sessionId, currentScenario]);

  const queueConvert = useCallback(
    (tracks: ConvertTrack[]) => {
      setConvertQueue((prev) => {
        const existing = new Set([
          ...prev.map((t) => t.bvid),
          ...Array.from(convertingSet),
          ...Array.from(convertedSet),
        ]);
        const fresh = tracks.filter((t) => !existing.has(t.bvid));
        if (!fresh.length) return prev;
        return [...prev, ...fresh];
      });

      if (!loadingRef.current) {
        setTimeout(() => flush(), 0);
      }
    },
    [convertingSet, convertedSet, flush]
  );

  const cancel = useCallback(() => {
    sseCancel();
    setConvertQueue([]);
    setConvertingSet(new Set());
  }, [sseCancel]);

  const prevLoadingRef = useRef(loading);
  const prevConvertingRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const wasLoading = prevLoadingRef.current;
    prevLoadingRef.current = loading;

    if (wasLoading && !loading) {
      const hadConverting = prevConvertingRef.current.size > 0;
      setConvertingSet((prev) => {
        if (prev.size > 0) {
          setConvertedSet((done) => {
            const next = new Set(done);
            for (const bv of prev) next.add(bv);
            return next;
          });
        }
        prevConvertingRef.current = prev;
        return new Set();
      });

      // Auto-add converted tracks to playlist
      if (hadConverting && onConvertedTracksRef.current) {
        const lastAgentMsg = [...messagesRef.current]
          .reverse()
          .find((m) => m.role === "agent");
        if (lastAgentMsg) {
          const tracks = parseTracksFromMessage(lastAgentMsg.content);
          if (tracks.length > 0) {
            onConvertedTracksRef.current(tracks);
          }
        }
      }

      if (convertQueueRef.current.length > 0) {
        setTimeout(() => flush(), 50);
      }
    }
  }, [loading, flush]);

  // 加载历史会话记录
  useEffect(() => {
    if (!userId || !sessionId) return;
    const loadHistory = async () => {
      try {
        const params = new URLSearchParams({
          user_id: userId,
          session_id: sessionId,
        });
        const res = await fetch(apiUrl(`/api/history?${params.toString()}`));
        if (res.ok) {
          const data = await res.json();
          if (data.history && Array.isArray(data.history)) {
            const clearOffset = data.clear_offset ?? 0;
            const filtered = data.history.slice(clearOffset);
            const historyMessages: ChatMessage[] = filtered.map((record: Record<string, unknown>) => ({
              id: newId(),
              role: (record.role === "agent" ? "agent" : "operator") as "agent" | "operator",
              content: record.content as string,
              timestamp: new Date(record.timestamp as string).getTime() || Date.now(),
            }));
            setMessages(historyMessages);
          }
        }
      } catch {
        // 加载失败不影响正常使用
      }
    };
    loadHistory();
  }, [userId, sessionId]);

  // 加载场景列表
  useEffect(() => {
    const loadScenarios = async () => {
      try {
        const res = await fetch(apiUrl("/api/scenarios"));
        if (res.ok) {
          const data = await res.json();
          if (data.scenarios && Array.isArray(data.scenarios)) {
            setScenarios(data.scenarios);
            setCurrentScenario((current) =>
              data.scenarios.includes(current)
                ? current
                : data.scenarios[0] || "默认"
            );
          }
        }
      } catch {
        // 加载失败使用默认值
      }
    };
    loadScenarios();
  }, []);

  const addScenario = useCallback(async (name: string) => {
    try {
      const res = await fetch(apiUrl("/api/scenarios"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.scenarios && Array.isArray(data.scenarios)) {
          setScenarios(data.scenarios);
          setCurrentScenario(name.trim());
        }
      }
    } catch {
      // 添加失败
    }
  }, []);

  const deleteScenario = useCallback(async (name: string) => {
    try {
      const res = await fetch(apiUrl(`/api/scenarios/${encodeURIComponent(name)}`), {
        method: "DELETE",
      });
      if (res.ok) {
        const data = await res.json();
        if (data.scenarios) {
          setScenarios(data.scenarios);
          if (currentScenario === name) {
            setCurrentScenario(data.scenarios[0] || "默认");
          }
        }
      }
    } catch {
      // 删除失败不影响正常使用
    }
  }, [currentScenario]);

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      // Handle /clear command
      if (trimmed === "/clear") {
        await send(trimmed, {
          user_id: userId ?? "local",
          session_id: sessionId ?? "default",
          scenario: currentScenario,
        });
        setMessages([]);
        return;
      }

      const ts = Date.now();
      setMessages((m) => {
        const next = [
          ...m,
          {
            id: newId(),
            role: "operator" as const,
            content: trimmed,
            timestamp: ts,
          },
        ];
        return next;
      });
      const currentPlayerState = playerStateRef.current;
      await send(trimmed, {
        user_id: userId ?? "local",
        session_id: sessionId ?? "default",
        scenario: currentScenario,
        player_state: {
          available: true,
          current: currentPlayerState.current
            ? {
                id: currentPlayerState.current.id,
                title: currentPlayerState.current.title,
                author: currentPlayerState.current.author,
                bvid: currentPlayerState.current.bvid,
              }
            : null,
          playlist: currentPlayerState.playlist.slice(0, 100).map((track) => ({
            id: track.id,
            title: track.title,
            author: track.author,
            bvid: track.bvid,
          })),
          playlist_count: currentPlayerState.playlist.length,
          playlist_truncated: currentPlayerState.playlist.length > 100,
          index: currentPlayerState.index,
          playing: currentPlayerState.playing,
          progress: currentPlayerState.progress,
          duration: currentPlayerState.duration,
          volume: currentPlayerState.volume,
        },
      });
    },
    [send, currentScenario, userId, sessionId]
  );

  const clearMessages = useCallback(() => {
    setMessages([]);
  }, []);

  const value = useMemo<AgentCtxValue>(
    () => ({
      messages,
      loading,
      sessionId,
      sendMessage,
      clearMessages,
      queueConvert,
      cancel,
      convertQueue,
      convertingSet,
      convertedSet,
      currentScenario,
      setCurrentScenario,
      scenarios,
      addScenario,
      deleteScenario,
      onConvertedTracksRef,
    }),
    [messages, loading, sessionId, sendMessage, clearMessages, queueConvert, cancel, convertQueue, convertingSet, convertedSet, currentScenario, scenarios, addScenario, deleteScenario]
  );

  return (
    <AgentContext.Provider value={value}>{children}</AgentContext.Provider>
  );
}

export function useAgent() {
  const v = useContext(AgentContext);
  if (!v) throw new Error("useAgent must be used within AgentProvider");
  return v;
}
