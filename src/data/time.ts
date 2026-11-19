/**
 * Al Kamel writes durations in several shapes, depending on file type and magnitude:
 *   "21.542"        seconds only (short sectors)
 *   "1:39.473"      m:ss.sss (Analysis lap times)
 *   "4:44:55.345"   h:mm:ss.sss (Analysis ELAPSED, PIT_TIME)
 *   "1'32.952"      m'ss.sss (Classification files use an apostrophe for minutes)
 *   "1:00'59.234"   h:mm'ss.sss (Classification TOTAL_TIME)
 * All of them are base-60 groups, so we normalise the apostrophe to ':' and fold left.
 * Returns seconds, or null for empty / unparseable input.
 */
export function parseDuration(raw: string | undefined): number | null {
  if (raw === undefined) return null;
  const s = raw.trim().replace(/'/g, ':');
  if (s === '') return null;
  if (!/^\d+(:\d{1,2})*(\.\d+)?$/.test(s)) return null;

  let total = 0;
  for (const part of s.split(':')) total = total * 60 + Number(part);
  // Round to the millisecond so sums of sectors don't drift (0.1 + 0.2 style errors).
  return Math.round(total * 1000) / 1000;
}

/** Plain decimal number ("307.7"), or null for empty / non-numeric input. */
export function parseNumber(raw: string | undefined): number | null {
  if (raw === undefined) return null;
  const s = raw.trim();
  if (s === '') return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

/** Seconds -> "1:32.066" (or "1:02:03.456" past an hour). For display only. */
export function formatDuration(seconds: number | null): string {
  if (seconds === null) return '-';
  const ms = Math.round(seconds * 1000);
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const sec = ((ms % 60_000) / 1000).toFixed(3).padStart(6, '0');
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}
