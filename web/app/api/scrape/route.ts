import { NextRequest, NextResponse } from "next/server";

export const maxDuration = 60;

function extractTracksFromHtml(html: string): { artist: string; title: string }[] {
  const tracks: { artist: string; title: string }[] = [];

  // Strategy 1: JSON-LD structured data
  const jsonLdMatches = html.match(/<script[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi);
  if (jsonLdMatches) {
    for (const match of jsonLdMatches) {
      const content = match.replace(/<script[^>]*>/i, "").replace(/<\/script>/i, "");
      try {
        const data = JSON.parse(content);
        const items = Array.isArray(data) ? data : [data];
        for (const item of items) {
          if (item["@type"] === "MusicRecording") {
            const byArtist = item.byArtist;
            const artistName = typeof byArtist === "object" ? byArtist?.name : String(byArtist || "");
            const title = item.name || "";
            if (artistName && title) tracks.push({ artist: artistName.trim(), title: title.trim() });
          }
        }
      } catch {}
    }
    if (tracks.length > 0) return tracks;
  }

  // Strategy 2: itemprop=tracks microdata
  const itempropRegex = /<[^>]*itemprop=['"]tracks['"][^>]*>([\s\S]*?)<\/[^>]+>/gi;
  let itempropMatch;
  while ((itempropMatch = itempropRegex.exec(html)) !== null) {
    const block = itempropMatch[0];
    const nameMatch = block.match(/itemprop=['"]name['"][^>]*content=['"]([^'"]+)['"]/i);
    const artistMatch = block.match(/itemprop=['"]byArtist['"][^>]*content=['"]([^'"]+)['"]/i);
    if (nameMatch) {
      let name = nameMatch[1].trim();
      let artist = artistMatch ? artistMatch[1].trim() : "";
      if (artist && name.startsWith(artist + " - ")) {
        name = name.slice(artist.length + 3).trim();
      } else if (!artist && name.includes(" - ")) {
        [artist, name] = name.split(" - ", 2).map((s) => s.trim());
      }
      if (artist && name) tracks.push({ artist, title: name });
    }
  }
  if (tracks.length > 0) return tracks;

  // Strategy 3: trackValue class (main 1001TL format)
  const trackValueRegex = /class="trackValue"[^>]*>([^<]+)</gi;
  let tvMatch;
  while ((tvMatch = trackValueRegex.exec(html)) !== null) {
    const text = tvMatch[1].trim();
    if (text.includes(" - ")) {
      const [artist, ...rest] = text.split(" - ");
      tracks.push({ artist: artist.trim(), title: rest.join(" - ").trim() });
    }
  }
  if (tracks.length > 0) return tracks;

  // Strategy 4: Broad text patterns ("1. Artist - Title" or "Artist - Title" lines)
  const strippedText = html
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, "")
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, "")
    .replace(/<[^>]+>/g, "\n")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");

  const numberedPattern = /^\s*\d+[\.\)]\s*(.+?)\s*[-–—]\s*(.+)/gm;
  let numMatch;
  while ((numMatch = numberedPattern.exec(strippedText)) !== null) {
    const artist = numMatch[1].trim();
    const title = numMatch[2].trim();
    if (artist.length > 1 && artist.length < 100 && title.length > 1 && title.length < 150) {
      tracks.push({ artist, title });
    }
  }
  if (tracks.length > 0) return tracks;

  return tracks;
}

export async function POST(request: NextRequest) {
  const { url } = await request.json();
  if (!url) {
    return NextResponse.json({ error: "No URL provided" }, { status: 400 });
  }

  // Try Bright Data Web Unlocker first
  const bdApiKey = process.env.BRIGHT_DATA_API_KEY;
  let html = "";

  if (bdApiKey) {
    try {
      const bdZone = process.env.BRIGHTDATA_ZONE || "web_unlocker1";
      const bdCustomer = process.env.BRIGHTDATA_CUSTOMER_ID || "hl_02c0928d";
      const bdPassword = process.env.BRIGHTDATA_ZONE_PASSWORD || "98d5odj6xyfn";

      const proxyUrl = `http://brd-customer-${bdCustomer}-zone-${bdZone}:${bdPassword}@brd.superproxy.io:33335`;

      // Use Bright Data's SERP/unlocker API endpoint
      const res = await fetch(`https://api.brightdata.com/request`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${bdApiKey}`,
        },
        body: JSON.stringify({
          zone: bdZone,
          url,
          format: "raw",
        }),
        signal: AbortSignal.timeout(30000),
      });

      if (res.ok) {
        html = await res.text();
      }
    } catch (e) {
      // Fall through to direct fetch
    }
  }

  // Fallback: direct fetch (works for some pages, fails for Cloudflare-protected ones)
  if (!html) {
    try {
      const res = await fetch(url, {
        headers: {
          "User-Agent":
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        },
        signal: AbortSignal.timeout(15000),
      });
      if (res.ok) html = await res.text();
    } catch {}
  }

  if (!html) {
    return NextResponse.json({ error: "Failed to fetch the page. Try pasting the tracklist manually instead." }, { status: 502 });
  }

  // Check for CAPTCHA gate
  if (html.includes('name="captcha"') && !html.includes('class="trackValue"')) {
    return NextResponse.json({
      error: "Page is behind a CAPTCHA gate. Try the Tracklist tab — paste the tracklist from the page manually.",
    }, { status: 403 });
  }

  const tracks = extractTracksFromHtml(html);

  if (tracks.length === 0) {
    return NextResponse.json({
      error: "No tracks found in the page. The page may be a shell without track data. Try the Tracklist tab instead.",
    }, { status: 404 });
  }

  return NextResponse.json({ tracks, count: tracks.length });
}
