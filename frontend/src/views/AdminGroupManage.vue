<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, type GroupDetail } from "../api/client";

const route = useRoute();
const router = useRouter();

const name = route.params.name as string;

const group = ref<GroupDetail | null>(null);
const loading = ref(true);
const pageError = ref<string | null>(null);

const newMember = ref("");
const memberError = ref<string | null>(null);
const memberBusy = ref(false);

async function refresh() {
  loading.value = true;
  pageError.value = null;
  try {
    group.value = await api.getGroup(name);
  } catch (e) {
    pageError.value = String(e).replace(/^Error:\s*/, "");
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);

async function addMember() {
  memberError.value = null;
  if (!newMember.value.trim()) return;
  memberBusy.value = true;
  try {
    group.value = await api.addGroupMember(name, newMember.value.trim());
    newMember.value = "";
  } catch (e) {
    memberError.value = String(e).replace(/^Error:\s*/, "");
  } finally {
    memberBusy.value = false;
  }
}

async function removeMember(email: string) {
  try {
    await api.removeGroupMember(name, email);
    if (group.value) {
      group.value = {
        ...group.value,
        members: group.value.members.filter((m) => m !== email),
      };
    }
  } catch (e) {
    pageError.value = String(e).replace(/^Error:\s*/, "");
  }
}
</script>

<template>
  <section class="animate-fade-up">
    <div class="mb-8">
      <button
        class="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-white transition mb-3"
        @click="router.push('/admin/groups')"
      >
        <i class="pi pi-arrow-left" style="font-size: 0.7rem" />
        Back to groups
      </button>
      <h1 class="text-3xl font-semibold tracking-tight mb-1">
        <span class="text-gradient">{{ name }}</span>
      </h1>
      <p v-if="group?.description" class="text-sm text-slate-400">{{ group.description }}</p>
    </div>

    <div v-if="loading" class="glass rounded-2xl p-8 text-sm text-slate-500">Loading…</div>

    <div
      v-else-if="pageError"
      class="glass rounded-2xl p-6 border-red-500/30 text-red-300"
    >
      <div class="flex items-start gap-3">
        <i class="pi pi-exclamation-triangle mt-1" />
        <div>
          <div class="font-medium mb-1">Could not load group</div>
          <div class="text-sm font-mono">{{ pageError }}</div>
        </div>
      </div>
    </div>

    <div v-else-if="group" class="glass rounded-2xl p-6 max-w-2xl">
      <div class="flex items-center justify-between mb-4">
        <div>
          <div class="text-xs font-mono uppercase tracking-widest text-slate-500">Members</div>
          <div class="text-xs text-slate-500 mt-1">
            Anyone in this group inherits all access grants made to it.
          </div>
        </div>
        <span class="text-xs font-mono text-slate-400">{{ group.members.length }}</span>
      </div>

      <ul v-if="group.members.length" class="space-y-1.5 mb-4">
        <li
          v-for="email in group.members"
          :key="email"
          class="flex items-center justify-between rounded-lg bg-white/5 px-3 py-2 ring-1 ring-white/5"
        >
          <div class="flex items-center gap-2 text-sm">
            <i class="pi pi-user text-slate-500" style="font-size: 0.8rem" />
            <span class="text-slate-200">{{ email }}</span>
          </div>
          <button
            class="text-xs text-slate-500 hover:text-red-400 transition"
            @click="removeMember(email)"
          >
            Remove
          </button>
        </li>
      </ul>
      <div v-else class="text-sm text-slate-500 mb-4">
        No members yet. Add one below.
      </div>

      <form class="flex items-center gap-2" @submit.prevent="addMember">
        <input
          v-model="newMember"
          type="email"
          placeholder="user@example.com"
          class="flex-1 rounded-lg bg-white/5 border border-white/10 px-3 py-1.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-fuchsia-500/50 focus:ring-1 focus:ring-fuchsia-500/30 transition"
        />
        <button
          type="submit"
          class="btn-primary"
          :disabled="memberBusy || !newMember.trim()"
        >
          <i
            v-if="memberBusy"
            class="pi pi-spin pi-spinner"
            style="font-size: 0.7rem"
          />
          <i v-else class="pi pi-plus" style="font-size: 0.7rem" />
          Add member
        </button>
      </form>
      <div v-if="memberError" class="mt-2 text-xs text-red-400 font-mono">{{ memberError }}</div>
      <p class="mt-3 text-xs text-slate-500">
        Users must have logged into the portal at least once before they can be added.
      </p>
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
