import { BACKEND_URL } from "@/app/lib/config";

export const maxDuration = 120;
export const dynamic = "force-dynamic";

const MAX_AUDIO_BYTES = 10 * 1024 * 1024;

async function readLimitedAudio(request: Request): Promise<Uint8Array | null> {
  if (!request.body) return new Uint8Array();
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_AUDIO_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const merged = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return merged;
}

export async function POST(request: Request) {
  const contentType = request.headers.get("content-type") || "application/octet-stream";
  const declaredLength = Number(request.headers.get("content-length") || "0");
  if (Number.isFinite(declaredLength) && declaredLength > MAX_AUDIO_BYTES) {
    return Response.json({ error: "录音超过 10MB 限制" }, { status: 413 });
  }
  const audio = await readLimitedAudio(request);
  if (audio === null) {
    return Response.json({ error: "录音超过 10MB 限制" }, { status: 413 });
  }
  if (!audio.byteLength) {
    return Response.json({ error: "录音内容为空" }, { status: 400 });
  }

  try {
    const response = await fetch(`${BACKEND_URL}/api/voice/transcribe`, {
      method: "POST",
      headers: { "Content-Type": contentType },
      body: audio.buffer as ArrayBuffer,
      signal: AbortSignal.timeout(120_000),
    });
    const body = await response.text();
    return new Response(body, {
      status: response.status,
      headers: { "Content-Type": response.headers.get("content-type") || "application/json" },
    });
  } catch (error) {
    return Response.json({ error: String(error) }, { status: 502 });
  }
}
