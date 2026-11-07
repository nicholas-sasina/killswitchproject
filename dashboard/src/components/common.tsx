export function Flame() {
  return (
    <svg viewBox="0 0 24 28" fill="none" aria-hidden="true">
      <path
        d="M13 2c1 7-6 8-4 14 2-1 3-3 3-5 5 3 8 6 8 10a8 8 0 0 1-16 0C2 13 10 10 13 2Z"
        fill="currentColor"
      />
    </svg>
  );
}

export function Status({ value }: { value: string }) {
  return (
    <span className={`status status-${value}`}>
      <span aria-hidden="true" />
      {value}
    </span>
  );
}

export function ErrorMessage({ message }: { message: string }) {
  return message ? (
    <p className="error" role="alert">
      {message}
    </p>
  ) : null;
}

export function Timestamp({ value }: { value: string }) {
  const date = new Date(value);
  return (
    <time dateTime={value} title={value}>
      {Number.isNaN(date.valueOf())
        ? "Unknown time"
        : date.toLocaleTimeString([], { hour12: false })}
    </time>
  );
}
