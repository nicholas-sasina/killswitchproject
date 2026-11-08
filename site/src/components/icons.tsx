// Hand-drawn 14px stroke icons: 1.3 stroke, round caps and joins.
import type { ReactNode } from "react";

type IconProps = { size?: number; stroke?: number; className?: string };

function Icon({
  size = 14,
  stroke = 1.3,
  className,
  children,
}: IconProps & { children: ReactNode }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 14 14"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  );
}

export const External = (p: IconProps) => (
  <svg
    className={p.className}
    width={9}
    height={9}
    viewBox="0 0 10 10"
    aria-hidden="true"
    fill="none"
    stroke="currentColor"
    strokeWidth={1.6}
    strokeLinecap="round"
  >
    <path d="M2 8 8 2M3.5 2H8v4.5" />
  </svg>
);

export const Inbox = (p: IconProps) => (
  <Icon {...p} stroke={1.6}>
    <path d="M3 2v5.5h8M8.5 5 11 7.5 8.5 10" />
  </Icon>
);

export const Check = (p: IconProps) => (
  <Icon {...p} stroke={1.8}>
    <path d="M2.5 7.5 5.5 10.5 11.5 3.5" />
  </Icon>
);

export const Headset = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 9V7a4.5 4.5 0 0 1 9 0v2" />
    <rect x="1.5" y="8" width="2.5" height="4" rx="1" />
    <rect x="10" y="8" width="2.5" height="4" rx="1" />
  </Icon>
);

export const Database = (p: IconProps) => (
  <Icon {...p}>
    <ellipse cx="7" cy="3.5" rx="4.5" ry="1.8" />
    <path d="M2.5 3.5v7c0 1 2 1.8 4.5 1.8s4.5-.8 4.5-1.8v-7M2.5 7c0 1 2 1.8 4.5 1.8S11.5 8 11.5 7" />
  </Icon>
);

export const Box = (p: IconProps) => (
  <Icon {...p}>
    <path d="M7 1.5 12.5 4v6L7 12.5 1.5 10V4z" />
    <path d="M1.5 4 7 6.5 12.5 4M7 6.5v6" />
  </Icon>
);

export const Code = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4.5 4 1.5 7l3 3M9.5 4l3 3-3 3" />
  </Icon>
);

export const Layers = (p: IconProps) => (
  <Icon {...p}>
    <path d="M7 1.5 1.5 4.5 7 7.5l5.5-3z" />
    <path d="M1.5 7 7 10l5.5-3" />
    <path d="M1.5 9.5 7 12.5l5.5-3" />
  </Icon>
);

export const Shield = (p: IconProps) => (
  <Icon {...p}>
    <path d="M7 1.5 2.5 3.2v3.6c0 2.8 1.9 4.6 4.5 5.7 2.6-1.1 4.5-2.9 4.5-5.7V3.2z" />
  </Icon>
);

export const ShieldCheck = (p: IconProps) => (
  <Icon {...p}>
    <path d="M7 1.5 2.5 3.2v3.6c0 2.8 1.9 4.6 4.5 5.7 2.6-1.1 4.5-2.9 4.5-5.7V3.2z" />
    <path d="m5 7 1.5 1.5L9.5 5.5" />
  </Icon>
);

export const Flask = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5.5 1.5h3M6 1.5v4L2.5 11.3c-.4.7.1 1.2.8 1.2h7.4c.7 0 1.2-.5.8-1.2L8 5.5v-4" />
  </Icon>
);

export const Branch = (p: IconProps) => (
  <Icon {...p}>
    <path d="M7 1.5v4M7 5.5 3 9.5M7 5.5l4 4" />
    <circle cx="3" cy="11" r="1.5" />
    <circle cx="11" cy="11" r="1.5" />
  </Icon>
);

export const Person = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="7" cy="4.5" r="2.5" />
    <path d="M2.5 12.5c.6-2.4 2.3-3.5 4.5-3.5s3.9 1.1 4.5 3.5" />
  </Icon>
);
