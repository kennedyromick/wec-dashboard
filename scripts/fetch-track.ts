/**
 * Fetch a circuit centreline and save it as data/tracks/<circuit>.geojson.
 *
 *   npm run fetch-track -- imola                     (bacinger/f1-circuits if listed, else OSM)
 *   npm run fetch-track -- imola --source osm        (force OpenStreetMap)
 *   npm run fetch-track -- lemans --bbox 47.90,0.19,47.97,0.24 --name "Circuit de la Sarthe"
 *
 * OSM options (the OSM data often has pit lanes and alternative layouts):
 *   --finish <nodeId>     node the loop starts/ends at (default: a raceway=finish node on the track)
 *   --exclude <id,id,...> OSM way ids to drop before building the loop
 *   --pick <n>            choose candidate loop n from the printed list (default 1 = longest)
 *   --dry-run             print the candidates without writing anything
 *   --length <m>          published lap length (stored as official_length_m)
 *   --direction cw|ccw    race direction; the loop is reversed if it runs the other way
 *
 * The saved line is a closed LineString ([lon, lat]) that starts at the start/finish line and
 * runs in race direction. Check it on the map before relying on it.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

type LonLat = [number, number];
type BBox = [south: number, west: number, north: number, east: number];

interface CircuitInfo {
  name: string;
  /** id in github.com/bacinger/f1-circuits, if the circuit is there */
  bacinger?: string;
  bbox: BBox;
  /** published lap length in metres (Al Kamel circuit map "Centerline") */
  officialLength?: number;
  /** race direction seen from above: the saved loop is reversed if OSM's runs the other way */
  direction?: 'cw' | 'ccw';
}

const CIRCUITS: Record<string, CircuitInfo> = {
  imola: { name: 'Autodromo Enzo e Dino Ferrari', bacinger: 'it-1953', bbox: [44.334, 11.699, 44.348, 11.728], officialLength: 4909, direction: 'ccw' },
  // spa, lemans, saopaulo, usa, fuji, barc, monza: built 2026-11-18 by hand from OSM (loop chosen by the
  // published length, finish line from OSM markers / PDF offsets), see each geojson's "source" and
  // "finish_line". This script's longest-loop default picks the wrong loop there, so they are not listed.
};

const BACINGER_URL = (id: string) => `https://raw.githubusercontent.com/bacinger/f1-circuits/master/circuits/${id}.geojson`;
// Main server first, then mirrors; each is retried with backoff when busy (429/502/503/504).
const OVERPASS_URLS = ['https://overpass-api.de/api/interpreter', 'https://overpass.private.coffee/api/interpreter', 'https://overpass.kumi.systems/api/interpreter'];
const USER_AGENT = 'wec-dashboard-fetch-track/0.1';

// --- args -----------------------------------------------------------------------

function parseArgs(argv: string[]) {
  const positional: string[] = [];
  const flags: Record<string, string | true> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a.startsWith('--')) {
      const next = argv[i + 1];
      if (next !== undefined && !next.startsWith('--')) {
        flags[a.slice(2)] = next;
        i++;
      } else flags[a.slice(2)] = true;
    } else positional.push(a);
  }
  return { positional, flags };
}

const str = (v: string | true | undefined) => (typeof v === 'string' ? v : undefined);

// --- geometry -------------------------------------------------------------------

const EARTH_RADIUS_M = 6371008.8;

function haversine(a: LonLat, b: LonLat): number {
  const toRad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * toRad;
  const dLon = (b[0] - a[0]) * toRad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * toRad) * Math.cos(b[1] * toRad) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(h));
}

/** Shoelace sign on lon/lat: > 0 means the closed loop runs anticlockwise seen from above. */
function signedArea(coords: LonLat[]): number {
  let a = 0;
  for (let i = 0; i + 1 < coords.length; i++) a += coords[i]![0] * coords[i + 1]![1] - coords[i + 1]![0] * coords[i]![1];
  return a / 2;
}

const lineLength = (coords: LonLat[]) => coords.slice(1).reduce((sum, p, i) => sum + haversine(coords[i]!, p), 0);

