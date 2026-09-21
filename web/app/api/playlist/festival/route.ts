import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { createPlaylist, spotifyFetch, refreshAccessToken } from "@/lib/spotify";

export const maxDuration = 300;

async function getToken(): Promise<string | null> {
  const cookieStore = cookies();
  let token = cookieStore.get("spotify_access_token")?.value;
  if (!token) {
    const refresh = cookieStore.get("spotify_refresh_token")?.value;
    if (refresh) {
      const refreshed = await refreshAccessToken(refresh);
      if (refreshed) token = refreshed.access_token;
    }
  }
  return token || null;
}

async function serpSearch(query: string): Promise<{ title: string; link: string; snippet: string }[]> {
  const key = process.env.SERP_API_KEY || process.env.SERPAPI_KEY;
  if (!key) return [];
  const params = new URLSearchParams({ q: query, api_key: key, num: "8" });
  const res = await fetch(`https://serpapi.com/search.json?${params}`);
  if (!res.ok) return [];
  const data = await res.json();
  return (data.organic_results || []).map((r: any) => ({
    title: r.title || "", link: r.link || "", snippet: r.snippet || "",
  }));
}

async function fetchPage(url: string): Promise<string> {
  try {
    const res = await fetch(url, {
      headers: { "User-Agent": "Mozilla/5.0 (compatible; MusicResearchBot/1.0)" },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) return "";
    const html = await res.text();
    return html
      .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, "")
      .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, "")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 12000);
  } catch {
    return "";
  }
}

async function extractArtistsWithLLM(text: string, festivalName: string): Promise<string[]> {
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key || !text) return [];

  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": key.replace(/^"|"$/g, ""),
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: "claude-haiku-4-5-20251001",
      max_tokens: 2000,
      messages: [{
        role: "user",
        content: `Extract the names of musical artists and performers from this text about "${festivalName}".

RULES:
1. Include ONLY musicians, DJs, bands, and musical performers
2. EXCLUDE talks, panels, workshops, venues, stages, sponsors, dates, locations, news outlets
3. Clean up names: remove "(Live)", "(cancelled)", "(DJ set)", work titles after colons
4. Return ONLY a JSON array of artist names, nothing else. Example: ["Artist One", "Artist Two"]

Text:
${text}`,
      }],
    }),
  });

  if (!res.ok) return [];
  const data = await res.json();
  const responseText = data.content?.[0]?.text || "[]";

  try {
    const match = responseText.match(/\[[\s\S]*?\]/);
    if (match) {
      const artists = JSON.parse(match[0]);
      const seen = new Set<string>();
      return artists.filter((a: string) => {
        const norm = a.toLowerCase().trim();
        if (!norm || norm.length < 2 || seen.has(norm)) return false;
        seen.add(norm);
        return true;
      });
    }
  } catch {}
  return [];
}

export async function POST(request: NextRequest) {
  const token = await getToken();
  if (!token) {
    return NextResponse.json({ error: "Not authenticated. Connect Spotify first." }, { status: 401 });
  }

  const { festival, tracksPerArtist = 3, name } = await request.json();
  if (!festival) {
    return NextResponse.json({ error: "No festival name provided" }, { status: 400 });
  }

  // 1. Search for festival lineup
  const queries = [
    `${festival} lineup`,
    `${festival} full lineup artists`,
  ];

  const allResults: { title: string; link: string; snippet: string }[] = [];
  for (const q of queries) {
    const results = await serpSearch(q);
    allResults.push(...results);
    await new Promise((r) => setTimeout(r, 300));
  }

  // Dedup URLs, prefer Wikipedia
  const seenUrls = new Set<string>();
  const uniqueResults = allResults
    .sort((a, b) => {
      const aWiki = a.link.includes("wikipedia.org") ? -1 : 0;
      const bWiki = b.link.includes("wikipedia.org") ? -1 : 0;
      return aWiki - bWiki;
    })
    .filter((r) => {
      if (seenUrls.has(r.link)) return false;
      seenUrls.add(r.link);
      return true;
    });

  // 2. Scrape top pages
  const pagesToScrape = uniqueResults.slice(0, 4);
  const pages = await Promise.all(pagesToScrape.map((r) => fetchPage(r.link)));

  // 3. Extract artists via LLM from each page
  let allArtists: string[] = [];
  for (const page of pages) {
    if (!page) continue;
    const artists = await extractArtistsWithLLM(page, festival);
    allArtists.push(...artists);
  }

  // Dedup
  const seen = new Set<string>();
  const lineup = allArtists.filter((a) => {
    const norm = a.toLowerCase().trim();
    if (seen.has(norm)) return false;
    seen.add(norm);
    return true;
  });

  if (lineup.length === 0) {
    return NextResponse.json({
      error: `No artists found for "${festival}". Try a more specific name like "Primavera Sound 2025".`,
    }, { status: 404 });
  }

  // 4. Search Spotify for each artist's tracks
  const trackIds: string[] = [];
  const foundArtists: string[] = [];
  const notFound: string[] = [];

  for (const artist of lineup) {
    const res = await spotifyFetch(
      `/search?${new URLSearchParams({
        q: `artist:${artist}`,
        type: "track",
        limit: String(tracksPerArtist),
      })}`,
      token
    );

    if (res.status === 429) break;
    if (!res.ok) { notFound.push(artist); continue; }

    const data = await res.json();
    const tracks = data.tracks?.items || [];
    if (tracks.length === 0) {
      notFound.push(artist);
      continue;
    }

    foundArtists.push(artist);
    for (const track of tracks.slice(0, tracksPerArtist)) {
      trackIds.push(track.id);
    }

    await new Promise((r) => setTimeout(r, 4000));
  }

  if (trackIds.length === 0) {
    return NextResponse.json({
      error: "Found lineup but couldn't find their tracks on Spotify",
      lineup,
    }, { status: 404 });
  }

  // 5. Create playlist
  const playlistName = name || `${festival} Sampler`;
  const result = await createPlaylist(token, playlistName, trackIds);
  if ("error" in result) {
    return NextResponse.json({ ...result, lineup, foundArtists }, { status: 500 });
  }

  return NextResponse.json({
    ...result,
    lineup,
    foundArtists,
    notFound,
  });
}
