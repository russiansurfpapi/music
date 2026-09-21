// Set shape + DJ archetype analysis — ported from Python set_shapes.py / dj_archetype.py

// Subgenre → family
export const FAMILY: Record<string, string> = {};
for (const sg of [
  "deep house", "tech house", "chicago house", "progressive house",
  "acid house", "afro house", "tropical house", "french house",
  "garage house", "soulful house", "funky house", "lo-fi house",
  "disco house", "jackin house", "ghetto house", "hip house",
  "house", "hard house", "tribal house", "ambient house",
  "outsider house", "bass house", "future house", "microhouse",
]) FAMILY[sg] = "house";
for (const sg of [
  "minimal techno", "detroit techno", "industrial techno", "hard techno",
  "dub techno", "acid techno", "melodic techno", "ambient techno",
  "peak time techno", "techno",
]) FAMILY[sg] = "techno";
for (const sg of ["progressive trance", "psytrance", "goa trance", "trance"])
  FAMILY[sg] = "trance";
for (const sg of ["uk garage", "speed garage", "two-step"])
  FAMILY[sg] = "garage";
for (const sg of ["jungle/dnb", "liquid dnb", "neurofunk", "jump up"])
  FAMILY[sg] = "dnb";
for (const sg of ["dubstep (original)", "post-dubstep", "brostep"])
  FAMILY[sg] = "dubstep";
for (const sg of [
  "trap", "boom bap", "lo-fi", "jazz rap", "instrumental hip hop",
  "conscious hip hop", "gangsta rap", "southern rap", "west coast rap",
  "uk rap", "underground hip hop", "cloud rap", "emo rap", "drill",
  "g-funk", "pop rap", "trap rap",
]) FAMILY[sg] = "hiphop";
for (const sg of ["electro", "italo disco", "nu-disco", "cosmic disco", "balearic"])
  FAMILY[sg] = "electro-disco";
for (const sg of ["neo-soul", "alternative rnb"])
  FAMILY[sg] = "rnb";

// Family → display color (tailwind classes)
export const FAMILY_COLORS: Record<string, { bg: string; text: string; hex: string }> = {
  house:         { bg: "bg-blue-500",    text: "text-blue-400",    hex: "#3b82f6" },
  techno:        { bg: "bg-red-500",     text: "text-red-400",     hex: "#ef4444" },
  trance:        { bg: "bg-purple-500",  text: "text-purple-400",  hex: "#a855f7" },
  garage:        { bg: "bg-cyan-500",    text: "text-cyan-400",    hex: "#06b6d4" },
  dnb:           { bg: "bg-green-500",   text: "text-green-400",   hex: "#22c55e" },
  dubstep:       { bg: "bg-indigo-500",  text: "text-indigo-400",  hex: "#6366f1" },
  hiphop:        { bg: "bg-orange-500",  text: "text-orange-400",  hex: "#f97316" },
  "electro-disco": { bg: "bg-pink-500", text: "text-pink-400",    hex: "#ec4899" },
  rnb:           { bg: "bg-amber-500",   text: "text-amber-400",   hex: "#f59e0b" },
  "disco-funk-soul": { bg: "bg-yellow-500", text: "text-yellow-400", hex: "#eab308" },
};
const DEFAULT_COLOR = { bg: "bg-zinc-600", text: "text-zinc-400", hex: "#71717a" };

export function familyColor(subgenre: string | null, genre: string | null) {
  if (subgenre && FAMILY[subgenre]) {
    return FAMILY_COLORS[FAMILY[subgenre]] || DEFAULT_COLOR;
  }
  if (genre) {
    return FAMILY_COLORS[genre] || DEFAULT_COLOR;
  }
  return DEFAULT_COLOR;
}

// Genre badge colors
export const GENRE_COLORS: Record<string, string> = {
  house: "bg-blue-900/50 text-blue-300 border-blue-700",
  techno: "bg-red-900/50 text-red-300 border-red-700",
  trance: "bg-purple-900/50 text-purple-300 border-purple-700",
  "hip-hop": "bg-orange-900/50 text-orange-300 border-orange-700",
  "disco-funk-soul": "bg-yellow-900/50 text-yellow-300 border-yellow-700",
  pop: "bg-pink-900/50 text-pink-300 border-pink-700",
  rock: "bg-stone-900/50 text-stone-300 border-stone-700",
  "uk bass": "bg-cyan-900/50 text-cyan-300 border-cyan-700",
  garage: "bg-cyan-900/50 text-cyan-300 border-cyan-700",
  dubstep: "bg-indigo-900/50 text-indigo-300 border-indigo-700",
  dnb: "bg-green-900/50 text-green-300 border-green-700",
  idm: "bg-violet-900/50 text-violet-300 border-violet-700",
  downtempo: "bg-teal-900/50 text-teal-300 border-teal-700",
  ambient: "bg-sky-900/50 text-sky-300 border-sky-700",
  jazz: "bg-amber-900/50 text-amber-300 border-amber-700",
  world: "bg-lime-900/50 text-lime-300 border-lime-700",
  "reggaetón": "bg-emerald-900/50 text-emerald-300 border-emerald-700",
};

export function genreBadgeClass(genre: string | null): string {
  if (!genre) return "bg-zinc-800/50 text-zinc-400 border-zinc-700";
  return GENRE_COLORS[genre] || "bg-zinc-800/50 text-zinc-400 border-zinc-700";
}

