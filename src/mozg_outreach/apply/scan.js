/* Lists the questions of a form and marks each with data-mozg-field so the actor finds it again. */
(mark) => {
  const text = (node) => (node ? node.innerText || node.textContent || "" : "").replace(/\s+/g, " ").trim();
  // A label wrapping its control would otherwise include the control's options and value.
  const ownText = (label) => {
    const copy = label.cloneNode(true);
    copy.querySelectorAll("input, select, textarea, option").forEach((n) => n.remove());
    return text(copy);
  };
  const labelOf = (el) => {
    if (el.labels && el.labels.length) return ownText(el.labels[0]);
    const aria = el.getAttribute("aria-label");
    if (aria) return aria.trim();
    const by = el.getAttribute("aria-labelledby");
    if (by) return by.split(/\s+/).map((id) => text(document.getElementById(id))).join(" ").trim();
    return (el.getAttribute("placeholder") || el.getAttribute("name") || "").trim();
  };
  const fields = [];
  const groups = new Map();
  let n = 0;
  for (const el of document.querySelectorAll("input, textarea, select")) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || (tag === "input" ? "text" : tag)).toLowerCase();
    if (["hidden", "submit", "button", "image", "reset", "search", "password"].includes(type)) continue;
    if (el.disabled || el.readOnly) continue;
    const required = el.required || el.getAttribute("aria-required") === "true";
    if (type === "radio") {
      const key = el.name || `radio-${n}`;
      let group = groups.get(key);
      if (!group) {
        const set = el.closest("fieldset");
        const legend = set ? text(set.querySelector("legend")) : "";
        group = { id: `f${n++}`, kind: "radio", label: legend || el.name || "", name: el.name || null,
                  required, options: [], max_length: null };
        groups.set(key, group);
        fields.push(group);
      }
      el.setAttribute(mark, group.id);
      group.required = group.required || required;
      group.options.push({ value: el.value, label: labelOf(el) });
      continue;
    }
    const id = `f${n++}`;
    el.setAttribute(mark, id);
    let kind = type;
    if (tag === "textarea") kind = "textarea";
    else if (tag === "select") kind = "select";
    else if (el.getAttribute("role") === "combobox") kind = "combobox";
    else if (!["email", "tel", "url", "number", "date", "checkbox", "file"].includes(type)) kind = "text";
    const options = tag === "select"
      ? [...el.options].filter((o) => o.value !== "").map((o) => ({ value: o.value, label: text(o) }))
      : [];
    const max = el.maxLength > 0 ? el.maxLength : null;
    fields.push({ id, kind, label: labelOf(el), name: el.getAttribute("name"), required, options,
                  max_length: max });
  }
  return fields;
}
