<script setup lang="ts">
import { defineAsyncComponent, onMounted, ref } from "vue";
import { RouterLink, RouterView, useRouter } from "vue-router";

import { api } from "./api/client";
import { useUserStore } from "./stores/user";

const userStore = useUserStore();
const router = useRouter();

const pendingCount = ref(0);
const displayName = ref("iap-portal");
const annotationsEnabled = ref(false);
const DevAnnotator = defineAsyncComponent(() => import("./components/DevAnnotator.vue"));

async function refreshPendingCount() {
  try {
    const pending = await api.listPendingAccessRequests();
    pendingCount.value = pending.length;
  } catch {
    pendingCount.value = 0;
  }
}

onMounted(async () => {
  // api client redirects to /login on 401, so no need to catch here
  const [config] = await Promise.all([api.config(), userStore.fetch()]);
  displayName.value = config.display_name;
  document.title = config.display_name;
  refreshPendingCount();
  if (userStore.user?.is_admin) {
    const response = await fetch("/dev/annotations", { cache: "no-store" }).catch(() => null);
    annotationsEnabled.value = response?.ok ?? false;
  }
});

// Re-fetch the pending count whenever the route changes — cheap and keeps the
// badge fresh after an approve/deny from the manage page.
router.afterEach(() => {
  if (userStore.loaded && userStore.user) refreshPendingCount();
});
</script>

<template>
  <div v-if="userStore.loaded && userStore.user" class="min-h-full flex flex-col">
    <header class="sticky top-0 z-20 glass-strong border-b border-white/10">
      <div class="max-w-6xl mx-auto px-6 h-14 flex items-center gap-8">
        <RouterLink to="/" class="flex items-center gap-2.5 group">
          <span
            class="w-8 h-8 rounded-lg flex items-center justify-center shadow-lg shadow-fuchsia-500/20"
            style="background-image: linear-gradient(135deg, #a855f7, #ec4899 55%, #06b6d4);"
            aria-hidden="true"
          >
            <span
              class="block w-[20px] h-[20px] bg-white"
              style="
                -webkit-mask: url('/assets/iap-logo.svg') center / contain no-repeat;
                        mask: url('/assets/iap-logo.svg') center / contain no-repeat;
              "
            />
          </span>
          <span class="font-semibold tracking-tight text-lg text-gradient truncate max-w-[16rem]" :title="displayName" style="letter-spacing: -0.02em;">
            {{ displayName }}
          </span>
        </RouterLink>
        <nav class="flex-1 flex gap-6">
          <RouterLink to="/" class="nav-link">Dashboard</RouterLink>
          <RouterLink v-if="userStore.user.is_admin" to="/admin/apps" class="nav-link">
            Apps
          </RouterLink>
          <RouterLink v-if="userStore.user.is_admin" to="/admin/groups" class="nav-link">
            Groups
          </RouterLink>
        </nav>
        <div class="flex items-center gap-4">
          <RouterLink
            v-if="pendingCount > 0"
            to="/admin/requests"
            class="relative inline-flex items-center justify-center w-9 h-9 rounded-lg bg-white/5 hover:bg-white/10 ring-1 ring-white/10 hover:ring-white/20 transition"
            title="Pending access requests"
          >
            <i class="pi pi-bell text-slate-300" style="font-size: 0.95rem" />
            <span
              class="absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 rounded-full bg-fuchsia-500 text-white text-[10px] font-bold flex items-center justify-center shadow-lg shadow-fuchsia-500/40"
            >
              {{ pendingCount > 99 ? "99+" : pendingCount }}
            </span>
          </RouterLink>
          <div class="hidden sm:flex items-center gap-2 text-sm">
            <span
              class="w-6 h-6 rounded-full bg-linear-to-br from-fuchsia-500 to-cyan-500 flex items-center justify-center text-[10px] font-semibold text-white uppercase"
            >
              {{ (userStore.user.name || userStore.user.email).slice(0, 1) }}
            </span>
            <span class="text-slate-300">
              {{ userStore.user.name || userStore.user.email }}
            </span>
            <span
              v-if="userStore.user.is_admin"
              class="rounded-sm px-1.5 py-0.5 text-[10px] font-mono uppercase tracking-wider bg-fuchsia-500/15 text-fuchsia-300 ring-1 ring-fuchsia-500/30"
            >
              admin
            </span>
          </div>
          <!-- POST so the portal's Origin check covers sign-out (no cross-site logout). -->
          <form method="post" action="/logout" class="flex">
            <button
              type="submit"
              class="text-sm text-slate-400 hover:text-white transition flex items-center gap-1.5"
            >
              <i class="pi pi-sign-out" style="font-size: 0.8rem" />
              Sign out
            </button>
          </form>
        </div>
      </div>
    </header>
    <main class="flex-1 max-w-6xl mx-auto w-full px-6 py-10">
      <RouterView />
      <DevAnnotator v-if="annotationsEnabled" />
    </main>
  </div>
</template>
