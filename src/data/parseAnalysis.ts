import { parseCsv } from './csv';
import { parseDuration, parseNumber } from './time';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * Track status as the car crossed the finish line (Al Kamel FLAG_AT_FL).
 * Seen in the 2026 Imola file: GF (green), SF (safety car), FF (chequered: only on
 * each finisher's final lap). FCY / red codes haven't appeared in a file yet; see FLAG_CODES.
 */
export type FlagStatus = 'green' | 'fcy' | 'sc' | 'red' | 'finish' | 'unknown';

export interface Lap {
  lap: number;
  /** Index of the driver in the car's line-up (1, 2, 3...). */
  driverNumber: number | null;
  driver: string;
  /** All times in seconds. null = missing in the file (e.g. a sector not recorded). */
  lapTime: number | null;
  s1: number | null;
  s2: number | null;
  s3: number | null;
  /** Race time elapsed when the car crossed the line at the end of this lap. */
  elapsed: number | null;
  /** Local time of day at the line crossing, as written by Al Kamel ("13:03:22.516"). */
  timeOfDay: string | null;
  /** Average lap speed, km/h. */
  avgSpeed: number | null;
  /** Highest speed-trap reading on the lap, km/h. Usually empty on lap 1. */
  topSpeed: number | null;
  /** In-lap: the car crossed the finish line in the pit lane at the end of this lap. */
  pitIn: boolean;
  /** Out-lap: this lap started from the pit lane (i.e. the previous lap was an in-lap). */
  pitOut: boolean;
  /** Time spent in the pit lane, recorded on the out-lap. */
  pitTime: number | null;
  flag: FlagStatus;
  /** The raw FLAG_AT_FL code, kept so unknown codes can be spotted. */
  flagRaw: string;
  /**
   * This is the car's official fastest lap (LAP_IMPROVEMENT > 0; 3 = overall fastest).
   * Al Kamel marks exactly one lap per car, and it matches the Classification's FL_TIME.
   * A raw-faster lap without this mark was deleted (e.g. track limits), so use this flag,
   * not min(lapTime), for "best lap".
   */
  officialBest: boolean;
}

export interface Car {
  /** Kept as a string: "007" and "7" are different cars (Aston Martin vs Toyota). */
  number: string;
  team: string;
  class: string;
  manufacturer: string;
  /** Line-up in driver-number order, built from the laps each driver did. */
  drivers: { number: number | null; name: string }[];
  laps: Lap[];
}

export interface ParseIssue {
  /** 1-based line in the CSV file. */
  line: number;
  /** true = the whole row was dropped; false = the lap was kept but one field is null. */
  fatal: boolean;
  message: string;
  raw?: string;
}

export interface AnalysisResult {
  cars: Car[];
  issues: ParseIssue[];
  /** Header names as found in the file (normalised). */
  headers: string[];
  /** Which file header each field was read from; undefined = column not present. */
  columns: Partial<Record<Field, string>>;
  /** Headers in the file that no field uses. */
  unmappedHeaders: string[];
}

// ---------------------------------------------------------------------------
// Column mapping
// ---------------------------------------------------------------------------

/**
 * Field -> candidate header names, most preferred first. Headers are normalised
 * (trimmed, upper-cased, spaces -> "_") before matching, so " LAP_TIME" matches LAP_TIME.
 * The first name in each list is what the 2026 files use. The later names are
 * fallbacks for other seasons and are unverified until we see such a file.
 */
const COLUMN_ALIASES = {
  number: ['NUMBER', 'CAR_NUMBER', 'NO'],
  driverNumber: ['DRIVER_NUMBER', 'DRIVER_NO'],
  lap: ['LAP_NUMBER', 'LAP'],
  lapTime: ['LAP_TIME'],
  lapImprovement: ['LAP_IMPROVEMENT'],
  pitIn: ['CROSSING_FINISH_LINE_IN_PIT'],
  // *_SECONDS columns are plain seconds; S1 can switch to "m:ss.sss" on slow
  // sectors, and S1_LARGE is always "m:ss.sss". parseDuration handles all three.
  s1: ['S1_SECONDS', 'S1', 'S1_LARGE'],
  s2: ['S2_SECONDS', 'S2', 'S2_LARGE'],
  s3: ['S3_SECONDS', 'S3', 'S3_LARGE'],
  avgSpeed: ['KPH'],
  elapsed: ['ELAPSED', 'ELAPSED_TIME'],
  timeOfDay: ['HOUR'],
  topSpeed: ['TOP_SPEED', 'TOPSPEED'],
  driver: ['DRIVER_NAME', 'DRIVER'],
  pitTime: ['PIT_TIME'],
  class: ['CLASS'],
  team: ['TEAM'],
  manufacturer: ['MANUFACTURER'],
  flag: ['FLAG_AT_FL', 'FLAG'],
} as const;

type Field = keyof typeof COLUMN_ALIASES;

/** Without these we can't place a row at all. */
const REQUIRED: Field[] = ['number', 'lap'];

const FLAG_CODES: Record<string, FlagStatus> = {
  GF: 'green',
  SF: 'sc',
  FF: 'finish',
  // Not yet seen in a file; best guesses at Al Kamel's codes.
  FCY: 'fcy',
  SC: 'sc',
  RF: 'red',
};

function resolveColumns(headers: string[]): Partial<Record<Field, number>> {
  const idx: Partial<Record<Field, number>> = {};
  for (const field of Object.keys(COLUMN_ALIASES) as Field[]) {
    for (const name of COLUMN_ALIASES[field]) {
      const i = headers.indexOf(name);
      if (i !== -1) { idx[field] = i; break; }
    }
  }
  return idx;
}

// ---------------------------------------------------------------------------
// Parser
// ---------------------------------------------------------------------------

/** Parse the text of an Al Kamel "Analysis" CSV. Pure function: no file system access, so it also runs in the browser. */
export function parseAnalysis(text: string): AnalysisResult {
  const { headers, rows } = parseCsv(text);
  const col = resolveColumns(headers);
  const issues: ParseIssue[] = [];

  const columns: AnalysisResult['columns'] = {};
  for (const [field, i] of Object.entries(col)) columns[field as Field] = headers[i];
  const used = new Set(Object.values(col));
  const unmappedHeaders = headers.filter((_, i) => !used.has(i));

  const missingRequired = REQUIRED.filter((f) => col[f] === undefined);
  if (missingRequired.length > 0) {
    issues.push({ line: 1, fatal: true, message: `Missing required column(s): ${missingRequired.join(', ')}` });
    return { cars: [], issues, headers, columns, unmappedHeaders };
  }

  const cars = new Map<string, Car>();

  for (const { line, fields } of rows) {
    const raw = (f: Field): string => {
      const i = col[f];
      return i === undefined ? '' : (fields[i] ?? '').trim();
    };
    // Parse a field; if a value is present but unreadable, record it and use null.
    const read = (f: Field, parse: (s: string) => number | null): number | null => {
      const s = raw(f);
      const v = parse(s);
      if (s !== '' && v === null) issues.push({ line, fatal: false, message: `Unreadable ${f}: "${s}"` });
      return v;
    };

    const number = raw('number');
    const lapNo = parseNumber(raw('lap'));
    if (number === '' || lapNo === null || !Number.isInteger(lapNo)) {
      issues.push({ line, fatal: true, message: 'Missing car number or invalid lap number', raw: fields.join(';') });
      continue;
    }

    let car = cars.get(number);
    if (!car) {
      car = { number, team: raw('team'), class: raw('class'), manufacturer: raw('manufacturer'), drivers: [], laps: [] };
      cars.set(number, car);
    }

    const flagRaw = raw('flag').toUpperCase();
    car.laps.push({
      lap: lapNo,
      driverNumber: read('driverNumber', parseNumber),
      driver: raw('driver'),
      lapTime: read('lapTime', parseDuration),
      s1: read('s1', parseDuration),
      s2: read('s2', parseDuration),
      s3: read('s3', parseDuration),
      elapsed: read('elapsed', parseDuration),
      timeOfDay: raw('timeOfDay') || null,
      avgSpeed: read('avgSpeed', parseNumber),
      topSpeed: read('topSpeed', parseNumber),
      pitIn: raw('pitIn') !== '',
      pitOut: false, // filled in below, once laps are sorted
      pitTime: read('pitTime', parseDuration),
      flag: FLAG_CODES[flagRaw] ?? 'unknown',
      flagRaw,
      officialBest: (read('lapImprovement', parseNumber) ?? 0) > 0,
    });
  }

  for (const car of cars.values()) {
    car.laps.sort((a, b) => a.lap - b.lap);

    for (let i = 0; i < car.laps.length; i++) {
      const lap = car.laps[i]!;
      const prev = car.laps[i - 1];
      if (prev && prev.lap === lap.lap) {
        issues.push({ line: 0, fatal: false, message: `Car ${car.number}: lap ${lap.lap} appears more than once` });
      }
      // The out-lap is the one after an in-lap. PIT_TIME also sits on the out-lap,
      // so either signal is enough.
      lap.pitOut = (prev?.pitIn ?? false) || lap.pitTime !== null;
    }

    const drivers = new Map<string, number | null>();
    for (const lap of car.laps) if (lap.driver && !drivers.has(lap.driver)) drivers.set(lap.driver, lap.driverNumber);
    car.drivers = [...drivers]
      .map(([name, number]) => ({ number, name }))
      .sort((a, b) => (a.number ?? 99) - (b.number ?? 99));
  }

  return { cars: [...cars.values()], issues, headers, columns, unmappedHeaders };
}
