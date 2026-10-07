<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import {
  api,
  type AccessRequestRecord,
  type App,
  type Grant,
} from "../api/client";
import { useUserStore } from "../stores/user";

const route = useRoute();
const router = useRouter();
const userStore = useUserStore();

const slug = route.params.slug as string;

const app = ref<App | null>(null);
const grants = ref<Grant[]>([]);
const loading = ref(true);
const pageError = ref<string | null>(null);

// Grant form
const grantKind = ref<"user" | "group">("user");
const grantTarget = ref("");
// Duration = 0 is sentinel for "permanent". Otherwise it's hours passed to the API.
const grantDurationHours = ref<number>(0);
const grantError = ref<string | null>(null);
const grantBusy = ref(false);
const groupNames = ref<string[]>([]);

const durationOptions: { label: string; hours: number }[] = [
  { label: "Permanent", hours: 0 },
  { label: "1 hour", hours: 1 },
  { label: "24 hours", hours: 24 },
  { label: "7 days", hours: 24 * 7 },
  { label: "30 days", hours: 24 * 30 },
];

function formatExpiry(iso: string | null): { text: string; tone: "permanent" | "soon" | "ok" } {
  if (!iso) return { text: "Permanent", tone: "permanent" };
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return { text: "Expired", tone: "soon" };
  const mins = Math.floor(ms / 60000);
  const hrs = Math.floor(mins / 60);
  const days = Math.floor(hrs / 24);
  let text: string;
  if (days >= 1) text = `${days}d ${hrs % 24}h left`;
  else if (hrs >= 1) text = `${hrs}h ${mins % 60}m left`;
  else text = `${mins}m left`;
  return { text, tone: hrs < 24 ? "soon" : "ok" };
}

// Owner form
const newOwner = ref("");
const ownerError = ref<string | null>(null);
const ownerBusy = ref(false);

// Enable/disable toggle
const toggleBusy = ref(false);

// Pending access requests for this app
const pendingRequests = ref<AccessRequestRecord[]>([]);
const requestBusyId = ref<number | null>(null);
// Per-request chosen duration (hours; 0 = permanent). Keyed by request id.
const requestDuration = ref<Record<number, number>>({});

const isAdmin = computed(() => !!userStore.user?.is_admin);

async function refresh() {
  loading.value = true;
  pageError.value = null;
  try {
    app.value = await api.getApp(slug);
    grants.value = await api.listGrants(slug);
    groupNames.value = await api.listGroupNames();
    const allPending = await api.listPendingAccessRequests();
    pendingRequests.value = allPending.filter((r) => r.app_slug === slug);
  } catch (e) {
    pageError.value = String(e).replace(/^Error:\s*/, "");
  } finally {
    loading.value = false;
  }
}

async function approvePending(r: AccessRequestRecord) {
  requestBusyId.value = r.id;
  try {
    const hours = requestDuration.value[r.id] ?? 0;
    await api.approveAccessRequest(
      r.id,
      hours > 0 ? { expires_in_hours: hours } : undefined,
    );
    pendingRequests.value = pendingRequests.value.filter((p) => p.id !== r.id);
    delete requestDuration.value[r.id];
    grants.value = await api.listGrants(slug);
  } catch (e) {
    pageError.value = String(e).replace(/^Error:\s*/, "");
  } finally {
    requestBusyId.value = null;
  }
}

async function denyPending(r: AccessRequestRecord) {
  const note = prompt(
    `Deny ${r.requester_email}? You can leave a short reason (optional):`,
    "",
  );
  if (note === null) return;
  requestBusyId.value = r.id;
  try {
    await api.denyAccessRequest(r.id, note || undefined);
    pendingRequests.value = pendingRequests.value.filter((p) => p.id !== r.id);
  } catch (e) {
    pageError.value = String(e).replace(/^Error:\s*/, "");
  } finally {
    requestBusyId.value = null;
  }
}

onMounted(refresh);

