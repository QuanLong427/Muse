"use client";

import { apiUrl } from "@/app/lib/api";
import { useCallback, useEffect, useRef, useState } from "react";

export type VoiceInputStatus = "idle" | "recording" | "transcribing";

const MAX_RECORDING_MS = 60_000;

function preferredMimeType(): string | undefined {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type));
}

async function responseError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as Record<string, unknown>;
    const detail = payload.detail || payload.error;
    if (typeof detail === "string") return detail;
  } catch {
    // Fall back to the status text below.
  }
  return response.statusText || `HTTP ${response.status}`;
}

export function useVoiceRecorder(onTranscript: (text: string) => void) {
  const [supported, setSupported] = useState(false);
  const [status, setStatus] = useState<VoiceInputStatus>("idle");
  const [error, setError] = useState("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onTranscriptRef = useRef(onTranscript);

  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  useEffect(() => {
    setSupported(
      typeof MediaRecorder !== "undefined" &&
        Boolean(navigator.mediaDevices?.getUserMedia)
    );
  }, []);

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recorderRef.current = null;
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = null;
  }, []);

  const transcribe = useCallback(async (blob: Blob) => {
    if (!blob.size) {
      setStatus("idle");
      setError("没有录到声音，请重试");
      return;
    }
    setStatus("transcribing");
    try {
      const response = await fetch(apiUrl("/api/voice/transcribe"), {
        method: "POST",
        headers: { "Content-Type": blob.type || "application/octet-stream" },
        body: blob,
      });
      if (!response.ok) throw new Error(await responseError(response));
      const payload = (await response.json()) as { text?: string };
      const text = payload.text?.trim();
      if (!text) throw new Error("没有识别到有效语音");
      setError("");
      onTranscriptRef.current(text);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "语音识别失败");
    } finally {
      setStatus("idle");
    }
  }, []);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
  }, []);

  const start = useCallback(async () => {
    if (!supported || status !== "idle") return;
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;
      chunksRef.current = [];
      const mimeType = preferredMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunksRef.current.push(event.data);
      };
      recorder.onerror = () => {
        setError("录音失败，请检查麦克风设备");
        setStatus("idle");
        releaseStream();
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || mimeType || "application/octet-stream",
        });
        releaseStream();
        void transcribe(blob);
      };
      recorder.start(250);
      setStatus("recording");
      timeoutRef.current = setTimeout(stop, MAX_RECORDING_MS);
    } catch (reason) {
      releaseStream();
      setStatus("idle");
      if (reason instanceof DOMException && reason.name === "NotAllowedError") {
        setError("未获得麦克风权限");
      } else {
        setError(reason instanceof Error ? reason.message : "无法启动录音");
      }
    }
  }, [releaseStream, status, stop, supported, transcribe]);

  const toggle = useCallback(() => {
    if (status === "recording") stop();
    else if (status === "idle") void start();
  }, [start, status, stop]);

  useEffect(
    () => () => {
      const recorder = recorderRef.current;
      recorder?.stream.getTracks().forEach((track) => track.stop());
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    },
    []
  );

  return { supported, status, error, toggle };
}
