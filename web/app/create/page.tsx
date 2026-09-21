"use client";

import { useState } from "react";
import Link from "next/link";

type Tab = "artists" | "tracklist" | "influences" | "festival";

export default function CreatePage() {
  const [tab, setTab] = useState<Tab>("artists");

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <Link href="/" className="text-xs text-zinc-500 hover:text-zinc-300">
          ← Dashboard
        </Link>
        <h1 className="text-xl font-bold text-white mt-2">Create Playlist</h1>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-zinc-900 rounded-lg p-1">
        {[
          { key: "artists" as Tab, label: "Artists" },
          { key: "tracklist" as Tab, label: "Tracklist" },
          { key: "influences" as Tab, label: "Influences" },
          { key: "festival" as Tab, label: "Festival" },
        ].map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex-1 text-sm py-2 px-3 rounded-md transition ${
              tab === t.key
                ? "bg-zinc-700 text-white"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "artists" && <ArtistSampler />}
      {tab === "tracklist" && <TracklistToPlaylist />}
      {tab === "influences" && <InfluenceDiscovery />}
      {tab === "festival" && <FestivalDiscovery />}
    </div>
  );
}

function ArtistSampler() {
  const [artists, setArtists] = useState("");
  const [playlistName, setPlaylistName] = useState("");
  const [tracksPerArtist, setTracksPerArtist] = useState(5);
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [result, setResult] = useState<any>(null);
  const [progress, setProgress] = useState("");

  async function handleCreate() {
    if (!artists.trim()) return;
    setState("loading");
    setProgress("Searching artists...");
    try {
      const res = await fetch("/api/playlist/from-artists", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          artists: artists.split(/[,\n]/).map((a) => a.trim()).filter(Boolean),
          name: playlistName || undefined,
          tracksPerArtist,
        }),
      });
      const data = await res.json();
      if (data.error) {
        setState("error");
        setResult(data);
      } else {
        setState("done");
        setResult(data);
      }
    } catch {
      setState("error");
      setResult({ error: "Network error" });
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-400">
        Enter artist names to create a sampler playlist with their top tracks.
      </p>
      <textarea
        value={artists}
        onChange={(e) => setArtists(e.target.value)}
        placeholder={"Duke Dumont\nDJ Koze\nNina Kraviz\nJamie xx"}
        rows={6}
        className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-3 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500 font-mono"
      />
      <div className="flex gap-4 items-end">
        <div className="flex-1">
          <label className="text-xs text-zinc-500 block mb-1">Playlist name (optional)</label>
          <input
            type="text"
            value={playlistName}
            onChange={(e) => setPlaylistName(e.target.value)}
            placeholder="Escuchar"
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
          />
        </div>
        <div>
          <label className="text-xs text-zinc-500 block mb-1">Tracks per artist</label>
          <select
            value={tracksPerArtist}
            onChange={(e) => setTracksPerArtist(Number(e.target.value))}
            className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200"
          >
            {[3, 5, 8, 10].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </div>
      </div>
      <ActionButton state={state} onClick={handleCreate} progress={progress} result={result} label="Create Sampler" />
    </div>
  );
}

function TracklistToPlaylist() {
  const [tracklist, setTracklist] = useState("");
  const [playlistName, setPlaylistName] = useState("");
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [result, setResult] = useState<any>(null);

  async function handleCreate() {
    if (!tracklist.trim()) return;
    setState("loading");
    try {
      const lines = tracklist.split("\n").map((l) => l.trim()).filter(Boolean);
      const res = await fetch("/api/playlist/from-tracklist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lines, name: playlistName || undefined }),
      });
      const data = await res.json();
      if (data.error) { setState("error"); setResult(data); }
      else { setState("done"); setResult(data); }
    } catch {
      setState("error");
      setResult({ error: "Network error" });
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-400">
        Paste a tracklist (one per line: &quot;Artist - Title&quot; or &quot;Artist — Title&quot;). Each line gets searched on Spotify.
      </p>
      <textarea
        value={tracklist}
        onChange={(e) => setTracklist(e.target.value)}
        placeholder={"Gesaffelstein - Belgium\nDuke Dumont - Red Light, Green Light\nThe Chemical Brothers - Hey Boy Hey Girl"}
        rows={10}
        className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-3 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500 font-mono"
      />
      <div>
        <label className="text-xs text-zinc-500 block mb-1">Playlist name (optional)</label>
        <input
          type="text"
          value={playlistName}
          onChange={(e) => setPlaylistName(e.target.value)}
          placeholder="Pasted Tracklist"
          className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
        />
      </div>
      <ActionButton state={state} onClick={handleCreate} result={result} label="Create Playlist" />
    </div>
  );
}

