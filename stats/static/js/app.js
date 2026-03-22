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

    // -------------------------------------------------------------------------
    // Card zoom modal
    // -------------------------------------------------------------------------
    function initCardZoom() {
        const modal = document.getElementById("card-zoom-modal");
        const zoomImg = document.getElementById("card-zoom-img");
        const backdrop = document.getElementById("card-zoom-backdrop");
        if (!modal || !zoomImg) return;

        function openZoom(src, fallback) {
            zoomImg.onerror = fallback
                ? function () { zoomImg.src = fallback; zoomImg.onerror = null; }
                : null;
            zoomImg.src = src;
            modal.classList.add("open");
            modal.setAttribute("aria-hidden", "false");
        }

        function closeZoom() {
            modal.classList.remove("open");
            modal.setAttribute("aria-hidden", "true");
            zoomImg.src = "";
        }

        backdrop.addEventListener("click", closeZoom);
        zoomImg.addEventListener("click", closeZoom);

        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape") closeZoom();
        });

        // Deck detail zoom buttons
        document.addEventListener("click", function (e) {
            const btn = e.target.closest(".card-zoom-btn[data-card-image]");
            if (!btn) return;
            e.preventDefault();
            openZoom(btn.dataset.cardImage, btn.dataset.cardFallback || "");
        });

        // Gallery card items
        document.addEventListener("click", function (e) {
            if (e.target.closest(".card-action-btn")) return;
            const item = e.target.closest(".card-item[data-card-image]");
            if (!item) return;
            openZoom(item.dataset.cardImage, item.dataset.cardFallback || "");
        });
    }

    // -------------------------------------------------------------------------
    // Copy card name
    // -------------------------------------------------------------------------
    function initCopyCardName() {
        document.addEventListener("click", function (e) {
            const btn = e.target.closest(".card-copy-name");
            if (!btn) return;
            e.stopPropagation();
            const name = btn.dataset.cardName;
            if (!name) return;
            navigator.clipboard.writeText(name).then(function () {
                const icon = btn.querySelector("i");
                if (icon) {
                    icon.classList.replace("bi-clipboard", "bi-clipboard-check");
                    setTimeout(function () { icon.classList.replace("bi-clipboard-check", "bi-clipboard"); }, 1500);
                }
            });
        });
    }

    // -------------------------------------------------------------------------
    // Card preview popup
    // -------------------------------------------------------------------------
    function initCardPreviews() {
        const popup = document.getElementById("card-preview-popup");
        const img = document.getElementById("card-preview-img");
        if (!popup || !img) return;

        const OFFSET = 16; // px gap from cursor

        function position(e) {
            const pw = popup.offsetWidth;
            const ph = popup.offsetHeight;
            let x = e.clientX + OFFSET;
            let y = e.clientY + OFFSET;
            if (x + pw > window.innerWidth) x = e.clientX - pw - OFFSET;
            if (y + ph > window.innerHeight) y = e.clientY - ph - OFFSET;
            popup.style.left = x + "px";
            popup.style.top = y + "px";
        }

        document.addEventListener("mouseover", function (e) {
            const link = e.target.closest("a.card-link[data-card-image]");
            if (!link) return;
            const src = link.dataset.cardImage;
            const fallback = link.dataset.cardFallback || "";
            img.onerror = fallback ? function () { img.src = fallback; img.onerror = null; } : null;
            img.src = src;
            popup.style.display = "block";
            position(e);
        });

        document.addEventListener("mousemove", function (e) {
            if (popup.style.display === "block") position(e);
        });

        document.addEventListener("mouseout", function (e) {
            const link = e.target.closest("a.card-link[data-card-image]");
            if (link) popup.style.display = "none";
        });
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

        initCardPreviews();
        initCardZoom();
        initCopyCardName();
    });
})();

