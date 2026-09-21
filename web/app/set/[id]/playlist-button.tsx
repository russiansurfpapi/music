"use client";

import { useState } from "react";

export function CreatePlaylistButton({
  setId,
  defaultName,
  trackCount,
}: {
  setId: string;
  defaultName: string;
  trackCount: number;
}) {
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [result, setResult] = useState<{ url?: string; error?: string; trackCount?: number } | null>(null);

  async function handleCreate() {
    setState("loading");
    try {
      const res = await fetch("/api/playlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ setId, name: defaultName }),
      });
      const data = await res.json();
      if (data.error) {
        setState("error");
        setResult(data);
      } else {
        setState("done");
        setResult(data);
      }
    } catch (e) {
      setState("error");
      setResult({ error: "Network error" });
    }
  }

  if (state === "done" && result?.url) {
    return (
      <div className="flex items-center gap-3">
        <a
          href={result.url}
          target="_blank"
          rel="noopener"
          className="text-sm bg-green-700 hover:bg-green-600 text-white rounded-lg px-4 py-2 transition"
        >
          Open in Spotify ({result.trackCount} tracks)
        </a>
        <span className="text-xs text-green-400">Playlist created</span>
      </div>
    );
  }

  if (state === "error") {
    return (
      <div className="flex items-center gap-3">
        <button
          onClick={handleCreate}
          className="text-sm bg-red-800 hover:bg-red-700 text-white rounded-lg px-4 py-2 transition"
        >
          Retry
        </button>
        <span className="text-xs text-red-400">{result?.error}</span>
      </div>
    );
  }

  return (
    <button
      onClick={handleCreate}
      disabled={state === "loading" || trackCount === 0}
      className="text-sm bg-green-700 hover:bg-green-600 disabled:bg-zinc-700 disabled:text-zinc-500 text-white rounded-lg px-4 py-2 transition"
    >
      {state === "loading"
        ? "Creating..."
        : trackCount > 0
          ? `Create Spotify Playlist (${trackCount} tracks)`
          : "No resolved tracks"}
    </button>
  );
}
