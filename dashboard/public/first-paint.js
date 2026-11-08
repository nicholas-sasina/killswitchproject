// Resolve the system theme before first paint. Without this the page renders
// light and then flips to dark once React mounts, which is a visible flash on
// every reload. index.html loads this as a plain script in <head>, so it runs
// before the first paint. It is a file, not inline, because the hosted
// dashboard's Content-Security-Policy (script-src 'self') blocks inline
// scripts. It holds up the first paint while it loads, so keep it tiny;
// src/theme.ts owns everything after mount.
(function () {
  try {
    var dark =
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches;
    var theme = dark ? "dark" : "light";
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    document
      .querySelector('meta[name="theme-color"]')
      .setAttribute("content", dark ? "#0d0b0a" : "#f1ede4");
  } catch (error) {
    // No matchMedia: the light default already on <html> stands.
  }
  // Without a session in this tab, the sign-in screen comes first. Paint
  // its night stage from the start (see src/components/signin.css), so the
  // website's fade to night is not followed by a flash of paper.
  try {
    if (
      !sessionStorage.getItem("burn.people.session") &&
      !sessionStorage.getItem("burn.local.continued")
    )
      document.documentElement.dataset.stage = "signin";
  } catch (error) {
    // No session storage: the page paints as usual.
  }
})();
