/**
 * A polite live region outside the app's root, for news that outlives the
 * control that caused it. Resolving a flag removes the flag's row (and its
 * buttons) at once, so a region inside that row would vanish before a
 * screen reader read it. This one lives on <body>, and stays readable while
 * a dialog makes #root inert.
 */
let region: HTMLElement | null = null;

export function announce(text: string): void {
  if (typeof document === "undefined") return;
  if (!region || !region.isConnected) {
    region = document.createElement("div");
    region.className = "visually-hidden";
    region.setAttribute("role", "status");
    region.setAttribute("aria-live", "polite");
    document.body.append(region);
  }
  region.textContent = text;
}
