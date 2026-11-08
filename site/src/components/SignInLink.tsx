import type { ReactNode } from "react";
import { DASHBOARD_URL } from "../env";

export function SignInLink({
  className,
  children = "Sign in",
}: {
  className?: string;
  children?: ReactNode;
}) {
  return (
    <a className={className} href={DASHBOARD_URL}>
      {children}
    </a>
  );
}
