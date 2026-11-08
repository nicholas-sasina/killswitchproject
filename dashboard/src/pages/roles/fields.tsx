import { useId, type InputHTMLAttributes } from "react";
import "./add-agents.css";

/** The API's role name rule. */
export const rolePattern = /^[a-z0-9][a-z0-9-]{0,31}$/;
/**
 * What is wrong with a role name before it is sent, or "": the API's
 * pattern, and "eval" and "eval-*", which Burn keeps for its evaluations.
 */
export function roleNameError(name: string): string {
  if (!rolePattern.test(name))
    return "Role name must be 1–32 lowercase letters, digits or hyphens, starting with a letter or digit.";
  if (name === "eval" || name.startsWith("eval-"))
    return "Names that are eval or start with eval- are kept for Burn's evaluations. Choose another name.";
  return "";
}

export const roleNameConflict =
  "A role with that name already exists. Choose a new role name.";

/** A set of mono chips that toggle: tools, keys, roles. */
export function Choices({
  title,
  hint,
  options,
  selected,
  onChange,
  empty = "No existing roles.",
}: {
  title: string;
  hint?: string;
  options: string[];
  selected: string[];
  onChange: (selected: string[]) => void;
  empty?: string;
}) {
  return (
    <fieldset className="add-field">
      <legend className="add-label">{title}</legend>
      {hint && <p className="add-hint">{hint}</p>}
      {options.length ? (
        <div className="add-chips">
          {options.map((option) => (
            <label key={option} className="add-chip">
              <input
                type="checkbox"
                checked={selected.includes(option)}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? [...selected, option]
                      : selected.filter((value) => value !== option),
                  )
                }
              />
              <span>{option}</span>
            </label>
          ))}
        </div>
      ) : (
        <p className="add-hint">{empty}</p>
      )}
    </fieldset>
  );
}

/** One protection, worded as the role page's Protections card words it. */
export function Protection({
  label,
  note,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  note: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  const id = useId();
  return (
    <div className="add-protection">
      <div>
        <label className="add-protection-label" htmlFor={id}>
          {label}
        </label>
        <span id={`${id}-note`} className="add-hint">
          {note}
        </span>
      </div>
      <input
        id={id}
        type="checkbox"
        aria-describedby={`${id}-note`}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
    </div>
  );
}

/** A labelled input with its hint read out as its description. */
export function TextField({
  label,
  hint,
  className = "",
  ...input
}: {
  label: string;
  hint?: string;
  className?: string;
} & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <div className="add-field">
      <label className="add-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className={`add-input ${className}`}
        aria-describedby={hint ? `${id}-hint` : undefined}
        {...input}
      />
      {hint && (
        <span id={`${id}-hint`} className="add-hint">
          {hint}
        </span>
      )}
    </div>
  );
}

/** Hosts typed as "api.example.com, *.example.org", or undefined if one is not a host. */
export function parseHosts(text: string): string[] | undefined {
  const hosts = text.split(/[\s,]+/).filter(Boolean);
  return hosts.every((host) => /^(\*\.)?[a-z0-9.-]{1,253}$/.test(host))
    ? hosts
    : undefined;
}
