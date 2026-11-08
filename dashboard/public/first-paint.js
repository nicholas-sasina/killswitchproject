// Runs before the first paint. index.html loads it as a plain script in
// <head>; it is a file, not inline, because the hosted dashboard's
// Content-Security-Policy (script-src 'self') blocks inline scripts. It holds
// up the first paint while it loads, so keep it tiny.
(function () {
  // Without a session in this tab, the sign-in screen comes first. Mark that
  // before React mounts (see src/components/signin.css), so the website's
  // fade to night is not followed by a flash.
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
