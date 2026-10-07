const base = "";  // same-origin; Vite dev server proxies /api

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(base + path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (resp.status === 401) {
    window.location.href = `/login?return_to=${encodeURIComponent(location.pathname)}`;
    throw new Error("unauthenticated");
  }
  if (!resp.ok) throw new Error(await resp.text());
  if (resp.status === 204 || resp.headers.get("content-length") === "0") {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

export interface User {
  email: string;
  name: string | null;
  picture_url: string | null;
  is_admin: boolean;
}

export interface Tile {
  slug: string;
  display_name: string;
  description: string | null;
  icon_url: string | null;
  url: string;
  can_manage: boolean;
}

export interface App {
  id: number;
  slug: string;
  display_name: string;
  description: string | null;
  icon_url: string | null;
  upstream_service: string;
  upstream_port: number;
  health_check_path: string;
  is_enabled: boolean;
  owners: string[];
}

export interface Grant {
  id: number;
  user_email: string | null;
  group_name: string | null;
  expires_at: string | null;
}

export interface Group {
  id: number;
  name: string;
  description: string | null;
  member_count: number;
}

export interface GroupDetail {
  id: number;
  name: string;
  description: string | null;
  members: string[];
}

export interface BrowsableApp {
  slug: string;
  display_name: string;
  description: string | null;
  icon_url: string | null;
  pending_request_id: number | null;
}

export type AccessRequestStatus = "pending" | "approved" | "denied";

export interface AccessRequestRecord {
  id: number;
  app_slug: string;
  app_display_name: string;
  requester_email: string;
  reason: string | null;
  status: AccessRequestStatus;
  requested_at: string;
  decided_at: string | null;
  decided_by_email: string | null;
  decided_note: string | null;
}

export const api = {
  config: () => request<{ display_name: string }>("/api/config"),
  me: () => request<User>("/api/me"),
  myApps: () => request<Tile[]>("/api/me/apps"),
  listApps: () => request<App[]>("/api/apps"),
  getApp: (slug: string) => request<App>(`/api/apps/${slug}`),
  createApp: (body: Partial<App> & { slug: string; display_name: string; upstream_service: string }) =>
    request<App>("/api/apps", { method: "POST", body: JSON.stringify(body) }),
  enableApp: (slug: string) =>
    request<App>(`/api/apps/${slug}/enable`, { method: "POST" }),
  disableApp: (slug: string) =>
    request<App>(`/api/apps/${slug}/disable`, { method: "POST" }),

  addOwner: (slug: string, email: string) =>
    request<App>(`/api/apps/${slug}/owners`, {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  removeOwner: (slug: string, email: string) =>
    request<void>(`/api/apps/${slug}/owners/${encodeURIComponent(email)}`, {
      method: "DELETE",
    }),

  listGrants: (slug: string) => request<Grant[]>(`/api/apps/${slug}/access`),
  addGrant: (
    slug: string,
    body: {
      user_email?: string;
      group_name?: string;
      expires_in_hours?: number;
    },
  ) =>
    request<Grant>(`/api/apps/${slug}/access`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  removeGrant: (slug: string, grantId: number) =>
    request<void>(`/api/apps/${slug}/access/${grantId}`, { method: "DELETE" }),

  browsableApps: () => request<BrowsableApp[]>("/api/apps/browsable"),
  createAccessRequest: (slug: string, reason: string | null) =>
    request<AccessRequestRecord>(`/api/apps/${slug}/access-requests`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  listMyAccessRequests: () =>
    request<AccessRequestRecord[]>("/api/access-requests/mine"),
  listPendingAccessRequests: () =>
    request<AccessRequestRecord[]>("/api/access-requests/pending"),
  approveAccessRequest: (
    id: number,
    opts?: { note?: string; expires_in_hours?: number },
  ) =>
    request<AccessRequestRecord>(`/api/access-requests/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({
        note: opts?.note ?? null,
        expires_in_hours: opts?.expires_in_hours ?? null,
      }),
    }),
  denyAccessRequest: (id: number, note?: string) =>
    request<AccessRequestRecord>(`/api/access-requests/${id}/deny`, {
      method: "POST",
      body: JSON.stringify({ note: note ?? null }),
    }),

  listGroupNames: () => request<string[]>("/api/groups/names"),
  listGroups: () => request<Group[]>("/api/groups"),
  getGroup: (name: string) => request<GroupDetail>(`/api/groups/${name}`),
  createGroup: (body: { name: string; description?: string | null }) =>
    request<Group>("/api/groups", { method: "POST", body: JSON.stringify(body) }),
  deleteGroup: (name: string) =>
    request<void>(`/api/groups/${name}`, { method: "DELETE" }),
  addGroupMember: (name: string, email: string) =>
    request<GroupDetail>(`/api/groups/${name}/members`, {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  removeGroupMember: (name: string, email: string) =>
    request<void>(`/api/groups/${name}/members/${encodeURIComponent(email)}`, {
      method: "DELETE",
    }),
};