// Set shape analysis
export interface SetAnalysis {
  n: number;
  shape: string;
  transitions: number;
  transitionRate: number;
  familyTransitions: number;
  maxRun: number;
  medianRun: number;
  topShare: number;
  distinct: number;
  topSubgenres: [string, number][];
  quintileTops: string[];
  trackColors: { hex: string; subgenre: string | null; genre: string | null }[];
}

export function analyzeSet(tracks: { subgenre: string | null; genre: string | null }[]): SetAnalysis {
  const n = tracks.length;
  const subs = tracks.map(t => t.subgenre || t.genre || null);

  const trackColors = tracks.map(t => ({
    hex: familyColor(t.subgenre, t.genre).hex,
    subgenre: t.subgenre,
    genre: t.genre,
  }));

  // Transitions
  let transitions = 0;
  for (let i = 1; i < n; i++) {
    if (subs[i] !== subs[i - 1] && subs[i] && subs[i - 1]) transitions++;
  }
  const transitionRate = n > 1 ? transitions / (n - 1) : 0;

  // Run lengths
  const runs: number[] = [];
  let cur: string | null = null;
  let ln = 0;
  for (const s of subs) {
    if (s === cur) {
      ln++;
    } else {
      if (cur !== null) runs.push(ln);
      cur = s;
      ln = 1;
    }
  }
  if (cur !== null) runs.push(ln);
  const maxRun = runs.length ? Math.max(...runs) : 0;
  const sorted = [...runs].sort((a, b) => a - b);
  const medianRun = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;

  // Family transitions
  const famSeq = subs.map(s => (s && FAMILY[s]) ? FAMILY[s] : s);
  let familyTransitions = 0;
  for (let i = 1; i < n; i++) {
    if (famSeq[i] !== famSeq[i - 1] && famSeq[i] && famSeq[i - 1]) familyTransitions++;
  }

  // Subgenre counts
  const subCounts: Record<string, number> = {};
  for (const s of subs) {
    if (s) subCounts[s] = (subCounts[s] || 0) + 1;
  }
  const total = Object.values(subCounts).reduce((a, b) => a + b, 0) || 1;
  const topShare = Object.values(subCounts).length
    ? Math.max(...Object.values(subCounts)) / total
    : 0;
  const distinct = Object.keys(subCounts).length;

  const topSubgenres = Object.entries(subCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5) as [string, number][];

  // Shape classification
  let shape: string;
  if (topShare >= 0.6) shape = "PLATEAU";
  else if (maxRun >= Math.max(6, Math.floor(n / 10))) shape = "BLOCKS";
  else if (transitionRate >= 0.85 && distinct >= Math.max(5, Math.floor(n / 8))) shape = "ECLECTIC";
  else if (transitionRate >= 0.65) shape = "WAVE";
  else shape = "MIXED";

  // Quintile tops
  const quintiles: string[][] = [[], [], [], [], []];
  for (let i = 0; i < n; i++) {
    const q = Math.min(4, Math.floor(5 * i / n));
    if (subs[i]) quintiles[q].push(subs[i]!);
  }
  const quintileTops = quintiles.map(q => {
    if (!q.length) return "?";
    const c: Record<string, number> = {};
    for (const s of q) c[s] = (c[s] || 0) + 1;
    return Object.entries(c).sort((a, b) => b[1] - a[1])[0][0];
  });

  return {
    n, shape, transitions, transitionRate, familyTransitions,
    maxRun, medianRun, topShare, distinct, topSubgenres, quintileTops, trackColors,
  };
}

// DJ archetype axes
export function entropy(counts: Record<string, number>): number {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  if (total === 0) return 0;
  return -Object.values(counts).reduce((sum, c) => {
    if (c === 0) return sum;
    const p = c / total;
    return sum + p * Math.log2(p);
  }, 0);
}

export function computeArchetypeAxes(tracks: {
  subgenre: string | null;
  genre: string | null;
  year: number | null;
}[]) {
  const classified = tracks.filter(t => t.subgenre || t.genre);
  if (classified.length < 5) return null;

  // Breadth (subgenre entropy)
  const subCounts: Record<string, number> = {};
  for (const t of classified) {
    const key = t.subgenre || t.genre || "?";
    subCounts[key] = (subCounts[key] || 0) + 1;
  }
  const breadth = entropy(subCounts);

  // Flow (median run length)
  const subs = classified.map(t => t.subgenre || t.genre || "?");
  const runs: number[] = [];
  let cur = "", ln = 0;
  for (const s of subs) {
    if (s === cur) ln++;
    else { if (cur) runs.push(ln); cur = s; ln = 1; }
  }
  if (cur) runs.push(ln);
  const sortedRuns = [...runs].sort((a, b) => a - b);
  const flow = sortedRuns.length ? sortedRuns[Math.floor(sortedRuns.length / 2)] : 0;

  // Anchor loyalty (top subgenre share)
  const total = classified.length;
  const topCount = Math.max(...Object.values(subCounts));
  const anchorLoyalty = topCount / total;
  const anchorGenre = Object.entries(subCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || "?";

  // Era posture
  const years = classified.filter(t => t.year).map(t => t.year!);
  const eraMedian = years.length
    ? years.sort((a, b) => a - b)[Math.floor(years.length / 2)]
    : null;

  return {
    breadth: Math.round(breadth * 100) / 100,
    flow,
    anchorLoyalty: Math.round(anchorLoyalty * 100) / 100,
    anchorGenre,
    eraMedian,
    totalClassified: classified.length,
    distinctSubgenres: Object.keys(subCounts).length,
  };
}
