import { readConfig, type ApiConfig } from "./api";
import { SignIn } from "./components/SignIn";
import { Shell } from "./shell/Shell";

/**
 * Burn's console. People sign in first (Cognito, PKCE); local development
 * runs against the mocks with the development token instead (main.tsx puts
 * the local screen in front).
 */
export function App({ config = readConfig() }: { config?: ApiConfig }) {
  return config.localDev ? (
    <Shell config={config} />
  ) : (
    <SignIn config={config}>
      {(auth) => <Shell config={config} auth={auth} />}
    </SignIn>
  );
}
