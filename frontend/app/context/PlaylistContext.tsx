"use client";

import type { NamedPlaylist, Track } from "@/app/lib/types";
import { apiUrl } from "@/app/lib/api";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

type PlaylistContextValue = {
  playlists: NamedPlaylist[];
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
  createPlaylist: (name: string, description?: string) => Promise<NamedPlaylist>;
  updatePlaylist: (
    playlistId: string,
    name: string,
    description?: string
  ) => Promise<NamedPlaylist>;
  deletePlaylist: (playlistId: string) => Promise<void>;
  addTrack: (playlistId: string, track: Track) => Promise<NamedPlaylist>;
  removeItem: (playlistId: string, itemId: string) => Promise<NamedPlaylist>;
  reorderItems: (playlistId: string, itemIds: string[]) => Promise<NamedPlaylist>;
};

const PlaylistContext = createContext<PlaylistContextValue | null>(null);

function responseError(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "code" in detail) {
      return String((detail as { code?: unknown }).code);
    }
  }
  return fallback;
}

export function PlaylistProvider({ children }: { children: ReactNode }) {
  const [playlists, setPlaylists] = useState<NamedPlaylist[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/playlists?user_id=local"), {
        cache: "no-store",
      });
      const payload = (await response.json()) as {
        playlists?: NamedPlaylist[];
        detail?: unknown;
      };
      if (!response.ok) throw new Error(responseError(payload, "无法读取歌单"));
      setPlaylists(payload.playlists ?? []);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取歌单");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const commit = useCallback((playlist: NamedPlaylist) => {
    setPlaylists((previous) => {
      const found = previous.some((item) => item.id === playlist.id);
      const next = found
        ? previous.map((item) => (item.id === playlist.id ? playlist : item))
        : [playlist, ...previous];
      return [...next].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    });
    setError("");
    return playlist;
  }, []);

  const playlistById = useCallback(
    (playlistId: string) => playlists.find((item) => item.id === playlistId),
    [playlists]
  );

  const createPlaylist = useCallback(
    async (name: string, description = "") => {
      const response = await fetch(apiUrl("/api/playlists"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: "local", name, description }),
      });
      const payload = (await response.json()) as NamedPlaylist;
      if (!response.ok) throw new Error(responseError(payload, "创建歌单失败"));
      return commit(payload);
    },
    [commit]
  );

  const updatePlaylist = useCallback(
    async (playlistId: string, name: string, description = "") => {
      const current = playlistById(playlistId);
      if (!current) throw new Error("歌单不存在");
      const response = await fetch(apiUrl(`/api/playlists/${playlistId}`), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: "local",
          name,
          description,
          expected_revision: current.revision,
        }),
      });
      const payload = (await response.json()) as NamedPlaylist;
      if (!response.ok) {
        if (response.status === 409) void refresh();
        throw new Error(responseError(payload, "更新歌单失败"));
      }
      return commit(payload);
    },
    [commit, playlistById, refresh]
  );

  const deletePlaylist = useCallback(
    async (playlistId: string) => {
      const current = playlistById(playlistId);
      if (!current) return;
      const params = new URLSearchParams({
        user_id: "local",
        expected_revision: String(current.revision),
      });
      const response = await fetch(
        apiUrl(`/api/playlists/${playlistId}?${params.toString()}`),
        { method: "DELETE" }
      );
      const payload = await response.json();
      if (!response.ok) {
        if (response.status === 409) void refresh();
        throw new Error(responseError(payload, "删除歌单失败"));
      }
      setPlaylists((previous) => previous.filter((item) => item.id !== playlistId));
    },
    [playlistById, refresh]
  );

  const addTrack = useCallback(
    async (playlistId: string, track: Track) => {
      const current = playlistById(playlistId);
      if (!current) throw new Error("歌单不存在");
      const response = await fetch(apiUrl(`/api/playlists/${playlistId}/items`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: "local",
          track_id: track.id,
          expected_revision: current.revision,
        }),
      });
      const payload = (await response.json()) as NamedPlaylist;
      if (!response.ok) {
        if (response.status === 409) void refresh();
        throw new Error(responseError(payload, "加入歌单失败"));
      }
      return commit(payload);
    },
    [commit, playlistById, refresh]
  );

  const removeItem = useCallback(
    async (playlistId: string, itemId: string) => {
      const current = playlistById(playlistId);
      if (!current) throw new Error("歌单不存在");
      const params = new URLSearchParams({
        user_id: "local",
        expected_revision: String(current.revision),
      });
      const response = await fetch(
        apiUrl(`/api/playlists/${playlistId}/items/${itemId}?${params.toString()}`),
        { method: "DELETE" }
      );
      const payload = (await response.json()) as NamedPlaylist;
      if (!response.ok) {
        if (response.status === 409) void refresh();
        throw new Error(responseError(payload, "移除歌曲失败"));
      }
      return commit(payload);
    },
    [commit, playlistById, refresh]
  );

  const reorderItems = useCallback(
    async (playlistId: string, itemIds: string[]) => {
      const current = playlistById(playlistId);
      if (!current) throw new Error("歌单不存在");
      const response = await fetch(apiUrl(`/api/playlists/${playlistId}/items`), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: "local",
          item_ids: itemIds,
          expected_revision: current.revision,
        }),
      });
      const payload = (await response.json()) as NamedPlaylist;
      if (!response.ok) {
        if (response.status === 409) void refresh();
        throw new Error(responseError(payload, "调整顺序失败"));
      }
      return commit(payload);
    },
    [commit, playlistById, refresh]
  );

  const value = useMemo<PlaylistContextValue>(
    () => ({
      playlists,
      loading,
      error,
      refresh,
      createPlaylist,
      updatePlaylist,
      deletePlaylist,
      addTrack,
      removeItem,
      reorderItems,
    }),
    [
      addTrack,
      createPlaylist,
      deletePlaylist,
      error,
      loading,
      playlists,
      refresh,
      removeItem,
      reorderItems,
      updatePlaylist,
    ]
  );

  return <PlaylistContext.Provider value={value}>{children}</PlaylistContext.Provider>;
}

export function usePlaylists() {
  const value = useContext(PlaylistContext);
  if (!value) throw new Error("usePlaylists must be used within PlaylistProvider");
  return value;
}
