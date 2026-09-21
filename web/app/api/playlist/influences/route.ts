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

// SerpAPI search
async function serpSearch(query: string): Promise<{ title: string; link: string; snippet: string }[]> {
  const key = process.env.SERP_API_KEY || process.env.SERPAPI_KEY;
  if (!key) return [];

  const params = new URLSearchParams({
    q: query,
    api_key: key,
    num: "5",
  });

  const res = await fetch(`https://serpapi.com/search.json?${params}`);
  if (!res.ok) return [];
  const data = await res.json();
  return (data.organic_results || []).map((r: any) => ({
    title: r.title || "",
    link: r.link || "",
    snippet: r.snippet || "",
  }));
}

// Scrape article text (simple fetch)
async function scrapeArticle(url: string): Promise<string> {
  try {
    const res = await fetch(url, {
      headers: { "User-Agent": "Mozilla/5.0 (compatible; MusicResearchBot/1.0)" },
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return "";
    const html = await res.text();
    // Strip HTML tags, get text content
    const text = html
      .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, "")
      .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, "")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    return text.slice(0, 8000); // Limit to 8k chars per article
  } catch {
    return "";
  }
}

// Claude Haiku extraction
async function extractInfluences(artist: string, articles: string[]): Promise<string[]> {
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) return [];

  const combined = articles
    .filter(Boolean)
    .map((a, i) => `[Article ${i + 1}]:\n${a}`)
    .join("\n\n---\n\n");

  if (!combined) return [];

  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": key.replace(/^"|"$/g, ""),
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: "claude-haiku-4-5-20251001",
      max_tokens: 1024,
      messages: [
        {
          role: "user",
          content: `From these articles about ${artist}, extract the names of musical artists, bands, or musicians who influenced or inspired ${artist}. Only include artists explicitly mentioned as influences, inspirations, or who ${artist} says they listened to growing up / learned from.

Return ONLY a JSON array of artist names, nothing else. Example: ["Artist One", "Artist Two"]

If no influences are mentioned, return [].

Articles:
${combined}`,
        },
      ],
    }),
  });

  if (!res.ok) return [];
  const data = await res.json();
  const text = data.content?.[0]?.text || "[]";

  try {
    const match = text.match(/\[[\s\S]*?\]/);
    if (match) return JSON.parse(match[0]);
  } catch {}
  return [];
}

// Fuzzy dedup
function dedup(names: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const name of names) {
    const key = name.toLowerCase().replace(/[^a-z0-9]/g, "");
    if (!seen.has(key) && key.length > 1) {
      seen.add(key);
      result.push(name);
    }
  }
  return result;
}

export async function POST(request: NextRequest) {
  const token = await getToken();
  if (!token) {
    return NextResponse.json({ error: "Not authenticated. Connect Spotify first." }, { status: 401 });
  }

  const { artist, name, tracksPerInfluence = 3 } = await request.json();
  if (!artist) {
    return NextResponse.json({ error: "No artist provided" }, { status: 400 });
  }

  // 1. SERP search with multiple query angles
  const queries = [
    `${artist} musical influences interviews`,
    `${artist} artists who inspired`,
    `${artist} favorite musicians grew up listening to`,
  ];

  const allResults: { title: string; link: string; snippet: string }[] = [];
  for (const q of queries) {
    const results = await serpSearch(q);
    allResults.push(...results);
    await new Promise((r) => setTimeout(r, 500));
  }

  // Dedup URLs
  const seenUrls = new Set<string>();
  const uniqueResults = allResults.filter((r) => {
    if (seenUrls.has(r.link)) return false;
    seenUrls.add(r.link);
    return true;
  });

  // 2. Scrape top articles
  const articlesToScrape = uniqueResults.slice(0, 6);
  const articles = await Promise.all(articlesToScrape.map((r) => scrapeArticle(r.link)));

  // 3. Extract influences via Claude
  const rawInfluences = await extractInfluences(artist, articles);
  const influences = dedup(rawInfluences);

  if (influences.length === 0) {
    return NextResponse.json({
      error: `No influences found for ${artist}. Try a more well-known artist.`,
    }, { status: 404 });
  }

  // 4. Search Spotify for each influence's tracks
  const trackIds: string[] = [];
  const notFound: string[] = [];

  for (const influence of influences) {
    const res = await spotifyFetch(
      `/search?${new URLSearchParams({
        q: `artist:${influence}`,
        type: "track",
        limit: String(tracksPerInfluence),
      })}`,
      token
    );

    if (res.status === 429) break;
    if (!res.ok) { notFound.push(influence); continue; }

    const data = await res.json();
    const tracks = data.tracks?.items || [];
    if (tracks.length === 0) {
      notFound.push(influence);
      continue;
    }

    for (const track of tracks.slice(0, tracksPerInfluence)) {
      trackIds.push(track.id);
    }

    await new Promise((r) => setTimeout(r, 4000));
  }

  if (trackIds.length === 0) {
    return NextResponse.json({
      error: "Found influences but couldn't find their tracks on Spotify",
      influences,
    }, { status: 404 });
  }

  // 5. Create playlist
  const playlistName = name || `Influences of ${artist}`;
  const result = await createPlaylist(token, playlistName, trackIds);
  if ("error" in result) return NextResponse.json({ ...result, influences }, { status: 500 });

  return NextResponse.json({ ...result, influences, notFound });
}
