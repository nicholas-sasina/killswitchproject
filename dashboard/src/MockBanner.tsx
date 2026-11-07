export function mockDataReducer(seen: boolean, headers: Headers): boolean {
  return seen || headers.get("x-burn-mock")?.trim().toLowerCase() === "true";
}

export function MockBanner({ visible }: { visible: boolean }) {
  return visible ? (
    <div className="mock-banner" role="alert">
      <strong>MOCK DATA</strong>
      <span>
        Development API response detected · reload after switching to live
        services
      </span>
    </div>
  ) : null;
}
