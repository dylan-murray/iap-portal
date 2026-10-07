<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";

import { api } from "../api/client";

const router = useRouter();

interface Preset {
  slug: string;
  display_name: string;
  description: string;
  upstream_service: string;
  upstream_port: number;
  health_check_path: string;
}

const presets: Record<string, Preset> = {
  "streamlit-example": {
    slug: "streamlit-example",
    display_name: "Streamlit Example",
    description: "Demo Streamlit app — validates subdomain routing, WebSocket, and auth.",
    upstream_service: "streamlit-example",
    upstream_port: 8090,
    health_check_path: "/_stcore/health",
  },
  "fastapi-sse": {
    slug: "fastapi-sse",
    display_name: "FastAPI SSE",
    description: "Demo FastAPI app with token-streaming endpoint for SSE validation.",
    upstream_service: "fastapi-sse",
    upstream_port: 8090,
    health_check_path: "/healthz",
  },
};

const slug = ref("");
const displayName = ref("");
const description = ref("");
const iconUrl = ref("");
const upstreamService = ref("");
const upstreamPort = ref(8090);
const healthCheckPath = ref("/healthz");
const ownersRaw = ref("");

const submitting = ref(false);
const error = ref<string | null>(null);

const slugValid = computed(() =>
  slug.value === "" || /^[a-z][a-z0-9-]{0,61}[a-z0-9]$/.test(slug.value),
);

const canSubmit = computed(
  () =>
    !submitting.value &&
    slug.value !== "" &&
    slugValid.value &&
    displayName.value !== "" &&
    upstreamService.value !== "",
);

function applyPreset(key: string) {
  const p = presets[key];
  if (!p) return;
  slug.value = p.slug;
  displayName.value = p.display_name;
  description.value = p.description;
  upstreamService.value = p.upstream_service;
  upstreamPort.value = p.upstream_port;
  healthCheckPath.value = p.health_check_path;
}