function InfluenceDiscovery() {
  const [artist, setArtist] = useState("");
  const [playlistName, setPlaylistName] = useState("");
  const [tracksPerInfluence, setTracksPerInfluence] = useState(3);
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [result, setResult] = useState<any>(null);
  const [progress, setProgress] = useState("");

  async function handleDiscover() {
    if (!artist.trim()) return;
    setState("loading");
    setProgress("Searching for interviews and influences...");
    try {
      const res = await fetch("/api/playlist/influences", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          artist: artist.trim(),
          name: playlistName || undefined,
          tracksPerInfluence,
        }),
      });
      const data = await res.json();
      if (data.error) { setState("error"); setResult(data); }
      else { setState("done"); setResult(data); }
    } catch {
      setState("error");
      setResult({ error: "Network error" });
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-400">
        Enter an artist name to discover their musical influences via interviews and articles,
        then create a Spotify playlist sampling each influence.
      </p>
      <input
        type="text"
        value={artist}
        onChange={(e) => setArtist(e.target.value)}
        placeholder="Don Toliver"
        className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-3 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
      />
      <div className="flex gap-4 items-end">
        <div className="flex-1">
          <label className="text-xs text-zinc-500 block mb-1">Playlist name (optional)</label>
          <input
            type="text"
            value={playlistName}
            onChange={(e) => setPlaylistName(e.target.value)}
            placeholder="Influences of [Artist]"
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
          />
        </div>
        <div>
          <label className="text-xs text-zinc-500 block mb-1">Tracks per influence</label>
          <select
            value={tracksPerInfluence}
            onChange={(e) => setTracksPerInfluence(Number(e.target.value))}
            className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200"
          >
            {[2, 3, 5].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </div>
      </div>
      <ActionButton state={state} onClick={handleDiscover} progress={progress} result={result} label="Discover & Create" />
    </div>
  );
}

function FestivalDiscovery() {
  const [festival, setFestival] = useState("");
  const [playlistName, setPlaylistName] = useState("");
  const [tracksPerArtist, setTracksPerArtist] = useState(3);
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [result, setResult] = useState<any>(null);
  const [progress, setProgress] = useState("");

  async function handleDiscover() {
    if (!festival.trim()) return;
    setState("loading");
    setProgress("Searching for festival lineup...");
    try {
      const res = await fetch("/api/playlist/festival", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          festival: festival.trim(),
          name: playlistName || undefined,
          tracksPerArtist,
        }),
      });
      const data = await res.json();
      if (data.error) { setState("error"); setResult(data); }
      else { setState("done"); setResult(data); }
    } catch {
      setState("error");
      setResult({ error: "Network error" });
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-400">
        Enter a festival name and year. The app searches for the lineup, extracts artist names,
        and creates a Spotify sampler playlist.
      </p>
      <input
        type="text"
        value={festival}
        onChange={(e) => setFestival(e.target.value)}
        placeholder="Primavera Sound 2025"
        className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-3 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
      />
      <div className="flex gap-4 items-end">
        <div className="flex-1">
          <label className="text-xs text-zinc-500 block mb-1">Playlist name (optional)</label>
          <input
            type="text"
            value={playlistName}
            onChange={(e) => setPlaylistName(e.target.value)}
            placeholder="Festival Sampler"
            className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
          />
        </div>
        <div>
          <label className="text-xs text-zinc-500 block mb-1">Tracks per artist</label>
          <select
            value={tracksPerArtist}
            onChange={(e) => setTracksPerArtist(Number(e.target.value))}
            className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200"
          >
            {[2, 3, 5].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </div>
      </div>
      <ActionButton state={state} onClick={handleDiscover} progress={progress} result={result} label="Find Lineup & Create" />
    </div>
  );
}

function ActionButton({
  state,
  onClick,
  result,
  label,
  progress,
}: {
  state: string;
  onClick: () => void;
  result: any;
  label: string;
  progress?: string;
}) {
  if (state === "done" && result?.url) {
    return (
      <div className="space-y-3">
        <a
          href={result.url}
          target="_blank"
          rel="noopener"
          className="inline-block text-sm bg-green-700 hover:bg-green-600 text-white rounded-lg px-5 py-2.5 transition"
        >
          Open in Spotify ({result.trackCount} tracks)
        </a>
        {(result.influences || result.foundArtists) && (
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <h3 className="text-xs text-zinc-500 uppercase tracking-wider mb-2">
              {result.influences
                ? `Discovered Influences (${result.influences.length})`
                : `Lineup Artists Found (${result.foundArtists.length})`}
            </h3>
            <div className="flex flex-wrap gap-1">
              {(result.influences || result.foundArtists).map((name: string) => (
                <span key={name} className="text-xs bg-zinc-800 text-zinc-300 px-2 py-0.5 rounded">
                  {name}
                </span>
              ))}
            </div>
          </div>
        )}
        {result.notFound && result.notFound.length > 0 && (
          <div className="text-xs text-zinc-600">
            Not found on Spotify: {result.notFound.join(", ")}
          </div>
        )}
      </div>
    );
  }

  if (state === "error") {
    return (
      <div className="space-y-2">
        <button
          onClick={onClick}
          className="text-sm bg-red-800 hover:bg-red-700 text-white rounded-lg px-5 py-2.5 transition"
        >
          Retry
        </button>
        <div className="text-xs text-red-400">{result?.error}</div>
      </div>
    );
  }

  return (
    <div>
      <button
        onClick={onClick}
        disabled={state === "loading"}
        className="text-sm bg-green-700 hover:bg-green-600 disabled:bg-zinc-700 disabled:text-zinc-500 text-white rounded-lg px-5 py-2.5 transition"
      >
        {state === "loading" ? "Working..." : label}
      </button>
      {state === "loading" && progress && (
        <div className="text-xs text-zinc-500 mt-2 animate-pulse">{progress}</div>
      )}
    </div>
  );
}
