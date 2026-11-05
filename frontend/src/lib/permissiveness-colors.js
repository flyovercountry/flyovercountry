/** Resolve a permissiveness level (0-2) to its map fill colour. The palette
 * lives in countries.modes.json's `permissiveness_levels`, next to each
 * level's legend text, so fill and legend cannot drift; this module holds
 * no copy. A level without a colour gets a neutral grey and one warning. */
const UNKNOWN_COLOR = "#5a5a5a";

let warned = false;

/**
 * @param {number|string} level  permissiveness level, 0-2
 * @param {object} levels        fc.permissivenessLevels (data.js's camelCase
 *                               view of countries.modes.json's
 *                               permissiveness_levels)
 */
export function colorForLevel(level, levels) {
  const color = levels?.[String(level)]?.color;
  if (color) return color;
  if (!warned) {
    warned = true;
    console.warn(
      `permissiveness level ${level} has no "color" in countries.modes.json's ` +
        `permissiveness_levels — falling back to ${UNKNOWN_COLOR}`,
    );
  }
  return UNKNOWN_COLOR;
}
