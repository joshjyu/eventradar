/**
 * Pure helpers for the events page: formatting, filtering, grouping.
 * No DOM access here, so these run under `node --test` as well.
 */

export const DEFAULT_TZ = "America/Los_Angeles";
const NEW_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;
const PLATFORMS = [
  ["luma-", "Luma"],
  ["meetup-", "Meetup"],
  ["devpost-", "Devpost"],
  ["eventbrite-", "Eventbrite"],
  ["site-", "Event website"],
];
const STATE_PART = /^([A-Z]{2}|California)( \d{5}(-\d{4})?)?$/;

/**
 * Pick the zone an event is local to.
 *
 * @param {object} event Published event.
 * @returns {string} IANA zone name.
 */
export function zoneOf(event) {
  return event.tz || DEFAULT_TZ;
}

/**
 * Calendar day of the event's start, in its own zone.
 *
 * @param {object} event Published event.
 * @returns {string} Day as YYYY-MM-DD.
 */
export function localDay(event) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: zoneOf(event),
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(event.start_utc));
}

/**
 * Heading for a day group, e.g. "Monday, October 12".
 *
 * @param {string} day Day as YYYY-MM-DD.
 * @returns {string} Readable heading.
 */
export function dayHeading(day) {
  const [y, m, d] = day.split("-").map(Number);
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(new Date(Date.UTC(y, m - 1, d)));
}

/**
 * Format the event's time span in its own zone.
 *
 * Same-day events show times; multi-day events show dates.
 *
 * @param {object} event Published event.
 * @returns {string} e.g. "6:00 PM – 9:00 PM PDT" or "Oct 19 – Oct 24".
 */
export function timeRange(event) {
  const timeZone = zoneOf(event);
  const start = new Date(event.start_utc);
  const end = event.end_utc ? new Date(event.end_utc) : null;
  if (end && localDay({ ...event, start_utc: event.end_utc }) !== localDay(event)) {
    const date = new Intl.DateTimeFormat("en-US", {
      timeZone,
      month: "short",
      day: "numeric",
    });
    return `${date.format(start)} – ${date.format(end)}`;
  }
  const time = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hour: "numeric",
    minute: "2-digit",
  });
  const zone = new Intl.DateTimeFormat("en-US", {
    timeZone,
    timeZoneName: "short",
  })
    .formatToParts(start)
    .find((p) => p.type === "timeZoneName").value;
  const span = end ? `${time.format(start)} – ${time.format(end)}` : time.format(start);
  return `${span} ${zone}`;
}

/**
 * Extract the city from a one-line address.
 *
 * @param {string|null} address e.g. "858 Production Pl, Newport Beach, CA 92663, US".
 * @returns {string} The city, or "" when it cannot be found.
 */
export function cityOf(address) {
  if (!address) return "";
  const parts = address.split(",").map((p) => p.trim());
  const state = parts.findIndex((p) => STATE_PART.test(p));
  return state > 0 ? parts[state - 1] : "";
}

/**
 * Short place label: venue and city when known.
 *
 * @param {object} event Published event.
 * @returns {string} e.g. "Maker Hall · Pasadena".
 */
export function placeLabel(event) {
  if (event.attendance_mode === "online") return "Online";
  const city = cityOf(event.address);
  const parts = [event.venue, city].filter(Boolean);
  if (parts.length) return [...new Set(parts)].join(" · ");
  return event.address ? event.address.split(",")[0] : "";
}

/**
 * Human names of the platforms an event was found on.
 *
 * @param {object} event Published event.
 * @returns {string[]} Distinct platform names.
 */
export function platforms(event) {
  const names = (event.sources || []).map(({ source_id: id }) => {
    const match = PLATFORMS.find(([prefix]) => id.startsWith(prefix));
    return match ? match[1] : id;
  });
  return [...new Set(names)];
}

/**
 * Whether the event joined the feed within the last week.
 *
 * @param {object} event Published event.
 * @param {Date} now Reference time.
 * @returns {boolean} True for recent additions.
 */
export function isNew(event, now) {
  return now - new Date(event.first_seen) <= NEW_WINDOW_MS;
}

/**
 * Apply the page's filters.
 *
 * @param {object[]} events Published events.
 * @param {{query?: string, kind?: string, newOnly?: boolean}} filters
 *   Search text, kind, and new-only toggle.
 * @param {Date} now Reference time.
 * @returns {object[]} Matching events, in input order.
 */
export function filterEvents(events, filters, now) {
  const words = (filters.query || "").toLowerCase().split(/\s+/).filter(Boolean);
  return events.filter((event) => {
    if (filters.kind && !(event.kinds || []).includes(filters.kind)) return false;
    if (filters.newOnly && !isNew(event, now)) return false;
    if (!words.length) return true;
    const text = [event.title, event.venue, event.address, event.organizer]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return words.every((w) => text.includes(w));
  });
}

/**
 * Group events by local day, preserving order.
 *
 * @param {object[]} events Events sorted by start time.
 * @returns {Array<[string, object[]]>} Day and its events.
 */
export function groupByDay(events) {
  const groups = new Map();
  for (const event of events) {
    const day = localDay(event);
    if (!groups.has(day)) groups.set(day, []);
    groups.get(day).push(event);
  }
  return [...groups];
}

export const PAGE_SIZES = [10, 25, 50];
export const DEFAULT_PAGE_SIZE = 25;

/**
 * Read a page size from a control or query value.
 *
 * @param {string | null} value `10`, `25`, `50`, or `all`.
 * @returns {number} Events per page; 0 means all.
 */
export function pageSize(value) {
  if (value === "all") return 0;
  const size = Number(value);
  return PAGE_SIZES.includes(size) ? size : DEFAULT_PAGE_SIZE;
}

/**
 * Cut one page out of a list.
 *
 * @param {object[]} items Items to page through.
 * @param {number} size Items per page; 0 means all on one page.
 * @param {number} page Requested page, 1-based; clamped to the valid range.
 * @returns {{items: object[], page: number, pages: number, start: number,
 *   end: number}} The page's items, its number, the page count, and the
 *   0-based start and exclusive end within `items`.
 */
export function paginate(items, size, page) {
  const pages = size ? Math.max(1, Math.ceil(items.length / size)) : 1;
  const current = Math.min(Math.max(1, Math.trunc(page) || 1), pages);
  const start = size ? (current - 1) * size : 0;
  const end = size ? Math.min(start + size, items.length) : items.length;
  return { items: items.slice(start, end), page: current, pages, start, end };
}

/**
 * Describe what the list is showing.
 *
 * @param {{start: number, end: number, pages: number}} view Current page.
 * @param {number} matched Events matching the filters.
 * @param {number} total All upcoming events.
 * @returns {string} Summary line.
 */
export function summaryText(view, matched, total) {
  const range = `${view.start + 1}–${view.end}`;
  if (matched === total) {
    return view.pages > 1
      ? `Showing ${range} of ${total} upcoming events`
      : `${total} upcoming events`;
  }
  return view.pages > 1
    ? `Showing ${range} of ${matched} matching events (${total} upcoming)`
    : `${matched} of ${total} upcoming events`;
}
