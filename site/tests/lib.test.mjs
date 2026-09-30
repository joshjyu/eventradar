// Tests for the page's pure helpers.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_PAGE_SIZE,
  areaOf,
  cityOf,
  dayHeading,
  filterEvents,
  groupEvents,
  isNew,
  localDay,
  pageSize,
  paginate,
  placeLabel,
  platformOf,
  platforms,
  sortEvents,
  sortOrder,
  summaryText,
  timeRange,
  whenLabel,
} from "../public/lib.js";

const NOW = new Date("2026-10-01T00:00:00Z");

/**
 * Build an event with defaults.
 *
 * @param {object} fields Overrides.
 * @returns {object} Event.
 */
function event(fields) {
  return {
    title: "Hack Night",
    start_utc: "2026-10-15T01:00:00Z",
    end_utc: "2026-10-15T04:00:00Z",
    tz: "America/Los_Angeles",
    kinds: [],
    sources: [],
    first_seen: "2026-09-20T00:00:00Z",
    ...fields,
  };
}

test("days and times are local to the event", () => {
  const e = event({});
  assert.equal(localDay(e), "2026-10-14");
  assert.equal(dayHeading("2026-10-14"), "Wednesday, October 14");
  assert.equal(timeRange(e), "6:00 PM – 9:00 PM PDT");
  assert.equal(
    timeRange(event({ end_utc: "2026-10-18T01:00:00Z" })),
    "Oct 14 – Oct 17",
  );
  assert.equal(timeRange(event({ end_utc: null, tz: null })), "6:00 PM PDT");
});

test("city and place labels", () => {
  assert.equal(cityOf("858 Production Pl, Newport Beach, CA 92663, US"), "Newport Beach");
  assert.equal(cityOf("Irvine, California"), "Irvine");
  assert.equal(cityOf("Somewhere"), "");
  assert.equal(cityOf("2522 N Ontario St, Burbank, ca, 91504, us"), "Burbank");
  assert.equal(cityOf("Los Angeles, California, US"), "Los Angeles");
  assert.equal(cityOf("Toronto, Ontario, Canada"), "");
  assert.equal(cityOf("Somewhere, US"), "");
  assert.equal(
    cityOf("2701 Fairview Rd, Costa Mesa, CA 92626, USA, California"),
    "Costa Mesa",
  );
  assert.equal(
    cityOf("9736 Engineers Ln, La Jolla, CA 92093, USA, San Diego, California"),
    "San Diego",
  );
  assert.equal(
    cityOf("832 S. Olive St Los Angeles, Ca 90014, Los Angeles, CA, 90014, us"),
    "Los Angeles",
  );
  assert.equal(
    placeLabel(event({ venue: "Hall", address: "1 Main St, Pasadena, CA" })),
    "Hall · Pasadena",
  );
  assert.equal(placeLabel(event({ attendance_mode: "online" })), "Online");
  assert.equal(
    placeLabel(event({ venue: "Hall", address: null, area: "Orange County" })),
    "Hall · Orange County",
  );
});

test("platform names are deduplicated", () => {
  const e = event({
    sources: [
      { source_id: "luma-la-tech-week" },
      { source_id: "luma-la-city" },
      { source_id: "meetup-socal-tech" },
      { source_id: "mlh-hackathons" },
    ],
  });
  assert.deepEqual(platforms(e), ["Luma", "Meetup", "MLH"]);
});

test("events first seen within a week are new", () => {
  assert.equal(isNew(event({ first_seen: "2026-09-29T00:00:00Z" }), NOW), true);
  assert.equal(isNew(event({ first_seen: "2026-09-20T00:00:00Z" }), NOW), false);
});

test("filters combine search and kind", () => {
  const events = [
    event({ title: "AI Hack Night", kinds: ["hackathon"] }),
    event({ title: "Founders Mixer", venue: "Irvine Hub", kinds: ["meetup"] }),
  ];
  const titles = (f) => filterEvents(events, f).map((e) => e.title);
  assert.deepEqual(titles({ query: "irvine" }), ["Founders Mixer"]);
  assert.deepEqual(titles({ kind: "hackathon" }), ["AI Hack Night"]);
  assert.deepEqual(titles({ kind: "hackathon", query: "mixer" }), []);
  assert.deepEqual(titles({ query: "ai night" }), ["AI Hack Night"]);
});

test("grouping keeps order within days", () => {
  const a = event({ title: "A", start_utc: "2026-10-15T01:00:00Z" });
  const b = event({ title: "B", start_utc: "2026-10-15T02:00:00Z" });
  const c = event({ title: "C", start_utc: "2026-10-16T18:00:00Z" });
  const groups = groupEvents(sortEvents([a, b, c], "date"), "date");
  assert.deepEqual(
    groups.map(([day, list]) => [day, list.map((e) => e.title)]),
    [
      ["Wednesday, October 14", ["A", "B"]],
      ["Friday, October 16", ["C"]],
    ],
  );
});