async function addGrant() {
  grantError.value = null;
  if (!grantTarget.value.trim()) return;
  grantBusy.value = true;
  try {
    const body: Parameters<typeof api.addGrant>[1] =
      grantKind.value === "user"
        ? { user_email: grantTarget.value.trim() }
        : { group_name: grantTarget.value.trim() };
    if (grantDurationHours.value > 0) {
      body.expires_in_hours = grantDurationHours.value;
    }
    await api.addGrant(slug, body);
    grantTarget.value = "";
    grantDurationHours.value = 0;
    grants.value = await api.listGrants(slug);
  } catch (e) {
    grantError.value = String(e).replace(/^Error:\s*/, "");
  } finally {
    grantBusy.value = false;
  }
}

async function removeGrant(grantId: number) {
  try {
    await api.removeGrant(slug, grantId);
    grants.value = grants.value.filter((g) => g.id !== grantId);
  } catch (e) {
    pageError.value = String(e).replace(/^Error:\s*/, "");
  }
}

async function addOwner() {
  ownerError.value = null;
  if (!newOwner.value.trim()) return;
  ownerBusy.value = true;
  try {
    app.value = await api.addOwner(slug, newOwner.value.trim());
    newOwner.value = "";
  } catch (e) {
    ownerError.value = String(e).replace(/^Error:\s*/, "");
  } finally {
    ownerBusy.value = false;
  }
}

async function removeOwner(email: string) {
  try {
    await api.removeOwner(slug, email);
    if (app.value) {
      app.value = { ...app.value, owners: app.value.owners.filter((o) => o !== email) };
    }
  } catch (e) {
    pageError.value = String(e).replace(/^Error:\s*/, "");
  }
}

async function toggleEnabled() {
  if (!app.value) return;
  if (app.value.is_enabled) {
    const ok = confirm(
      `Disable "${app.value.display_name}"?\n\n` +
        `The tile will be hidden from every user's dashboard, and requests to ` +
        `${app.value.slug}.* will be blocked until you re-enable.`,
    );
    if (!ok) return;
  }
  toggleBusy.value = true;
  try {
    app.value = app.value.is_enabled
      ? await api.disableApp(slug)
      : await api.enableApp(slug);
  } catch (e) {
    pageError.value = String(e).replace(/^Error:\s*/, "");
  } finally {
    toggleBusy.value = false;
  }
}
</script>

