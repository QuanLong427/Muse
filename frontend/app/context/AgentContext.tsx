"use client";

import type { AgentState, ChatMessage, DownloadJob, Track, TrackCardData } from "@/app/lib/types";
import { useDownloads } from "@/app/context/DownloadContext";
import { useMode } from "@/app/context/ModeContext";
import { usePlayer } from "@/app/context/PlayerContext";
import { useScenario } from "@/app/context/ScenarioContext";
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

type AgentCtxValue = AgentState & {
  sendMessage: (text: string) => Promise<void>;
  clearMessages: () => void;
  cancel: () => void;
  currentScenario: string;
  setCurrentScenario: (s: string) => void;
  scenarios: string[];
  addScenario: (name: string) => Promise<void>;
  deleteScenario: (name: string) => Promise<void>;
  voiceOutputEnabled: boolean;
  voiceSpeaking: boolean;
  voiceOutputError: string;
  toggleVoiceOutput: () => void;
  stopVoiceOutput: () => void;
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
): string | null {
  if (!data || typeof data !== "object") return null;
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
    if (!Array.isArray(content)) return null;
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
    return null;
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
    return null;
  }

  if (t === "track_cards") {
    const tracks = d.tracks;
    if (!Array.isArray(tracks) || tracks.length === 0) return null;
    setMessages((m) => [
      ...m,
      {
        id: newId(),
        role: "agent" as const,
        content: "",
        timestamp: ts,
        trackCards: tracks as TrackCardData[],
      },
    ]);
    return null;
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
    return text || null;
  }

  if (t === "done") {
    streamingIdRef.current = null;
  }
  return null;
}

function waitForAudioPlayback(
  audio: HTMLAudioElement | null,
  trigger: () => void | Promise<void>,
  timeoutMs = 4000
): Promise<void> {
  if (!audio) return Promise.reject(new Error("播放器音频元素不可用"));
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      audio.removeEventListener("playing", onPlaying);
      audio.removeEventListener("error", onError);
      if (error) reject(error);
      else resolve();
    };
    const onPlaying = () => finish();
    const onError = () => finish(new Error("音频加载或播放失败"));
    const timer = window.setTimeout(
      () => finish(new Error("等待播放器开始播放超时")),
      timeoutMs
    );
    audio.addEventListener("playing", onPlaying, { once: true });
    audio.addEventListener("error", onError, { once: true });
    void Promise.resolve()
      .then(trigger)
      .then(() => {
        if (!audio.paused && Boolean(audio.currentSrc || audio.src)) finish();
      })
      .catch((error: unknown) =>
        finish(error instanceof Error ? error : new Error(String(error)))
      );
  });
}

