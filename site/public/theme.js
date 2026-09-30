/**
 * Color theme: "light", "dark", or "system" (follow the device).
 *
 * Loaded as a classic script in <head> so a saved choice applies before
 * the first paint. The choice is kept in localStorage, which may be
 * unavailable (private windows, blocked storage); the page then follows
 * the device.
 */
(() => {
  const KEY = "theme";
  const CHOICES = ["light", "dark", "system"];
  const root = document.documentElement;

  /**
   * Read the saved choice.
   *
   * @returns {string} A valid choice; "system" when none is saved.
   */
  function saved() {
    try {
      const value = localStorage.getItem(KEY);
      return CHOICES.includes(value) ? value : "system";
    } catch {
      return "system";
    }
  }

  /**
   * Apply a choice to the page and mark the pressed button.
   *
   * @param {string} choice "light", "dark", or "system".
   */
  function apply(choice) {
    if (choice === "system") delete root.dataset.theme;
    else root.dataset.theme = choice;
    for (const button of document.querySelectorAll("[data-theme-choice]")) {
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.themeChoice === choice),
      );
    }
  }

  /**
   * Save and apply a choice.
   *
   * @param {string} choice "light", "dark", or "system".
   */
  function choose(choice) {
    try {
      if (choice === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, choice);
    } catch {
      // Storage blocked: the choice lasts for this page view only.
    }
    apply(choice);
  }

  apply(saved());
  document.addEventListener("DOMContentLoaded", () => {
    apply(saved());
    for (const button of document.querySelectorAll("[data-theme-choice]")) {
      button.addEventListener("click", () => choose(button.dataset.themeChoice));
    }
  });
})();
