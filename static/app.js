// small UX touches for kiosk; you can expand as needed
document.addEventListener("DOMContentLoaded", () => {
  // auto-focus first empty input on each page
  const first = document.querySelector("input:not([type=hidden]), select");
  if (first) first.focus();
});
