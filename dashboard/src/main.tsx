import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { readConfig } from "./api";
import { LocalSignIn } from "./components/SignIn";
import "./styles.css";

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
