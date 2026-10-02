"use client";

import type {
  DownloadJob,
  DownloadJobItem,
  DownloadRequestTrack,
} from "@/app/lib/types";
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

type DownloadContextValue = {
  jobs: DownloadJob[];
  loading: boolean;
  error: string;
  queueDownload: (tracks: DownloadRequestTrack[]) => Promise<DownloadJob | null>;
  cancelJob: (jobId: string) => Promise<void>;
  retryJob: (jobId: string) => Promise<DownloadJob | null>;
  registerJob: (job: DownloadJob) => void;
  refresh: () => Promise<void>;
  itemByBvid: Map<string, DownloadJobItem & { job_id: string }>;
};

const DownloadContext = createContext<DownloadContextValue | null>(null);
const ACTIVE = new Set(["queued", "running", "cancel_requested"]);

function currentUserId() {
  return window.localStorage.getItem("musicer.user-id") || "local";
}

function errorMessage(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object") {
    const detail = (payload as Record<string, unknown>).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return fallback;
}

export function DownloadProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<DownloadJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const registerJob = useCallback((job: DownloadJob) => {
    setJobs((previous) => [job, ...previous.filter((item) => item.id !== job.id)]);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const params = new URLSearchParams({ user_id: currentUserId(), limit: "50" });
      const response = await fetch(apiUrl(`/api/download-jobs?${params.toString()}`), {
        cache: "no-store",
      });
      const payload = (await response.json()) as { jobs?: DownloadJob[]; detail?: string };
      if (!response.ok) throw new Error(errorMessage(payload, "无法读取下载任务"));
      setJobs(Array.isArray(payload.jobs) ? payload.jobs : []);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取下载任务");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const hasActiveJobs = jobs.some((job) => ACTIVE.has(job.status));
  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [hasActiveJobs, refresh]);

  const queueDownload = useCallback(
    async (tracks: DownloadRequestTrack[]) => {
      const unique = Array.from(
        new Map(tracks.filter((track) => track.bvid).map((track) => [track.bvid, track])).values()
      );
      if (!unique.length) return null;
      try {
        const response = await fetch(apiUrl("/api/download-jobs"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            user_id: currentUserId(),
            items: unique.map((track) => ({
              bvid: track.bvid,
              url: `https://www.bilibili.com/video/${track.bvid}`,
              title: track.title || "",
              artist: track.author || "",
              uploader: track.uploader || "",
              video_title: track.videoTitle || "",
            })),
          }),
        });
        const payload = (await response.json()) as { job?: DownloadJob; detail?: string };
        if (!response.ok || !payload.job) {
          throw new Error(errorMessage(payload, "创建下载任务失败"));
        }
        registerJob(payload.job);
        setError("");
        return payload.job;
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "创建下载任务失败");
        return null;
      }
    },
    [registerJob]
  );

  const cancelJob = useCallback(
    async (jobId: string) => {
      const response = await fetch(
        apiUrl(`/api/download-jobs/${encodeURIComponent(jobId)}/cancel`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user_id: currentUserId() }),
        }
      );
      const payload = (await response.json()) as DownloadJob & { detail?: string };
      if (!response.ok) {
        const message = errorMessage(payload, "取消下载任务失败");
        setError(message);
        throw new Error(message);
      }
      registerJob(payload);
    },
    [registerJob]
  );

  const retryJob = useCallback(
    async (jobId: string) => {
      const response = await fetch(
        apiUrl(`/api/download-jobs/${encodeURIComponent(jobId)}/retry`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user_id: currentUserId() }),
        }
      );
      const payload = (await response.json()) as { job?: DownloadJob; detail?: string };
      if (!response.ok || !payload.job) {
        const message = errorMessage(payload, "重试下载任务失败");
        setError(message);
        return null;
      }
      registerJob(payload.job);
      return payload.job;
    },
    [registerJob]
  );

  const itemByBvid = useMemo(() => {
    const result = new Map<string, DownloadJobItem & { job_id: string }>();
    for (const job of jobs) {
      for (const item of job.items) {
        if (!result.has(item.bvid)) result.set(item.bvid, { ...item, job_id: job.id });
      }
    }
    return result;
  }, [jobs]);

  const value = useMemo(
    () => ({
      jobs,
      loading,
      error,
      queueDownload,
      cancelJob,
      retryJob,
      registerJob,
      refresh,
      itemByBvid,
    }),
    [jobs, loading, error, queueDownload, cancelJob, retryJob, registerJob, refresh, itemByBvid]
  );

  return <DownloadContext.Provider value={value}>{children}</DownloadContext.Provider>;
}

export function useDownloads() {
  const value = useContext(DownloadContext);
  if (!value) throw new Error("useDownloads must be used within DownloadProvider");
  return value;
}