const round6 = (p: LonLat): LonLat => [Math.round(p[0] * 1e6) / 1e6, Math.round(p[1] * 1e6) / 1e6];

// --- bacinger -------------------------------------------------------------------

async function fromBacinger(id: string) {
  const res = await fetch(BACINGER_URL(id), { headers: { 'User-Agent': USER_AGENT } });
  if (!res.ok) throw new Error(`bacinger/f1-circuits: HTTP ${res.status} for ${id}`);
  const fc = (await res.json()) as { features: { properties: Record<string, unknown>; geometry: { type: string; coordinates: LonLat[] } }[] };
  const feature = fc.features.find((f) => f.geometry.type === 'LineString');
  if (!feature) throw new Error(`bacinger/f1-circuits: no LineString in ${id}`);
  const coords = feature.geometry.coordinates.map(round6);
  const first = coords[0]!;
  const last = coords[coords.length - 1]!;
  if (first[0] !== last[0] || first[1] !== last[1]) coords.push(first);
  const official = typeof feature.properties.length === 'number' ? feature.properties.length : undefined;
  return { coords, source: `bacinger/f1-circuits ${id}`, officialLength: official };
}

// --- OpenStreetMap --------------------------------------------------------------

interface OsmWay {
  type: 'way';
  id: number;
  nodes: number[];
  geometry: { lat: number; lon: number }[];
  tags?: Record<string, string>;
}
interface OsmNode {
  type: 'node';
  id: number;
  lat: number;
  lon: number;
  tags?: Record<string, string>;
}

