// Keep selection in the URL: each Command tab addresses one independent host.
export function teamApiUrl(input, pageUrl) {
  const page = new URL(pageUrl);
  const url = new URL(input, page);
  const selected = page.searchParams.get("team");
  if (selected && url.host === page.host && url.pathname.startsWith("/api/") && !url.pathname.startsWith("/api/command/")) {
    url.searchParams.set("team", selected);
  }
  return url.href;
}

export function teamPageUrl(teamId, pageUrl) {
  const url = new URL(pageUrl);
  url.searchParams.set("team", teamId);
  return url.href;
}
