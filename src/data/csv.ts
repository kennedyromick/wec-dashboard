export interface CsvTable {
  /** Normalised header names (see normaliseHeader). */
  headers: string[];
  /** Data rows; `line` is the 1-based line number in the original file, for error messages. */
  rows: { line: number; fields: string[] }[];
}

/**
 * "  driver_number﻿" -> "DRIVER_NUMBER".
 * Al Kamel headers carry a UTF-8 BOM on the first column and stray leading spaces
 * (in 2026 Analysis files, columns 2-15 all start with a space).
 */
export function normaliseHeader(h: string): string {
  return h.replace(/﻿/g, '').trim().toUpperCase().replace(/[\s-]+/g, '_');
}

/** Split one line on `delimiter`, honouring double-quoted fields (Al Kamel doesn't quote today, but it's cheap insurance). */
function splitLine(line: string, delimiter: string): string[] {
  if (!line.includes('"')) return line.split(delimiter);
  const out: string[] = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === '"') {
      if (inQuotes && line[i + 1] === '"') { cur += '"'; i++; }
      else inQuotes = !inQuotes;
    } else if (c === delimiter && !inQuotes) {
      out.push(cur);
      cur = '';
    } else {
      cur += c;
    }
  }
  out.push(cur);
  return out;
}

/** Parse semicolon-separated text (CRLF or LF). Blank lines are skipped. */
export function parseCsv(text: string, delimiter = ';'): CsvTable {
  const lines = text.replace(/^﻿/, '').split(/\r?\n/);
  const headerIdx = lines.findIndex((l) => l.trim() !== '');
  if (headerIdx === -1) return { headers: [], rows: [] };

  const headers = splitLine(lines[headerIdx]!, delimiter).map(normaliseHeader);
  // Every Al Kamel line ends with ';', which produces one empty trailing column. Drop it.
  while (headers.length > 0 && headers[headers.length - 1] === '') headers.pop();

  const rows: CsvTable['rows'] = [];
  for (let i = headerIdx + 1; i < lines.length; i++) {
    const line = lines[i]!;
    if (line.trim() === '') continue;
    rows.push({ line: i + 1, fields: splitLine(line, delimiter) });
  }
  return { headers, rows };
}
