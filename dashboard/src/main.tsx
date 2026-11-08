// The tokens and base styles load first, so the shared pieces and each
// page's own styles come after them in the cascade and win a tie.
import "./styles/index.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { readConfig } from "./api";
import { LocalSignIn } from "./components/SignIn";

const config = readConfig();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {config.localDev ? (
      <LocalSignIn>
        <App config={config} />
      </LocalSignIn>
    ) : (
      <App config={config} />
    )}
  </StrictMode>,
);
