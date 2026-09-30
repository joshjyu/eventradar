/**
 * Events page: loads the published feed and renders it.
 *
 * Event data comes from third-party sites, so it is only ever inserted as
 * text (textContent), never as HTML.
 */

import {
  DEFAULT_PAGE_SIZE,
  filterEvents,
  groupEvents,
  isNew,
  pageSize,
  paginate,
  placeLabel,
  platforms,
  sortEvents,
  sortOrder,
  summaryText,
  whenLabel,
} from "./lib.js";

const params = new URLSearchParams(location.search);
const PROFILE = /^[a-z0-9-]+$/.test(params.get("profile") || "")
  ? params.get("profile")
  : "socal-tech";
const BASE = `/v1/${PROFILE}`;
const KIND_LABELS = {
  hackathon: "Hackathon",
  conference: "Conference",
  workshop: "Workshop",
  meetup: "Meetup",
};

const $ = (id) => document.getElementById(id);

// Current page, 1-based; filter changes return to the first page.
let page = Number(params.get("page")) || 1;

/**
 * Create an element with optional class and text.
 *
 * @param {string} tag Tag name.
 * @param {string} [className] Class attribute.
 * @param {string} [text] Text content.
 * @returns {HTMLElement} The element.
 */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * Read the current filters from the controls.
 *
 * @returns {{query: string, kind: string, sort: string, size: number}}
 *   Filters, sort order, and page size (0 for all).
 */
function currentFilters() {
  const pressed = document.querySelector(".chip[aria-pressed='true']");
  return {
    query: $("q").value.trim(),
    kind: pressed ? pressed.dataset.kind : "",
    sort: sortOrder($("sort").value),
    size: pageSize($("per-page").value),
  };
}

/**
 * Mirror the filters and page in the address bar so views can be shared.
 *
 * @param {{query: string, kind: string, sort: string, size: number}}
 *   filters Filters, sort order, and page size.
 * @param {number} shownPage Page being shown.
 */
function saveFilters(filters, shownPage) {
  const next = new URLSearchParams();
  if (PROFILE !== "socal-tech") next.set("profile", PROFILE);
  if (filters.query) next.set("q", filters.query);
  if (filters.kind) next.set("kind", filters.kind);
  if (filters.sort !== "date") next.set("sort", filters.sort);
  if (filters.size !== DEFAULT_PAGE_SIZE) {
    next.set("show", filters.size ? String(filters.size) : "all");
  }
  if (shownPage > 1) next.set("page", String(shownPage));
  const query = next.toString();
  history.replaceState(null, "", query ? `?${query}` : location.pathname);
}

/**
 * Apply filters from the address bar to the controls.
 */
function restoreFilters() {
  $("q").value = params.get("q") || "";
  $("sort").value = sortOrder(params.get("sort"));
  const size = pageSize(params.get("show"));
  $("per-page").value = size ? String(size) : "all";
  const kind = params.get("kind") || "";
  for (const chip of document.querySelectorAll(".chip")) {
    chip.setAttribute("aria-pressed", String(chip.dataset.kind === kind));
  }
}

/**
 * Render one event as a list item.
 *
 * @param {object} event Published event.
 * @param {Date} now Reference time.
 * @param {boolean} withDay Show the date (headings are not days).
 * @returns {HTMLElement} The card.
 */
function card(event, now, withDay) {
  const item = el("li", "event");
  if (event.status === "cancelled") item.classList.add("cancelled");
  item.append(el("p", "when", whenLabel(event, withDay)));
  const title = el("h3", "title");
  if (event.url && /^https?:\/\//.test(event.url)) {
    const link = el("a", "", event.title);
    link.href = event.url;
    link.rel = "noopener noreferrer";
    link.target = "_blank";
    title.append(link);
  } else {
    title.textContent = event.title;
  }
  item.append(title);
  const place = placeLabel(event);
  if (place) item.append(el("p", "where", place));
  const tags = el("p", "tags");
  if (event.status === "cancelled") tags.append(el("span", "tag alert", "Cancelled"));
  if (isNew(event, now)) tags.append(el("span", "tag new", "New"));
  for (const kind of event.kinds || []) {
    tags.append(el("span", "tag", KIND_LABELS[kind] || kind));
  }
  if (event.size_signal) {
    tags.append(el("span", "tag", `${event.size_signal.toLocaleString()} signed up`));
  }
  tags.append(el("span", "source", `via ${platforms(event).join(", ")}`));
  item.append(tags);
  return item;
}

