/* MTG Arena Stats custom JS - extend via this file */

(function () {
    const DARK = "dark";
    const LIGHT = "light";

    function applyTheme(theme) {
        document.documentElement.setAttribute("data-theme", theme);
        localStorage.setItem("theme", theme);
        const btn = document.getElementById("theme-toggle");
        if (btn) {
            btn.textContent = theme === DARK ? "☀" : "🌙";
            btn.setAttribute("aria-label", theme === DARK ? "Switch to light mode" : "Switch to dark mode");
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        const current = document.documentElement.getAttribute("data-theme") || DARK;
        applyTheme(current);

        const btn = document.getElementById("theme-toggle");
        if (btn) {
            btn.addEventListener("click", function () {
                const next = document.documentElement.getAttribute("data-theme") === DARK ? LIGHT : DARK;
                applyTheme(next);
            });
        }
    });
})();

