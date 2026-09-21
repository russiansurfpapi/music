"use client";

import { useState } from "react";
import Link from "next/link";

type Tab = "tracklist-url" | "youtube";

export default function IngestPage() {
  const [tab, setTab] = useState<Tab>("tracklist-url");

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div>
        <Link href="/" className="text-xs text-zinc-500 hover:text-zinc-300">
          ← Dashboard
        </Link>
        <h1 className="text-xl font-bold text-white mt-2">Ingest</h1>
        <p className="text-sm text-zinc-400 mt-1">
          Scrape tracklists from 1001Tracklists or fingerprint YouTube DJ sets.
        </p>
      </div>

      <div className="flex gap-1 bg-zinc-900 rounded-lg p-1">
        {[
          { key: "tracklist-url" as Tab, label: "1001Tracklists" },
          { key: "youtube" as Tab, label: "YouTube Fingerprint" },
        ].map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex-1 text-sm py-2 px-3 rounded-md transition ${
              tab === t.key ? "bg-zinc-700 text-white" : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "tracklist-url" && <TracklistScraper />}
      {tab === "youtube" && <YoutubeFingerprint />}
    </div>
  );
}

function TracklistScraper() {
  const [url, setUrl] = useState("");
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [tracks, setTracks] = useState<{ artist: string; title: string }[]>([]);
  const [error, setError] = useState("");
  const [playlistState, setPlaylistState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [playlistResult, setPlaylistResult] = useState<any>(null);
  const [playlistName, setPlaylistName] = useState("");

  async function handleScrape() {
    if (!url.trim()) return;
    setState("loading");
    setTracks([]);
    setError("");
    try {
      const res = await fetch("/api/scrape", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      const data = await res.json();
      if (data.error) {
        setState("error");
        setError(data.error);
      } else {
        setState("done");
        setTracks(data.tracks);
      }
    } catch {
      setState("error");
      setError("Network error");
    }
  }

  async function handleCreatePlaylist() {
    if (tracks.length === 0) return;
    setPlaylistState("loading");
    const lines = tracks.map((t) => `${t.artist} - ${t.title}`);
    try {
      const res = await fetch("/api/playlist/from-tracklist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lines, name: playlistName || "1001TL Scrape" }),
      });
      const data = await res.json();
      if (data.error) {
        setPlaylistState("error");
        setPlaylistResult(data);
      } else {
        setPlaylistState("done");
        setPlaylistResult(data);
      }
    } catch {
      setPlaylistState("error");
      setPlaylistResult({ error: "Network error" });
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-400">
        Paste a 1001Tracklists URL. The app fetches the page via Bright Data and extracts the tracklist.
      </p>
      <div className="flex gap-2">
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.1001tracklists.com/tracklist/..."
          className="flex-1 bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2.5 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
        />
        <button
          onClick={handleScrape}
          disabled={state === "loading" || !url.trim()}
          className="text-sm bg-blue-700 hover:bg-blue-600 disabled:bg-zinc-700 disabled:text-zinc-500 text-white rounded-lg px-5 py-2.5 transition whitespace-nowrap"
        >
          {state === "loading" ? "Scraping..." : "Scrape"}
        </button>
      </div>

      {state === "error" && <div className="text-sm text-red-400">{error}</div>}

      {tracks.length > 0 && (
        <div className="space-y-4">
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
            <div className="px-4 py-2 border-b border-zinc-800 flex items-center justify-between">
              <span className="text-sm font-medium text-zinc-300">{tracks.length} tracks found</span>
            </div>
            <div className="max-h-80 overflow-y-auto">
              <table className="w-full text-sm">
                <tbody>
                  {tracks.map((t, i) => (
                    <tr key={i} className="border-b border-zinc-800/50">
                      <td className="px-4 py-1.5 text-zinc-600 w-8">{i + 1}</td>
                      <td className="px-2 py-1.5 text-zinc-200">{t.artist}</td>
                      <td className="px-2 py-1.5 text-zinc-400">{t.title}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="flex gap-3 items-end">
            <div className="flex-1">
              <label className="text-xs text-zinc-500 block mb-1">Playlist name</label>
              <input
                type="text"
                value={playlistName}
                onChange={(e) => setPlaylistName(e.target.value)}
                placeholder="1001TL Scrape"
                className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
              />
            </div>
            <button
              onClick={handleCreatePlaylist}
              disabled={playlistState === "loading"}
              className="text-sm bg-green-700 hover:bg-green-600 disabled:bg-zinc-700 text-white rounded-lg px-5 py-2.5 transition whitespace-nowrap"
            >
              {playlistState === "loading" ? "Creating..." : "Create Spotify Playlist"}
            </button>
          </div>

          {playlistState === "done" && playlistResult?.url && (
            <a href={playlistResult.url} target="_blank" rel="noopener"
              className="inline-block text-sm text-green-400 hover:text-green-300">
              Open playlist in Spotify ({playlistResult.trackCount} tracks) →
            </a>
          )}
          {playlistState === "error" && (
            <div className="text-sm text-red-400">{playlistResult?.error}</div>
          )}
        </div>
      )}
    </div>
  );
}

function YoutubeFingerprint() {
  const [url, setUrl] = useState("");
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [tracks, setTracks] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [meta, setMeta] = useState<any>(null);
  const [playlistState, setPlaylistState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [playlistResult, setPlaylistResult] = useState<any>(null);
  const [playlistName, setPlaylistName] = useState("");

  async function handleFingerprint() {
    if (!url.trim()) return;
    setState("loading");
    setTracks([]);
    setError("");
    try {
      const res = await fetch("/api/fingerprint", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      const data = await res.json();
      if (data.error) {
        setState("error");
        setError(data.error);
      } else {
        setState("done");
        setTracks(data.tracks || []);
        setMeta(data);
      }
    } catch {
      setState("error");
      setError("Network error or timeout");
    }
  }

  async function handleCreatePlaylist() {
    if (tracks.length === 0) return;
    setPlaylistState("loading");
    const lines = tracks.map((t: any) => `${t.artist} - ${t.title}`);
    try {
      const res = await fetch("/api/playlist/from-tracklist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lines, name: playlistName || "YouTube Fingerprint" }),
      });
      const data = await res.json();
      if (data.error) { setPlaylistState("error"); setPlaylistResult(data); }
      else { setPlaylistState("done"); setPlaylistResult(data); }
    } catch {
      setPlaylistState("error");
      setPlaylistResult({ error: "Network error" });
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-400">
        Paste a YouTube DJ set URL. The app downloads the audio, fingerprints it with ACRCloud,
        and identifies the tracks. This can take a few minutes for long sets.
      </p>
      <div className="flex gap-2">
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.youtube.com/watch?v=..."
          className="flex-1 bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2.5 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
        />
        <button
          onClick={handleFingerprint}
          disabled={state === "loading" || !url.trim()}
          className="text-sm bg-red-700 hover:bg-red-600 disabled:bg-zinc-700 disabled:text-zinc-500 text-white rounded-lg px-5 py-2.5 transition whitespace-nowrap"
        >
          {state === "loading" ? "Fingerprinting..." : "Fingerprint"}
        </button>
      </div>

      {state === "loading" && (
        <div className="text-sm text-zinc-500 animate-pulse">
          Downloading audio and identifying tracks... this may take 2-5 minutes.
        </div>
      )}

      {state === "error" && <div className="text-sm text-red-400">{error}</div>}

      {tracks.length > 0 && (
        <div className="space-y-4">
          {meta && (
            <div className="text-xs text-zinc-500">
              {meta.uniqueTracks} unique tracks from {meta.totalIdentified} identifications
            </div>
          )}

          <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
            <div className="max-h-80 overflow-y-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-zinc-500 border-b border-zinc-800">
                    <th className="px-4 py-2 text-left w-16">Time</th>
                    <th className="px-2 py-2 text-left">Artist</th>
                    <th className="px-2 py-2 text-left">Title</th>
                    <th className="px-2 py-2 text-right w-16">Conf</th>
                  </tr>
                </thead>
                <tbody>
                  {tracks.map((t: any, i: number) => (
                    <tr key={i} className="border-b border-zinc-800/50">
                      <td className="px-4 py-1.5 text-zinc-500 tabular-nums text-xs">
                        {Math.floor(t.timestamp / 60)}:{String(t.timestamp % 60).padStart(2, "0")}
                      </td>
                      <td className="px-2 py-1.5 text-zinc-200">{t.artist}</td>
                      <td className="px-2 py-1.5 text-zinc-400">{t.title}</td>
                      <td className="px-2 py-1.5 text-right text-zinc-500 text-xs">
                        {Math.round(t.confidence * 100)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="flex gap-3 items-end">
            <div className="flex-1">
              <label className="text-xs text-zinc-500 block mb-1">Playlist name</label>
              <input
                type="text"
                value={playlistName}
                onChange={(e) => setPlaylistName(e.target.value)}
                placeholder="YouTube Fingerprint"
                className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
              />
            </div>
            <button
              onClick={handleCreatePlaylist}
              disabled={playlistState === "loading"}
              className="text-sm bg-green-700 hover:bg-green-600 disabled:bg-zinc-700 text-white rounded-lg px-5 py-2.5 transition whitespace-nowrap"
            >
              {playlistState === "loading" ? "Creating..." : "Create Spotify Playlist"}
            </button>
          </div>

          {playlistState === "done" && playlistResult?.url && (
            <a href={playlistResult.url} target="_blank" rel="noopener"
              className="inline-block text-sm text-green-400 hover:text-green-300">
              Open playlist in Spotify ({playlistResult.trackCount} tracks) →
            </a>
          )}
          {playlistState === "error" && (
            <div className="text-sm text-red-400">{playlistResult?.error}</div>
          )}
        </div>
      )}
    </div>
  );
}