async function submit() {
  error.value = null;
  submitting.value = true;
  try {
    await api.createApp({
      slug: slug.value,
      display_name: displayName.value,
      description: description.value || null,
      icon_url: iconUrl.value || null,
      upstream_service: upstreamService.value,
      upstream_port: upstreamPort.value,
      health_check_path: healthCheckPath.value,
      owners: ownersRaw.value
        .split(/[,\s]+/)
        .map((s) => s.trim())
        .filter(Boolean),
    });
    router.push("/admin/apps");
  } catch (e) {
    error.value = String(e).replace(/^Error: /, "");
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <section class="animate-fade-up max-w-2xl">
    <div class="mb-8">
      <div class="inline-flex items-center gap-2 text-xs text-slate-400 font-mono uppercase tracking-widest mb-3">
        <i class="pi pi-plus-circle text-fuchsia-400" style="font-size: 0.7rem" />
        New app
      </div>
      <h1 class="text-3xl font-semibold tracking-tight mb-1">
        <span class="text-gradient">Register app</span>
      </h1>
      <p class="text-sm text-slate-400">
        Add a new application to the portal registry.
      </p>
    </div>

    <div class="glass rounded-2xl p-6 mb-4">
      <div class="text-xs font-mono uppercase tracking-widest text-slate-500 mb-3">
        Quick presets
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          v-for="(p, k) in presets"
          :key="k"
          type="button"
          class="inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm bg-white/5 hover:bg-white/10 ring-1 ring-white/10 hover:ring-white/20 transition"
          @click="applyPreset(k)"
        >
          <i class="pi pi-bolt text-fuchsia-400" style="font-size: 0.7rem" />
          {{ p.display_name }}
        </button>
      </div>
    </div>

    <form class="glass rounded-2xl p-6 space-y-6" @submit.prevent="submit">
      <div>
        <div class="text-xs font-mono uppercase tracking-widest text-slate-500 mb-3">
          Identity
        </div>
        <div class="space-y-4">
          <div>
            <label class="block text-sm text-slate-300 mb-1.5">
              Slug
              <span class="text-slate-500 font-mono ml-1">a-z, 0-9, hyphens</span>
            </label>
            <input
              v-model="slug"
              type="text"
              placeholder="my-app"
              class="w-full rounded-lg bg-white/5 border px-3 py-2 text-sm text-slate-100 font-mono placeholder-slate-600 focus:outline-none transition"
              :class="
                slugValid
                  ? 'border-white/10 focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30'
                  : 'border-red-500/40 focus:border-red-500/60 focus:ring-1 focus:ring-red-500/30'
              "
            />
            <div v-if="!slugValid" class="mt-1 text-xs text-red-400">
              Must start with a letter, end alphanumeric, and contain only lowercase letters/digits/hyphens.
            </div>
          </div>

          <div>
            <label class="block text-sm text-slate-300 mb-1.5">Display name</label>
            <input
              v-model="displayName"
              type="text"
              placeholder="My App"
              class="w-full rounded-lg bg-white/5 border border-white/10 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30 transition"
            />
          </div>

          <div>
            <label class="block text-sm text-slate-300 mb-1.5">
              Description <span class="text-slate-500">(optional)</span>
            </label>
            <textarea
              v-model="description"
              rows="2"
              placeholder="What does this app do?"
              class="w-full rounded-lg bg-white/5 border border-white/10 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30 transition resize-none"
            />
          </div>

          <div>
            <label class="block text-sm text-slate-300 mb-1.5">
              Icon URL <span class="text-slate-500">(optional)</span>
            </label>
            <input
              v-model="iconUrl"
              type="url"
              placeholder="https://..."
              class="w-full rounded-lg bg-white/5 border border-white/10 px-3 py-2 text-sm text-slate-100 font-mono placeholder-slate-600 focus:outline-none focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30 transition"
            />
          </div>
        </div>
      </div>

      <div class="border-t border-white/5 pt-6">
        <div class="text-xs font-mono uppercase tracking-widest text-slate-500 mb-3">
          Routing
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-4">
          <div>
            <label class="block text-sm text-slate-300 mb-1.5">Upstream service</label>
            <input
              v-model="upstreamService"
              type="text"
              placeholder="my-app.iap-app-my-app.svc.cluster.local"
              class="w-full rounded-lg bg-white/5 border border-white/10 px-3 py-2 text-sm text-slate-100 font-mono placeholder-slate-600 focus:outline-none focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30 transition"
            />
          </div>
          <div>
            <label class="block text-sm text-slate-300 mb-1.5">Port</label>
            <input
              v-model.number="upstreamPort"
              type="number"
              class="w-24 rounded-lg bg-white/5 border border-white/10 px-3 py-2 text-sm text-slate-100 font-mono focus:outline-none focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30 transition"
            />
          </div>
        </div>
        <div class="mt-4">
          <label class="block text-sm text-slate-300 mb-1.5">Health check path</label>
          <input
            v-model="healthCheckPath"
            type="text"
            class="w-full rounded-lg bg-white/5 border border-white/10 px-3 py-2 text-sm text-slate-100 font-mono focus:outline-none focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30 transition"
          />
        </div>
      </div>

      <div class="border-t border-white/5 pt-6">
        <div class="text-xs font-mono uppercase tracking-widest text-slate-500 mb-3">
          Access
        </div>
        <label class="block text-sm text-slate-300 mb-1.5">
          Owners <span class="text-slate-500">(comma or space separated emails)</span>
        </label>
        <input
          v-model="ownersRaw"
          type="text"
          placeholder="alice@example.com, bob@example.com"
          class="w-full rounded-lg bg-white/5 border border-white/10 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30 transition"
        />
        <p class="mt-2 text-xs text-slate-500">
          You'll be added as an owner automatically.
        </p>
      </div>

      <div
        v-if="error"
        class="rounded-lg bg-red-500/10 border border-red-500/20 px-4 py-3 text-sm text-red-300"
      >
        <div class="flex items-start gap-2">
          <i class="pi pi-exclamation-triangle mt-0.5" style="font-size: 0.85rem" />
          <span class="font-mono text-xs">{{ error }}</span>
        </div>
      </div>

      <div class="flex items-center gap-3 pt-2">
        <button type="submit" class="btn-primary" :disabled="!canSubmit">
          <i
            v-if="submitting"
            class="pi pi-spin pi-spinner"
            style="font-size: 0.75rem"
          />
          <i v-else class="pi pi-check" style="font-size: 0.75rem" />
          {{ submitting ? "Registering…" : "Register app" }}
        </button>
        <button
          type="button"
          class="text-sm text-slate-400 hover:text-white transition"
          @click="router.push('/admin/apps')"
        >
          Cancel
        </button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none !important;
}
</style>
