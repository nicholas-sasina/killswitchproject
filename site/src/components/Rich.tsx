import { Fragment } from "react";

/** Renders `identifiers` in backticks as code (Geist Mono); the rest as text. */
export function Rich({ text }: { text: string }) {
  const parts = text.split("`");
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <code key={i}>{part}</code>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}

/** The same text without the backticks, for labels and announcements. */
export function plain(text: string): string {
  return text.replaceAll("`", "");
}
