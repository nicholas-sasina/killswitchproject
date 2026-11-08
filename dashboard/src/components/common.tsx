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
