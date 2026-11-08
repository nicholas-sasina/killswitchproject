import type { MouseEvent, ReactNode } from "react";
import { DASHBOARD_URL } from "../env";
import { leaveTo } from "../lib/leave";

export function SignInLink({
  className,
  children = "Sign in",
}: {
  className?: string;
  children?: ReactNode;
}) {
  const onClick = (e: MouseEvent<HTMLAnchorElement>) => {
    // New tabs and windows keep the browser's own behaviour.
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey)
      return;
    e.preventDefault();
    leaveTo(DASHBOARD_URL);
  };
  return (
    <a className={className} href={DASHBOARD_URL} onClick={onClick}>
      {children}
    </a>
  );
}
