/**
 * Theme control: a three-way segmented switch rather than a sun/moon toggle.
 *
 * A two-state toggle cannot express "follow the system", and it never shows
 * which state you are actually in. Three labelled segments say both at once,
 * and the labels match the design language: paper and ink, not light and dark.
 */
import { THEME_LABEL, THEME_ORDER, type ThemeChoice } from "../theme";

const SHORT: Record<ThemeChoice, string> = {
  system: "Auto",
  light: "Paper",
  dark: "Ink",
};

export function ThemeToggle({
  choice,
  resolved,
  onChange,
}: {
  choice: ThemeChoice;
  resolved: "light" | "dark";
  onChange: (next: ThemeChoice) => void;
}) {
  return (
    <div
      className="theme-switch"
      role="group"
      aria-label={`Theme, currently ${THEME_LABEL[choice].toLowerCase()}${
        choice === "system" ? ` (${resolved})` : ""
      }`}
    >
      {THEME_ORDER.map((option) => (
        <button
          key={option}
          type="button"
          className="theme-option"
          aria-pressed={choice === option}
          onClick={() => onChange(option)}
          title={
            option === "system"
              ? "Follow the operating system setting"
              : `Always use ${THEME_LABEL[option].toLowerCase()}`
          }
        >
          {SHORT[option]}
        </button>
      ))}
    </div>
  );
}
