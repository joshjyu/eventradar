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
  ["mlh-", "MLH"],
  ["eventbrite-", "Eventbrite"],
  ["site-", "Event website"],
];
// A state, optionally followed by a ZIP; platforms differ in case
// ("CA 92663", "ca", "California").
const STATE_PART = /^([a-z]{2}|california)(\s+\d{5}(-\d{4})?)?$/i;
const COUNTRY = /^(us|usa|u\.s\.a?\.?|united states)$/i;
const STATES = new Set(
  (
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI " +
    "MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT " +
    "VA WA WV WI WY PR CALIFORNIA"
  ).split(" "),
);

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
 * Whether an event ends on a later local day than it starts.
 *
 * @param {object} event Published event.
 * @returns {boolean} True for multi-day events.
 */
function spansDays(event) {
  return (
    Boolean(event.end_utc) &&
    localDay({ ...event, start_utc: event.end_utc }) !== localDay(event)
  );
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
  if (end && spansDays(event)) {
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
  const isState = (p) => {
    const match = STATE_PART.exec(p);
    return match !== null && STATES.has(match[1].toUpperCase());
  };
  const isFiller = (p) => isState(p) || COUNTRY.test(p) || /^\d{5}/.test(p);
  // The last "City, ST" pair wins: platforms may repeat a messy street
  // line first ("832 S Olive St Los Angeles, Ca 90014, Los Angeles, CA,
  // 90014, us") or append the state again after the country ("Costa
  // Mesa, CA 92626, USA, California").
  for (let i = parts.length - 1; i > 0; i--) {
    if (isState(parts[i]) && parts[i - 1] && !isFiller(parts[i - 1])) {
      return parts[i - 1];
    }
  }
  return "";
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
  const parts = [event.venue, city || event.area].filter(Boolean);
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
 * @param {{query?: string, kind?: string}} filters Search text and kind.
 * @returns {object[]} Matching events, in input order.
 */
export function filterEvents(events, filters) {
  const words = (filters.query || "").toLowerCase().split(/\s+/).filter(Boolean);
  return events.filter((event) => {
    if (filters.kind && !(event.kinds || []).includes(filters.kind)) return false;
    if (!words.length) return true;
    const text = [event.title, event.venue, event.address, event.organizer]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return words.every((w) => text.includes(w));
  });
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

export const SORTS = ["date", "city", "source"];
const ONLINE = "Online";
const UNLISTED = "Location not listed";

/**
 * Read a sort order from a control or query value.
 *
 * @param {string | null} value `date`, `city`, or `source`.
 * @returns {string} The sort; `date` when unknown.
 */
export function sortOrder(value) {
  return SORTS.includes(value) ? value : "date";
}

/**
 * The city an event is in, for grouping; the region's area (e.g. a
 * county) when the address names no city.
 *
 * @param {object} event Published event.
 * @returns {string} City, area, "Online", or "Location not listed".
 */
export function areaOf(event) {
  if (event.attendance_mode === "online") return ONLINE;
  return cityOf(event.address) || event.area || UNLISTED;
}

/**
 * The platform an event is grouped under: the first it was found on.
 *
 * @param {object} event Published event.
 * @returns {string} Platform name.
 */
export function platformOf(event) {
  return platforms(event)[0] || "Other";
}

/**
 * Group key and heading for an event under a sort order.
 *
 * @param {object} event Published event.
 * @param {string} sort `date`, `city`, or `source`.
 * @returns {string} Heading text; events with equal headings group.
 */
function headingOf(event, sort) {
  if (sort === "city") return areaOf(event);
  if (sort === "source") return platformOf(event);
  return dayHeading(localDay(event));
}

/**
 * Compare two group headings: alphabetical, catch-all groups last.
 *
 * @param {string} a Heading.
 * @param {string} b Heading.
 * @returns {number} Sort comparison.
 */
function compareHeadings(a, b) {
  const rank = (h) => (h === UNLISTED ? 2 : h === ONLINE ? 1 : 0);
  return rank(a) - rank(b) || a.localeCompare(b, "en", { sensitivity: "base" });
}

/**
 * Order events for display. Date order is the feed's own; city and source
 * orders are alphabetical by group, then by start time within a group.
 *
 * @param {object[]} events Events sorted by start time.
 * @param {string} sort `date`, `city`, or `source`.
 * @returns {object[]} A new, ordered array.
 */
export function sortEvents(events, sort) {
  if (sort === "date") return [...events];
  // Array.prototype.sort is stable, so start-time order survives in groups.
  return [...events].sort((a, b) =>
    compareHeadings(headingOf(a, sort), headingOf(b, sort)),
  );
}

/**
 * Group consecutive events under headings for a sort order.
 *
 * City headings compare case-insensitively ("Irvine" and "irvine" group
 * together, shown as first written).
 *
 * @param {object[]} events Events already ordered by `sortEvents`.
 * @param {string} sort `date`, `city`, or `source`.
 * @returns {Array<[string, object[]]>} Heading and its events.
 */
export function groupEvents(events, sort) {
  const groups = [];
  for (const event of events) {
    const heading = headingOf(event, sort);
    const last = groups[groups.length - 1];
    if (last && compareHeadings(last[0], heading) === 0) {
      last[1].push(event);
    } else {
      groups.push([heading, [event]]);
    }
  }
  return groups;
}

/**
 * When an event happens, with its day when the heading does not say it.
 *
 * @param {object} event Published event.
 * @param {boolean} withDay Prefix a short date to same-day events.
 * @returns {string} e.g. "Tue, Oct 13 · 6:00 PM – 9:00 PM PDT".
 */
export function whenLabel(event, withDay) {
  const range = timeRange(event);
  if (!withDay || spansDays(event)) return range;
  const [y, m, d] = localDay(event).split("-").map(Number);
  const day = new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    weekday: "short",
    month: "short",
    day: "numeric",
  }).format(new Date(Date.UTC(y, m - 1, d)));
  return `${day} · ${range}`;
}
