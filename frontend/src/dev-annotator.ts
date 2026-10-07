import { createApp } from "vue";
import DevAnnotator from "./components/DevAnnotator.vue";

const root = document.getElementById("dev-annotation-root");
if (root) createApp(DevAnnotator, { token: root.dataset.token }).mount(root);
