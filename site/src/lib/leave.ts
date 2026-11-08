// Leaving for sign-in: fade the page to night, then navigate, so the
// dashboard's dark sign-in stage arrives without a white flash.

const FADE_MS = 220;

export function leaveTo(href: string) {
  if (document.querySelector(".leave-veil")) return;
  const veil = document.createElement("div");
  veil.className = "leave-veil";
  document.body.appendChild(veil);
  const go = () => window.location.assign(href);
  if (typeof veil.animate !== "function") return go();
  const fade = veil.animate([{ opacity: 0 }, { opacity: 1 }], {
    duration: FADE_MS,
    easing: "ease-out",
    fill: "forwards",
  });
  let gone = false;
  const once = () => {
    if (!gone) {
      gone = true;
      go();
    }
  };
  fade.onfinish = once;
  // A hidden tab may never finish the animation; leave anyway.
  window.setTimeout(once, FADE_MS + 200);
}

/** Coming back with the browser's back button must not land on the veil. */
export function clearVeilOnReturn() {
  window.addEventListener("pageshow", (e) => {
    if (e.persisted) {
      document.querySelectorAll(".leave-veil").forEach((v) => v.remove());
    }
  });
}
