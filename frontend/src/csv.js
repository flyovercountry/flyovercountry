/** Shared CSV building for flight exports — app.js's full-dataset download
 * and rect-export.js's area-select download build the same per-flight row
 * shape and the same escaping/formula-injection guard. One module keeps the
 * two from drifting apart. */
import { dayToDateStr, roleSlug } from "./data.js";

export const FLIGHT_CSV_COLUMNS = [
  "date", "hex", "roles", "type_code", "type", "countries", "origin",
  "destination", "origin_country", "dest_country", "distance_km",
  "altitude_kft", "theater_tasking", "theater_ever", "adsbexchange_url",
];

/** One flight's CSV row, keyed by FLIGHT_CSV_COLUMNS. `flight` is the
 * per-flight shape data.js's lookupFlight() returns (hex/ti/rs/day/cc/oa/
 * da/occ/dcc/path/kft/thc/the) — callers reading the columnar flights table
 * directly by row index build one of these per row first. `types` is
 * manifest.types[]. */
export function flightToRow(flight, types) {
  const typeObj = types?.[flight.ti];
  const date = flight.day != null ? dayToDateStr(flight.day) : "";
  const hex = flight.hex || "";
  const url = hex && date
    ? `https://globe.adsbexchange.com/?icao=${hex}&showTrace=${date}` : "";
  // Roles column is union-semantics-aware: EVERY role the flight has (not
  // just the ones matching an active filter), "+"-joined slugs so the field
  // stays comma-safe (see the roles taxonomy in data/in/config/roles.json).
  const roles = (flight.rs || []).map(roleSlug).join("+");
  return {
    date, hex, roles,
    type_code: typeObj?.code || "",
    type: typeObj?.label || typeObj?.code || "",
    countries: flight.cc || "",
    origin: flight.oa || "",
    destination: flight.da || "",
    origin_country: flight.occ || "",
    dest_country: flight.dcc || "",
    distance_km: flight.path || 0,
    altitude_kft: flight.kft ?? 0,
    theater_tasking: flight.thc ? "yes" : "no",
    theater_ever: flight.the ? "yes" : "no",
    adsbexchange_url: url,
  };
}

/** One CSV field, quoted/escaped for Excel/Sheets. Neutralizes spreadsheet
 * formula injection: a field starting with = + - @ (or tab/CR) can execute
 * as a formula in Excel/Sheets. Per-flight text (e.g. callsigns feeding
 * origin/destination) is broadcast by the aircraft itself, so every
 * exported cell is treated as untrusted. */
function escapeCsvField(v) {
  let s = String(v == null ? "" : v);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return s.includes(",") || s.includes('"') || s.includes("\n") ? `"${s.replace(/"/g, '""')}"` : s;
}

/** One CSV row (array of raw values) escaped and comma-joined. */
export function csvLine(values) {
  return values.map(escapeCsvField).join(",");
}

/** `rows` (array of flightToRow-shaped objects) -> full CSV text, header
 * row first, columns in FLIGHT_CSV_COLUMNS order. */
export function rowsToCsv(rows) {
  const lines = [csvLine(FLIGHT_CSV_COLUMNS)];
  for (const row of rows) {
    lines.push(csvLine(FLIGHT_CSV_COLUMNS.map(c => row[c])));
  }
  return lines.join("\n");
}
