"use client";

import { useMemo, useState } from "react";
import { Label } from "@/app/components/atoms/Label";
import { usePlayer } from "@/app/context/PlayerContext";
import { usePlaylists } from "@/app/context/PlaylistContext";
import { useScenario } from "@/app/context/ScenarioContext";
import { useDownloads } from "@/app/context/DownloadContext";
import { apiUrl } from "@/app/lib/api";
import type { SmartPlaylistSaveResult, SmartPlaylistPreview, Track } from "@/app/lib/types";

function splitValues(value: string): string[] {
  return value
    .split(/[,，、;；\n]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function responseError(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export function SmartPlaylistPanel() {
  const { currentScenario } = useScenario();
  const { refresh: refreshPlaylists } = usePlaylists();
  const { registerJob, itemByBvid } = useDownloads();
  const [sourcePolicy, setSourcePolicy] = useState("balanced");
  const {
    replaceCollectionWithUndo,
    undoCollectionReplacement,
    canUndoCollectionReplacement,
  } = usePlayer();
  const [count, setCount] = useState("3");
  const [duration, setDuration] = useState("");
  const [genre, setGenre] = useState("");
  const [mood, setMood] = useState("");
  const [language, setLanguage] = useState("");
  const [includeArtists, setIncludeArtists] = useState("");
  const [excludeArtists, setExcludeArtists] = useState("");
  const [excludeVersions, setExcludeVersions] = useState<string[]>([]);
  const [energyCurve, setEnergyCurve] = useState("");
  const [preview, setPreview] = useState<SmartPlaylistPreview | null>(null);
  const [playlistName, setPlaylistName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const playableTracks = useMemo(() => {
    if (!preview) return [];
    return preview.recommendations.flatMap((item) => {
      if (!("source_type" in item.track)) return [item.track as Track];
      const downloaded = itemByBvid.get(item.track.bvid);
      const track = downloaded?.result.track;
      return downloaded?.status === "downloaded" && track && typeof track === "object"
        ? [track as Track] : [];
    });
  }, [preview, itemByBvid]);

  const durationLabel = useMemo(() => {
    if (!preview) return "";
    const minutes = Math.round(preview.estimated_duration_seconds / 60);
    return `${preview.duration_is_estimated ? "约 " : ""}${minutes} 分钟`;
  }, [preview]);

  const toggleVersion = (value: string) => {
    setExcludeVersions((previous) =>
      previous.includes(value)
        ? previous.filter((item) => item !== value)
        : [...previous, value]
    );
  };

  const generate = async (loadAfterGenerate = false) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const parsedCount = Number.parseInt(count, 10);
      const parsedDuration = Number.parseFloat(duration);
      const response = await fetch(apiUrl("/api/smart-playlists/preview"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: "local",
          scenario: currentScenario,
          source_policy: sourcePolicy,
          count: Number.isFinite(parsedCount) && parsedCount > 0 ? parsedCount : null,
          duration_minutes:
            Number.isFinite(parsedDuration) && parsedDuration > 0 ? parsedDuration : null,
          include_artists: splitValues(includeArtists),
          exclude_artists: splitValues(excludeArtists),
          genre: genre.trim(),
          mood: mood.trim(),
          language: language.trim(),
          exclude_versions: excludeVersions,
          energy_curve: energyCurve,
        }),
      });
      const payload = (await response.json()) as SmartPlaylistPreview;
      if (!response.ok) throw new Error(responseError(payload, "智能歌单生成失败"));
      setPreview(payload);
      setPlaylistName(payload.suggested_name);
      if (!payload.result_count) {
        setNotice("没有找到符合当前强约束的歌曲。");
      } else if (loadAfterGenerate && payload.remote_candidates.length) {
        setNotice("预览含联网歌曲。添加为歌单时会自动下载，完成后可从我的歌单播放。");
      } else if (loadAfterGenerate && payload.batch_id && payload.warnings.length === 0) {
        replaceCollectionWithUndo(payload.tracks, "smart_playlist", payload.batch_id);
        setNotice("已生成并加载到播放详情；可以撤销恢复之前的播放内容。");
      } else if (loadAfterGenerate && payload.warnings.length > 0) {
        setNotice("存在未满足或无法核验的约束，已停在预览，没有修改当前播放内容。");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "智能歌单生成失败");
    } finally {
      setBusy(false);
    }
  };

  const startPlayback = () => {
    if (!preview?.batch_id || !playableTracks.length || playableTracks.length !== preview.result_count) return;
    replaceCollectionWithUndo(playableTracks, "smart_playlist", preview.batch_id);
    setNotice("已加载到播放详情并开始播放；可以撤销恢复之前的播放内容。");
  };

  const undo = () => {
    if (undoCollectionReplacement()) setNotice("已恢复智能歌单加载前的播放内容和进度。");
  };

  const save = async () => {
    if (!preview?.batch_id || !playlistName.trim()) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(
        apiUrl(`/api/smart-playlists/${encodeURIComponent(preview.batch_id)}/save`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user_id: "local", name: playlistName.trim() }),
        }
      );
      const payload = (await response.json()) as SmartPlaylistSaveResult;
      if (!response.ok) throw new Error(responseError(payload, "保存歌单失败"));
      await refreshPlaylists();
      if (payload.download_job) {
        registerJob(payload.download_job);
        const state = ["queued", "running"].includes(payload.download_job.status)
          ? "联网歌曲正在后台下载，成功后自动加入。"
          : payload.download_job.status === "completed" ? "联网歌曲已下载并加入。" : "联网下载尚未全部完成。";
        setNotice(`歌单“${payload.name}”已创建，${state}可在下载任务中查看进度、取消或重试。`);
      } else {
        setNotice(`已添加为歌单“${payload.name}”。`);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存歌单失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-[var(--glass-border)] bg-[rgba(255,255,255,0.04)]">
      <div className="border-b border-[var(--glass-border)] p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <Label size="md">SMART PLAYLIST</Label>
            <p className="mt-1 text-[10px] text-[var(--color-on-surface-muted)]">
              场景：{currentScenario} · 本地与联网混合预览 · 添加歌单会下载联网歌曲
            </p>
          </div>
          {canUndoCollectionReplacement && (
            <button type="button" onClick={undo} className="rounded-full border border-[var(--glass-border)] px-3 py-1 text-[10px] text-[var(--color-primary)]">
              UNDO LOAD
            </button>
          )}
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-[var(--color-on-surface-muted)]">歌曲来源</span>
            <select value={sourcePolicy} onChange={(event) => setSourcePolicy(event.target.value)} className="rounded border border-[var(--glass-border)] bg-[var(--color-surface)] px-2 py-1.5">
              <option value="balanced">本地 + 联网（约各 50%）</option>
              <option value="local">仅本地</option>
              <option value="cloud">仅联网</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-[var(--color-on-surface-muted)]">歌曲数量</span>
            <input value={count} onChange={(event) => setCount(event.target.value)} inputMode="numeric" placeholder="留空则按时长" className="rounded border border-[var(--glass-border)] bg-transparent px-2 py-1.5 outline-none" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-[var(--color-on-surface-muted)]">目标时长（分钟）</span>
            <input value={duration} onChange={(event) => setDuration(event.target.value)} inputMode="decimal" placeholder="例如 30" className="rounded border border-[var(--glass-border)] bg-transparent px-2 py-1.5 outline-none" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-[var(--color-on-surface-muted)]">指定歌手（逗号分隔）</span>
            <input value={includeArtists} onChange={(event) => setIncludeArtists(event.target.value)} placeholder="周杰伦, Coldplay" className="rounded border border-[var(--glass-border)] bg-transparent px-2 py-1.5 outline-none" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-[var(--color-on-surface-muted)]">排除歌手</span>
            <input value={excludeArtists} onChange={(event) => setExcludeArtists(event.target.value)} placeholder="不想听的歌手" className="rounded border border-[var(--glass-border)] bg-transparent px-2 py-1.5 outline-none" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-[var(--color-on-surface-muted)]">流派</span>
            <input value={genre} onChange={(event) => setGenre(event.target.value)} placeholder="摇滚 / 华语流行" className="rounded border border-[var(--glass-border)] bg-transparent px-2 py-1.5 outline-none" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-[var(--color-on-surface-muted)]">情绪</span>
            <input value={mood} onChange={(event) => setMood(event.target.value)} placeholder="轻松 / 专注" className="rounded border border-[var(--glass-border)] bg-transparent px-2 py-1.5 outline-none" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-[var(--color-on-surface-muted)]">语言</span>
            <input value={language} onChange={(event) => setLanguage(event.target.value)} placeholder="中文 / 英文" className="rounded border border-[var(--glass-border)] bg-transparent px-2 py-1.5 outline-none" />
          </label>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2 text-[10px] text-[var(--color-on-surface-muted)]">
          <span>排除版本：</span>
          {[["live", "LIVE"], ["cover", "COVER"], ["remix", "REMIX"], ["instrumental", "伴奏"]].map(([value, label]) => (
            <button key={value} type="button" aria-pressed={excludeVersions.includes(value)} onClick={() => toggleVersion(value)} className={`rounded-full border px-2 py-1 ${excludeVersions.includes(value) ? "border-[var(--color-primary)] text-[var(--color-primary)]" : "border-[var(--glass-border)]"}`}>
              {label}
            </button>
          ))}
          <select value={energyCurve} onChange={(event) => setEnergyCurve(event.target.value)} className="ml-auto rounded border border-[var(--glass-border)] bg-[var(--color-surface)] px-2 py-1 outline-none">
            <option value="">不限能量曲线</option>
            <option value="rising">逐渐提速</option>
            <option value="falling">逐渐舒缓</option>
            <option value="wave">波浪变化</option>
          </select>
          <button type="button" disabled={busy} onClick={() => void generate(false)} className="rounded-full border border-[var(--glass-border)] px-3 py-1.5 disabled:opacity-40">
            {busy ? "GENERATING…" : "GENERATE PREVIEW"}
          </button>
          <button type="button" disabled={busy} onClick={() => void generate(true)} className="rounded-full border border-[rgba(129,140,248,0.4)] bg-[rgba(129,140,248,0.14)] px-4 py-1.5 text-[var(--color-primary)] disabled:opacity-40">
            GENERATE &amp; PLAY
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {error && <p className="border-b border-[var(--glass-border)] p-3 text-xs text-[var(--color-error)]">{error}</p>}
        {notice && <p className="border-b border-[var(--glass-border)] p-3 text-xs text-[var(--color-primary)]">{notice}</p>}
        {!preview ? (
          <p className="p-4 text-sm text-[var(--color-on-surface-muted)]">填写必要约束后生成预览。场景偏好、近期行为和长期偏好会自动参与排序。</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2 border-b border-[var(--glass-border)] px-4 py-3">
              <span className="text-xs font-semibold">{preview.result_count} 首 · 本地 {preview.source_plan.actual.local} / 联网 {preview.source_plan.actual.cloud} · 总播放时长 {durationLabel}</span>
              <input value={playlistName} onChange={(event) => setPlaylistName(event.target.value)} className="ml-auto min-w-32 rounded border border-[var(--glass-border)] bg-transparent px-2 py-1 text-xs outline-none" aria-label="歌单名称" />
              <button type="button" disabled={busy || !playableTracks.length || playableTracks.length !== preview.result_count} onClick={startPlayback} className="rounded-full border border-[rgba(129,140,248,0.4)] px-3 py-1 text-[10px] text-[var(--color-primary)] disabled:opacity-40">PLAY PREVIEW</button>
              <button type="button" disabled={busy || !preview.batch_id || !playlistName.trim()} onClick={() => void save()} className="rounded-full border border-[var(--glass-border)] px-3 py-1 text-[10px] disabled:opacity-40">+ ADD{preview.remote_candidates.length ? " · 下载联网歌曲" : ""}</button>
            </div>
            {preview.warnings.map((warning) => (
              <p key={warning.code} className="border-b border-[rgba(251,191,36,0.15)] bg-[rgba(251,191,36,0.05)] px-4 py-2 text-[10px] text-amber-200/80">
                {warning.message}
              </p>
            ))}
            {preview.recommendations.map((item, index) => {
              const remote = "source_type" in item.track && item.track.source_type === "bilibili";
              const download = item.track.bvid ? itemByBvid.get(item.track.bvid) : undefined;
              const state = !remote ? "本地 · 可播放" : download?.status === "downloaded" ? "已下载到本地" : download?.status === "failed" ? "下载失败 · 可在下载任务中重试" : download?.status === "cancelled" ? "下载已取消" : download ? `下载中 ${Math.round(download.progress)}%` : "↓ DOWNLOAD · 添加歌单时下载";
              return (
              <div key={item.track.id} className="flex items-start gap-3 border-b border-[var(--glass-border)] px-4 py-2.5 last:border-b-0">
                <span className="w-5 pt-0.5 text-xs text-[var(--color-on-surface-muted)]">{index + 1}</span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{item.track.title}</p>
                  <p className="truncate text-[10px] text-[var(--color-on-surface-muted)]">{item.track.author || "未知歌手"} · {Math.round(item.duration_seconds / 60)} 分钟</p>
                  <p className="mt-1 text-[10px] text-[var(--color-primary)]">{state}</p>
                  <p className="mt-1 line-clamp-2 text-[10px] text-[var(--color-on-surface-muted)]">{item.reasons.map((reason) => reason.detail).join("；")}</p>
                </div>
              </div>
              );
            })}
          </>
        )}
      </div>
    </section>
  );
}
