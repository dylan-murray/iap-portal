<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { api, type Group } from "../api/client";

const router = useRouter();

const groups = ref<Group[]>([]);
const loading = ref(true);

const newName = ref("");
const newDescription = ref("");
const createError = ref<string | null>(null);
const creating = ref(false);

const nameValid = computed(
  () => newName.value === "" || /^[a-z][a-z0-9-]{0,61}[a-z0-9]$/.test(newName.value),
);

async function refresh() {
  loading.value = true;
  try {
    groups.value = await api.listGroups();
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);

async function create() {
  createError.value = null;
  if (!newName.value.trim() || !nameValid.value) return;
  creating.value = true;
  try {
    await api.createGroup({
      name: newName.value.trim(),
      description: newDescription.value.trim() || null,
    });
    newName.value = "";
    newDescription.value = "";
    await refresh();
  } catch (e) {
    createError.value = String(e).replace(/^Error:\s*/, "");
  } finally {
    creating.value = false;
  }
}

async function remove(name: string) {
  if (!confirm(`Delete group "${name}"? Any access grants to this group will also be removed.`)) return;
  try {
    await api.deleteGroup(name);
    groups.value = groups.value.filter((g) => g.name !== name);
  } catch (e) {
    alert(String(e).replace(/^Error:\s*/, ""));
  }
}
</script>

<template>
  <section class="animate-fade-up">
    <div class="mb-8">
      <div class="inline-flex items-center gap-2 text-xs text-slate-400 font-mono uppercase tracking-widest mb-3">
        <i class="pi pi-users text-fuchsia-400" style="font-size: 0.7rem" />
        Admin
      </div>
      <h1 class="text-3xl font-semibold tracking-tight mb-1">
        <span class="text-gradient">Groups</span>
      </h1>
      <p class="text-sm text-slate-400">
        Collections of users that can be granted access to apps as a unit.
      </p>
    </div>

    <div class="glass rounded-2xl p-6 mb-6 max-w-2xl">
      <div class="text-xs font-mono uppercase tracking-widest text-slate-500 mb-3">
        New group
      </div>
      <form class="space-y-3" @submit.prevent="create">
        <div class="grid grid-cols-1 sm:grid-cols-[1fr_2fr] gap-3">
          <input
            v-model="newName"
            type="text"
            placeholder="engineering"
            class="rounded-lg bg-white/5 border px-3 py-2 text-sm text-slate-100 font-mono placeholder-slate-600 focus:outline-none transition"
            :class="
              nameValid
                ? 'border-white/10 focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30'
                : 'border-red-500/40'
            "
          />
          <input
            v-model="newDescription"
            type="text"
            placeholder="Description (optional)"
            class="rounded-lg bg-white/5 border border-white/10 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30 transition"
          />
        </div>
        <div v-if="!nameValid" class="text-xs text-red-400">
          Lowercase letters, digits, hyphens. Must start with a letter, end alphanumeric.
        </div>
        <div class="flex items-center gap-3">
          <button
            type="submit"
            class="btn-primary"
            :disabled="creating || !newName.trim() || !nameValid"
          >
            <i
              v-if="creating"
              class="pi pi-spin pi-spinner"
              style="font-size: 0.7rem"
            />
            <i v-else class="pi pi-plus" style="font-size: 0.7rem" />
            Create group
          </button>
          <span v-if="createError" class="text-xs text-red-400 font-mono">{{ createError }}</span>
        </div>
      </form>
    </div>

    <div class="glass rounded-2xl overflow-hidden">
      <div v-if="loading" class="p-6 text-sm text-slate-500">Loading…</div>
      <div v-else-if="groups.length === 0" class="p-8 text-center text-sm text-slate-500">
        No groups yet. Create one above.
      </div>
      <ul v-else class="divide-y divide-white/5">
        <li
          v-for="g in groups"
          :key="g.id"
          class="flex items-center justify-between px-5 py-3 hover:bg-white/[0.02] transition"
        >
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2">
              <router-link
                :to="`/admin/groups/${g.name}`"
                class="font-mono text-sm text-fuchsia-300 hover:text-fuchsia-200 hover:underline transition"
              >
                {{ g.name }}
              </router-link>
              <span class="text-xs font-mono text-slate-500">
                {{ g.member_count }} {{ g.member_count === 1 ? "member" : "members" }}
              </span>
            </div>
            <div v-if="g.description" class="text-xs text-slate-500 mt-0.5 truncate">
              {{ g.description }}
            </div>
          </div>
          <div class="flex items-center gap-3">
            <button
              class="text-xs text-slate-500 hover:text-white transition"
              @click="router.push(`/admin/groups/${g.name}`)"
            >
              Manage
            </button>
            <button
              class="text-xs text-slate-500 hover:text-red-400 transition"
              @click="remove(g.name)"
            >
              Delete
            </button>
          </div>
        </li>
      </ul>
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
