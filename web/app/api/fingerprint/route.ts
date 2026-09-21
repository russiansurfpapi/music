import { NextRequest, NextResponse } from "next/server";
import crypto from "crypto";

export const maxDuration = 300;

function extractVideoId(url: string): string | null {
  const m = url.match(/(?:v=|youtu\.be\/|\/shorts\/)([A-Za-z0-9_-]{11})/);
  return m ? m[1] : null;
}

async function downloadAudio(youtubeUrl: string): Promise<ArrayBuffer | null> {
  // Try cobalt.tools API — open source YouTube audio downloader
  try {
    const res = await fetch("https://api.cobalt.tools/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({
        url: youtubeUrl,
        audioFormat: "wav",
        isAudioOnly: true,
        filenameStyle: "basic",
      }),
      signal: AbortSignal.timeout(30000),
    });

    if (!res.ok) {
      // Try alternative cobalt endpoint
      const res2 = await fetch("https://cobalt-api.kwiatekmiki.com/", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ url: youtubeUrl, audioFormat: "mp3", isAudioOnly: true }),
        signal: AbortSignal.timeout(30000),
      });
      if (!res2.ok) return null;
      const data2 = await res2.json();
      if (!data2.url) return null;
      const audio2 = await fetch(data2.url, { signal: AbortSignal.timeout(60000) });
      return audio2.ok ? audio2.arrayBuffer() : null;
    }

    const data = await res.json();
    if (!data.url) return null;

    const audioRes = await fetch(data.url, { signal: AbortSignal.timeout(120000) });
    return audioRes.ok ? audioRes.arrayBuffer() : null;
  } catch {
    return null;
  }
}

async function acrIdentifyChunk(
  audioData: ArrayBuffer,
  host: string,
  key: string,
  secret: string
): Promise<{ artist: string; title: string; confidence: number; spotifyId?: string } | null> {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const stringToSign = `POST\n/v1/identify\n${key}\naudio\n1\n${timestamp}`;
  const signature = crypto
    .createHmac("sha1", secret)
    .update(stringToSign)
    .digest("base64");

  const formData = new FormData();
  formData.append("access_key", key);
  formData.append("sample_bytes", String(audioData.byteLength));
  formData.append("timestamp", timestamp);
  formData.append("signature", signature);
  formData.append("data_type", "audio");
  formData.append("signature_version", "1");
  formData.append("sample", new Blob([audioData]), "chunk.wav");

  try {
    const res = await fetch(`https://${host}/v1/identify`, {
      method: "POST",
      body: formData,
      signal: AbortSignal.timeout(15000),
    });
    const resp = await res.json();
    const status = resp?.status?.code;
    if (status === 0 && resp?.metadata?.music?.length > 0) {
      const m = resp.metadata.music[0];
      const artist = (m.artists || []).map((a: any) => a.name).join(", ");
      const title = m.title || "";
      const score = (m.score || 0) / 100;
      const spotifyId = m.external_metadata?.spotify?.track?.id || undefined;
      return { artist, title, confidence: score, spotifyId };
    }
  } catch {}
  return null;
}

function dedupeConsecutive(
  results: { t: number; artist: string; title: string; confidence: number; spotifyId?: string }[]
): { artist: string; title: string; timestamp: number; confidence: number; spotifyId?: string }[] {
  const deduped: typeof results extends (infer T)[] ? (T & { timestamp: number })[] : never = [];
  let prevKey = "";
  for (const r of results) {
    const key = `${r.artist.toLowerCase()}||${r.title.toLowerCase()}`;
    if (key !== prevKey) {
      deduped.push({ ...r, timestamp: r.t });
      prevKey = key;
    }
  }
  return deduped;
}

export async function POST(request: NextRequest) {
  const { url } = await request.json();
  if (!url) {
    return NextResponse.json({ error: "No YouTube URL provided" }, { status: 400 });
  }

  const videoId = extractVideoId(url);
  if (!videoId) {
    return NextResponse.json({ error: "Invalid YouTube URL" }, { status: 400 });
  }

  const acrHost = process.env.ACR_HOST;
  const acrKey = process.env.ACR_KEY;
  const acrSecret = process.env.ACR_SECRET;
  if (!acrHost || !acrKey || !acrSecret) {
    return NextResponse.json({ error: "ACRCloud credentials not configured" }, { status: 500 });
  }

  // Download audio
  const audioBuffer = await downloadAudio(url);
  if (!audioBuffer) {
    return NextResponse.json({
      error: "Couldn't download audio from YouTube. The video may be restricted or the download service is unavailable. Try the Tracklist tab to paste tracks manually.",
    }, { status: 502 });
  }

  // Chunk the audio and identify
  // Audio is likely MP3/WAV. ACRCloud accepts raw audio samples.
  // We'll send overlapping 20-second chunks every 45 seconds.
  const CHUNK_DURATION = 20; // seconds
  const STEP = 45; // seconds between chunk starts
  const SAMPLE_RATE = 44100; // assumed
  const BYTES_PER_SECOND = SAMPLE_RATE * 2; // 16-bit mono

  // For raw audio, we estimate positions. ACRCloud handles various formats.
  const totalBytes = audioBuffer.byteLength;
  const estimatedDuration = totalBytes / BYTES_PER_SECOND;
  const chunkBytes = CHUNK_DURATION * BYTES_PER_SECOND;
  const stepBytes = STEP * BYTES_PER_SECOND;

  const results: { t: number; artist: string; title: string; confidence: number; spotifyId?: string }[] = [];

  for (let offset = 0; offset < totalBytes - chunkBytes; offset += stepBytes) {
    const t = Math.floor(offset / BYTES_PER_SECOND);
    const chunk = audioBuffer.slice(offset, offset + chunkBytes);

    const match = await acrIdentifyChunk(chunk, acrHost, acrKey, acrSecret);
    if (match) {
      results.push({ t, ...match });
    }

    // Brief pause between API calls
    await new Promise((r) => setTimeout(r, 800));
  }

  if (results.length === 0) {
    return NextResponse.json({
      error: "No tracks identified. The audio may be too short, heavily mixed, or the download format wasn't recognized by ACRCloud.",
      estimatedDuration: Math.round(estimatedDuration),
    }, { status: 404 });
  }

  const tracks = dedupeConsecutive(results);

  return NextResponse.json({
    videoId,
    tracks,
    totalIdentified: results.length,
    uniqueTracks: tracks.length,
    estimatedDuration: Math.round(estimatedDuration),
  });
}
