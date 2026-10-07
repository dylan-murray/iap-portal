<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { routerKey } from "vue-router";
import { inject } from "vue";

type Rect = { x: number; y: number; width: number; height: number };
type Target = { kind: "element" | "area"; path: string; selector: string; text: string;
  bounds: Rect; anchor: Rect; viewport: Rect; scroll_x: number; scroll_y: number };
type Note = Target & { id: string; note: string; author: string; created_at: string };
const props = defineProps<{ token?: string }>();
const router = inject(routerKey, null);
const path = computed(() => router?.currentRoute.value.path ?? location.pathname);
const mode = ref<"off" | "element" | "area">("off");
const notes = ref<Note[]>([]);
const draft = ref<Target | null>(null);
const message = ref("");
const status = ref("");
const busy = ref(false);
const showNotes = ref(false);
const hover = ref<Rect | null>(null);
const tick = ref(0);
const textarea = ref<HTMLTextAreaElement>();
let start: { x: number; y: number } | null = null;
let dragged = false;
let dragLauncher: { x: number; y: number } | null = null;
const launcher = ref({ x: 20, y: 20 });
const rect = (r: DOMRect): Rect => ({ x: r.x, y: r.y, width: r.width, height: r.height });
const boxStyle = (r: Rect) => ({ left: `${r.x}px`, top: `${r.y}px`, width: `${r.width}px`, height: `${r.height}px` });
const own = (e: Event) => (e.target as Element)?.closest?.("[data-dev-annotator]");

function selector(el: Element): string {
  const parts: string[] = [];
  while (el !== document.documentElement) {
    if (el.id) { parts.unshift(`#${CSS.escape(el.id)}`); break; }
    const index = [...el.parentElement!.children].filter(s => s.tagName === el.tagName).indexOf(el) + 1;
    parts.unshift(`${el.tagName.toLowerCase()}:nth-of-type(${index})`);
    el = el.parentElement!;
  }
  return parts.join(" > ") || "html";
}
function target(el: Element, bounds: Rect, kind: Target["kind"]): Target {
  return { kind, path: location.pathname, selector: selector(el),
    text: (el.textContent || "").trim().slice(0, 2000), bounds, anchor: rect(el.getBoundingClientRect()),
    viewport: { x: 0, y: 0, width: innerWidth, height: innerHeight }, scroll_x: scrollX, scroll_y: scrollY };
}
async function edit(t: Target) {
  draft.value = t; message.value = ""; hover.value = null; status.value = "";
  await nextTick(); textarea.value?.focus();
}
function pointerDown(e: PointerEvent) {
  if (mode.value !== "area" || own(e) || draft.value || e.button !== 0) return;
  e.preventDefault(); e.stopImmediatePropagation(); start = { x: e.clientX, y: e.clientY }; dragged = false;
}
function pointerMove(e: PointerEvent) {
  if (dragLauncher) {
    launcher.value = { x: Math.max(8, Math.min(innerWidth - 280, e.clientX - dragLauncher.x)),
      y: Math.max(8, Math.min(innerHeight - 52, e.clientY - dragLauncher.y)) }; return;
  }
  if (mode.value === "off" || draft.value || own(e)) { hover.value = null; return; }
  if (start) {
    hover.value = { x: Math.min(start.x, e.clientX), y: Math.min(start.y, e.clientY),
      width: Math.abs(e.clientX - start.x), height: Math.abs(e.clientY - start.y) };
  } else if (mode.value === "element") hover.value = rect((e.target as Element).getBoundingClientRect());
}
function pointerUp(e: PointerEvent) {
  if (dragLauncher) {
    dragLauncher = null;
    try { localStorage.setItem("iap-annotation-position", JSON.stringify(launcher.value)); } catch { /* optional preference */ }
  }
  if (!start) return;
  e.preventDefault(); e.stopImmediatePropagation(); start = null; dragged = true;
  const area = hover.value; hover.value = null;
  if (!area || area.width < 12 || area.height < 12) return;
  const el = document.elementFromPoint(area.x + area.width / 2, area.y + area.height / 2);
  if (el && !el.closest("[data-dev-annotator]")) edit(target(el, area, "area"));
}
function click(e: MouseEvent) {
  if (own(e) || mode.value === "off") return;
  e.preventDefault(); e.stopImmediatePropagation();
  if (dragged) { dragged = false; return; }
  if (draft.value || mode.value !== "element") return;
  const el = e.target as Element;
  edit(target(el, rect(el.getBoundingClientRect()), "element"));
}
function escape(e: KeyboardEvent) {
  if (e.key !== "Escape") return;
  if (draft.value) { draft.value = null; e.stopImmediatePropagation(); }
  else { mode.value = "off"; hover.value = null; start = null; }
}
async function request(path = "", init?: RequestInit) {
  const response = await fetch(`/dev/annotations${path}`, { ...init, cache: "no-store", headers: { "Content-Type": "application/json", ...(props.token ? { "X-Dev-Annotation-Token": props.token } : {}) } });
  if (!response.ok) throw new Error(`Feedback could not be saved or loaded (${response.status}). Your draft is still here.`);
  return response.status === 204 ? null : response.json();
}
async function save() {
  if (!draft.value || !message.value.trim() || busy.value) return;
  busy.value = true;
  try {
    const saved = await request("", { method: "POST", body: JSON.stringify({ ...draft.value, note: message.value }) });
    notes.value.push(saved); draft.value = null; message.value = ""; status.value = "Saved — ready for review";
  } catch (error) { status.value = String(error); }
  finally { busy.value = false; }
}
async function remove(id: string) {
  try { await request(`/${id}`, { method: "DELETE" }); notes.value = notes.value.filter(n => n.id !== id); }
  catch (error) { status.value = String(error); }
}
async function copy() {
  try { await navigator.clipboard.writeText(JSON.stringify(notes.value, null, 2)); status.value = "Copied feedback with target context"; }
  catch { status.value = "Clipboard unavailable. Notes are still saved for review."; }
}
const pins = computed(() => {
  void tick.value; void path.value;
  return notes.value.filter(n => n.path === path.value).flatMap((n, index) => {
    const el = document.querySelector(n.selector);
    if (!el) return [];
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return [];
    const b = n.kind === "element" ? rect(r) : { ...n.bounds, x: r.x + n.bounds.x - n.anchor.x, y: r.y + n.bounds.y - n.anchor.y };
    return [{ note: n, index: index + 1, bounds: b }];
  });
});
function refresh() { tick.value++; hover.value = null; }
function toggle(next: "element" | "area") { mode.value = mode.value === next ? "off" : next; draft.value = null; hover.value = null; }
onMounted(async () => {
  try {
    const saved = JSON.parse(localStorage.getItem("iap-annotation-position") || "null");
    if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) launcher.value = { x: Math.max(8, Math.min(innerWidth - 280, saved.x)), y: Math.max(8, Math.min(innerHeight - 52, saved.y)) };
  } catch { /* optional preference */ }
  document.addEventListener("pointerdown", pointerDown, true);
  document.addEventListener("pointermove", pointerMove, true);
  document.addEventListener("pointerup", pointerUp, true);
  document.addEventListener("click", click, true);
  document.addEventListener("keydown", escape, true);
  window.addEventListener("scroll", refresh, true);
  window.addEventListener("resize", refresh);
  try { notes.value = await request(); } catch (error) { status.value = String(error); }
});
onUnmounted(() => {
  document.removeEventListener("pointerdown", pointerDown, true);
  document.removeEventListener("pointermove", pointerMove, true);
  document.removeEventListener("pointerup", pointerUp, true);
  document.removeEventListener("click", click, true);
  document.removeEventListener("keydown", escape, true);
  window.removeEventListener("scroll", refresh, true);
  window.removeEventListener("resize", refresh);
});
</script>

