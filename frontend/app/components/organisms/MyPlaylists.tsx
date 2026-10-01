"use client";

import { useEffect, useMemo, useState } from "react";
import { Label } from "@/app/components/atoms/Label";
import { usePlayer } from "@/app/context/PlayerContext";
import { usePlaylists } from "@/app/context/PlaylistContext";

export function MyPlaylists() {
  const {
    playlists,
    loading,
    error,
    createPlaylist,
    updatePlaylist,
    deletePlaylist,
    removeItem,
    reorderItems,
  } = usePlaylists();
  const { playCollection, playTrack, addTracks } = usePlayer();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");

  useEffect(() => {
    if (!selectedId && playlists[0]) setSelectedId(playlists[0].id);
    if (selectedId && !playlists.some((playlist) => playlist.id === selectedId)) {
      setSelectedId(playlists[0]?.id ?? null);
    }
  }, [playlists, selectedId]);

  const selected = useMemo(
    () => playlists.find((playlist) => playlist.id === selectedId) ?? null,
    [playlists, selectedId]
  );

  const run = async (operation: () => Promise<unknown>) => {
    setBusy(true);
    setActionError("");
    try {
      await operation();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    const name = newName.trim();
    if (!name) return;
    await run(async () => {
      const playlist = await createPlaylist(name);
      setSelectedId(playlist.id);
      setNewName("");
    });
  };

  const rename = () => {
    if (!selected) return;
    const name = window.prompt("新的歌单名称", selected.name)?.trim();
    if (!name || name === selected.name) return;
    void run(() => updatePlaylist(selected.id, name, selected.description));
  };

  const removeSelected = () => {
    if (!selected || !window.confirm(`确定删除歌单“${selected.name}”吗？本地歌曲不会被删除。`)) return;
    void run(() => deletePlaylist(selected.id));
  };

  const move = (index: number, direction: -1 | 1) => {
    if (!selected) return;
    const target = index + direction;
    if (target < 0 || target >= selected.items.length) return;
    const ids = selected.items.map((item) => item.id);
    [ids[index], ids[target]] = [ids[target], ids[index]];
    void run(() => reorderItems(selected.id, ids));
  };

  return (
    <section className="grid min-h-0 flex-1 grid-cols-[9rem_1fr] overflow-hidden rounded-xl border border-[var(--glass-border)] bg-[rgba(255,255,255,0.04)]">
      <aside className="flex min-h-0 flex-col border-r border-[var(--glass-border)]">
        <div className="border-b border-[var(--glass-border)] p-3">
          <Label size="md">MY PLAYLISTS</Label>
          <div className="mt-2 flex gap-1">
            <input value={newName} onChange={(event) => setNewName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void create(); }} placeholder="新歌单" className="min-w-0 flex-1 rounded border border-[var(--glass-border)] bg-transparent px-2 py-1 text-xs outline-none" />
            <button type="button" disabled={busy || !newName.trim()} onClick={() => void create()} className="rounded border border-[var(--glass-border)] px-2 text-[var(--color-primary)] disabled:opacity-40">+</button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          {playlists.map((playlist) => (
            <button key={playlist.id} type="button" onClick={() => setSelectedId(playlist.id)} className={`block w-full border-b border-[var(--glass-border)] px-3 py-2.5 text-left ${selectedId === playlist.id ? "bg-[rgba(129,140,248,0.12)] text-[var(--color-primary)]" : "text-[var(--color-on-surface-muted)]"}`}>
              <span className="block truncate text-xs font-medium">{playlist.name}</span>
              <span className="text-[10px] opacity-60">{playlist.items.length} 首</span>
            </button>
          ))}
        </div>
      </aside>
      <div className="flex min-h-0 flex-col">
        {loading ? (
          <p className="p-4 text-sm text-[var(--color-on-surface-muted)]">正在读取歌单…</p>
        ) : error || actionError ? (
          <p className="p-4 text-sm text-[var(--color-error)]">{actionError || error}</p>
        ) : !selected ? (
          <p className="p-4 text-sm text-[var(--color-on-surface-muted)]">创建一个歌单开始整理歌曲</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--glass-border)] px-4 py-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">{selected.name}</p>
                <p className="text-[10px] text-[var(--color-on-surface-muted)]">长期歌单 · {selected.items.length} 首</p>
              </div>
              <div className="flex gap-1.5">
                <button type="button" disabled={!selected.items.length} onClick={() => playCollection(selected.items.map((item) => item.track), "playlist", selected.id)} className="rounded-full border border-[rgba(129,140,248,0.3)] px-2.5 py-1 text-[10px] text-[var(--color-primary)] disabled:opacity-40">PLAY ALL</button>
                <button type="button" onClick={rename} className="rounded-full border border-[var(--glass-border)] px-2.5 py-1 text-[10px]">RENAME</button>
                <button type="button" onClick={removeSelected} className="rounded-full border border-[rgba(251,113,133,0.3)] px-2.5 py-1 text-[10px] text-[var(--color-error)]">DELETE</button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-auto">
              {selected.items.length === 0 ? (
                <p className="p-4 text-sm text-[var(--color-on-surface-muted)]">从本地曲库选择“PLAYLIST +”加入歌曲</p>
              ) : selected.items.map((item, index) => (
                <div key={item.id} className="flex items-center gap-2 border-b border-[var(--glass-border)] px-3 py-2.5 last:border-b-0">
                  <span className="w-5 text-xs text-[var(--color-on-surface-muted)]">{index + 1}</span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm">{item.track.title}</p>
                    <p className="truncate text-[10px] text-[var(--color-on-surface-muted)]">{item.track.author || "未知歌手"}</p>
                  </div>
                  <button type="button" onClick={() => playTrack(item.track, undefined, "playlist", selected.id)} className="text-[10px] text-[var(--color-primary)]">PLAY</button>
                  <button type="button" onClick={() => addTracks([item.track], "playlist", selected.id)} className="text-[10px] text-[var(--color-on-surface-muted)]">ADD</button>
                  <button type="button" disabled={index === 0 || busy} onClick={() => move(index, -1)} className="text-xs disabled:opacity-25" aria-label="上移">↑</button>
                  <button type="button" disabled={index === selected.items.length - 1 || busy} onClick={() => move(index, 1)} className="text-xs disabled:opacity-25" aria-label="下移">↓</button>
                  <button type="button" disabled={busy} onClick={() => void run(() => removeItem(selected.id, item.id))} className="text-xs text-[var(--color-error)]">×</button>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