function toSpeechText(content: string): string {
  return content
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!?(?:\[)([^\]]+)(?:\])\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^[-*+]\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 2000);
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
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [userId, setUserId] = useState<string | null>(null);
  const streamingIdRef = useRef<string | null>(null);

  const {
    currentScenario,
    setCurrentScenario,
    scenarios,
    addScenario,
    deleteScenario,
  } = useScenario();
  const { registerJob } = useDownloads();
  const [voiceOutputEnabled, setVoiceOutputEnabled] = useState(false);
  const [voiceSpeaking, setVoiceSpeaking] = useState(false);
  const [voiceOutputError, setVoiceOutputError] = useState("");
  const voiceOutputEnabledRef = useRef(false);
  const speechAudioRef = useRef<HTMLAudioElement | null>(null);
  const speechAbortRef = useRef<AbortController | null>(null);
  const speechObjectUrlRef = useRef<string | null>(null);

  const {
    state: playerState,
    play,
    pause,
    next: playNext,
    prev: playPrevious,
    stop,
    seek,
    setVolume,
    playTrack,
    playCollection,
    addTracks,
    insertNext,
    removeSessionItem,
    reorderSession,
    clearSession,
    setPlaybackMode,
    audioRef,
  } = usePlayer();
  const playerStateRef = useRef(playerState);
  useEffect(() => {
    playerStateRef.current = playerState;
  }, [playerState]);

  useEffect(() => {
    const enabled = window.localStorage.getItem("musicer.voice-output") === "true";
    voiceOutputEnabledRef.current = enabled;
    setVoiceOutputEnabled(enabled);
  }, []);

  const stopVoiceOutput = useCallback(() => {
    speechAbortRef.current?.abort();
    speechAbortRef.current = null;
    const audio = speechAudioRef.current;
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
    }
    speechAudioRef.current = null;
    if (speechObjectUrlRef.current) URL.revokeObjectURL(speechObjectUrlRef.current);
    speechObjectUrlRef.current = null;
    setVoiceSpeaking(false);
  }, []);

  const speak = useCallback(async (content: string) => {
    if (!voiceOutputEnabledRef.current) return;
    const text = toSpeechText(content);
    if (!text) return;

    stopVoiceOutput();
    const controller = new AbortController();
    speechAbortRef.current = controller;
    setVoiceSpeaking(true);
    setVoiceOutputError("");
    try {
      const response = await fetch(apiUrl("/api/voice/synthesize"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
        signal: controller.signal,
      });
      if (!response.ok) {
        let detail = response.statusText || `HTTP ${response.status}`;
        try {
          const payload = (await response.json()) as Record<string, unknown>;
          if (typeof payload.detail === "string") detail = payload.detail;
        } catch {
          // Keep the HTTP status fallback.
        }
        throw new Error(detail);
      }
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      const objectUrl = URL.createObjectURL(blob);
      speechObjectUrlRef.current = objectUrl;
      const audio = new Audio(objectUrl);
      speechAudioRef.current = audio;
      const finish = () => {
        if (speechAudioRef.current === audio) {
          speechAudioRef.current = null;
          setVoiceSpeaking(false);
        }
        URL.revokeObjectURL(objectUrl);
        if (speechObjectUrlRef.current === objectUrl) speechObjectUrlRef.current = null;
      };
      audio.addEventListener("ended", finish, { once: true });
      audio.addEventListener("error", finish, { once: true });
      await audio.play();
    } catch (reason) {
      if (!controller.signal.aborted) {
        setVoiceOutputError(reason instanceof Error ? reason.message : "语音播报失败");
      }
      stopVoiceOutput();
    } finally {
      if (speechAbortRef.current === controller) speechAbortRef.current = null;
    }
  }, [stopVoiceOutput]);

  const toggleVoiceOutput = useCallback(() => {
    const next = !voiceOutputEnabledRef.current;
    voiceOutputEnabledRef.current = next;
    setVoiceOutputEnabled(next);
    window.localStorage.setItem("musicer.voice-output", String(next));
    setVoiceOutputError("");
    if (!next) stopVoiceOutput();
  }, [stopVoiceOutput]);

  useEffect(() => stopVoiceOutput, [stopVoiceOutput]);

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

  const executePlayerAction = useCallback(async (data: unknown) => {
    if (!data || typeof data !== "object") return false;
    const actionData = data as Record<string, unknown>;
    if (actionData.type !== "client_action" || actionData.target !== "player") {
      return false;
    }

    const value = actionData.value;
    switch (actionData.action) {
      case "play":
        await waitForAudioPlayback(audioRef.current, () => play());
        break;
      case "play_track": {
        const track = actionData.track;
        if (!track || typeof track !== "object") return false;
        const candidate = track as Partial<Track>;
        if (
          typeof candidate.id !== "string" ||
          typeof candidate.title !== "string" ||
          typeof candidate.url !== "string"
        ) {
          return false;
        }
        await waitForAudioPlayback(audioRef.current, () => {
          playTrack(candidate as Track);
        });
        break;
      }
      case "play_collection": {
        const tracks = actionData.tracks;
        if (!Array.isArray(tracks) || tracks.length === 0) return false;
        const canonical = tracks.filter(
          (track): track is Track =>
            Boolean(
              track &&
                typeof track === "object" &&
                typeof (track as Partial<Track>).id === "string" &&
                typeof (track as Partial<Track>).title === "string" &&
                typeof (track as Partial<Track>).url === "string"
            )
        );
        if (!canonical.length) return false;
        await waitForAudioPlayback(audioRef.current, () => {
          playCollection(
            canonical,
            actionData.origin_type === "playlist" ? "playlist" : "manual",
            typeof actionData.origin_id === "string" ? actionData.origin_id : null
          );
        });
        break;
      }
      case "insert_next": {
        const track = actionData.track;
        if (!track || typeof track !== "object") return false;
        const candidate = track as Partial<Track>;
        if (
          typeof candidate.id !== "string" ||
          typeof candidate.title !== "string" ||
          typeof candidate.url !== "string"
        ) {
          return false;
        }
        insertNext(candidate as Track, "agent");
        break;
      }
      case "add_tracks": {
        const tracks = actionData.tracks;
        if (!Array.isArray(tracks)) return false;
        const canonical = tracks.filter(
          (track): track is Track =>
            Boolean(
              track &&
                typeof track === "object" &&
                typeof (track as Partial<Track>).id === "string" &&
                typeof (track as Partial<Track>).title === "string" &&
                typeof (track as Partial<Track>).url === "string"
            )
        );
        if (!canonical.length) return false;
        addTracks(
          canonical,
          actionData.origin_type === "radio" ? "radio" : "recommendation",
          typeof actionData.origin_id === "string" ? actionData.origin_id : null
        );
        break;
      }
      case "remove_session_item":
        if (typeof actionData.item_id !== "string") return false;
        removeSessionItem(actionData.item_id);
        break;
      case "reorder_session":
        if (
          !Array.isArray(actionData.item_ids) ||
          !actionData.item_ids.every((id) => typeof id === "string")
        ) {
          return false;
        }
        if (!reorderSession(actionData.item_ids as string[])) return false;
        break;
      case "clear_session":
        clearSession();
        break;
      case "set_playback_mode": {
        const orderMode = actionData.order_mode;
        const repeatMode = actionData.repeat_mode;
        if (
          orderMode !== undefined &&
          orderMode !== "sequential" &&
          orderMode !== "shuffle" &&
          orderMode !== "radio"
        ) {
          return false;
        }
        if (
          repeatMode !== undefined &&
          repeatMode !== "off" &&
          repeatMode !== "all" &&
          repeatMode !== "one"
        ) {
          return false;
        }
        setPlaybackMode(orderMode, repeatMode);
        break;
      }
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
  }, [addTracks, audioRef, clearSession, insertNext, pause, play, playCollection, playNext, playPrevious, playTrack, removeSessionItem, reorderSession, seek, setPlaybackMode, setVolume, stop]);

  const acknowledgePlayerAction = useCallback(
    async (
      actionId: string,
      status: "succeeded" | "failed",
      result: Record<string, unknown>
    ) => {
      const response = await fetch(
        apiUrl(`/api/playback-session/actions/${encodeURIComponent(actionId)}/ack`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            user_id:
              window.localStorage.getItem("musicer.user-id") ?? userId ?? "local",
            session_id:
              window.localStorage.getItem("musicer.session-id") ??
              sessionId ??
              "default",
            status,
            result,
          }),
        }
      );
      if (!response.ok) throw new Error(`播放器 ACK 提交失败：${response.status}`);
    },
    [sessionId, userId]
  );

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
        if (msg.data && typeof msg.data === "object") {
          const action = msg.data as Record<string, unknown>;
          if (action.type === "client_action" && action.target === "player") {
            const actionId = action.action_id;
            if (typeof actionId !== "string" || !actionId) return;
            void executePlayerAction(action)
              .then((executed) => {
                if (!executed) throw new Error("播放器拒绝了无效动作");
                return acknowledgePlayerAction(actionId, "succeeded", {
                  action: action.action,
                  observed_at: new Date().toISOString(),
                });
              })
              .catch((error: unknown) =>
                acknowledgePlayerAction(actionId, "failed", {
                  action: action.action,
                  error: error instanceof Error ? error.message : String(error),
                  observed_at: new Date().toISOString(),
                }).catch(() => undefined)
              );
            return;
          }
        }
        if (msg.data && typeof msg.data === "object") {
          const payload = msg.data as Record<string, unknown>;
          if (
            payload.type === "tool_result" &&
            payload.name === "convert_video" &&
            typeof payload.content === "string"
          ) {
            try {
              const result = JSON.parse(payload.content) as Record<string, unknown>;
              if (result.job && typeof result.job === "object") {
                registerJob(result.job as DownloadJob);
              }
            } catch {
              // The visible tool message still exposes malformed results for diagnosis.
            }
          }
        }
        const finalText = appendFromSdkPayload(
          msg.data,
          setMessages,
          setSessionId,
          streamingIdRef
        );
        if (finalText) void speak(finalText);
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

  const cancel = useCallback(() => {
    sseCancel();
  }, [sseCancel]);

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
            const historyMessages: ChatMessage[] = filtered.map((record: Record<string, unknown>) => {
              const metadata =
                record.metadata && typeof record.metadata === "object"
                  ? (record.metadata as Record<string, unknown>)
                  : {};
              const cards = Array.isArray(metadata.track_cards)
                ? (metadata.track_cards as TrackCardData[])
                : undefined;
              return {
                id: newId(),
                role: (record.role === "agent" ? "agent" : "operator") as "agent" | "operator",
                content: record.content as string,
                timestamp: new Date(record.timestamp as string).getTime() || Date.now(),
                ...(cards?.length ? { trackCards: cards } : {}),
              };
            });
            setMessages(historyMessages);
          }
        }
      } catch {
        // 加载失败不影响正常使用
      }
    };
    loadHistory();
  }, [userId, sessionId]);

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
          playback_session_id: currentPlayerState.sessionId,
          current_item_id: currentPlayerState.currentItemId,
          session_items: currentPlayerState.items.slice(0, 100).map((item) => ({
            item_id: item.id,
            id: item.track.id,
            title: item.track.title,
            author: item.track.author,
            bvid: item.track.bvid,
            origin_type: item.origin_type,
          })),
          session_item_count: currentPlayerState.items.length,
          session_items_truncated: currentPlayerState.items.length > 100,
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
      cancel,
      currentScenario,
      setCurrentScenario,
      scenarios,
      addScenario,
      deleteScenario,
      voiceOutputEnabled,
      voiceSpeaking,
      voiceOutputError,
      toggleVoiceOutput,
      stopVoiceOutput,
    }),
    [messages, loading, sessionId, sendMessage, clearMessages, cancel, currentScenario, scenarios, addScenario, deleteScenario, voiceOutputEnabled, voiceSpeaking, voiceOutputError, toggleVoiceOutput, stopVoiceOutput]
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
