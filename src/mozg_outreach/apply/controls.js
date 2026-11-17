/* Lists the links, tabs and buttons outside every form and marks them so Python finds them again. */
(mark) => {
  const text = (el) => (el.innerText || el.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim();
  const shown = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  // Pages carry long menus; controls that sound like applying go first so the cap never drops them.
  const applying = /appl|bewerb|postul|candidat|solicit/i;
  const found = [];
  for (const el of document.querySelectorAll("a[href], button, [role=tab], [role=button]")) {
    // A control inside a form, or a button owned by one, could send it.
    if (!shown(el) || el.closest("form") || el.form) continue;
    const label = text(el).slice(0, 80);
    if (!label) continue;
    const link = el.tagName === "A" && /^https?:/.test(el.href) && !el.getAttribute("href").startsWith("#");
    const kind = link ? "link" : el.getAttribute("role") === "tab" ? "tab" : "button";
    found.push({ el, kind, text: label, href: link ? el.href : null });
  }
  found.sort((a, b) => Number(applying.test(b.text)) - Number(applying.test(a.text)));
  return found.slice(0, 60).map((c, i) => {
    c.el.setAttribute(mark, `c${i}`);
    return { id: `c${i}`, kind: c.kind, text: c.text, href: c.href };
  });
}
