/**
 * The AWS region the console is looking at, read from the API's own URL
 * (an API Gateway or Lambda URL names its region). Local mocks have none.
 */
export function regionFromUrl(url: string | undefined): string | undefined {
  const match = url?.match(
    /\.((?:[a-z]{2})(?:-gov)?-[a-z]+-\d)\.(?:amazonaws\.com|on\.aws)\b/,
  );
  return match?.[1];
}
