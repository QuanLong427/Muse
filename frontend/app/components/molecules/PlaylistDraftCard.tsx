"use client";

import { useCallback, useEffect, useState } from "react";
import { apiUrl } from "@/app/lib/api";
import type { PlaylistDraft, Track } from "@/app/lib/types";
import { usePlaylists } from "@/app/context/PlaylistContext";
import { useDownloads } from "@/app/context/DownloadContext";
import { usePlayer } from "@/app/context/PlayerContext";

const buttonClass = "rounded-full border border-[var(--glass-border)] px-2 py-1 text-[10px] disabled:opacity-40";

export function PlaylistDraftCard({ draftId }: { draftId: string }) {
  const [draft, setDraft] = useState<PlaylistDraft | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { refresh: refreshPlaylists } = usePlaylists();
  const { registerJob, itemByBvid } = useDownloads();
  const { playTrack } = usePlayer();
  const userId = () => window.localStorage.getItem("musicer.user-id") || "local";
  const adopt = useCallback((next: PlaylistDraft) => {
    setDraft((previous) => {
      if (!previous) return next;
      const ranks = { draft: 0, confirming: 1, saved: 2, cancelled: 2 };
      return next.revision < previous.revision || (next.revision === previous.revision && ranks[next.status] < ranks[previous.status]) ? previous : next;
    });
  }, []);
  useEffect(() => { if (draft) setName(draft.name); }, [draft?.name, draft?.revision]);

  const load = useCallback(async () => {
    const response = await fetch(apiUrl(`/api/playlist-drafts/${encodeURIComponent(draftId)}?user_id=${encodeURIComponent(userId())}`), { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "无法读取歌单草稿");
    adopt(payload);
  }, [draftId, adopt]);

  useEffect(() => {
    let active = true;
    const refresh = () => { if (active) void load().catch((reason) => setError(String(reason.message || reason))); };
    refresh();
    const listener = (event: Event) => { if ((event as CustomEvent<string>).detail === draftId) refresh(); };
    window.addEventListener("musicer:draft-updated", listener);
    return () => { active = false; window.removeEventListener("musicer:draft-updated", listener); };
  }, [draftId, load]);

  const request = async (method: string, suffix: string, body: Record<string, unknown>): Promise<PlaylistDraft> => {
    const response = await fetch(apiUrl(`/api/playlist-drafts/${encodeURIComponent(draftId)}${suffix}`), {
      method, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId(), ...body }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "草稿操作失败，请刷新后重试");
    adopt(payload);
    window.dispatchEvent(new CustomEvent("musicer:draft-updated", { detail: draftId }));
    return payload;
  };

  const run = async (action: string, extra: Record<string, unknown> = {}) => {
    if (!draft || busy) return;
    setBusy(true); setError("");
    try {
      await request("PATCH", "", { expected_revision: draft.revision, action, ...extra });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "修改失败");
      await load().catch(() => undefined);
    } finally { setBusy(false); }
  };

  const confirm = async () => {
    if (!draft || busy) return;
    setBusy(true); setError("");
    try {
      let current = draft;
      if (!draft.target_playlist_id && name.trim() !== draft.name && draft.status === "draft") {
        current = await request("PATCH", "", { expected_revision: draft.revision, action: "rename", name: name.trim() });
      }
      const saved = await request("POST", "/confirm", { expected_revision: current.revision });
      if (saved.receipt?.download_job) registerJob(saved.receipt.download_job);
      await refreshPlaylists();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "确认失败");
      await load().catch(() => undefined);
    } finally { setBusy(false); }
  };

  const audition = async (track: Track) => {
    try {
      // Resolve the file again; a stale source snapshot is not a playable Track.
      const response = await fetch(apiUrl(`/api/tracks/by-id?track_id=${encodeURIComponent(track.id)}`), { cache: "no-store" });
      if (!response.ok) throw new Error("这首本地歌曲已不可用");
      playTrack(await response.json() as Track);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "试听失败"); }
  };

  if (!draft) return <div className="my-2 rounded-xl border border-[var(--glass-border)] p-3 text-xs">{error || "正在读取歌单草稿…"}</div>;
  const editable = draft.status === "draft" && !busy;
  const remoteCount = draft.items.filter((item) => item.track.source_type === "bilibili" && !draft.local_tracks?.[item.track.id]).length;
  const jobId = draft.receipt?.download_job?.id;
  const status = { draft: "尚未保存", confirming: "已冻结 · 等待保存结果", saved: "已保存", cancelled: "已取消" }[draft.status];
  const move = (index: number, direction: number) => {
    const ids = draft.items.map((item) => item.item_id);
    [ids[index], ids[index + direction]] = [ids[index + direction], ids[index]];
    void run("reorder", { item_ids: ids });
  };

  return <section aria-label="智能歌单草稿" className="my-2 min-w-0 rounded-xl border border-[var(--glass-border)] bg-[rgba(255,255,255,0.03)] p-3">
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="font-semibold">智能歌单{draft.target_playlist_id ? "追加" : ""}草稿</span>
      <span className="text-[var(--color-primary)]">{status}</span>
      <span className="text-[10px] opacity-60">v{draft.revision}</span>
    </div>
    <div className="my-2 flex gap-2">
      <input aria-label="草稿歌单名称" value={name} maxLength={100} disabled={!editable || Boolean(draft.target_playlist_id)} onChange={(event) => setName(event.target.value)} className="min-w-0 flex-1 rounded border border-[var(--glass-border)] bg-transparent px-2 py-1 text-xs disabled:opacity-60" />
      {!draft.target_playlist_id && <button className={buttonClass} disabled={!editable || !name.trim() || name.trim() === draft.name} onClick={() => void run("rename", { name: name.trim() })}>改名</button>}
    </div>
    <p className="my-2 text-[10px] opacity-70">{draft.items.length} 首 · 本地 {draft.items.length - remoteCount} / 联网 {remoteCount}{draft.target_playlist_id ? " · 确认后追加到已有歌单" : ""}</p>
    {draft.warnings.map((warning) => <p key={warning.code} className="my-1 text-[10px] text-amber-200/80">{warning.message}</p>)}
    <ol className="m-0 max-h-80 list-none overflow-y-auto p-0">
      {draft.items.map((item, index) => {
        const remote = item.track.source_type === "bilibili";
        const downloaded = item.track.bvid ? itemByBvid.get(item.track.bvid) : undefined;
        const ownDownload = downloaded?.job_id === jobId ? downloaded : undefined;
        const canonical = draft.local_tracks?.[item.track.id] || (ownDownload?.status === "downloaded" ? ownDownload.result.track as Track | undefined : undefined);
        return <li key={item.item_id} className="border-t border-[var(--glass-border)] py-2">
          <p className="m-0 break-words text-xs">{index + 1}. {item.track.title} · {item.track.author || "歌手待核实"}</p>
          {remote && <p className="my-1 break-words text-[10px] opacity-60">来源：{item.track.video_title || item.track.title}{item.track.uploader ? ` · 上传者：${item.track.uploader}` : ""}</p>}
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <span className="mr-auto text-[10px] text-[var(--color-primary)]">{canonical ? "本地 · 可试听" : !remote ? "本地文件已不可用" : ownDownload?.status === "failed" ? "下载失败，可在下载任务中重试" : ownDownload?.status === "cancelled" ? "下载已取消" : ownDownload ? `下载中 ${Math.round(ownDownload.progress)}%` : draft.status === "saved" ? "请查看下载任务" : "联网候选 · 确认后下载"}</span>
            {canonical && <button className={buttonClass} onClick={() => void audition(canonical)}>试听</button>}
            {remote && item.track.bvid && <a className={buttonClass} target="_blank" rel="noopener noreferrer" href={`https://www.bilibili.com/video/${item.track.bvid}`}>查看来源</a>}
            {draft.status === "draft" && <>
              <button className={buttonClass} disabled={!editable || index === 0} aria-label={`上移第${index + 1}首`} onClick={() => move(index, -1)}>↑</button>
              <button className={buttonClass} disabled={!editable || index === draft.items.length - 1} aria-label={`下移第${index + 1}首`} onClick={() => move(index, 1)}>↓</button>
              <button className={buttonClass} disabled={!editable} onClick={() => void run("replace", { item_ids: [item.item_id] })}>替换</button>
              <button className={buttonClass} disabled={!editable} onClick={() => void run("remove", { item_ids: [item.item_id] })}>移除</button>
            </>}
          </div>
        </li>;
      })}
    </ol>
    {draft.status === "draft" && <div className="mt-2 flex flex-wrap gap-2">
      <button className={buttonClass} disabled={!editable || draft.items.length >= 50} onClick={() => void run("add")}>再加一首</button>
      <button className={buttonClass} disabled={!editable} onClick={() => void run("cancel")}>取消草稿</button>
      <span className="text-[10px] opacity-60">也可以继续对话调整这份草稿。</span>
    </div>}
    {["draft", "confirming"].includes(draft.status) && <button disabled={busy || !draft.items.length || !name.trim()} onClick={() => void confirm()} className="mt-3 w-full rounded-lg border border-[var(--color-primary)] px-3 py-2 text-xs text-[var(--color-primary)] disabled:opacity-40">
      {busy ? "正在处理…" : draft.status === "confirming" ? "重试保存同一份草稿" : `确认添加到歌单${remoteCount ? ` · 下载 ${remoteCount} 首联网歌曲` : ""}`}
    </button>}
    {draft.status === "saved" && <p className="mb-0 mt-2 text-[10px]">已保存到《{draft.receipt?.name || draft.name}》，不会自动切换播放。{jobId ? "联网歌曲下载成功后加入；知识库在后台同步。" : ""}</p>}
    {(error || draft.last_error) && <p role="alert" className="mb-0 mt-2 text-xs text-rose-300">{error || draft.last_error}</p>}
  </section>;
}
