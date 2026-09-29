/* ══════════════════════════════════════════════════════════════
   AI Video Assistant — landing page behaviour.
   Scroll reveals, sticky-nav state, active section links and the
   mobile menu. The analyzer itself lives in main.js.
   ══════════════════════════════════════════════════════════════ */

(() => {
    const nav = document.getElementById("nav");
    const menuToggle = document.getElementById("menuToggle");
    const navLinks = document.getElementById("navLinks");

    /* ── Scroll reveal ─────────────────────────────────────────── */
    const reveals = document.querySelectorAll(".reveal");

    if ("IntersectionObserver" in window) {
        const revealer = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                if (!entry.isIntersecting) continue;
                entry.target.classList.add("is-visible");
                revealer.unobserve(entry.target);
            }
        }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });

        reveals.forEach((el) => revealer.observe(el));
    } else {
        reveals.forEach((el) => el.classList.add("is-visible"));
    }

    /* ── Highlight the nav link for the section in view ────────── */
    const links = [...navLinks.querySelectorAll('a[href^="#"]')];
    const sections = links
        .map((link) => document.querySelector(link.getAttribute("href")))
        .filter(Boolean);

    if ("IntersectionObserver" in window) {
        const spy = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                if (!entry.isIntersecting) continue;
                const id = `#${entry.target.id}`;
                links.forEach((link) => {
                    const active = link.getAttribute("href") === id;
                    link.classList.toggle("active", active);
                    if (active) link.setAttribute("aria-current", "true");
                    else link.removeAttribute("aria-current");
                });
            }
        }, { rootMargin: "-45% 0px -50% 0px" });

        sections.forEach((section) => spy.observe(section));
    }

    /* ── Nav border once the page scrolls ──────────────────────── */
    const onScroll = () => {
        nav.classList.toggle("scrolled", window.scrollY > 8);
        // Above the first linked section (hero, analyzer) no nav link is current.
        if (sections[0] && sections[0].getBoundingClientRect().top > window.innerHeight / 2) {
            links.forEach((link) => {
                link.classList.remove("active");
                link.removeAttribute("aria-current");
            });
        }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();

    /* ── Mobile menu ───────────────────────────────────────────── */
    function setMenu(open) {
        navLinks.classList.toggle("open", open);
        menuToggle.setAttribute("aria-expanded", String(open));
        menuToggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    }

    menuToggle.addEventListener("click", () => {
        setMenu(menuToggle.getAttribute("aria-expanded") !== "true");
    });

    links.forEach((link) => link.addEventListener("click", () => setMenu(false)));

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && navLinks.classList.contains("open")) {
            setMenu(false);
            menuToggle.focus();
        }
    });

    document.addEventListener("click", (event) => {
        if (navLinks.classList.contains("open") && !nav.contains(event.target)) setMenu(false);
    });

    /* ── Keep browser UI colour in step with the theme ─────────── */
    const themeMeta = document.querySelector('meta[name="theme-color"]');
    const syncThemeColor = () => {
        themeMeta.content = document.documentElement.dataset.theme === "light" ? "#f6fbfa" : "#0a1214";
    };
    new MutationObserver(syncThemeColor)
        .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    syncThemeColor();
})();
