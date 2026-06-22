// small UX touches for kiosk; you can expand as needed
document.addEventListener("DOMContentLoaded", () => {
  // auto-focus first empty input on each page
  const first = document.querySelector("input:not([type=hidden]), select");
  if (first) first.focus();

  // Guard against lag-induced double submits: once a form is submitted, disable
  // its submit button and block any further submits from the same form. The
  // server also rejects duplicates via a one-time token, but this stops the
  // extra taps from ever leaving the tablet.
  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", (e) => {
      // a prior handler (e.g. a confirm() dialog the user dismissed) already
      // cancelled this submit — don't lock the form
      if (e.defaultPrevented) return;
      if (form.dataset.submitted === "true") {
        e.preventDefault();
        return;
      }
      form.dataset.submitted = "true";
      const btn = form.querySelector("button[type=submit], button:not([type])");
      if (btn) {
        btn.disabled = true;
        btn.dataset.originalText = btn.textContent;
        btn.textContent = "Working…";
      }
    });
  });
});