/**
 * Re-render the list for the current filters.
 *
 * @param {object[]} events All upcoming events.
 */
function render(events) {
  const now = new Date();
  const filters = currentFilters();
  const matched = sortEvents(filterEvents(events, filters), filters.sort);
  const view = paginate(matched, filters.size, page);
  page = view.page;
  saveFilters(filters, page);
  const container = $("events");
  container.replaceChildren();
  $("summary").textContent = summaryText(view, matched.length, events.length);
  $("pager").hidden = view.pages < 2;
  $("page-info").textContent = `Page ${view.page} of ${view.pages}`;
  $("prev-page").disabled = view.page <= 1;
  $("next-page").disabled = view.page >= view.pages;
  if (!matched.length) {
    container.append(el("p", "empty", "No events match these filters."));
    return;
  }
  const withDay = filters.sort !== "date";
  for (const [heading, group] of groupEvents(view.items, filters.sort)) {
    const section = el("section", "day");
    section.append(el("h2", "", heading));
    const list = el("ol", "event-list");
    for (const event of group) list.append(card(event, now, withDay));
    section.append(list);
    container.append(section);
  }
}

/**
 * Describe feed freshness from the manifest.
 *
 * @param {object} manifest Published manifest.
 * @returns {string} Status line.
 */
function statusLine(manifest) {
  const updated = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Los_Angeles",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(manifest.generated_at));
  const ok = manifest.sources.filter((s) => s.status === "ok").length;
  return `Updated ${updated} PT from ${ok} source${ok === 1 ? "" : "s"}.`;
}

/**
 * Load the feed and wire up the controls.
 */
async function main() {
  restoreFilters();
  let feed;
  try {
    const [feedResponse, manifestResponse] = await Promise.all([
      fetch(`${BASE}/events.json`),
      fetch(`${BASE}/manifest.json`),
    ]);
    if (!feedResponse.ok) throw new Error(`HTTP ${feedResponse.status}`);
    feed = await feedResponse.json();
    if (manifestResponse.ok) {
      $("status").textContent = statusLine(await manifestResponse.json());
    }
  } catch (error) {
    $("status").textContent = "Events could not be loaded. Try again later.";
    console.error(error);
    return;
  }
  const events = feed.events;
  $("calendar-link").href = `webcal://${location.host}${BASE}/events.ics`;
  $("ics-link").href = `${BASE}/events.ics`;
  $("rss-link").href = `${BASE}/feed.xml`;
  $("json-link").href = `${BASE}/events.json`;
  const refilter = () => {
    page = 1;
    render(events);
  };
  const turn = (step) => {
    page += step;
    render(events);
    // Bring the list's top back into view below the sticky controls.
    const controls = document.querySelector(".controls").offsetHeight;
    const top = $("summary").getBoundingClientRect().top + scrollY;
    scrollTo({ top: top - controls - 8 });
  };
  $("q").addEventListener("input", refilter);
  $("per-page").addEventListener("change", refilter);
  $("sort").addEventListener("change", refilter);
  $("prev-page").addEventListener("click", () => turn(-1));
  $("next-page").addEventListener("click", () => turn(1));
  for (const chip of document.querySelectorAll(".chip")) {
    chip.addEventListener("click", () => {
      for (const other of document.querySelectorAll(".chip")) {
        other.setAttribute("aria-pressed", String(other === chip));
      }
      refilter();
    });
  }
  render(events);
}

main();