<template>
  <section class="animate-fade-up">
    <div class="mb-8">
      <button
        class="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-white transition mb-3"
        @click="router.push('/admin/apps')"
      >
        <i class="pi pi-arrow-left" style="font-size: 0.7rem" />
        Back to apps
      </button>
      <h1 class="text-3xl font-semibold tracking-tight mb-1">
        <span class="text-gradient">
          {{ app?.display_name ?? slug }}
        </span>
      </h1>
      <p class="text-sm text-slate-400 font-mono">{{ slug }}</p>
    </div>

    <div v-if="loading" class="glass rounded-2xl p-8 text-sm text-slate-500">Loading…</div>

    <div
      v-else-if="pageError"
      class="glass rounded-2xl p-6 border-red-500/30 text-red-300"
    >
      <div class="flex items-start gap-3">
        <i class="pi pi-exclamation-triangle mt-1" />
        <div>
          <div class="font-medium mb-1">Could not load app</div>
          <div class="text-sm font-mono">{{ pageError }}</div>
        </div>
      </div>
    </div>

    <div v-else-if="app" class="space-y-6 max-w-3xl">
      <!-- App info -->
      <div class="glass rounded-2xl p-6">
        <div class="text-xs font-mono uppercase tracking-widest text-slate-500 mb-4">
          App
        </div>
        <dl class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
          <div>
            <dt class="text-slate-500 mb-0.5">Upstream</dt>
            <dd class="font-mono text-slate-200">
              {{ app.upstream_service }}<span class="text-slate-500">:{{ app.upstream_port }}</span>
            </dd>
          </div>
          <div>
            <dt class="text-slate-500 mb-0.5">Health check</dt>
            <dd class="font-mono text-slate-200">{{ app.health_check_path }}</dd>
          </div>
          <div class="sm:col-span-2">
            <dt class="text-slate-500 mb-1">Status</dt>
            <dd class="flex items-center gap-3 flex-wrap">
              <span
                v-if="app.is_enabled"
                class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs bg-emerald-500/10 text-emerald-300 ring-1 ring-emerald-500/20"
              >
                <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]" />
                Enabled
              </span>
              <span
                v-else
                class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs bg-slate-500/10 text-slate-400 ring-1 ring-slate-500/20"
              >
                <span class="w-1.5 h-1.5 rounded-full bg-slate-500" />
                Disabled
              </span>
              <button
                v-if="app.is_enabled"
                type="button"
                class="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs ring-1 ring-red-500/20 bg-red-500/5 text-red-300 hover:bg-red-500/10 hover:ring-red-500/40 transition disabled:opacity-50"
                :disabled="toggleBusy"
                @click="toggleEnabled"
              >
                <i
                  v-if="toggleBusy"
                  class="pi pi-spin pi-spinner"
                  style="font-size: 0.65rem"
                />
                <i v-else class="pi pi-power-off" style="font-size: 0.65rem" />
                {{ toggleBusy ? "Disabling…" : "Disable" }}
              </button>
              <button
                v-else
                type="button"
                class="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs ring-1 ring-emerald-500/20 bg-emerald-500/5 text-emerald-300 hover:bg-emerald-500/10 hover:ring-emerald-500/40 transition disabled:opacity-50"
                :disabled="toggleBusy"
                @click="toggleEnabled"
              >
                <i
                  v-if="toggleBusy"
                  class="pi pi-spin pi-spinner"
                  style="font-size: 0.65rem"
                />
                <i v-else class="pi pi-play" style="font-size: 0.65rem" />
                {{ toggleBusy ? "Enabling…" : "Enable" }}
              </button>
            </dd>
            <p v-if="!app.is_enabled" class="mt-2 text-xs text-slate-500">
              This app is hidden from user dashboards and will return 404 at its subdomain.
            </p>
          </div>
          <div v-if="app.description" class="sm:col-span-2">
            <dt class="text-slate-500 mb-0.5">Description</dt>
            <dd class="text-slate-200">{{ app.description }}</dd>
          </div>
        </dl>
      </div>

      <!-- Pending access requests -->
      <div v-if="pendingRequests.length" class="glass rounded-2xl p-6 ring-1 ring-amber-500/20">
        <div class="flex items-center justify-between mb-4">
          <div>
            <div class="text-xs font-mono uppercase tracking-widest text-amber-300">
              Pending requests
            </div>
            <div class="text-xs text-slate-500 mt-1">
              Users asking for access to this app. Approving creates a direct grant.
            </div>
          </div>
          <span class="text-xs font-mono text-amber-300">{{ pendingRequests.length }}</span>
        </div>
        <ul class="space-y-2">
          <li
            v-for="r in pendingRequests"
            :key="r.id"
            class="flex flex-col sm:flex-row sm:items-center gap-3 rounded-lg bg-white/3 px-4 py-3 ring-1 ring-white/5"
          >
            <div class="flex-1 min-w-0">
              <div class="text-sm text-slate-100 truncate">{{ r.requester_email }}</div>
              <div
                v-if="r.reason"
                class="text-xs text-slate-400 italic mt-0.5 line-clamp-2"
              >
                "{{ r.reason }}"
              </div>
            </div>
            <div class="flex items-center gap-2 flex-wrap">
              <select
                :value="requestDuration[r.id] ?? 0"
                @change="requestDuration[r.id] = Number(($event.target as HTMLSelectElement).value)"
                title="Grant duration"
                class="rounded-md bg-white/5 border border-white/10 px-2 py-1 text-xs text-slate-100 focus:outline-hidden focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/30 transition"
              >
                <option v-for="opt in durationOptions" :key="opt.hours" :value="opt.hours">
                  {{ opt.label }}
                </option>
              </select>
              <button
                type="button"
                class="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs ring-1 ring-red-500/20 bg-red-500/5 text-red-300 hover:bg-red-500/10 hover:ring-red-500/40 transition disabled:opacity-50"
                :disabled="requestBusyId === r.id"
                @click="denyPending(r)"
              >
                <i class="pi pi-times" style="font-size: 0.65rem" />
                Deny
              </button>
              <button
                type="button"
                class="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium ring-1 ring-emerald-500/20 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20 hover:ring-emerald-500/40 transition disabled:opacity-50"
                :disabled="requestBusyId === r.id"
                @click="approvePending(r)"
              >
                <i
                  v-if="requestBusyId === r.id"
                  class="pi pi-spin pi-spinner"
                  style="font-size: 0.65rem"
                />
                <i v-else class="pi pi-check" style="font-size: 0.65rem" />
                Approve
              </button>
            </div>
          </li>
        </ul>
      </div>

      <!-- Access -->
      <div class="glass rounded-2xl p-6">
        <div class="flex items-center justify-between mb-4">
          <div>
            <div class="text-xs font-mono uppercase tracking-widest text-slate-500">
              Access
            </div>
            <div class="text-xs text-slate-500 mt-1">
              Users and groups who can reach this app.
            </div>
          </div>
          <span class="text-xs font-mono text-slate-400">{{ grants.length }}</span>
        </div>

        <ul v-if="grants.length" class="space-y-1.5 mb-4">
          <li
            v-for="g in grants"
            :key="g.id"
            class="flex items-center justify-between rounded-lg bg-white/5 px-3 py-2 ring-1 ring-white/5"
          >
            <div class="flex items-center gap-2 text-sm flex-wrap">
              <i
                :class="g.user_email ? 'pi pi-user' : 'pi pi-users'"
                class="text-slate-500"
                style="font-size: 0.8rem"
              />
              <span class="text-slate-200">{{ g.user_email || g.group_name }}</span>
              <span
                class="text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded-sm bg-white/5 text-slate-400"
              >
                {{ g.user_email ? "user" : "group" }}
              </span>
              <span
                v-if="formatExpiry(g.expires_at).tone === 'permanent'"
                class="inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded-sm bg-emerald-500/10 text-emerald-300 ring-1 ring-emerald-500/20"
                title="No expiration"
              >
                <i class="pi pi-infinity" style="font-size: 0.55rem" />
                permanent
              </span>
              <span
                v-else-if="formatExpiry(g.expires_at).tone === 'soon'"
                class="inline-flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded-sm bg-amber-500/10 text-amber-300 ring-1 ring-amber-500/20"
                :title="g.expires_at ?? ''"
              >
                <i class="pi pi-clock" style="font-size: 0.55rem" />
                {{ formatExpiry(g.expires_at).text }}
              </span>
              <span
                v-else
                class="inline-flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded-sm bg-sky-500/10 text-sky-300 ring-1 ring-sky-500/20"
                :title="g.expires_at ?? ''"
              >
                <i class="pi pi-clock" style="font-size: 0.55rem" />
                {{ formatExpiry(g.expires_at).text }}
              </span>
            </div>
            <button
              class="text-xs text-slate-500 hover:text-red-400 transition"
              @click="removeGrant(g.id)"
            >
              Remove
            </button>
          </li>
        </ul>
        <div v-else class="text-sm text-slate-500 mb-4">
          No one has been granted access yet.
          <span class="text-slate-600">(Owners and admins always have access.)</span>
        </div>

        <form class="flex flex-wrap items-center gap-2" @submit.prevent="addGrant">
          <div class="flex rounded-lg bg-white/5 ring-1 ring-white/10 overflow-hidden">
            <button
              type="button"
              class="px-3 py-1.5 text-xs transition"
              :class="
                grantKind === 'user'
                  ? 'bg-white/10 text-white'
                  : 'text-slate-400 hover:text-white'
              "
              @click="grantKind = 'user'; grantTarget = ''"
            >
              User
            </button>
            <button
              type="button"
              class="px-3 py-1.5 text-xs transition"
              :class="
                grantKind === 'group'
                  ? 'bg-white/10 text-white'
                  : 'text-slate-400 hover:text-white'
              "
              @click="grantKind = 'group'; grantTarget = ''"
            >
              Group
            </button>
          </div>
          <input
            v-if="grantKind === 'user'"
            v-model="grantTarget"
            type="email"
            placeholder="user@example.com"
            class="flex-1 min-w-[180px] rounded-lg bg-white/5 border border-white/10 px-3 py-1.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-hidden focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30 transition"
          />
          <select
            v-else
            v-model="grantTarget"
            class="flex-1 min-w-[180px] rounded-lg bg-white/5 border border-white/10 px-3 py-1.5 text-sm text-slate-100 focus:outline-hidden focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30 transition"
          >
            <option value="" disabled>Pick a group…</option>
            <option v-for="g in groupNames" :key="g" :value="g">{{ g }}</option>
          </select>
          <select
            v-model.number="grantDurationHours"
            title="Access duration"
            class="rounded-lg bg-white/5 border border-white/10 px-3 py-1.5 text-sm text-slate-100 focus:outline-hidden focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30 transition"
          >
            <option v-for="opt in durationOptions" :key="opt.hours" :value="opt.hours">
              {{ opt.label }}
            </option>
          </select>
          <button type="submit" class="btn-primary" :disabled="grantBusy || !grantTarget.trim()">
            <i
              v-if="grantBusy"
              class="pi pi-spin pi-spinner"
              style="font-size: 0.7rem"
            />
            <i v-else class="pi pi-plus" style="font-size: 0.7rem" />
            Grant
          </button>
        </form>
        <div v-if="grantError" class="mt-2 text-xs text-red-400 font-mono">{{ grantError }}</div>
      </div>

      <!-- Owners -->
      <div class="glass rounded-2xl p-6">
        <div class="flex items-center justify-between mb-4">
          <div>
            <div class="text-xs font-mono uppercase tracking-widest text-slate-500">
              Owners
            </div>
            <div class="text-xs text-slate-500 mt-1">
              Owners can grant access. Admins can add or remove owners.
            </div>
          </div>
          <span class="text-xs font-mono text-slate-400">{{ app.owners.length }}</span>
        </div>

        <ul class="space-y-1.5 mb-4">
          <li
            v-for="email in app.owners"
            :key="email"
            class="flex items-center justify-between rounded-lg bg-white/5 px-3 py-2 ring-1 ring-white/5"
          >
            <div class="flex items-center gap-2 text-sm">
              <i class="pi pi-shield text-fuchsia-400" style="font-size: 0.8rem" />
              <span class="text-slate-200">{{ email }}</span>
            </div>
            <button
              v-if="isAdmin && app.owners.length > 1"
              class="text-xs text-slate-500 hover:text-red-400 transition"
              @click="removeOwner(email)"
            >
              Remove
            </button>
          </li>
        </ul>

        <form v-if="isAdmin" class="flex items-center gap-2" @submit.prevent="addOwner">
          <input
            v-model="newOwner"
            type="email"
            placeholder="new-owner@example.com"
            class="flex-1 rounded-lg bg-white/5 border border-white/10 px-3 py-1.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-hidden focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30 transition"
          />
          <button type="submit" class="btn-primary" :disabled="ownerBusy || !newOwner.trim()">
            <i
              v-if="ownerBusy"
              class="pi pi-spin pi-spinner"
              style="font-size: 0.7rem"
            />
            <i v-else class="pi pi-plus" style="font-size: 0.7rem" />
            Add owner
          </button>
        </form>
        <div v-else class="text-xs text-slate-500 italic">
          Only admins can modify owners.
        </div>
        <div v-if="ownerError" class="mt-2 text-xs text-red-400 font-mono">{{ ownerError }}</div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none !important;
}
</style>
