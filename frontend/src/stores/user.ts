import { defineStore } from "pinia";
import { ref } from "vue";

import { api, type User } from "../api/client";

export const useUserStore = defineStore("user", () => {
  const user = ref<User | null>(null);
  const loaded = ref(false);

  async function fetch() {
    user.value = await api.me();
    loaded.value = true;
  }

  return { user, loaded, fetch };
});
