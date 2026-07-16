export function withBackendSessionToken(
  url: string,
  backendOrigin: string | undefined,
  token: string,
  headers: Record<string, string>,
): Record<string, string> {
  if (!backendOrigin || !token || !url.startsWith(`${backendOrigin}/api/`)) {
    return headers;
  }
  return { ...headers, "x-fulua-session-token": token };
}
