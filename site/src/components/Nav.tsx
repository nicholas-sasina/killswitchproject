import type { MouseEvent } from "react";
import { REPO_URL } from "../env";
import { Mark } from "./Mark";
import { SignInLink } from "./SignInLink";
import { External } from "./icons";

export function Nav({ onWatch }: { onWatch: () => void }) {
  const watch = (e: MouseEvent<HTMLAnchorElement>) => {
    // The link still goes to #how; this also restarts the pipeline there.
    if (e.button === 0 && !e.metaKey && !e.ctrlKey) onWatch();
  };
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <a href="#top" className="topbar-home" aria-label="Burn, back to top">
          <Mark />
        </a>
        <nav className="topbar-nav" aria-label="Primary">
          <a href="#how">How it works</a>
          <a href="#scenario">Exposure ledger</a>
          <a href="#aws">AWS architecture</a>
        </nav>
        <div className="topbar-actions">
          <a className="topbar-link topbar-github" href={REPO_URL}>
            GitHub
            <External />
          </a>
          <SignInLink className="topbar-link" />
          <a className="topbar-cta" href="#how" onClick={watch}>
            Watch the incident
            <External />
          </a>
        </div>
      </div>
    </header>
  );
}
