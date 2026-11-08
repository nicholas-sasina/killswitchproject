/** Where "Sign in" goes: the Burn dashboard, which asks people to sign in. */
export const DASHBOARD_URL: string =
  import.meta.env.VITE_DASHBOARD_URL || "http://localhost:5173/";

export const REPO_URL = "https://github.com/nicholas-sasina/killswitchproject";

/** The exit-check numbers the page quotes, and how they were measured. */
export const RESULTS_URL = `${REPO_URL}/blob/main/eval/RESULTS.md`;

export const SURVEY_URL =
  "https://www.nasdaq.com/press-release/sailpoint-research-highlights-rapid-ai-agent-adoption-driving-urgent-need-evolved";
