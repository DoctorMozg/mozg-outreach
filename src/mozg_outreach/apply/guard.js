/* Turns form submission off in this frame while the service fills it; on=false turns it back on. */
(on) => {
  let guard = window.__mozgSubmitGuard;
  if (!guard) {
    guard = { on: false, blocked: 0 };
    window.__mozgSubmitGuard = guard;
    const proto = HTMLFormElement.prototype;
    const submit = proto.submit;
    const requestSubmit = proto.requestSubmit;
    proto.submit = function () {
      if (guard.on) { guard.blocked += 1; return; }
      return submit.call(this);
    };
    proto.requestSubmit = function (...args) {
      if (guard.on) { guard.blocked += 1; return; }
      return requestSubmit.apply(this, args);
    };
    window.addEventListener("submit", (event) => {
      if (!guard.on) return;
      guard.blocked += 1;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
  }
  guard.on = on;
  return guard.blocked;
}
