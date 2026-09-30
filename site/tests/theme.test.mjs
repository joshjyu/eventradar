// Tests for the theme script, run against a minimal stand-in document.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const SOURCE = readFileSync(new URL("../public/theme.js", import.meta.url), "utf8");

/**
 * Build a button stand-in.
 *
 * @param {string} choice Its data-theme-choice value.
 * @returns {object} Button with attributes and a click handler slot.
 */
function button(choice) {
  return {
    dataset: { themeChoice: choice },
    attributes: {},
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
    addEventListener(_type, handler) {
      this.click = handler;
    },
  };
}

/**
 * Run theme.js with a given storage and return the page stand-ins.
 *
 * @param {object} storage localStorage stand-in.
 * @returns {{root: object, buttons: object[]}} <html> and the buttons.
 */
function load(storage) {
  const root = { dataset: {} };
  const buttons = ["light", "dark", "system"].map(button);
  let ready;
  const document = {
    documentElement: root,
    querySelectorAll: () => buttons,
    addEventListener: (_type, handler) => {
      ready = handler;
    },
  };
  vm.runInNewContext(SOURCE, { document, localStorage: storage });
  ready();
  return { root, buttons };
}

/**
 * An in-memory localStorage.
 *
 * @param {object} [initial] Starting entries.
 * @returns {object} Storage stand-in.
 */
function memory(initial = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: (key) => (key in data ? data[key] : null),
    setItem: (key, value) => {
      data[key] = value;
    },
    removeItem: (key) => {
      delete data[key];
    },
  };
}

const pressed = (buttons) =>
  buttons.filter((b) => b.attributes["aria-pressed"] === "true").map((b) => b.dataset.themeChoice);

test("a saved choice applies on load", () => {
  const { root, buttons } = load(memory({ theme: "dark" }));
  assert.equal(root.dataset.theme, "dark");
  assert.deepEqual(pressed(buttons), ["dark"]);
});

test("no or unknown choice follows the device", () => {
  for (const storage of [memory(), memory({ theme: "purple" })]) {
    const { root, buttons } = load(storage);
    assert.equal(root.dataset.theme, undefined);
    assert.deepEqual(pressed(buttons), ["system"]);
  }
});

test("choosing saves, and System clears the saved choice", () => {
  const storage = memory();
  const { root, buttons } = load(storage);
  buttons[0].click();
  assert.equal(root.dataset.theme, "light");
  assert.equal(storage.data.theme, "light");
  buttons[2].click();
  assert.equal(root.dataset.theme, undefined);
  assert.equal("theme" in storage.data, false);
  assert.deepEqual(pressed(buttons), ["system"]);
});

test("blocked storage still switches the theme for the page view", () => {
  const blocked = {
    getItem() {
      throw new Error("blocked");
    },
    setItem() {
      throw new Error("blocked");
    },
    removeItem() {
      throw new Error("blocked");
    },
  };
  const { root, buttons } = load(blocked);
  assert.equal(root.dataset.theme, undefined);
  buttons[1].click();
  assert.equal(root.dataset.theme, "dark");
});
