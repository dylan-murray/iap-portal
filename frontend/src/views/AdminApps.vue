<script setup lang="ts">
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import Tag from "primevue/tag";
import { computed, onMounted, ref } from "vue";

import { api, type App } from "../api/client";

const apps = ref<App[]>([]);
const loading = ref(true);

const enabledCount = computed(() => apps.value.filter((a) => a.is_enabled).length);

onMounted(async () => {
  apps.value = await api.listApps();
  loading.value = false;
});
</script>

<template>
  <section class="animate-fade-up">
    <div class="flex items-end justify-between mb-8 gap-4 flex-wrap">
      <div>
        <div class="inline-flex items-center gap-2 text-xs text-slate-400 font-mono uppercase tracking-widest mb-3">
          <i class="pi pi-shield text-fuchsia-400" style="font-size: 0.7rem" />
          Admin
        </div>
        <h1 class="text-3xl font-semibold tracking-tight mb-1">
          <span class="text-gradient">Registered apps</span>
        </h1>
        <p class="text-sm text-slate-400">
          {{ apps.length }} total
          <span class="text-slate-600">·</span>
          <span class="text-emerald-400">{{ enabledCount }} enabled</span>
        </p>
      </div>
      <button class="btn-primary" @click="$router.push('/admin/apps/new')">
        <i class="pi pi-plus" style="font-size: 0.75rem" />
        Register app
      </button>
    </div>

    <div class="glass rounded-2xl overflow-hidden">
      <DataTable
        :value="apps"
        :loading="loading"
        stripedRows
        :pt="{
          root: { class: 'text-sm' },
        }"
      >
        <Column field="slug" header="Slug" sortable>
          <template #body="{ data }">
            <router-link
              :to="`/admin/apps/${data.slug}`"
              class="font-mono text-xs text-fuchsia-300 hover:text-fuchsia-200 hover:underline transition"
            >
              {{ data.slug }}
            </router-link>
          </template>
        </Column>
        <Column field="display_name" header="Name" sortable>
          <template #body="{ data }">
            <router-link
              :to="`/admin/apps/${data.slug}`"
              class="font-medium text-slate-100 hover:text-white hover:underline transition"
            >
              {{ data.display_name }}
            </router-link>
          </template>
        </Column>
        <Column field="upstream_service" header="Upstream">
          <template #body="{ data }">
            <span class="font-mono text-xs text-slate-400">
              {{ data.upstream_service }}<span class="text-slate-600">:{{ data.upstream_port }}</span>
            </span>
          </template>
        </Column>
        <Column header="Status">
          <template #body="{ data }">
            <span
              v-if="data.is_enabled"
              class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium bg-emerald-500/10 text-emerald-300 ring-1 ring-emerald-500/20"
            >
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]" />
              Enabled
            </span>
            <span
              v-else
              class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium bg-slate-500/10 text-slate-400 ring-1 ring-slate-500/20"
            >
              <span class="w-1.5 h-1.5 rounded-full bg-slate-500" />
              Disabled
            </span>
          </template>
        </Column>
        <Column header="Owners">
          <template #body="{ data }">
            <div class="flex flex-wrap gap-1">
              <span
                v-for="o in data.owners"
                :key="o"
                class="inline-flex rounded-md px-2 py-0.5 text-xs bg-white/5 text-slate-300 ring-1 ring-white/10"
              >
                {{ o }}
              </span>
            </div>
          </template>
        </Column>
      </DataTable>
    </div>
  </section>
</template>
