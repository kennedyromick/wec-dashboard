/**
 * npm run inspect                  -> newest *Analysis*.csv found under data/
 * npm run inspect -- path/to.csv   -> a specific file
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { parseAnalysis, type Car, type Lap } from '../src/data/parseAnalysis';
import { formatDuration } from '../src/data/time';

function findAnalysisFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...findAnalysisFiles(p));
    else if (/analysis/i.test(name) && /\.csv$/i.test(name)) out.push(p);
  }
  return out;
}

function pickFile(): string {
  const arg = process.argv[2];
  if (arg) return arg;
  const files = findAnalysisFiles('data').sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
  if (files.length === 0) {
    console.error('No *Analysis*.csv found under data/. Pass a path: npm run inspect -- <file>');
    process.exit(1);
  }
  return files[0]!;
}

/** Sort car numbers numerically but keep "007" after "7" rather than equal to it. */
const byCarNumber = (a: Car, b: Car) => Number(a.number) - Number(b.number) || a.number.length - b.number.length;

/** Official best lap if the file marks one, else the raw fastest timed lap. */
function bestLap(car: Car): Lap | null {
  const official = car.laps.find((l) => l.officialBest);
  if (official) return official;
  let best: Lap | null = null;
  for (const l of car.laps) if (l.lapTime !== null && (!best || l.lapTime < best.lapTime!)) best = l;
  return best;
}

const heading = (s: string) => console.log(`\n=== ${s} ${'='.repeat(Math.max(0, 70 - s.length))}`);

// ---------------------------------------------------------------------------

const file = pickFile();
const result = parseAnalysis(readFileSync(file, 'utf8'));
const { cars, issues } = result;
const totalLaps = cars.reduce((n, c) => n + c.laps.length, 0);

console.log(`File: ${relative(process.cwd(), file)}`);
console.log(`${cars.length} cars, ${totalLaps} laps`);

heading('Columns');
for (const [field, header] of Object.entries(result.columns)) console.log(`  ${field.padEnd(14)} <- ${header}`);
const missing = ['number', 'driverNumber', 'lap', 'lapTime', 'lapImprovement', 'pitIn', 's1', 's2', 's3', 'avgSpeed', 'elapsed',
  'timeOfDay', 'topSpeed', 'driver', 'pitTime', 'class', 'team', 'manufacturer', 'flag']
  .filter((f) => !(f in result.columns));
if (missing.length) console.log(`  MISSING: ${missing.join(', ')}`);
if (result.unmappedHeaders.length) console.log(`  Unused headers: ${result.unmappedHeaders.join(', ')}`);

heading('Cars per class');
const classes = [...new Set(cars.map((c) => c.class))];
for (const cls of classes) {
  const nums = cars.filter((c) => c.class === cls).sort(byCarNumber).map((c) => `#${c.number}`);
  console.log(`  ${cls.padEnd(10)} ${String(nums.length).padStart(3)}  ${nums.join(' ')}`);
}

heading('Laps per car');
console.log(`  ${'Car'.padEnd(5)} ${'Class'.padEnd(9)} ${'Team'.padEnd(26)} ${'Laps'.padStart(4)}  ${'Stops'.padStart(5)}  ${'Best'.padStart(8)}  Drivers`);
for (const cls of classes) {
  for (const car of cars.filter((c) => c.class === cls).sort(byCarNumber)) {
    const stops = car.laps.filter((l) => l.pitIn).length;
    console.log(
      `  #${car.number.padEnd(4)} ${car.class.padEnd(9)} ${car.team.slice(0, 26).padEnd(26)} ${String(car.laps.length).padStart(4)}  ` +
      `${String(stops).padStart(5)}  ${formatDuration(bestLap(car)?.lapTime ?? null).padStart(8)}  ${car.drivers.map((d) => d.name).join(', ')}`,
    );
  }
}

heading('Fastest lap per class');
for (const cls of classes) {
  let best: { car: Car; lap: Lap } | null = null;
  for (const car of cars.filter((c) => c.class === cls)) {
    const lap = bestLap(car);
    if (lap?.lapTime != null && (!best || lap.lapTime < best.lap.lapTime!)) best = { car, lap };
  }
  if (!best) { console.log(`  ${cls}: no timed laps`); continue; }
  const { car, lap } = best;
  console.log(
    `  ${cls.padEnd(10)} ${formatDuration(lap.lapTime)}  #${car.number} ${car.team}, ${lap.driver}, lap ${lap.lap}` +
    `  (S1 ${lap.s1?.toFixed(3)}  S2 ${lap.s2?.toFixed(3)}  S3 ${lap.s3?.toFixed(3)})`,
  );
}

heading('Track status (laps by flag at the line)');
const flagCounts = new Map<string, number>();
for (const car of cars) for (const lap of car.laps) {
  const key = `${lap.flag} (${lap.flagRaw || 'empty'})`;
  flagCounts.set(key, (flagCounts.get(key) ?? 0) + 1);
}
for (const [k, n] of flagCounts) console.log(`  ${k.padEnd(18)} ${n}`);

heading('Data gaps (kept, value is null)');
const count = (pred: (l: Lap) => boolean) => cars.reduce((n, c) => n + c.laps.filter(pred).length, 0);
console.log(`  laps missing a lap time   ${count((l) => l.lapTime === null)}`);
console.log(`  laps missing any sector   ${count((l) => l.s1 === null || l.s2 === null || l.s3 === null)}`);
console.log(`  laps missing top speed    ${count((l) => l.topSpeed === null)}`);
console.log(`  laps missing elapsed      ${count((l) => l.elapsed === null)}`);
for (const car of cars) {
  const official = car.laps.find((l) => l.officialBest);
  for (const l of car.laps) {
    if (official && l.lapTime !== null && l.lapTime < official.lapTime!) {
      console.log(`  #${car.number} lap ${l.lap} (${formatDuration(l.lapTime)}) beats the official best (${formatDuration(official.lapTime)}, lap ${official.lap}): probably a deleted lap`);
    }
  }
}

heading(`Parse issues (${issues.length})`);
if (issues.length === 0) console.log('  none');
for (const i of issues.slice(0, 50)) {
  console.log(`  ${i.fatal ? 'ROW DROPPED' : 'field null '}  line ${i.line}: ${i.message}${i.raw ? `\n      ${i.raw}` : ''}`);
}
if (issues.length > 50) console.log(`  ... and ${issues.length - 50} more`);
