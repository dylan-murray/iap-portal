import { createRouter, createWebHistory } from "vue-router";

import AdminAccessRequests from "./views/AdminAccessRequests.vue";
import AdminAppManage from "./views/AdminAppManage.vue";
import AdminAppRegister from "./views/AdminAppRegister.vue";
import AdminApps from "./views/AdminApps.vue";
import AdminGroupManage from "./views/AdminGroupManage.vue";
import AdminGroups from "./views/AdminGroups.vue";
import Dashboard from "./views/Dashboard.vue";
import { useUserStore } from "./stores/user";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "dashboard", component: Dashboard },
    {
      path: "/admin/apps",
      name: "admin-apps",
      component: AdminApps,
      meta: { requiresAdmin: true },
    },
    {
      path: "/admin/apps/new",
      name: "admin-apps-new",
      component: AdminAppRegister,
      meta: { requiresAdmin: true },
    },
    {
      // Manage page — no admin meta, because owners (not just admins) can reach it.
      // Backend returns 403 if the caller has neither role; the page surfaces that.
      path: "/admin/apps/:slug",
      name: "admin-app-manage",
      component: AdminAppManage,
    },
    {
      path: "/admin/groups",
      name: "admin-groups",
      component: AdminGroups,
      meta: { requiresAdmin: true },
    },
    {
      path: "/admin/groups/:name",
      name: "admin-group-manage",
      component: AdminGroupManage,
      meta: { requiresAdmin: true },
    },
    {
      // Visible to admins AND owners — backend returns only requests the caller
      // can decide, so no admin guard here. Non-admins who own nothing just see
      // an empty list.
      path: "/admin/requests",
      name: "admin-access-requests",
      component: AdminAccessRequests,
    },
  ],
});

router.beforeEach((to) => {
  if (to.meta.requiresAdmin) {
    const userStore = useUserStore();
    if (userStore.loaded && !userStore.user?.is_admin) {
      return { path: "/" };
    }
  }
});
