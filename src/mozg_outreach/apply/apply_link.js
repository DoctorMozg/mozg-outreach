/* The visible link a job page offers to its form; Python opens its address and never clicks it. */
() => {
  const words = /^(apply|apply now|apply here|apply online|apply for (this )?(job|position|role)|jetzt bewerben|bewerben|postuler|candidater)$/i;
  for (const a of document.querySelectorAll("a[href]")) {
    const label = (a.innerText || "").replace(/\s+/g, " ").trim();
    if (!words.test(label) || a.closest("form") || a.getClientRects().length === 0) continue;
    if (/^https?:/.test(a.href)) return a.href;
  }
  return null;
}