<template>
  <Teleport to="body">
    <div data-dev-annotator class="annotator" :class="{ picking: mode !== 'off' }">
      <div class="annotation-bar" :style="{ left: `${launcher.x}px`, top: `${launcher.y}px` }" aria-label="Dev annotation tools">
        <button title="Drag annotation toolbar" aria-label="Move annotation toolbar" class="handle" @pointerdown="dragLauncher = { x: $event.clientX - launcher.x, y: $event.clientY - launcher.y }">⠿</button>
        <span class="dev-label">DEV</span>
        <button :aria-pressed="mode === 'element'" @click="toggle('element')">Pin</button>
        <button :aria-pressed="mode === 'area'" @click="toggle('area')">Area</button>
        <button :aria-expanded="showNotes" @click="showNotes = !showNotes">Notes {{ notes.length }}</button>
        <button v-if="mode !== 'off'" @click="mode = 'off'; draft = null; hover = null">Done</button>
      </div>
      <div v-if="mode !== 'off'" class="annotation-hint">{{ mode === 'area' ? 'Drag a rectangle around a section' : 'Click an element to leave feedback' }} · Esc to exit</div>
      <div v-if="hover" class="annotation-outline" :style="boxStyle(hover)" />
      <div v-for="pin in pins" :key="pin.note.id" class="annotation-outline saved" :style="boxStyle(pin.bounds)">
        <button class="pin" :title="pin.note.note" @click="showNotes = true">{{ pin.index }}</button>
      </div>
      <section v-if="draft" class="annotation-editor" aria-label="Leave UI feedback">
        <div class="panel-heading"><strong>Leave feedback</strong><button aria-label="Close feedback editor" @click="draft = null">×</button></div>
        <p class="target-label">{{ draft.text.slice(0, 100) || draft.selector }}</p>
        <textarea ref="textarea" v-model="message" aria-label="Feedback" placeholder="What should change here?" maxlength="10000" @keydown.enter.meta.prevent="save" @keydown.enter.ctrl.prevent="save" />
        <div class="panel-footer"><span>⌘ / Ctrl + Enter</span><button :disabled="busy || !message.trim()" @click="save">{{ busy ? 'Saving…' : 'Save feedback' }}</button></div>
      </section>
      <section v-if="showNotes" class="annotation-notes" aria-label="Saved UI feedback">
        <div class="panel-heading"><strong>UI feedback · {{ notes.length }}</strong><button aria-label="Close notes" @click="showNotes = false">×</button></div>
        <p>Saved locally for review. Ask your agent to review annotations, or copy the context into your chat.</p>
        <button :disabled="!notes.length" @click="copy">Copy feedback</button>
        <p v-if="!notes.length">No notes yet. Choose Pin or Area to start.</p>
        <article v-for="note in notes" :key="note.id">
          <div class="panel-heading"><small>{{ note.path }} · {{ note.kind }}</small><button title="Delete note" @click="remove(note.id)">Delete</button></div>
          <blockquote>{{ note.note }}</blockquote>
          <small>{{ note.text.slice(0, 140) || note.selector }}</small>
        </article>
      </section>
      <div v-if="status" class="annotation-status" role="status" @click="status = ''">{{ status }}</div>
    </div>
  </Teleport>
