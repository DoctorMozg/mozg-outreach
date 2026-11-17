/* Lists the questions of a form and marks each with data-mozg-field so the actor finds it again. */
(mark) => {
  const text = (node) => (node ? node.innerText || node.textContent || "" : "").replace(/\s+/g, " ").trim();
  // Text of a node without its controls, so a label or block doesn't include options and values.
  const ownText = (node, drop = "input, select, textarea, option") => {
    const copy = node.cloneNode(true);
    copy.querySelectorAll(drop).forEach((n) => n.remove());
    return text(copy);
  };
  const CONTROLS = "input:not([type=hidden]), select, textarea";
  // An unlabelled control is named by the nearest block around it that holds no other question,
  // like Ashby's "Autofill from resume" card or a question above a group of options.
  const blockText = (start, mine) => {
    for (let node = start; node && node !== document.body; node = node.parentElement) {
      if ([...node.querySelectorAll(CONTROLS)].some((c) => !mine(c))) return "";
      const found = ownText(node, "input, select, textarea, option, label, button");
      if (found) return found.slice(0, 300);
    }
    return "";
  };
  const labelOf = (el) => {
    if (el.labels && el.labels.length) return ownText(el.labels[0]);
    const aria = el.getAttribute("aria-label");
    if (aria) return aria.trim();
    const by = el.getAttribute("aria-labelledby");
    if (by) return by.split(/\s+/).map((id) => text(document.getElementById(id))).join(" ").trim();
    return "";
  };
  // An upload's own label is often just "Attach"; the question is the group around it.
  const uploadGroup = (el) => {
    const group = el.getAttribute("type") === "file" && el.closest("[role=group]");
    return group ? labelOf(group) : "";
  };
  const nameOf = (el) =>
    uploadGroup(el) || labelOf(el) || blockText(el.parentElement, (c) => c === el) || (el.getAttribute("placeholder") || el.getAttribute("name") || "").trim();
  // Job pages carry closed menus, filters and modals; their fields can't be filled and aren't the form.
  // A file input hidden behind a styled button still takes files, so it stays while its label or form shows.
  const rendered = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  // A group's question is its fieldset legend or its ARIA group label; each input's label is an option.
  const groupLabel = (el) => {
    const set = el.closest("fieldset");
    const legend = set ? text(set.querySelector("legend")) : "";
    if (legend) return legend;
    const group = el.closest("[role=radiogroup], [role=group]");
    if (group && labelOf(group)) return labelOf(group);
    // Start where every option is inside, so one option's text isn't taken for the question.
    const members = [...document.getElementsByName(el.name)];
    let start = el.parentElement;
    while (start && !members.every((m) => start.contains(m))) start = start.parentElement;
    let found = blockText(start, (c) => c.name === el.name);
    for (const m of members) {
      const option = labelOf(m);
      if (option) found = found.replace(option, "");
    }
    return found.replace(/\s+/g, " ").trim();
  };
  // What the page or its resume autofill already put in; a select without a blank option always has one.
  const hasValue = (el, tag, type) => {
    if (type === "file") return el.files.length > 0;
    if (type === "checkbox" || type === "radio") return el.checked;
    if (tag === "select") return el.value !== "" && [...el.options].some((o) => o.value === "");
    return el.value.trim() !== "";
  };
  // Checkboxes sharing a name answer one multiple-choice question.
  const boxNames = new Map();
  for (const el of document.querySelectorAll("input[type=checkbox][name]")) {
    boxNames.set(el.name, (boxNames.get(el.name) || 0) + 1);
  }
  const fields = [];
  const groups = new Map();
  let n = 0;
  for (const el of document.querySelectorAll("input, textarea, select")) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || (tag === "input" ? "text" : tag)).toLowerCase();
    if (["hidden", "submit", "button", "image", "reset", "search", "password"].includes(type)) continue;
    if (el.disabled || el.readOnly) continue;
    // Site menus (a region picker in the footer) and the hidden mirrors custom dropdowns keep.
    if (!el.closest("form") && el.closest("header, nav, footer, [role=banner], [role=navigation], [role=contentinfo]")) continue;
    if (type !== "file" && el.closest("[aria-hidden=true]")) continue;
    const host = el.closest("label, form");
    if (!rendered(el) && !(type === "file" && host && rendered(host))) continue;
    const required = el.required || el.getAttribute("aria-required") === "true";
    const filled = hasValue(el, tag, type);
    const multiple = type === "checkbox" && boxNames.get(el.name) > 1;
    if (type === "radio" || multiple) {
      const kind = multiple ? "checkboxes" : "radio";
      const key = `${kind}:${el.name || `radio-${n}`}`;
      let group = groups.get(key);
      if (!group) {
        group = { id: `f${n++}`, kind, label: groupLabel(el) || el.name || "", name: el.name || null,
                  required, options: [], max_length: null, filled: false };
        groups.set(key, group);
        fields.push(group);
      }
      el.setAttribute(mark, group.id);
      group.required = group.required || required;
      group.filled = group.filled || filled;
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
    fields.push({ id, kind, label: nameOf(el), name: el.getAttribute("name"), required, options,
                  max_length: max, filled });
  }
  return fields;
}
