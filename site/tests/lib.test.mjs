// Tests for the page's pure helpers.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_PAGE_SIZE,
  cityOf,
  dayHeading,
  filterEvents,
  groupByDay,
  localDay,
  pageSize,
  paginate,
  placeLabel,
  platforms,
  summaryText,
  timeRange,
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
    placeLabel(event({ venue: "Hall", address: "1 Main St, Pasadena, CA" })),
    "Hall · Pasadena",
  );
  assert.equal(placeLabel(event({ attendance_mode: "online" })), "Online");
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

test("filters combine search, kind, and recency", () => {
  const events = [
    event({ title: "AI Hack Night", kinds: ["hackathon"] }),
    event({ title: "Founders Mixer", venue: "Irvine Hub", kinds: ["meetup"] }),
    event({ title: "Fresh Talk", first_seen: "2026-09-29T00:00:00Z" }),
  ];
  const titles = (f) => filterEvents(events, f, NOW).map((e) => e.title);
  assert.deepEqual(titles({ query: "irvine" }), ["Founders Mixer"]);
  assert.deepEqual(titles({ kind: "hackathon" }), ["AI Hack Night"]);
  assert.deepEqual(titles({ newOnly: true }), ["Fresh Talk"]);
  assert.deepEqual(titles({ query: "ai night" }), ["AI Hack Night"]);
});

test("grouping keeps order within days", () => {
  const a = event({ title: "A", start_utc: "2026-10-15T01:00:00Z" });
  const b = event({ title: "B", start_utc: "2026-10-15T02:00:00Z" });
  const c = event({ title: "C", start_utc: "2026-10-16T18:00:00Z" });
  const groups = groupByDay([a, b, c]);
  assert.deepEqual(
    groups.map(([day, list]) => [day, list.map((e) => e.title)]),
    [
      ["2026-10-14", ["A", "B"]],
      ["2026-10-16", ["C"]],
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