test("pageSize accepts only offered sizes", () => {
  assert.equal(pageSize("10"), 10);
  assert.equal(pageSize("50"), 50);
  assert.equal(pageSize("all"), 0);
  assert.equal(pageSize("7"), DEFAULT_PAGE_SIZE);
  assert.equal(pageSize(null), DEFAULT_PAGE_SIZE);
});

test("paginate slices and clamps pages", () => {
  const items = Array.from({ length: 23 }, (_, i) => i);
  const third = paginate(items, 10, 3);
  assert.deepEqual(third.items, [20, 21, 22]);
  assert.deepEqual([third.page, third.pages, third.start, third.end], [3, 3, 20, 23]);
  assert.equal(paginate(items, 10, 99).page, 3);
  assert.equal(paginate(items, 10, 0).page, 1);
  assert.equal(paginate(items, 10, Number.NaN).page, 1);
  const all = paginate(items, 0, 2);
  assert.deepEqual([all.items.length, all.page, all.pages], [23, 1, 1]);
  assert.deepEqual(paginate([], 10, 1).pages, 1);
});

test("summaryText mentions the range only when paging", () => {
  const items = Array.from({ length: 40 }, (_, i) => i);
  assert.equal(
    summaryText(paginate(items, 25, 2), 40, 40),
    "Showing 26–40 of 40 upcoming events",
  );
  assert.equal(
    summaryText(paginate(items, 10, 1), 40, 169),
    "Showing 1–10 of 40 matching events (169 upcoming)",
  );
  assert.equal(summaryText(paginate(items, 0, 1), 40, 40), "40 upcoming events");
  assert.equal(summaryText(paginate(items, 50, 1), 40, 169), "40 of 169 upcoming events");
});

test("sortOrder falls back to date", () => {
  assert.equal(sortOrder("city"), "city");
  assert.equal(sortOrder("source"), "source");
  assert.equal(sortOrder("nope"), "date");
  assert.equal(sortOrder(null), "date");
});

test("city order groups alphabetically, catch-alls last, by time within", () => {
  const events = [
    event({ title: "A", start_utc: "2026-10-01T01:00:00Z", address: "1 Main St, Irvine, CA" }),
    event({ title: "B", start_utc: "2026-10-02T01:00:00Z", address: null }),
    event({ title: "F", start_utc: "2026-10-02T02:00:00Z", address: null, area: "Orange County" }),
    event({ title: "C", start_utc: "2026-10-03T01:00:00Z", attendance_mode: "online" }),
    event({ title: "D", start_utc: "2026-10-04T01:00:00Z", address: "Burbank, ca, 91504, us" }),
    event({ title: "E", start_utc: "2026-10-05T01:00:00Z", address: "irvine, CA 92618" }),
  ];
  const groups = groupEvents(sortEvents(events, "city"), "city");
  assert.deepEqual(
    groups.map(([heading, list]) => [heading, list.map((e) => e.title)]),
    [
      ["Burbank", ["D"]],
      ["Irvine", ["A", "E"]],
      ["Orange County", ["F"]],
      ["Online", ["C"]],
      ["Location not listed", ["B"]],
    ],
  );
  assert.equal(areaOf(events[1]), "Location not listed");
});

test("source order groups by first platform", () => {
  const events = [
    event({ title: "A", sources: [{ source_id: "meetup-socal-tech" }] }),
    event({ title: "B", sources: [{ source_id: "mlh-hackathons" }] }),
    event({ title: "C", sources: [{ source_id: "luma-la-city" }, { source_id: "meetup-socal-tech" }] }),
  ];
  const groups = groupEvents(sortEvents(events, "source"), "source");
  assert.deepEqual(
    groups.map(([heading, list]) => [heading, list.map((e) => e.title)]),
    [
      ["Luma", ["C"]],
      ["Meetup", ["A"]],
      ["MLH", ["B"]],
    ],
  );
  assert.equal(platformOf(event({ sources: [] })), "Other");
});

test("whenLabel adds the day only for same-day events", () => {
  const sameDay = event({});
  assert.equal(whenLabel(sameDay, false), timeRange(sameDay));
  assert.equal(whenLabel(sameDay, true), `Wed, Oct 14 · ${timeRange(sameDay)}`);
  const multiDay = event({ end_utc: "2026-10-17T01:00:00Z" });
  assert.equal(whenLabel(multiDay, true), timeRange(multiDay));
});
