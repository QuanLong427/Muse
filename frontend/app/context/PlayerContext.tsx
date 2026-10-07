"use client";

import type {
  PlaybackOrderMode,
  PlaybackOrigin,
  PlaybackRepeatMode,
  PlaybackSessionItem,
  PlaybackSessionSnapshot,
  PlayerState,
  Track,
} from "@/app/lib/types";
import {
  useAudioPlayer,
  type AudioPlaybackEvent,
} from "@/app/hooks/useAudioPlayer";
import { apiUrl } from "@/app/lib/api";
import { useScenario } from "@/app/context/ScenarioContext";
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

type PlayerCtx = {
  state: PlayerState;
  playTrack: (
    track: Track,
    tracks?: Track[],
    origin?: PlaybackOrigin,
    originId?: string | null
  ) => void;
  playCollection: (
    tracks: Track[],
    origin: PlaybackOrigin,
    originId?: string | null
  ) => void;
  replaceCollectionWithUndo: (
    tracks: Track[],
    origin: PlaybackOrigin,
    originId?: string | null
  ) => void;
  undoCollectionReplacement: () => boolean;
  canUndoCollectionReplacement: boolean;
  addTracks: (
    tracks: Track[],
    origin?: PlaybackOrigin,
    originId?: string | null
  ) => void;
  removeSessionItem: (itemId: string) => void;
  insertNext: (track: Track, origin?: PlaybackOrigin, originId?: string | null) => void;
  reorderSession: (itemIds: string[]) => boolean;
  clearSession: () => void;
  setPlaybackMode: (orderMode?: PlaybackOrderMode, repeatMode?: PlaybackRepeatMode) => void;
  play: () => void | Promise<void>;
  pause: () => void;
  next: () => void;
  prev: () => void;
  togglePlay: () => void | Promise<void>;
  seek: (n: number) => void;
  setVolume: (n: number) => void;
  stop: () => void;
  audioRef: React.RefObject<HTMLAudioElement | null>;
  trackRemoved: boolean;
  clearTrackRemoved: () => void;
};

const PlayerContext = createContext<PlayerCtx | null>(null);

type SessionRestorePoint = {
  items: PlaybackSessionItem[];
  currentItemId: string | null;
  orderMode: PlaybackOrderMode;
  repeatMode: PlaybackRepeatMode;
  historyItemIds: string[];
  historyCursor: number;
  shuffleBagItemIds: string[];
  progress: number;
  playing: boolean;
  volume: number;
};

