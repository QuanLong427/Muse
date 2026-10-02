"use client";

import { useDownloads } from "@/app/context/DownloadContext";

const statusLabel: Record<string, string> = {
  queued: "等待中",
  running: "下载中",
  cancel_requested: "正在取消",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
};

export function DownloadsPanel() {
  const { jobs, loading, error, cancelJob, retryJob } = useDownloads();

  return (
    <section aria-label="下载任务" className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-[var(--glass-border)] bg-[rgba(255,255,255,0.03)]">
      <header className="flex items-center justify-between border-b border-[var(--glass-border)] px-4 py-3">
        <h2 className="m-0 text-xs font-medium uppercase tracking-wider text-[var(--color-on-surface-muted)]">DOWNLOADS</h2>
        <span className="text-[10px] text-[var(--color-on-surface-muted)]">{jobs.length} TASKS</span>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading && !jobs.length ? (
          <p className="p-4 text-sm text-[var(--color-on-surface-muted)]">正在读取下载任务…</p>
        ) : !jobs.length ? (
          <p className="p-4 text-sm text-[var(--color-on-surface-muted)]">暂无下载任务</p>
        ) : (
          jobs.map((job) => {
            const active = ["queued", "running", "cancel_requested"].includes(job.status);
            const retryable = ["failed", "cancelled", "partial"].includes(job.status);
            return (
              <article key={job.id} className="border-b border-[var(--glass-border)] p-3 last:border-b-0">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="m-0 truncate text-xs text-[var(--color-on-surface)]">
                      {job.items.map((item) => item.title || item.video_title || item.bvid).join("、")}
                    </p>
                    <p className="m-0 mt-1 text-[10px] text-[var(--color-on-surface-muted)]">
                      {statusLabel[job.status] ?? job.status} · {job.completed_items}/{job.total_items}
                    </p>
                  </div>
                  {active && job.status !== "cancel_requested" ? (
                    <button type="button" onClick={() => void cancelJob(job.id)} className="rounded-full border border-[var(--glass-border)] px-2 py-0.5 text-[10px] text-[var(--color-on-surface-muted)]">CANCEL</button>
                  ) : retryable ? (
                    <button type="button" onClick={() => void retryJob(job.id)} className="rounded-full border border-[rgba(129,140,248,0.3)] px-2 py-0.5 text-[10px] text-[var(--color-primary)]">RETRY</button>
                  ) : null}
                </div>
                <div className="h-1 overflow-hidden rounded-full bg-[rgba(255,255,255,0.08)]">
                  <div className="h-full rounded-full bg-[var(--color-primary)] transition-[width] duration-300" style={{ width: `${Math.max(0, Math.min(job.progress, 100))}%` }} />
                </div>
                {job.last_error ? <p className="mb-0 mt-2 text-[10px] leading-relaxed text-rose-300">{job.last_error}</p> : null}
              </article>
            );
          })
        )}
      </div>
      {error ? <p className="m-0 border-t border-[var(--glass-border)] px-3 py-2 text-[10px] text-rose-300">{error}</p> : null}
    </section>
  );
}
