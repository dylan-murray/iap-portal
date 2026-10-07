<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api, type AccessRequestRecord } from "../api/client";

const pending = ref<AccessRequestRecord[]>([]);
const loading = ref(true);
const pageError = ref<string | null>(null);
const busyId = ref<number | null>(null);
// Per-request chosen duration (hours; 0 = permanent). Keyed by request id.
const durationByRequest = ref<Record<number, number>>({});

const durationOptions: { label: string; hours: number }[] = [
  { label: "Permanent", hours: 0 },
  { label: "1 hour", hours: 1 },
  { label: "24 hours", hours: 24 },
  { label: "7 days", hours: 24 * 7 },
  { label: "30 days", hours: 24 * 30 },
];

async function refresh() {
  loading.value = true;
  pageError.value = null;
  try {
    pending.value = await api.listPendingAccessRequests();
  } catch (e) {
    pageError.value = String(e).replace(/^Error:\s*/, "");
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);

async function approve(r: AccessRequestRecord) {
  busyId.value = r.id;
  try {
    const hours = durationByRequest.value[r.id] ?? 0;
    await api.approveAccessRequest(
      r.id,
      hours > 0 ? { expires_in_hours: hours } : undefined,
    );
    pending.value = pending.value.filter((p) => p.id !== r.id);
    delete durationByRequest.value[r.id];
  } catch (e) {
    pageError.value = String(e).replace(/^Error:\s*/, "");
  } finally {
    busyId.value = null;
  }
}

async function deny(r: AccessRequestRecord) {
  const note = prompt(
    `Deny ${r.requester_email}'s request for ${r.app_display_name}? ` +
      `You can leave a short reason (optional):`,
    "",
  );
  if (note === null) return;
  busyId.value = r.id;
  try {
    await api.denyAccessRequest(r.id, note || undefined);
    pending.value = pending.value.filter((p) => p.id !== r.id);
  } catch (e) {
    pageError.value = String(e).replace(/^Error:\s*/, "");
  } finally {
    busyId.value = null;
  }
}

function fmtTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

const hasAny = computed(() => pending.value.length > 0);
</script>

<template>
  <section class="animate-fade-up">
    <div class="mb-8">
      <div class="inline-flex items-center gap-2 text-xs text-slate-400 font-mono uppercase tracking-widest mb-3">
        <i class="pi pi-bell text-fuchsia-400" style="font-size: 0.7rem" />
        Inbox
      </div>
      <h1 class="text-3xl font-semibold tracking-tight mb-1">
        <span class="text-gradient">Access requests</span>
      </h1>
      <p class="text-sm text-slate-400">
        Pending requests for apps you own or administer.
      </p>
    </div>

    <div v-if="loading" class="glass rounded-2xl p-8 text-sm text-slate-500">Loading…</div>

    <div
      v-else-if="pageError"
      class="glass rounded-2xl p-6 border-red-500/30 text-red-300"
    >
      <div class="flex items-start gap-3">
        <i class="pi pi-exclamation-triangle mt-1" />
        <div>
          <div class="font-medium mb-1">Could not load requests</div>
          <div class="text-sm font-mono">{{ pageError }}</div>
        </div>
      </div>
    </div>

    <div v-else-if="!hasAny" class="glass rounded-2xl p-10 text-center">
      <div class="w-14 h-14 mx-auto rounded-2xl bg-white/5 flex items-center justify-center mb-4">
        <i class="pi pi-inbox text-2xl text-slate-500" />
      </div>
      <div class="font-medium mb-1">All caught up</div>
      <div class="text-sm text-slate-400">
        No pending access requests for your apps.
      </div>
    </div>

    <ul v-else class="space-y-3">
      <li
        v-for="r in pending"
        :key="r.id"
        class="glass rounded-2xl p-5 flex flex-col sm:flex-row sm:items-center gap-4"
      >
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap mb-1">
            <span class="font-medium text-slate-100">{{ r.requester_email }}</span>
            <span class="text-slate-500 text-sm">wants access to</span>
            <router-link
              :to="`/admin/apps/${r.app_slug}`"
              class="font-mono text-sm text-fuchsia-300 hover:text-fuchsia-200 hover:underline transition"
            >
              {{ r.app_slug }}
            </router-link>
          </div>
          <div v-if="r.reason" class="text-sm text-slate-400 italic mb-1">
            "{{ r.reason }}"
          </div>
          <div class="text-xs text-slate-500 font-mono">
            requested {{ fmtTime(r.requested_at) }}
          </div>
        </div>
        <div class="flex items-center gap-2 flex-wrap">
          <select
            :value="durationByRequest[r.id] ?? 0"
            @change="durationByRequest[r.id] = Number(($event.target as HTMLSelectElement).value)"
            title="Grant duration"
            class="rounded-md bg-white/5 border border-white/10 px-2.5 py-1.5 text-xs text-slate-100 focus:outline-hidden focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/30 transition"
          >
            <option v-for="opt in durationOptions" :key="opt.hours" :value="opt.hours">
              {{ opt.label }}
            </option>
          </select>
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-xs ring-1 ring-red-500/20 bg-red-500/5 text-red-300 hover:bg-red-500/10 hover:ring-red-500/40 transition disabled:opacity-50"
            :disabled="busyId === r.id"
            @click="deny(r)"
          >
            <i class="pi pi-times" style="font-size: 0.65rem" />
            Deny
          </button>
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-xs font-medium ring-1 ring-emerald-500/20 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20 hover:ring-emerald-500/40 transition disabled:opacity-50"
            :disabled="busyId === r.id"
            @click="approve(r)"
          >
            <i
              v-if="busyId === r.id"
              class="pi pi-spin pi-spinner"
              style="font-size: 0.65rem"
            />
            <i v-else class="pi pi-check" style="font-size: 0.65rem" />
            Approve
          </button>
        </div>
      </li>
    </ul>
  </section>
</template>