function newItemId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `playback-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function makeItem(
  track: Track,
  position: number,
  originType: PlaybackOrigin = "manual",
  originId: string | null = null
): PlaybackSessionItem {
  return {
    id: newItemId(),
    position,
    track,
    origin_type: originType,
    origin_id: originId,
    added_at: new Date().toISOString(),
  };
}

function reindex(items: PlaybackSessionItem[]): PlaybackSessionItem[] {
  return items.map((item, position) =>
    item.position === position ? item : { ...item, position }
  );
}

function shuffled(values: string[]): string[] {
  const result = [...values];
  for (let index = result.length - 1; index > 0; index -= 1) {
    const target = Math.floor(Math.random() * (index + 1));
    [result[index], result[target]] = [result[target], result[index]];
  }
  return result;
}

export function PlayerProvider({ children }: { children: ReactNode }) {
  const { currentScenario } = useScenario();
  const [sessionId, setSessionId] = useState("playback:local");
  const [revision, setRevision] = useState(0);
  const [items, setItems] = useState<PlaybackSessionItem[]>([]);
  const [currentItemId, setCurrentItemId] = useState<string | null>(null);
  const [orderMode, setOrderModeState] = useState<PlaybackOrderMode>("sequential");
  const [repeatMode, setRepeatModeState] = useState<PlaybackRepeatMode>("off");
  const [historyItemIds, setHistoryItemIds] = useState<string[]>([]);
  const [historyCursor, setHistoryCursor] = useState(-1);
  const [shuffleBagItemIds, setShuffleBagItemIds] = useState<string[]>([]);
  const [trackRemoved, setTrackRemoved] = useState(false);
  const [canUndoCollectionReplacement, setCanUndoCollectionReplacement] = useState(false);
  const collectionRestoreRef = useRef<SessionRestorePoint | null>(null);

  const itemsRef = useRef<PlaybackSessionItem[]>([]);
  const currentItemIdRef = useRef<string | null>(null);
  const revisionRef = useRef(0);
  const hydratedRef = useRef(false);
  const persistChainRef = useRef<Promise<void>>(Promise.resolve());
  const playTrackInternalRef = useRef<(track: Track) => void>(() => {});
  const switchingTrackRef = useRef(false);
  const orderModeRef = useRef<PlaybackOrderMode>("sequential");
  const repeatModeRef = useRef<PlaybackRepeatMode>("off");
  const historyItemIdsRef = useRef<string[]>([]);
  const historyCursorRef = useRef(-1);
  const shuffleBagItemIdsRef = useRef<string[]>([]);
  const advancePlaybackRef = useRef<(reason: "ended" | "manual") => void>(() => {});
  const radioRequestKeyRef = useRef("");

  const postPlaybackEvent = useCallback(
    (
      eventType:
        | "play_started"
        | "play_resumed"
        | "play_paused"
        | "play_completed"
        | "play_skipped"
        | "play_stopped",
      event: AudioPlaybackEvent
    ) => {
      if (!event.track) return;
      const item = itemsRef.current.find(
        (candidate) =>
          candidate.track.id === event.track?.id ||
          Boolean(event.track?.bvid && candidate.track.bvid === event.track.bvid)
      );
      void fetch(apiUrl("/api/playback-events"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: "local",
          session_id: sessionId,
          item_id: item?.id ?? null,
          event_type: eventType,
          track: event.track,
          position_seconds: event.currentTime,
          duration_seconds: event.duration,
          origin_type: item?.origin_type ?? "manual",
          origin_id: item?.origin_id ?? null,
          scenario: currentScenario,
        }),
      }).catch(() => undefined);
    },
    [currentScenario, sessionId]
  );

  const handlePlayEvent = useCallback(
    (event: AudioPlaybackEvent) => {
      postPlaybackEvent(event.currentTime > 0.5 ? "play_resumed" : "play_started", event);
    },
    [postPlaybackEvent]
  );

  const handlePauseEvent = useCallback(
    (event: AudioPlaybackEvent) => {
      if (switchingTrackRef.current) return;
      if (event.duration > 0 && event.currentTime >= event.duration - 0.5) return;
      postPlaybackEvent("play_paused", event);
    },
    [postPlaybackEvent]
  );

  const handleEnded = useCallback((event: AudioPlaybackEvent) => {
    postPlaybackEvent("play_completed", event);
    advancePlaybackRef.current("ended");
  }, [postPlaybackEvent]);

  const {
    audioRef,
    playing,
    progress,
    duration,
    volume,
    toggle,
    seek,
    setVolume,
    play,
    playTrack: playAudioTrack,
    pause,
  } = useAudioPlayer({
    onPlay: handlePlayEvent,
    onPause: handlePauseEvent,
    onEnded: handleEnded,
  });

  const startTrackAudio = useCallback(
    (track: Track, startAt = 0, autoplay = true) => {
      switchingTrackRef.current = true;
      playAudioTrack(track, { startAt, autoplay });
      window.setTimeout(() => {
        switchingTrackRef.current = false;
      }, 0);
    },
    [playAudioTrack]
  );

  useEffect(() => {
    playTrackInternalRef.current = startTrackAudio;
  }, [startTrackAudio]);

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  useEffect(() => {
    currentItemIdRef.current = currentItemId;
  }, [currentItemId]);

  useEffect(() => {
    revisionRef.current = revision;
  }, [revision]);

  useEffect(() => {
    orderModeRef.current = orderMode;
  }, [orderMode]);

  useEffect(() => {
    repeatModeRef.current = repeatMode;
  }, [repeatMode]);

  useEffect(() => {
    historyItemIdsRef.current = historyItemIds;
  }, [historyItemIds]);

  useEffect(() => {
    historyCursorRef.current = historyCursor;
  }, [historyCursor]);

  useEffect(() => {
    shuffleBagItemIdsRef.current = shuffleBagItemIds;
  }, [shuffleBagItemIds]);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: number | undefined;
    const load = () => {
      fetch(apiUrl("/api/playback-session?user_id=local"), { cache: "no-store" })
        .then(async (response) => {
          if (!response.ok) throw new Error("playback session unavailable");
          return (await response.json()) as PlaybackSessionSnapshot;
        })
        .then((snapshot) => {
          if (cancelled) return;
          const loadedItems = reindex(snapshot.items ?? []);
          setSessionId(snapshot.id || "playback:local");
          setRevision(snapshot.revision ?? 0);
          revisionRef.current = snapshot.revision ?? 0;
          setItems(loadedItems);
          itemsRef.current = loadedItems;
          const validCurrent = loadedItems.some(
            (item) => item.id === snapshot.current_item_id
          )
            ? snapshot.current_item_id
            : null;
          setCurrentItemId(validCurrent);
          currentItemIdRef.current = validCurrent;
          const loadedOrderMode = snapshot.order_mode ?? "sequential";
          const loadedRepeatMode = snapshot.repeat_mode ?? "off";
          setOrderModeState(loadedOrderMode);
          orderModeRef.current = loadedOrderMode;
          setRepeatModeState(loadedRepeatMode);
          repeatModeRef.current = loadedRepeatMode;
          const validIds = new Set(loadedItems.map((item) => item.id));
          let loadedHistory = (snapshot.history_item_ids ?? []).filter((id) =>
            validIds.has(id)
          );
          let loadedCursor = loadedHistory.length
            ? Math.max(0, Math.min(snapshot.history_cursor ?? 0, loadedHistory.length - 1))
            : -1;
          if (validCurrent && loadedHistory[loadedCursor] !== validCurrent) {
            loadedHistory = [...loadedHistory.slice(0, loadedCursor + 1), validCurrent];
            loadedCursor = loadedHistory.length - 1;
          }
          const loadedShuffleBag = (snapshot.shuffle_bag_item_ids ?? []).filter(
            (id, index, values) =>
              validIds.has(id) && id !== validCurrent && values.indexOf(id) === index
          );
          setHistoryItemIds(loadedHistory);
          historyItemIdsRef.current = loadedHistory;
          setHistoryCursor(loadedCursor);
          historyCursorRef.current = loadedCursor;
          setShuffleBagItemIds(loadedShuffleBag);
          shuffleBagItemIdsRef.current = loadedShuffleBag;
          setVolume(snapshot.volume ?? 0.8);
          const currentItem = loadedItems.find((item) => item.id === validCurrent);
          if (currentItem) {
            startTrackAudio(currentItem.track, snapshot.progress_seconds ?? 0, false);
          }
          hydratedRef.current = true;
        })
        .catch(() => {
          if (!cancelled) retryTimer = window.setTimeout(load, 2000);
        });
    };
    load();
    return () => {
      cancelled = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    };
  }, [setVolume, startTrackAudio]);

  const persistSnapshot = useCallback(
    (snapshotItems: PlaybackSessionItem[], snapshotCurrentId: string | null) => {
      if (!hydratedRef.current) return;
      const payload = {
        user_id: "local",
        current_item_id: snapshotCurrentId,
        status: playing ? "playing" : snapshotCurrentId ? "paused" : "stopped",
        order_mode: orderMode,
        repeat_mode: repeatMode,
        progress_seconds: progress,
        volume,
        history_item_ids: historyItemIdsRef.current,
        history_cursor: historyCursorRef.current,
        shuffle_bag_item_ids: shuffleBagItemIdsRef.current,
        items: snapshotItems,
      };
      persistChainRef.current = persistChainRef.current
        .catch(() => undefined)
        .then(async () => {
          const response = await fetch(apiUrl("/api/playback-session"), {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              ...payload,
              expected_revision: revisionRef.current,
            }),
          });
          if (response.status === 409) {
            const conflict = (await response.json()) as {
              detail?: { actual_revision?: number };
            };
            const actual = conflict.detail?.actual_revision;
            if (typeof actual === "number") {
              revisionRef.current = actual;
              setRevision(actual);
            }
            return;
          }
          if (!response.ok) return;
          const saved = (await response.json()) as PlaybackSessionSnapshot;
          revisionRef.current = saved.revision;
          setRevision(saved.revision);
        });
    },
    [orderMode, playing, progress, repeatMode, volume]
  );

  useEffect(() => {
    if (!hydratedRef.current) return;
    const timer = window.setTimeout(
      () => persistSnapshot(items, currentItemId),
      750
    );
    return () => window.clearTimeout(timer);
  }, [currentItemId, historyCursor, historyItemIds, items, persistSnapshot, shuffleBagItemIds]);

  const currentIndex = items.findIndex((item) => item.id === currentItemId);
  const current = currentIndex >= 0 ? items[currentIndex]?.track ?? null : null;

  const commitNavigation = useCallback(
    (history: string[], cursor: number, bag = shuffleBagItemIdsRef.current) => {
      historyItemIdsRef.current = history;
      historyCursorRef.current = cursor;
      shuffleBagItemIdsRef.current = bag;
      setHistoryItemIds(history);
      setHistoryCursor(cursor);
      setShuffleBagItemIds(bag);
    },
    []
  );

  const setPlaybackMode = useCallback(
    (nextOrderMode?: PlaybackOrderMode, nextRepeatMode?: PlaybackRepeatMode) => {
      if (nextOrderMode) {
        orderModeRef.current = nextOrderMode;
        setOrderModeState(nextOrderMode);
        const bag =
          nextOrderMode === "shuffle"
            ? shuffled(
                itemsRef.current
                  .map((item) => item.id)
                  .filter((id) => id !== currentItemIdRef.current)
              )
            : [];
        shuffleBagItemIdsRef.current = bag;
        setShuffleBagItemIds(bag);
      }
      if (nextRepeatMode) {
        repeatModeRef.current = nextRepeatMode;
        setRepeatModeState(nextRepeatMode);
      }
    },
    []
  );

  const addTracks = useCallback(
    (
      tracks: Track[],
      origin: PlaybackOrigin = "manual",
      originId: string | null = null
    ) => {
      setItems((previous) => {
        const ids = new Set(previous.map((item) => item.track.id));
        const bvids = new Set(
          previous.map((item) => item.track.bvid).filter(Boolean)
        );
        const fresh = tracks.filter(
          (track) => !ids.has(track.id) && !(track.bvid && bvids.has(track.bvid))
        );
        if (!fresh.length) return previous;
        const next = [
          ...previous,
          ...fresh.map((track, offset) =>
            makeItem(track, previous.length + offset, origin, originId)
          ),
        ];
        itemsRef.current = next;
        if (orderModeRef.current === "shuffle") {
          const freshIds = next
            .slice(previous.length)
            .map((item) => item.id)
            .filter((id) => id !== currentItemIdRef.current);
          const nextBag = [...shuffleBagItemIdsRef.current, ...shuffled(freshIds)];
          shuffleBagItemIdsRef.current = nextBag;
          setShuffleBagItemIds(nextBag);
        }
        return next;
      });
    },
    []
  );

  useEffect(() => {
    if (!hydratedRef.current || orderMode !== "radio" || !items.length) return;
    const activeIndex = items.findIndex((item) => item.id === currentItemId);
    const remaining = activeIndex >= 0 ? items.length - activeIndex - 1 : items.length;
    if (remaining >= 3) return;
    const excludeTrackIds = items.map((item) => item.track.id);
    const requestKey = `${currentScenario}:${currentItemId ?? "none"}:${excludeTrackIds.join("|")}`;
    if (radioRequestKeyRef.current === requestKey) return;
    radioRequestKeyRef.current = requestKey;
    void fetch(apiUrl("/api/recommendations/radio"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: "local",
        exclude_track_ids: excludeTrackIds,
        limit: 5,
        scenario: currentScenario,
        current_track_id: current?.id ?? null,
      }),
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("radio recommendation unavailable");
        return (await response.json()) as { tracks?: Track[]; batch_id?: string | null };
      })
      .then((payload) => {
        if (payload.tracks?.length) {
          addTracks(payload.tracks, "radio", payload.batch_id ?? `radio:${Date.now()}`);
        }
      })
      .catch(() => undefined);
  }, [addTracks, current, currentItemId, currentScenario, items, orderMode]);

  const removeSessionItem = useCallback(
    (itemId: string) => {
      const previous = itemsRef.current;
      const removedIndex = previous.findIndex((item) => item.id === itemId);
      if (removedIndex < 0) return;
      const removedCurrent = currentItemIdRef.current === itemId;
      const nextItems = reindex(previous.filter((item) => item.id !== itemId));
      itemsRef.current = nextItems;
      setItems(nextItems);

      const filteredHistory = historyItemIdsRef.current.filter((id) => id !== itemId);
      const filteredBag = shuffleBagItemIdsRef.current.filter((id) => id !== itemId);

      if (!removedCurrent) {
        const currentHistoryIndex = currentItemIdRef.current
          ? filteredHistory.lastIndexOf(currentItemIdRef.current)
          : -1;
        commitNavigation(filteredHistory, currentHistoryIndex, filteredBag);
        return;
      }
      setTrackRemoved(true);
      const replacement = nextItems[Math.min(removedIndex, nextItems.length - 1)];
      if (!replacement) {
        currentItemIdRef.current = null;
        setCurrentItemId(null);
        commitNavigation([], -1, []);
        const element = audioRef.current;
        element?.pause();
        if (element) {
          element.removeAttribute("src");
          element.load();
        }
        return;
      }
      const replacementHistory = [...filteredHistory, replacement.id];
      commitNavigation(
        replacementHistory,
        replacementHistory.length - 1,
        filteredBag.filter((id) => id !== replacement.id)
      );
      currentItemIdRef.current = replacement.id;
      setCurrentItemId(replacement.id);
      if (playing) {
        startTrackAudio(replacement.track);
      } else {
        const element = audioRef.current;
        if (element) {
          element.removeAttribute("src");
          element.load();
        }
      }
    },
    [audioRef, commitNavigation, playing, startTrackAudio]
  );

  const reportCurrentEvent = useCallback(
    (eventType: "play_skipped" | "play_stopped") => {
      const currentItem = itemsRef.current.find(
        (item) => item.id === currentItemIdRef.current
      );
      if (!currentItem) return;
      const element = audioRef.current;
      postPlaybackEvent(eventType, {
        track: currentItem.track,
        currentTime: element?.currentTime ?? 0,
        duration:
          element && Number.isFinite(element.duration) ? element.duration : 0,
      });
    },
    [audioRef, postPlaybackEvent]
  );

  const selectSessionItem = useCallback(
    (
      itemId: string,
      options: {
        recordHistory?: boolean;
        reportSkip?: boolean;
        historyCursor?: number;
      } = {}
    ) => {
      const item = itemsRef.current.find((candidate) => candidate.id === itemId);
      if (!item) return false;
      const previousId = currentItemIdRef.current;
      if (options.reportSkip && previousId && previousId !== item.id) {
        reportCurrentEvent("play_skipped");
      }

      let nextHistory = historyItemIdsRef.current;
      let nextCursor = historyCursorRef.current;
      if (options.recordHistory !== false) {
        nextHistory = nextHistory.slice(0, nextCursor + 1);
        if (nextHistory[nextHistory.length - 1] !== item.id) nextHistory.push(item.id);
        nextCursor = nextHistory.length - 1;
      } else if (typeof options.historyCursor === "number") {
        nextCursor = options.historyCursor;
      }
      const nextBag = shuffleBagItemIdsRef.current.filter((id) => id !== item.id);
      commitNavigation(nextHistory, nextCursor, nextBag);
      currentItemIdRef.current = item.id;
      setCurrentItemId(item.id);
      startTrackAudio(item.track);
      return true;
    },
    [commitNavigation, reportCurrentEvent, startTrackAudio]
  );

  const advancePlayback = useCallback(
    (reason: "ended" | "manual") => {
      const currentId = currentItemIdRef.current;
      if (!itemsRef.current.length) return;
      if (reason === "ended" && repeatModeRef.current === "one" && currentId) {
        const currentItem = itemsRef.current.find((item) => item.id === currentId);
        if (currentItem) startTrackAudio(currentItem.track);
        return;
      }

      const forwardCursor = historyCursorRef.current + 1;
      const forwardId = historyItemIdsRef.current[forwardCursor];
      if (forwardId && itemsRef.current.some((item) => item.id === forwardId)) {
        selectSessionItem(forwardId, {
          recordHistory: false,
          reportSkip: reason === "manual",
          historyCursor: forwardCursor,
        });
        return;
      }

      let targetId: string | undefined;
      if (orderModeRef.current === "shuffle") {
        let bag = shuffleBagItemIdsRef.current.filter(
          (id) => id !== currentId && itemsRef.current.some((item) => item.id === id)
        );
        if (!bag.length && repeatModeRef.current === "all") {
          bag = shuffled(
            itemsRef.current.map((item) => item.id).filter((id) => id !== currentId)
          );
        }
        targetId = bag[0];
        const remaining = targetId ? bag.slice(1) : bag;
        shuffleBagItemIdsRef.current = remaining;
        setShuffleBagItemIds(remaining);
      } else {
        const currentIndexValue = itemsRef.current.findIndex((item) => item.id === currentId);
        let nextIndex = currentIndexValue < 0 ? 0 : currentIndexValue + 1;
        if (nextIndex >= itemsRef.current.length) {
          if (repeatModeRef.current !== "all") return;
          nextIndex = 0;
        }
        targetId = itemsRef.current[nextIndex]?.id;
      }
      if (!targetId) return;
      selectSessionItem(targetId, {
        reportSkip: reason === "manual",
        recordHistory: true,
      });
    },
    [selectSessionItem, startTrackAudio]
  );

  useEffect(() => {
    advancePlaybackRef.current = advancePlayback;
  }, [advancePlayback]);

  const playTrack = useCallback(
    (
      track: Track,
      tracks?: Track[],
      origin: PlaybackOrigin = "manual",
      originId: string | null = null
    ) => {
      let nextItems = itemsRef.current;
      let target: PlaybackSessionItem | undefined;
      if (tracks?.length) {
        if (currentItemIdRef.current) reportCurrentEvent("play_skipped");
        nextItems = tracks.map((candidate, position) =>
          makeItem(candidate, position, origin, originId)
        );
        target = nextItems.find((item) => item.track.id === track.id) ?? nextItems[0];
        itemsRef.current = nextItems;
        setItems(nextItems);
        const bag =
          orderModeRef.current === "shuffle"
            ? shuffled(nextItems.map((item) => item.id).filter((id) => id !== target?.id))
            : [];
        commitNavigation([], -1, bag);
      } else {
        target = nextItems.find(
          (item) =>
            item.track.id === track.id ||
            Boolean(track.bvid && item.track.bvid === track.bvid)
        );
        if (!target) {
          target = makeItem(track, nextItems.length, origin, originId);
          nextItems = [...nextItems, target];
          itemsRef.current = nextItems;
          setItems(nextItems);
        }
      }
      if (target) selectSessionItem(target.id, { reportSkip: !tracks?.length });
    },
    [commitNavigation, reportCurrentEvent, selectSessionItem]
  );

  const playCollection = useCallback(
    (tracks: Track[], origin: PlaybackOrigin, originId: string | null = null) => {
      if (!tracks.length) return;
      if (currentItemIdRef.current) reportCurrentEvent("play_skipped");
      const nextItems = tracks.map((track, position) =>
        makeItem(track, position, origin, originId)
      );
      itemsRef.current = nextItems;
      setItems(nextItems);
      const bag =
        orderModeRef.current === "shuffle"
          ? shuffled(nextItems.slice(1).map((item) => item.id))
          : [];
      commitNavigation([], -1, bag);
      selectSessionItem(nextItems[0].id);
    },
    [commitNavigation, reportCurrentEvent, selectSessionItem]
  );

  const replaceCollectionWithUndo = useCallback(
    (tracks: Track[], origin: PlaybackOrigin, originId: string | null = null) => {
      if (!tracks.length) return;
      collectionRestoreRef.current = {
        items: itemsRef.current.map((item) => ({ ...item, track: { ...item.track } })),
        currentItemId: currentItemIdRef.current,
        orderMode: orderModeRef.current,
        repeatMode: repeatModeRef.current,
        historyItemIds: [...historyItemIdsRef.current],
        historyCursor: historyCursorRef.current,
        shuffleBagItemIds: [...shuffleBagItemIdsRef.current],
        progress,
        playing,
        volume,
      };
      setCanUndoCollectionReplacement(true);
      playCollection(tracks, origin, originId);
    },
    [playCollection, playing, progress, volume]
  );

  const undoCollectionReplacement = useCallback(() => {
    const restore = collectionRestoreRef.current;
    if (!restore) return false;
    collectionRestoreRef.current = null;
    setCanUndoCollectionReplacement(false);
    if (currentItemIdRef.current) reportCurrentEvent("play_stopped");

    const restoredItems = restore.items.map((item) => ({
      ...item,
      track: { ...item.track },
    }));
    itemsRef.current = restoredItems;
    setItems(restoredItems);
    currentItemIdRef.current = restore.currentItemId;
    setCurrentItemId(restore.currentItemId);
    setOrderModeState(restore.orderMode);
    orderModeRef.current = restore.orderMode;
    setRepeatModeState(restore.repeatMode);
    repeatModeRef.current = restore.repeatMode;
    commitNavigation(
      restore.historyItemIds,
      restore.historyCursor,
      restore.shuffleBagItemIds
    );
    setVolume(restore.volume);

    const restoredCurrent = restoredItems.find(
      (item) => item.id === restore.currentItemId
    );
    if (restoredCurrent) {
      startTrackAudio(restoredCurrent.track, restore.progress, restore.playing);
    } else {
      pause();
      const element = audioRef.current;
      if (element) {
        element.removeAttribute("src");
        element.load();
      }
    }
    return true;
  }, [audioRef, commitNavigation, pause, reportCurrentEvent, setVolume, startTrackAudio]);

  const next = useCallback(() => advancePlayback("manual"), [advancePlayback]);

  const prev = useCallback(() => {
    const targetCursor = historyCursorRef.current - 1;
    if (targetCursor < 0) return;
    const targetId = historyItemIdsRef.current[targetCursor];
    if (!targetId) return;
    selectSessionItem(targetId, {
      recordHistory: false,
      reportSkip: true,
      historyCursor: targetCursor,
    });
  }, [selectSessionItem]);

  const insertNext = useCallback(
    (
      track: Track,
      origin: PlaybackOrigin = "manual",
      originId: string | null = null
    ) => {
      const existing = itemsRef.current.find(
        (item) =>
          item.track.id === track.id ||
          Boolean(track.bvid && item.track.bvid === track.bvid)
      );
      if (existing?.id === currentItemIdRef.current) return;
      const withoutTarget = existing
        ? itemsRef.current.filter((item) => item.id !== existing.id)
        : [...itemsRef.current];
      const currentPosition = withoutTarget.findIndex(
        (item) => item.id === currentItemIdRef.current
      );
      const insertionIndex = currentPosition >= 0 ? currentPosition + 1 : 0;
      const target = existing ?? makeItem(track, insertionIndex, origin, originId);
      withoutTarget.splice(insertionIndex, 0, target);
      const nextItems = reindex(withoutTarget);
      itemsRef.current = nextItems;
      setItems(nextItems);

      const truncatedHistory = historyItemIdsRef.current.slice(
        0,
        historyCursorRef.current + 1
      );
      const nextBag = [
        target.id,
        ...shuffleBagItemIdsRef.current.filter((id) => id !== target.id),
      ];
      commitNavigation(truncatedHistory, truncatedHistory.length - 1, nextBag);
    },
    [commitNavigation]
  );

  const reorderSession = useCallback((itemIds: string[]) => {
    const currentItems = itemsRef.current;
    if (
      itemIds.length !== currentItems.length ||
      new Set(itemIds).size !== currentItems.length ||
      itemIds.some((id) => !currentItems.some((item) => item.id === id))
    ) {
      return false;
    }
    const byId = new Map(currentItems.map((item) => [item.id, item]));
    const reordered = reindex(
      itemIds.map((id) => byId.get(id)).filter((item): item is PlaybackSessionItem => Boolean(item))
    );
    itemsRef.current = reordered;
    setItems(reordered);
    return true;
  }, []);

  const clearSession = useCallback(() => {
    if (currentItemIdRef.current) reportCurrentEvent("play_stopped");
    pause();
    const element = audioRef.current;
    if (element) {
      element.removeAttribute("src");
      element.load();
    }
    itemsRef.current = [];
    setItems([]);
    currentItemIdRef.current = null;
    setCurrentItemId(null);
    commitNavigation([], -1, []);
    setTrackRemoved(false);
  }, [audioRef, commitNavigation, pause, reportCurrentEvent]);

  const togglePlay = useCallback(() => {
    if (!itemsRef.current.length) return;
    if (!currentItemIdRef.current) {
      const first = itemsRef.current[0];
      if (!first) return;
      currentItemIdRef.current = first.id;
      setCurrentItemId(first.id);
      startTrackAudio(first.track);
      return;
    }
    const currentItem = itemsRef.current.find(
      (item) => item.id === currentItemIdRef.current
    );
    if (currentItem && !audioRef.current?.getAttribute("src")) {
      startTrackAudio(currentItem.track);
      return;
    }
    return toggle();
  }, [audioRef, startTrackAudio, toggle]);

  const stop = useCallback(() => {
    reportCurrentEvent("play_stopped");
    pause();
    seek(0);
  }, [pause, reportCurrentEvent, seek]);

  const playCurrent = useCallback(() => {
    if (!itemsRef.current.length) return;
    if (!currentItemIdRef.current) {
      const first = itemsRef.current[0];
      if (!first) return;
      currentItemIdRef.current = first.id;
      setCurrentItemId(first.id);
      startTrackAudio(first.track);
      return;
    }
    const currentItem = itemsRef.current.find(
      (item) => item.id === currentItemIdRef.current
    );
    if (currentItem && !audioRef.current?.getAttribute("src")) {
      startTrackAudio(currentItem.track);
      return;
    }
    return play();
  }, [audioRef, play, startTrackAudio]);

  const clearTrackRemoved = useCallback(() => setTrackRemoved(false), []);

  const state: PlayerState = useMemo(
    () => ({
      current,
      sessionId,
      revision,
      items,
      currentItemId,
      index: currentIndex < 0 ? 0 : currentIndex,
      playing,
      progress,
      duration,
      volume,
      orderMode,
      repeatMode,
    }),
    [current, currentIndex, currentItemId, duration, items, orderMode, playing, progress, repeatMode, revision, sessionId, volume]
  );

  const value = useMemo<PlayerCtx>(
    () => ({
      state,
      playTrack,
      playCollection,
      replaceCollectionWithUndo,
      undoCollectionReplacement,
      canUndoCollectionReplacement,
      addTracks,
      removeSessionItem,
      insertNext,
      reorderSession,
      clearSession,
      setPlaybackMode,
      play: playCurrent,
      pause,
      next,
      prev,
      togglePlay,
      seek,
      setVolume,
      stop,
      audioRef,
      trackRemoved,
      clearTrackRemoved,
    }),
    [addTracks, audioRef, canUndoCollectionReplacement, clearSession, clearTrackRemoved, insertNext, next, pause, playCollection, playCurrent, playTrack, prev, removeSessionItem, reorderSession, replaceCollectionWithUndo, seek, setPlaybackMode, setVolume, state, stop, togglePlay, trackRemoved, undoCollectionReplacement]
  );

  return (
    <PlayerContext.Provider value={value}>
      <audio ref={audioRef} className="hidden" preload="metadata" aria-hidden />
      {children}
    </PlayerContext.Provider>
  );
}

export function usePlayer() {
  const value = useContext(PlayerContext);
  if (!value) throw new Error("usePlayer must be used within PlayerProvider");
  return value;
}
