<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import {
  api,
  type AccessRequestRecord,
  type BrowsableApp,
  type Tile,
} from "../api/client";
import { useUserStore } from "../stores/user";

const router = useRouter();
const userStore = useUserStore();

function greeting(): string {
  const h = new Date().getHours();
  if (h < 5) return "Up late";
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  if (h < 22) return "Good evening";
  return "Up late";
}

const firstName = computed(() => {
  const n = userStore.user?.name?.trim();
  if (n) return n.split(/\s+/)[0];
  return userStore.user?.email?.split("@")[0] ?? "";
});

const eyebrow = computed(() => {
  const g = greeting();
  const who = firstName.value ? `, ${firstName.value.toUpperCase()}` : "";
  const count = tiles.value.length;
  const apps =
    count === 0 ? "NO APPS YET" : `${count} APP${count === 1 ? "" : "S"}`;
  return `${g.toUpperCase()}${who} · ${apps}`;
});

function goManage(slug: string, e: Event) {
  e.stopPropagation();
  e.preventDefault();
  router.push(`/admin/apps/${slug}`);
}

const tiles = ref<Tile[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);

const gradients = [
  "linear-gradient(135deg, #a855f7 0%, #ec4899 100%)",
  "linear-gradient(135deg, #06b6d4 0%, #3b82f6 100%)",
  "linear-gradient(135deg, #f59e0b 0%, #ef4444 100%)",
  "linear-gradient(135deg, #10b981 0%, #06b6d4 100%)",
  "linear-gradient(135deg, #8b5cf6 0%, #06b6d4 100%)",
  "linear-gradient(135deg, #ec4899 0%, #f59e0b 100%)",
];

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function gradientFor(slug: string): string {
  return gradients[hash(slug) % gradients.length];
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .map((w) => w[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

const hasTiles = computed(() => tiles.value.length > 0);

const browsable = ref<BrowsableApp[]>([]);
const myRequests = ref<AccessRequestRecord[]>([]);

// Request dialog state
const requestTarget = ref<BrowsableApp | null>(null);
const requestReason = ref("");
const requesting = ref(false);
const requestError = ref<string | null>(null);

async function loadRequestSections() {
  try {
    const [b, r] = await Promise.all([
      api.browsableApps(),
      api.listMyAccessRequests(),
    ]);
    browsable.value = b;
    myRequests.value = r;
  } catch {
    // Non-fatal: the main tile list already rendered. Silently ignore.
  }
}

function openRequest(app: BrowsableApp) {
  requestTarget.value = app;
  requestReason.value = "";
  requestError.value = null;
}

function cancelRequest() {
  requestTarget.value = null;
  requestReason.value = "";
  requestError.value = null;
}

async function submitRequest() {
  if (!requestTarget.value) return;
  requesting.value = true;
  requestError.value = null;
  try {
    await api.createAccessRequest(
      requestTarget.value.slug,
      requestReason.value.trim() || null,
    );
    cancelRequest();
    await loadRequestSections();
  } catch (e) {
    requestError.value = String(e).replace(/^Error:\s*/, "");
  } finally {
    requesting.value = false;
  }
}

onMounted(async () => {
  try {
    tiles.value = await api.myApps();
  } catch (e) {
    error.value = String(e);
  } finally {
    loading.value = false;
  }
  loadRequestSections();
});
</script>

<template>
  <section>
    <div class="mb-10 animate-fade-up">
      <div class="inline-flex items-center gap-2 text-xs text-slate-400 font-mono uppercase tracking-widest mb-3">
        <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse-slow shadow-[0_0_12px_rgba(52,211,153,0.8)]" />
        {{ eyebrow }}
      </div>
      <h1 class="text-4xl font-semibold tracking-tight mb-2">
        <span class="text-gradient">Applications</span>
      </h1>
      <p class="text-slate-400 text-sm">
        Launch any of the tools you have access to. Need more?
        <a href="#browse" class="text-fuchsia-400 hover:text-fuchsia-300 transition">Browse available apps</a>.
      </p>
    </div>

    <div v-if="loading" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      <div
        v-for="i in 6"
        :key="i"
        class="glass rounded-2xl p-5 h-[116px] overflow-hidden"
      >
        <div class="flex gap-4">
          <div class="skeleton w-12 h-12 rounded-xl" />
          <div class="flex-1 space-y-2">
            <div class="skeleton h-4 rounded w-2/3" />
            <div class="skeleton h-3 rounded w-full" />
            <div class="skeleton h-3 rounded w-1/2" />
          </div>
        </div>
      </div>
    </div>

    <div
      v-else-if="error"
      class="glass rounded-2xl p-6 border-red-500/30 text-red-300"
    >
      <div class="flex items-start gap-3">
        <i class="pi pi-exclamation-triangle mt-1" />
        <div>
          <div class="font-medium mb-1">Could not load apps</div>
          <div class="text-sm text-red-300/80 font-mono">{{ error }}</div>
        </div>
      </div>
    </div>

    <div
      v-else-if="!hasTiles"
      class="glass rounded-2xl p-10 text-center"
    >
      <div class="w-14 h-14 mx-auto rounded-2xl bg-white/5 flex items-center justify-center mb-4">
        <i class="pi pi-inbox text-2xl text-slate-500" />
      </div>
      <div class="font-medium mb-1">No apps yet</div>
      <div class="text-sm text-slate-400 max-w-md mx-auto">
        You don't have access to any apps yet.
        <a href="#browse" class="text-fuchsia-400 hover:text-fuchsia-300 transition">Browse what's available</a>
        and request access, or contact your administrator.
      </div>
    </div>

    <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      <a
        v-for="(t, idx) in tiles"
        :key="t.slug"
        :href="t.url"
        class="group relative glass ring-gradient rounded-2xl p-5 overflow-hidden transition duration-300 hover:-translate-y-0.5 animate-fade-up"
        :style="{ animationDelay: `${idx * 40}ms` }"
      >
        <div
          class="absolute -top-20 -right-20 w-44 h-44 rounded-full blur-3xl opacity-0 group-hover:opacity-40 transition duration-500 pointer-events-none"
          :style="{ background: gradientFor(t.slug) }"
        />

        <button
          v-if="t.can_manage"
          type="button"
          class="absolute top-3 right-3 z-10 inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-mono uppercase tracking-wider bg-white/5 text-slate-400 ring-1 ring-white/10 opacity-0 group-hover:opacity-100 hover:bg-fuchsia-500/15 hover:text-fuchsia-200 hover:ring-fuchsia-500/30 transition"
          title="Manage access & owners"
          @click="goManage(t.slug, $event)"
        >
          <i class="pi pi-cog" style="font-size: 0.65rem" />
          Manage
        </button>

        <div class="relative flex items-start gap-4">
          <div class="relative">
            <img
              v-if="t.icon_url"
              :src="t.icon_url"
              alt=""
              class="w-12 h-12 rounded-xl object-cover ring-1 ring-white/10"
            />
            <div
              v-else
              class="w-12 h-12 rounded-xl flex items-center justify-center text-white font-semibold text-sm shadow-lg"
              :style="{ backgroundImage: gradientFor(t.slug) }"
            >
              {{ initials(t.display_name) }}
            </div>
          </div>

          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 mb-0.5">
              <div class="font-medium truncate text-slate-100">{{ t.display_name }}</div>
              <i
                class="pi pi-arrow-up-right text-xs text-slate-500 opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0 transition"
              />
            </div>
            <div class="text-xs font-mono text-slate-500 mb-1.5">{{ t.slug }}</div>
            <div class="text-sm text-slate-400 line-clamp-2">
              {{ t.description || t.url }}
            </div>
          </div>
        </div>
      </a>
    </div>

    <!-- My requests strip -->
    <div v-if="myRequests.length" class="mt-14">
      <div class="text-xs font-mono uppercase tracking-widest text-slate-500 mb-3">
        Your requests
      </div>
      <ul class="space-y-1.5">
        <li
          v-for="r in myRequests"
          :key="r.id"
          class="flex items-center justify-between rounded-lg bg-white/[0.03] px-4 py-2.5 ring-1 ring-white/5 text-sm"
        >
          <div class="flex items-center gap-3 min-w-0">
            <span
              class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[10px] font-mono uppercase tracking-wider"
              :class="{
                'bg-amber-500/10 text-amber-300 ring-1 ring-amber-500/20': r.status === 'pending',
                'bg-emerald-500/10 text-emerald-300 ring-1 ring-emerald-500/20': r.status === 'approved',
                'bg-red-500/10 text-red-300 ring-1 ring-red-500/20': r.status === 'denied',
              }"
            >
              <span
                class="w-1.5 h-1.5 rounded-full"
                :class="{
                  'bg-amber-400 animate-pulse': r.status === 'pending',
                  'bg-emerald-400': r.status === 'approved',
                  'bg-red-400': r.status === 'denied',
                }"
              />
              {{ r.status }}
            </span>
            <span class="text-slate-200 truncate">{{ r.app_display_name }}</span>
            <span class="text-xs font-mono text-slate-500 truncate">{{ r.app_slug }}</span>
          </div>
          <div
            v-if="r.decided_note"
            class="text-xs text-slate-500 italic truncate max-w-[40%]"
            :title="r.decided_note"
          >
            "{{ r.decided_note }}"
          </div>
        </li>
      </ul>
    </div>

    <!-- Browse available apps -->
    <div v-if="browsable.length" id="browse" class="mt-14 scroll-mt-20">
      <div class="text-xs font-mono uppercase tracking-widest text-slate-500 mb-3">
        Browse more apps
      </div>
      <p class="text-sm text-slate-400 mb-4">
        Apps you don't have access to yet. Request access and an owner will decide.
      </p>
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <div
          v-for="a in browsable"
          :key="a.slug"
          class="glass rounded-2xl p-5 flex flex-col gap-4"
        >
          <div class="flex items-start gap-4">
            <div
              v-if="a.icon_url"
              class="w-12 h-12 rounded-xl bg-cover ring-1 ring-white/10"
              :style="{ backgroundImage: `url(${a.icon_url})` }"
            />
            <div
              v-else
              class="w-12 h-12 rounded-xl flex items-center justify-center text-white font-semibold text-sm shadow-lg opacity-70"
              :style="{ backgroundImage: gradientFor(a.slug) }"
            >
              {{ initials(a.display_name) }}
            </div>
            <div class="flex-1 min-w-0">
              <div class="font-medium truncate text-slate-100">{{ a.display_name }}</div>
              <div class="text-xs font-mono text-slate-500 mb-1.5">{{ a.slug }}</div>
              <div v-if="a.description" class="text-sm text-slate-400 line-clamp-2">
                {{ a.description }}
              </div>
            </div>
          </div>
          <div class="mt-auto pt-1">
            <button
              v-if="a.pending_request_id"
              type="button"
              disabled
              class="w-full inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium bg-amber-500/10 text-amber-300 ring-1 ring-amber-500/20 cursor-not-allowed"
            >
              <span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
              Request pending
            </button>
            <button
              v-else
              type="button"
              class="w-full btn-primary justify-center"
              @click="openRequest(a)"
            >
              <i class="pi pi-send" style="font-size: 0.75rem" />
              Request access
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Request modal -->
    <div
      v-if="requestTarget"
      class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      @click.self="cancelRequest"
    >
      <div class="glass-strong rounded-2xl p-6 max-w-md w-full">
        <div class="text-xs font-mono uppercase tracking-widest text-slate-500 mb-3">
          Request access
        </div>
        <h3 class="text-lg font-semibold mb-1">{{ requestTarget.display_name }}</h3>
        <p class="text-sm text-slate-400 mb-5">
          Owners and admins will be notified. Optional: tell them why you need it.
        </p>
        <textarea
          v-model="requestReason"
          rows="3"
          placeholder="What do you need it for?"
          class="w-full rounded-lg bg-white/5 border border-white/10 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30 transition resize-none mb-4"
        />
        <div
          v-if="requestError"
          class="mb-3 text-xs text-red-400 font-mono"
        >
          {{ requestError }}
        </div>
        <div class="flex items-center gap-3">
          <button type="button" class="btn-primary" :disabled="requesting" @click="submitRequest">
            <i
              v-if="requesting"
              class="pi pi-spin pi-spinner"
              style="font-size: 0.75rem"
            />
            <i v-else class="pi pi-send" style="font-size: 0.75rem" />
            {{ requesting ? "Sending…" : "Send request" }}
          </button>
          <button
            type="button"
            class="text-sm text-slate-400 hover:text-white transition"
            @click="cancelRequest"
          >
            Cancel
          </button>
        </div>
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