async function overpass(bbox: BBox): Promise<(OsmWay | OsmNode)[]> {
  const b = bbox.join(',');
  const query = `[out:json][timeout:60];(way["highway"="raceway"](${b});node["raceway"~"^(finish|start)$"](${b}););out body geom;`;
  const errors: string[] = [];
  for (const url of OVERPASS_URLS) {
    for (let attempt = 0, wait = 2000; attempt < 3; attempt++, wait *= 2) {
      try {
        const res = await fetch(url, {
          method: 'POST',
          headers: { 'User-Agent': USER_AGENT, 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({ data: query }),
          signal: AbortSignal.timeout(90_000),
        });
        if (res.ok) return ((await res.json()) as { elements: (OsmWay | OsmNode)[] }).elements;
        errors.push(`${url}: HTTP ${res.status}`);
        if (![429, 502, 503, 504].includes(res.status)) break;
      } catch (err) {
        errors.push(`${url}: ${err instanceof Error ? err.message : err}`);
      }
      console.log(`Overpass busy (${errors[errors.length - 1]}), retrying in ${wait / 1000}s...`);
      await new Promise((r) => setTimeout(r, wait));
    }
  }
  throw new Error(`Overpass failed on every server:\n  ${errors.join('\n  ')}`);
}

/** Ways that are never part of the racing line. */
function isPitOrService(w: OsmWay): boolean {
  const t = w.tags ?? {};
  if (/pit|paddock|box/i.test(t.name ?? '')) return true;
  if (/pit/i.test(t.raceway ?? '') || /pit/i.test(t.service ?? '') || t.pit_lane !== undefined) return true;
  if (t.area === 'yes') return true;
  return false;
}

interface Edge {
  to: number;
  way: OsmWay;
}

interface Loop {
  nodes: number[];
  ways: OsmWay[];
  length: number;
}

function findLoops(ways: OsmWay[], start: number, coordOf: Map<number, LonLat>, maxLoops = 500): Loop[] {
  const graph = new Map<number, Edge[]>();
  const add = (a: number, b: number, way: OsmWay) => {
    if (!graph.has(a)) graph.set(a, []);
    graph.get(a)!.push({ to: b, way });
  };
  for (const w of ways) {
    const oneway = w.tags?.oneway;
    for (let i = 0; i + 1 < w.nodes.length; i++) {
      const a = w.nodes[i]!;
      const b = w.nodes[i + 1]!;
      if (oneway === '-1') add(b, a, w);
      else {
        add(a, b, w);
        if (!(oneway === 'yes' || oneway === '1' || oneway === 'true')) add(b, a, w);
      }
    }
  }

  const loops: Loop[] = [];
  const path: number[] = [start];
  const pathWays: OsmWay[] = [];
  const onPath = new Set<number>([start]);

  // Iterative DFS so long tracks don't overflow the stack.
  const stack: { node: number; next: number }[] = [{ node: start, next: 0 }];
  while (stack.length && loops.length < maxLoops) {
    const top = stack[stack.length - 1]!;
    const edges = graph.get(top.node) ?? [];
    if (top.next >= edges.length) {
      stack.pop();
      onPath.delete(path.pop()!);
      pathWays.pop();
      continue;
    }
    const e = edges[top.next++]!;
    if (e.to === start && path.length > 2) {
      const nodes = [...path, start];
      const coords = nodes.map((n) => coordOf.get(n)!);
      loops.push({ nodes, ways: [...pathWays, e.way], length: lineLength(coords) });
      continue;
    }
    if (onPath.has(e.to)) continue;
    path.push(e.to);
    pathWays.push(e.way);
    onPath.add(e.to);
    stack.push({ node: e.to, next: 0 });
  }
  return loops.filter((l) => l.length > 500).sort((a, b) => b.length - a.length);
}

const wayLabel = (w: OsmWay) => `${w.id}${w.tags?.name ? ` "${w.tags.name}"` : ''}${w.tags?.ref ? ` [T${w.tags.ref}]` : ''}`;

async function fromOsm(bbox: BBox, opts: { finish?: number; exclude: Set<number>; pick: number }) {
  const elements = await overpass(bbox);
  const allWays = elements.filter((e): e is OsmWay => e.type === 'way');
  const markerNodes = elements.filter((e): e is OsmNode => e.type === 'node');

  const dropped = allWays.filter((w) => isPitOrService(w) || opts.exclude.has(w.id));
  const ways = allWays.filter((w) => !dropped.includes(w));
  console.log(`OSM: ${allWays.length} raceway ways, ${dropped.length} dropped:`);
  for (const w of dropped) console.log(`  - ${wayLabel(w)}${opts.exclude.has(w.id) ? ' (--exclude)' : ' (pit/service)'}`);

  const coordOf = new Map<number, LonLat>();
  for (const w of ways) w.nodes.forEach((n, i) => coordOf.set(n, [w.geometry[i]!.lon, w.geometry[i]!.lat]));

  let startCandidates: number[];
  if (opts.finish !== undefined) startCandidates = [opts.finish];
  else {
    const onTrack = (n: OsmNode) => coordOf.has(n.id);
    const finish = markerNodes.filter((n) => n.tags?.raceway === 'finish' && onTrack(n));
    const start = markerNodes.filter((n) => n.tags?.raceway === 'start' && onTrack(n));
    startCandidates = [...finish, ...start].map((n) => n.id);
    console.log(`Start/finish nodes on the track: ${[...finish.map((n) => `${n.id} (finish)`), ...start.map((n) => `${n.id} (start)`)].join(', ') || 'none'}`);
  }
  if (startCandidates.length === 0) throw new Error('No raceway=finish/start node on the track. Pass --finish <nodeId>.');

  for (const startNode of startCandidates) {
    if (!coordOf.has(startNode)) throw new Error(`Node ${startNode} is not on any kept raceway way.`);
    const loops = findLoops(ways, startNode, coordOf);
    if (loops.length === 0) {
      console.log(`No closed loop through node ${startNode}.`);
      continue;
    }
    console.log(`\nLoops through node ${startNode} (longest first):`);
    const chosen = loops[opts.pick - 1] ?? loops[0]!;
    loops.forEach((l, i) => {
      const used = new Set(l.ways.map((w) => w.id));
      const notInChosen = [...new Set(l.ways)].filter((w) => !chosen.ways.includes(w));
      const missingFromThis = [...new Set(chosen.ways)].filter((w) => !used.has(w.id));
      console.log(`  ${i + 1}. ${l.length.toFixed(0)} m, ${used.size} ways${l === chosen ? '  <- chosen' : ''}`);
      if (l !== chosen) {
        if (notInChosen.length) console.log(`       uses:  ${notInChosen.map(wayLabel).join(', ')}`);
        if (missingFromThis.length) console.log(`       skips: ${missingFromThis.map(wayLabel).join(', ')}`);
      }
    });
    const unused = ways.filter((w) => !chosen.ways.includes(w));
    if (unused.length) console.log(`  Raceway ways not in the chosen loop: ${unused.map(wayLabel).join(', ')}`);
    const coords = chosen.nodes.map((n) => round6(coordOf.get(n)!));
    return { coords, source: `OpenStreetMap (start node ${startNode}, ways ${[...new Set(chosen.ways.map((w) => w.id))].join(',')})` };
  }
  throw new Error('Could not build a closed loop. Try --finish <nodeId> or --exclude <wayIds>.');
}

// --- main -----------------------------------------------------------------------

async function main() {
  const { positional, flags } = parseArgs(process.argv.slice(2));
  const circuit = positional[0];
  if (!circuit) {
    console.error('Usage: npm run fetch-track -- <circuit> [--source bacinger|osm] [--bbox s,w,n,e] [--bacinger id] [--name ..]');
    console.error(`Known circuits: ${Object.keys(CIRCUITS).join(', ')}`);
    process.exit(1);
  }
  const known = CIRCUITS[circuit];
  const bboxArg = str(flags.bbox)?.split(',').map(Number);
  const bbox = (bboxArg?.length === 4 ? bboxArg : known?.bbox) as BBox | undefined;
  const bacingerId = str(flags.bacinger) ?? known?.bacinger;
  const name = str(flags.name) ?? known?.name ?? circuit;
  const source = str(flags.source) ?? (bacingerId ? 'bacinger' : 'osm');

  let result: { coords: LonLat[]; source: string; officialLength?: number };
  if (source === 'bacinger') {
    if (!bacingerId) throw new Error(`No bacinger id for "${circuit}". Pass --bacinger <id> or --source osm.`);
    result = await fromBacinger(bacingerId);
  } else if (source === 'osm') {
    if (!bbox) throw new Error(`No bounding box for "${circuit}". Pass --bbox south,west,north,east.`);
    const exclude = new Set((str(flags.exclude) ?? '').split(',').filter(Boolean).map(Number));
    const finish = str(flags.finish) ? Number(flags.finish) : undefined;
    result = await fromOsm(bbox, { finish, exclude, pick: Number(str(flags.pick) ?? 1) });
  } else throw new Error(`Unknown --source ${source}`);
  const direction = (str(flags.direction) ?? known?.direction) as 'cw' | 'ccw' | undefined;
  if (direction) {
    const runs = signedArea(result.coords) > 0 ? 'ccw' : 'cw';
    if (runs !== direction) {
      result.coords.reverse(); // still starts and ends on the same start/finish point
      console.log(`Reversed the loop to run ${direction} (race direction).`);
    }
  }
  const officialArg = str(flags.length) ? Number(flags.length) : undefined;
  result.officialLength = officialArg ?? known?.officialLength ?? result.officialLength;

  const length = lineLength(result.coords);
  console.log(`\n${name}: ${result.coords.length} points, ${length.toFixed(1)} m${result.officialLength ? ` (published length ${result.officialLength} m)` : ''}`);
  console.log(`Source: ${result.source}`);

  if (flags['dry-run']) return;

  const out = str(flags.out) ?? join('data', 'tracks', `${circuit}.geojson`);
  const geojson = {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        properties: {
          circuit,
          name,
          source: result.source,
          fetched: new Date().toISOString().slice(0, 10),
          length_m: Math.round(length * 10) / 10,
          ...(result.officialLength ? { official_length_m: result.officialLength } : {}),
          note: 'Closed loop; first point is the start/finish line; runs in race direction.',
        },
        geometry: { type: 'LineString', coordinates: result.coords },
      },
    ],
  };
  mkdirSync(join(out, '..'), { recursive: true });
  writeFileSync(out, JSON.stringify(geojson, null, 1) + '\n');
  console.log(`Saved ${out}`);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