</template>

<style scoped>
.annotator, .annotator * { box-sizing: border-box; }
.annotator { position: fixed; inset: 0; z-index: 2147483646; pointer-events: none; color: #e2e8f0; font: 13px/1.5 system-ui, sans-serif; }
.annotation-bar, .annotation-editor, .annotation-notes { pointer-events: auto; position: fixed; background: #151c2d; border: 1px solid #475569; border-radius: 12px; box-shadow: 0 12px 40px #0006; }
.annotation-bar { display: flex; align-items: center; gap: 4px; padding: 6px; max-width: calc(100vw - 16px); }
.annotator button { background: transparent; border: 1px solid transparent; color: inherit; padding: 6px 9px; border-radius: 6px; cursor: pointer; font: inherit; }
.annotator button:hover, .annotator button[aria-pressed=true] { background: #334155; border-color: #64748b; }
.annotator button:focus-visible, .annotator textarea:focus-visible { outline: 2px solid #a5b4fc; outline-offset: 2px; }
.annotator button:disabled { opacity: .45; cursor: default; }
.annotator .handle { cursor: move; touch-action: none; }
.dev-label { font-size: 10px; letter-spacing: .08em; color: #c4b5fd; margin-right: 4px; }
.annotation-hint { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: #151c2d; padding: 8px 14px; border-radius: 20px; width: max-content; max-width: 90vw; }
.annotation-outline { position: fixed; border: 2px solid #a78bfa; background: #a78bfa18; pointer-events: none; }
.annotation-outline.saved { border: 1px dashed #a78bfa; background: transparent; }
.annotator .pin { pointer-events: auto; position: absolute; top: -12px; left: -10px; background: #6d28d9; color: white; border-radius: 50%; padding: 2px 7px; min-width: 24px; }
.annotation-editor { right: 20px; bottom: 60px; width: min(370px, calc(100vw - 32px)); padding: 16px; z-index: 2; }
.panel-heading, .panel-footer { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.target-label { color: #94a3b8; overflow-wrap: anywhere; margin: 8px 0; }
.annotator textarea { display: block; width: 100%; min-height: 110px; resize: vertical; max-height: 40vh; color: #f8fafc; background: #0f172a; border: 1px solid #64748b; border-radius: 6px; padding: 10px; font: inherit; }
.panel-footer { margin-top: 10px; color: #94a3b8; font-size: 11px; }
.panel-footer button { background: #6d28d9; color: white; font-size: 13px; }
.annotation-notes { top: 80px; right: 20px; width: min(370px, calc(100vw - 32px)); max-height: calc(100vh - 160px); overflow: auto; padding: 16px; }
.annotation-notes p { color: #94a3b8; margin: 12px 0; }
.annotation-notes article { border-top: 1px solid #334155; padding: 12px 0; }
.annotation-notes blockquote { white-space: pre-wrap; overflow-wrap: anywhere; margin: 8px 0; }
.annotation-notes small { color: #94a3b8; overflow-wrap: anywhere; }
.annotation-status { pointer-events: auto; position: fixed; bottom: 16px; right: 20px; max-width: min(400px, 90vw); padding: 10px 14px; background: #1e293b; border: 1px solid #64748b; border-radius: 8px; }
</style>
