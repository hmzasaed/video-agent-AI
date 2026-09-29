/* ══════════════════════════════════════════════════════════════
   AI Video Assistant — frontend controller.
   Starts an analysis job, polls it for progress, renders the results,
   and answers questions about the video from its Q&A index.
   ══════════════════════════════════════════════════════════════ */

const $ = (id) => document.getElementById(id);

const POLL_INTERVAL_MS = 2000;
const STAGE_ORDER = ["download", "transcribe", "summarize", "extract", "tasks", "index"];
const RECENT_KEY = "recentSources";
const THEME_KEY = "theme";
const MAX_RECENT = 6;

const HTML_ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
};

/* Known failures mapped to a plain-language fix. */
const ERROR_HINTS = [
    [/GOOGLE_API_KEY/i, "Add GOOGLE_API_KEY to your .env file and restart the server."],
    [/rate limit|429/i, "The Gemini free tier is rate limited. Wait a minute, then retry."],
    [/no quota left/i, "Your Gemini API key's quota is used up for this feature. Check usage at ai.dev/rate-limit, wait for the quota to reset, or enable billing."],
    [/overloaded|high demand|did not respond in time/i, "Gemini is busy right now. Wait a moment and ask again."],
    [/SMTP login failed/i, "For Gmail, create an App Password (Google Account → Security → App passwords) and put it in SMTP_PASSWORD."],
    [/Email is not configured/i, "Add SMTP_HOST, SMTP_USER, SMTP_PASSWORD and SMTP_FROM to .env, then restart the server."],
    [/whisper is not installed/i, "Run: pip install -r Requirements.txt"],
    [/ffmpeg|Errno 2.*ffmpeg/i, "FFmpeg is missing from your PATH. Install it, then restart the server."],
    [/no such file/i, "Check the file path — the file was not found on the server."],
    [/private|unavailable|sign in|age.?restricted/i, "yt-dlp could not access this video. It may be private, age-restricted, or region-locked."],
    [/no speech was detected/i, "The audio track appears to be silent or music-only."],
    [/indexing failed|onnx|embedding/i, "The Q&A index could not be built. The embedding model downloads on first use — check your connection and re-run the analysis."],
];

let pollTimer = null;
let elapsedTimer = null;
let startedAt = 0;
let latestJob = null;
let currentJobId = null;
let chatHistory = [];
let askBusy = false;

/* ── Storage (may throw in private mode) ───────────────────────── */
function readStore(key, fallback) {
    try {
        const raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : fallback;
    } catch {
        return fallback;
    }
}

function writeStore(key, value) {
    try {
        localStorage.setItem(key, JSON.stringify(value));
    } catch {
        /* storage may be blocked */
    }
}

/* ── Minimal markdown renderer (headings, bold, italic, code, lists) ── */
function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (char) => HTML_ESCAPES[char]);
}

function renderInline(text) {
    return escapeHtml(text)
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
}

function renderMarkdown(text) {
    if (!text || !text.trim()) {
        return '<p class="empty">Nothing was returned for this section.</p>';
    }

    const html = [];
    let inList = false;

    const closeList = () => {
        if (inList) {
            html.push("</ul>");
            inList = false;
        }
    };

    for (const rawLine of text.split("\n")) {
        const line = rawLine.trim();

        if (!line) {
            closeList();
            continue;
        }

        const heading = line.match(/^(#{1,6})\s+(.*)$/);
        if (heading) {
            closeList();
            const level = Math.min(heading[1].length + 1, 4);
            html.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
            continue;
        }

        const bullet = line.match(/^[-*•]\s+(.*)$/);
        const numbered = line.match(/^\d+[.)]\s+(.*)$/);
        if (bullet || numbered) {
            if (!inList) {
                html.push("<ul>");
                inList = true;
            }
            html.push(`<li>${renderInline((bullet || numbered)[1])}</li>`);
            continue;
        }

        closeList();
        html.push(`<p>${renderInline(line)}</p>`);
    }

    closeList();
    return html.join("");
}

/* Turn citations in rendered answer HTML into superscript markers.
   Handles [1] and [1, 2] (single-video Q&A) and [V1-3], [V1], [W2], [V1-2, W1] (agent). */
const CITE_ID = String.raw`(?:V\d{1,2}(?:-\d{1,3})?|W\d{1,2}|\d{1,2})`;
const CITE_RE = new RegExp(String.raw`\[(${CITE_ID}(?:\s*,\s*${CITE_ID})*)\]`, "g");

function linkCitations(html) {
    return html.replace(CITE_RE, (_, ids) => {
        const clean = ids.replace(/\s+/g, "");
        return `<sup class="cite" data-refs="${clean}">[${clean}]</sup>`;
    });
}

/* ── Small pure helpers shared with meetings.js and agent.js ───── */

/* Text being typed after an "@" in an owner field, lower-cased. */
function mentionQuery(value) {
    return String(value || "").replace(/^\s*@/, "").trim().toLowerCase();
}

/* Contacts matching a mention query: name prefix first, then word, alias, email. */
function filterContacts(contacts, query, limit = 8) {
    const q = mentionQuery(query);
    if (!q) return contacts.slice(0, limit);
    const scored = [];
    for (const contact of contacts) {
        const name = contact.name.toLowerCase();
        const aliases = (contact.aliases || "").toLowerCase().split(",").map((a) => a.trim());
        let score = -1;
        if (name.startsWith(q)) score = 0;
        else if (name.split(/\s+/).some((word) => word.startsWith(q))) score = 1;
        else if (aliases.some((alias) => alias && alias.startsWith(q))) score = 2;
        else if (contact.email.toLowerCase().startsWith(q)) score = 3;
        if (score >= 0) scored.push([score, contact]);
    }
    return scored
        .sort((a, b) => a[0] - b[0] || a[1].name.localeCompare(b[1].name))
        .slice(0, limit)
        .map(([, contact]) => contact);
}

/* Tasks grouped by assigned contact, skipping dismissed ones. Unassigned under null. */
function groupTasksByContact(tasks) {
    const groups = new Map();
    for (const task of tasks) {
        if (task.status === "dismissed") continue;
        const key = task.contact_id || null;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(task);
    }
    return groups;
}

const STATUS_LABELS = {
    proposed: "Proposed",
    approved: "Approved",
    drafted: "Draft ready",
    emailed: "Emailed",
    failed: "Send failed",
    dismissed: "Dismissed",
    done: "Done",
    sent: "Sent",
    "dry-run": "Dry run",
};

function statusLabel(status) {
    return STATUS_LABELS[status] || String(status || "");
}

/* fetch wrapper: JSON in, JSON out, throws Error(message) on failure. */
async function api(path, { method = "GET", body, form } = {}) {
    const options = { method };
    if (form) options.body = form;
    else if (body !== undefined) {
        options.headers = { "Content-Type": "application/json" };
        options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    let data = {};
    try { data = await response.json(); } catch { /* empty body */ }
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
}

/* ── Formatting helpers ────────────────────────────────────────── */
function formatClock(ms) {
    const total = Math.floor(ms / 1000);
    const mm = String(Math.floor(total / 60)).padStart(2, "0");
    const ss = String(total % 60).padStart(2, "0");
    return `${mm}:${ss}`;
}

function formatDuration(seconds) {
    if (!seconds) return "";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    return h
        ? `${h}h ${String(m).padStart(2, "0")}m`
        : `${m}m ${String(s).padStart(2, "0")}s`;
}

function countWords(text) {
    return (text || "").trim().split(/\s+/).filter(Boolean).length;
}

/* ── Toast ─────────────────────────────────────────────────────── */
let toastTimer = null;

function toast(message) {
    const el = $("toast");
    el.textContent = message;
    el.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("visible"), 2200);
}

/* ── Errors ────────────────────────────────────────────────────── */
function hintFor(message) {
    const match = ERROR_HINTS.find(([pattern]) => pattern.test(message));
    return match ? match[1] : "";
}

function showError(message) {
    const hint = hintFor(message);
    $("errorMessage").innerHTML =
        `<div class="alert-title">Analysis failed</div>` +
        `<div>${escapeHtml(message)}</div>` +
        (hint
            ? `<div class="alert-fix"><svg class="icon" aria-hidden="true"><use href="#i-bulb"/></svg>` +
              `<span>${escapeHtml(hint)}</span></div>`
            : "");
    $("errorMessage").classList.add("visible");
}

function clearError() {
    $("errorMessage").classList.remove("visible");
}

/* ── Recent sources ────────────────────────────────────────────── */
function rememberSource(source) {
    const recent = [source, ...readStore(RECENT_KEY, []).filter((s) => s !== source)]
        .slice(0, MAX_RECENT);
    writeStore(RECENT_KEY, recent);
    paintRecent();
}

function paintRecent() {
    const recent = readStore(RECENT_KEY, []);
    const wrap = $("recent");
    const list = $("recentList");

    if (!recent.length) {
        wrap.style.display = "none";
        return;
    }

    list.innerHTML = "";
    for (const source of recent) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "recent-item";
        btn.textContent = source.replace(/^https?:\/\/(www\.)?/, "");
        btn.title = source;
        btn.addEventListener("click", () => {
            $("videoUrl").value = source;
            startAnalysis();
        });
        list.appendChild(btn);
    }
    wrap.style.display = "block";
}

/* ── Progress UI ───────────────────────────────────────────────── */
function paintStages(currentStage) {
    const currentIndex = STAGE_ORDER.indexOf(currentStage);
    STAGE_ORDER.forEach((stage, index) => {
        const el = $(`step-${stage}`);
        if (!el) return;
        el.classList.toggle("active", index === currentIndex);
        el.classList.toggle(
            "done",
            currentStage === "done" || (currentIndex > -1 && index < currentIndex)
        );
    });
}

function paintMetadata(meta) {
    const box = $("videoMeta");
    if (!meta || !meta.title) {
        box.classList.remove("visible");
        return;
    }

    const thumb = $("metaThumb");
    if (meta.thumbnail) {
        thumb.src = meta.thumbnail;
        thumb.alt = `Thumbnail for ${meta.title}`;
        thumb.style.display = "block";
    } else {
        thumb.style.display = "none";
    }

    $("metaTitle").textContent = meta.title;
    $("metaSub").textContent = [meta.uploader, formatDuration(meta.duration)]
        .filter(Boolean)
        .join(" · ");
    box.classList.add("visible");
}

function stopTimers() {
    clearTimeout(pollTimer);
    clearInterval(elapsedTimer);
    pollTimer = null;
    elapsedTimer = null;
}

function setBusy(busy) {
    $("analyzeBtn").disabled = busy || uploading;
    $("analyzeBtn").textContent = busy ? "Analyzing..." : "Analyze";
    $("videoUrl").disabled = busy || Boolean(uploaded);
    $("uploadBtn").disabled = busy;
    document.querySelectorAll('input[name="kind"]').forEach((radio) => { radio.disabled = busy; });
}

function looksLikeUrl(value) {
    return /^https?:\/\//i.test(value);
}

/* ── Mode (video / meeting) and uploads ────────────────────────── */
let uploaded = null;   // {path, name} of a recording saved on the server
let uploading = false;

function currentKind() {
    return $("modeMeeting").checked ? "meeting" : "video";
}

function paintMode() {
    const meeting = currentKind() === "meeting";
    $("modeHint").innerHTML = meeting
        ? "Names spoken in the meeting are matched to your contacts, so you can email everyone their tasks."
        : "Drop a file here, or paste a link. <kbd>Ctrl</kbd>+<kbd>K</kbd> focuses this box.";
}

/* Meetings have an extra Tasks step; renumber the stepper to match. */
function paintStepper(kind) {
    const meeting = kind === "meeting";
    $("step-tasks").hidden = !meeting;
    $("step-index").querySelector(".step-dot").textContent = meeting ? "6" : "5";
    $("stepList").style.gridTemplateColumns = `repeat(${meeting ? 6 : 5}, 1fr)`;
}

function clearUpload() {
    uploaded = null;
    $("uploadChip").hidden = true;
    $("videoUrl").value = "";
    $("videoUrl").disabled = false;
    $("fileInput").value = "";
}

function uploadFile(file) {
    if (!file) return;
    clearError();
    uploaded = null;
    uploading = true;
    $("uploadChip").hidden = false;
    $("uploadName").textContent = file.name;
    $("uploadStatus").textContent = "Uploading 0%";
    $("analyzeBtn").disabled = true;

    // XMLHttpRequest rather than fetch, for upload progress.
    const request = new XMLHttpRequest();
    const form = new FormData();
    form.append("file", file);
    request.open("POST", "/api/upload");
    request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
            $("uploadStatus").textContent = `Uploading ${Math.round((event.loaded / event.total) * 100)}%`;
        }
    });
    request.addEventListener("load", () => {
        uploading = false;
        let data = {};
        try { data = JSON.parse(request.responseText); } catch { /* not JSON */ }
        if (request.status !== 201) {
            clearUpload();
            showError(data.error || "The upload failed.");
            $("analyzeBtn").disabled = false;
            return;
        }
        uploaded = data;
        $("uploadStatus").textContent = "Ready";
        $("videoUrl").value = data.name;
        $("videoUrl").disabled = true;
        $("analyzeBtn").disabled = false;
        if (!$("modeMeeting").checked && /meeting|sync|standup|call|zoom|teams/i.test(file.name)) {
            toast("Looks like a meeting — switch to Meeting mode to get tasks");
        }
    });
    request.addEventListener("error", () => {
        uploading = false;
        clearUpload();
        showError("The upload failed. Check the server is running.");
        $("analyzeBtn").disabled = false;
    });
    request.send(form);
}

/* ── Job lifecycle ─────────────────────────────────────────────── */
async function startAnalysis() {
    const source = uploaded ? uploaded.path : $("videoUrl").value.trim();
    const kind = currentKind();

    if (uploading) return;
    if (!source) {
        showError("Paste a YouTube URL, drop a recording, or enter a local file path first.");
        $("videoUrl").focus();
        return;
    }

    clearError();
    $("results").classList.remove("visible");
    $("progressCard").style.display = "block";
    $("progressFill").style.width = "2%";
    $("progressMessage").textContent = "Starting...";
    $("percentLabel").textContent = "0%";
    paintMetadata(null);
    paintStepper(kind);
    paintStages("download");
    setBusy(true);

    startedAt = Date.now();
    $("elapsed").textContent = "00:00";
    elapsedTimer = setInterval(() => {
        $("elapsed").textContent = formatClock(Date.now() - startedAt);
    }, 1000);

    try {
        const response = await fetch("/api/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: source, kind, title: uploaded ? uploaded.name : "" }),
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || "Could not start the analysis.");
        }

        currentJobId = data.job_id;
        // Uploaded files live on the server under random names; don't list them as recent.
        if (!uploaded) rememberSource(source);
        pollJob(currentJobId);
    } catch (error) {
        failWith(error.message);
    }
}

async function pollJob(jobId) {
    try {
        const response = await fetch(`/api/jobs/${jobId}`);
        const job = await response.json();

        if (!response.ok) {
            throw new Error(job.error || "Lost track of the analysis job.");
        }

        latestJob = job;
        const percent = job.percent || 0;
        $("progressMessage").textContent = job.message || "Working...";
        $("progressFill").style.width = `${percent}%`;
        $("percentLabel").textContent = `${percent}%`;
        $("progressFill").parentElement.setAttribute("aria-valuenow", String(percent));
        paintStages(job.stage);
        paintMetadata(job.metadata);

        if (job.status === "running") {
            pollTimer = setTimeout(() => pollJob(jobId), POLL_INTERVAL_MS);
            return;
        }

        if (job.status === "cancelled") {
            stopTimers();
            setBusy(false);
            $("progressCard").style.display = "none";
            toast("Analysis cancelled");
            return;
        }

        if (job.status === "error") {
            failWith(job.error || "The analysis failed.");
            return;
        }

        renderResults(job);
    } catch (error) {
        failWith(error.message);
    }
}

async function cancelAnalysis() {
    if (!currentJobId) return;
    $("cancelBtn").disabled = true;
    try {
        await fetch(`/api/jobs/${currentJobId}/cancel`, { method: "POST" });
        $("progressMessage").textContent = "Cancelling after the current step...";
    } catch {
        toast("Could not reach the server to cancel");
    } finally {
        $("cancelBtn").disabled = false;
    }
}

function failWith(message) {
    stopTimers();
    $("progressCard").style.display = "none";
    setBusy(false);
    showError(message);
}

function renderResults(job, { quiet = false } = {}) {
    stopTimers();
    currentJobId = job.id;
    latestJob = job;
    if (uploaded) clearUpload();
    setBusy(false);
    $("progressCard").style.display = "none";

    // Meeting-only tabs: minutes, and the task board (rendered by meetings.js).
    const meeting = job.kind === "meeting";
    document.querySelector('.tab[data-tab="minutes"]').hidden = !meeting;
    document.querySelector('.tab[data-tab="tasks"]').hidden = !meeting;
    $("panel-minutes").innerHTML = meeting ? renderMarkdown(job.minutes) : "";
    $("panel-tasks").innerHTML = "";
    if (meeting && typeof renderTaskBoard === "function") {
        renderTaskBoard($("panel-tasks"), job.id);
    }

    $("panel-summary").innerHTML = renderMarkdown(job.summary);
    $("panel-actions").innerHTML = renderMarkdown(job.action_items);
    $("panel-decisions").innerHTML = renderMarkdown(job.decisions);
    $("panel-questions").innerHTML = renderMarkdown(job.questions);

    $("transcriptText").textContent = job.transcript || "";
    $("transcriptSearch").value = "";
    $("searchCount").textContent = "";

    const summaryWords = countWords(job.summary);
    const transcriptWords = countWords(job.transcript);
    const took = job.started_at && job.finished_at
        ? Math.round((new Date(job.finished_at) - new Date(job.started_at)) / 1000)
        : 0;

    $("panelStats").textContent =
        `${summaryWords.toLocaleString()} words of summary · ` +
        `${transcriptWords.toLocaleString()} words transcribed` +
        (took ? ` · analyzed in ${formatClock(took * 1000)}` : "");

    resetChat(job);
    selectTab(meeting ? "tasks" : "summary");
    $("results").classList.add("visible");
    $("results").scrollIntoView({ behavior: "smooth", block: "start" });
    if (!quiet) toast(meeting ? "Meeting analyzed — review the tasks" : "Analysis complete");
    document.dispatchEvent(new CustomEvent("analysis:done", { detail: job }));
}

/* Load a stored analysis into the Analyze tab (from history or a workspace). */
async function openAnalysis(analysisId) {
    try {
        const job = await api(`/api/analyses/${analysisId}`);
        selectAppTab("analyze");
        renderResults(job, { quiet: true });
    } catch (error) {
        toast(error.message);
    }
}

/* ── App-level tools: Analyze · Compare & Ask · Meetings ───────── */
function selectAppTab(name, { focus = false } = {}) {
    document.querySelectorAll(".app-tab").forEach((tab) => {
        const selected = tab.dataset.app === name;
        tab.setAttribute("aria-selected", String(selected));
        tab.tabIndex = selected ? 0 : -1;
        if (selected && focus) tab.focus();
    });
    document.querySelectorAll(".app-panel").forEach((panel) => {
        panel.hidden = panel.id !== `app-${name}`;
    });
    document.dispatchEvent(new CustomEvent("apptab:change", { detail: name }));
}

function moveAppTab(offset) {
    const tabs = [...document.querySelectorAll(".app-tab")];
    const current = tabs.findIndex((t) => t.getAttribute("aria-selected") === "true");
    selectAppTab(tabs[(current + offset + tabs.length) % tabs.length].dataset.app, { focus: true });
}

/* ── Ask (Q&A over the video) ──────────────────────────────────── */
const SOURCE_LABELS = {
    summary: "Summary",
    minutes: "Minutes",
    action_items: "Action items",
    decisions: "Decisions",
    questions: "Questions",
    transcript: "Transcript",
};

function resetChat(job) {
    chatHistory = [];
    $("chatLog").innerHTML = "";
    $("askSuggestions").style.display = "";

    const ready = Boolean(job.qa_ready);
    const notice = $("askNotice");
    notice.textContent = ready
        ? ""
        : `Q&A is unavailable for this analysis. ${job.qa_error || ""}`.trim();
    notice.classList.toggle("visible", !ready);
    setAskBusy(false, ready);
}

function setAskBusy(busy, enabled = true) {
    askBusy = busy;
    const disabled = busy || !enabled;
    $("askInput").disabled = disabled;
    $("askBtn").disabled = disabled;
    $("askBtn").textContent = busy ? "Thinking..." : "Ask";
    document.querySelectorAll(".suggestion").forEach((btn) => { btn.disabled = disabled; });
}

function appendBubble(role, { html = "", text = "", extraClass = "" } = {}) {
    const bubble = document.createElement("div");
    bubble.className = `bubble ${role} ${extraClass}`.trim();
    if (html) bubble.innerHTML = html;
    else bubble.textContent = text;
    $("chatLog").appendChild(bubble);
    bubble.scrollIntoView({ block: "nearest", behavior: "smooth" });
    return bubble;
}

function renderSources(sources) {
    const details = document.createElement("details");
    details.className = "sources";

    const summary = document.createElement("summary");
    summary.textContent = `${sources.length} source${sources.length === 1 ? "" : "s"}`;
    details.appendChild(summary);

    const list = document.createElement("ol");
    for (const source of sources) {
        const item = document.createElement("li");
        item.value = source.id;
        const tag = document.createElement("span");
        tag.className = "source-tag";
        tag.textContent = SOURCE_LABELS[source.section] || source.section;
        item.append(tag, source.text);
        list.appendChild(item);
    }
    details.appendChild(list);
    return details;
}

async function askQuestion(question) {
    question = (question || "").trim();
    if (!question || askBusy || !currentJobId) return;

    $("askInput").value = "";
    $("askSuggestions").style.display = "none";
    appendBubble("user", { text: question });
    const pending = appendBubble("assistant", { text: "Searching the video...", extraClass: "pending" });
    setAskBusy(true);

    try {
        const response = await fetch(`/api/jobs/${currentJobId}/ask`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question, history: chatHistory }),
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || "Could not answer that question.");
        }

        pending.className = "bubble assistant";
        pending.innerHTML = linkCitations(renderMarkdown(data.answer));
        if (data.sources && data.sources.length) {
            pending.appendChild(renderSources(data.sources));
        }

        chatHistory.push(
            { role: "user", content: question },
            { role: "assistant", content: data.answer },
        );
    } catch (error) {
        const hint = hintFor(error.message);
        pending.className = "bubble assistant error";
        pending.textContent = error.message + (hint ? ` — ${hint}` : "");
    } finally {
        setAskBusy(false);
        $("askInput").focus();
    }
}

/* ── Tabs (ARIA + arrow-key navigation) ────────────────────────── */
function selectTab(name) {
    document.querySelectorAll(".tab").forEach((tab) => {
        const selected = tab.dataset.tab === name;
        tab.setAttribute("aria-selected", String(selected));
        tab.tabIndex = selected ? 0 : -1;
    });
    document.querySelectorAll(".panel").forEach((panel) => {
        panel.classList.toggle("active", panel.id === `panel-${name}`);
    });
}

function moveTab(offset) {
    const tabs = [...document.querySelectorAll(".tab:not([hidden])")];
    const current = tabs.findIndex((t) => t.getAttribute("aria-selected") === "true");
    const next = tabs[(current + offset + tabs.length) % tabs.length];
    selectTab(next.dataset.tab);
    next.focus();
}

/* ── Transcript search ─────────────────────────────────────────── */
function searchTranscript() {
    const term = $("transcriptSearch").value.trim();
    const text = latestJob ? latestJob.transcript || "" : "";
    const box = $("transcriptText");

    if (!term) {
        box.textContent = text;
        $("searchCount").textContent = "";
        return;
    }

    // Highlight against the escaped text, so the pattern is escaped the same way
    // and the count always matches what is actually marked up.
    const escapedText = escapeHtml(text);
    const pattern = new RegExp(
        escapeHtml(term).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
        "gi"
    );

    let hits = 0;
    box.innerHTML = escapedText.replace(pattern, (hit) => {
        hits += 1;
        return `<mark>${hit}</mark>`;
    });

    $("searchCount").textContent = hits
        ? `${hits} match${hits === 1 ? "" : "es"}`
        : "no matches";

    const first = box.querySelector("mark");
    if (first) first.scrollIntoView({ block: "center" });
}

/* ── Copy / export ─────────────────────────────────────────────── */
async function copyActivePanel() {
    const panel = document.querySelector(".panel.active");
    if (!panel) return;

    try {
        await navigator.clipboard.writeText(panel.innerText.trim());
        toast("Copied to clipboard");
    } catch {
        toast("Clipboard blocked — select and copy manually");
    }
}

function buildReport(format) {
    if (!latestJob) return "";

    const meta = latestJob.metadata || {};
    const sections = [["Summary", latestJob.summary]];
    if (latestJob.kind === "meeting") {
        sections.push(["Meeting Minutes", latestJob.minutes]);
        const tasks = (latestJob.tasks || []).filter((t) => t.status !== "dismissed");
        sections.push(["Assigned Tasks", tasks.map((t) =>
            `- ${t.text} — ${t.contact_name || t.owner_name || "Unassigned"}` +
            (t.due ? ` (due ${t.due})` : "")).join("\n")]);
    }
    sections.push(
        ["Action Items", latestJob.action_items],
        ["Decisions", latestJob.decisions],
        ["Questions", latestJob.questions],
        ["Full Transcript", latestJob.transcript],
    );

    if (chatHistory.length) {
        const qa = [];
        for (let i = 0; i + 1 < chatHistory.length; i += 2) {
            qa.push(`Q: ${chatHistory[i].content}`, `A: ${chatHistory[i + 1].content}`, "");
        }
        sections.push(["Q&A", qa.join("\n").trim()]);
    }

    if (format === "md") {
        return [
            `# ${meta.title || "Video Analysis"}`,
            "",
            `- **Source:** ${latestJob.source}`,
            meta.uploader ? `- **Channel:** ${meta.uploader}` : "",
            meta.duration ? `- **Duration:** ${formatDuration(meta.duration)}` : "",
            `- **Generated:** ${latestJob.finished_at || ""}`,
            "",
            ...sections.flatMap(([title, body]) => [`## ${title}`, "", body || "_None_", ""]),
        ].filter((line) => line !== "").join("\n");
    }

    return [
        "AI VIDEO ASSISTANT REPORT",
        `Title:     ${meta.title || "(unknown)"}`,
        `Source:    ${latestJob.source}`,
        `Generated: ${latestJob.finished_at || ""}`,
        "",
        ...sections.flatMap(([title, body]) => [
            `=== ${title.toUpperCase()} ===`,
            body || "None",
            "",
        ]),
    ].join("\n");
}

function downloadReport(format) {
    if (!latestJob) return;

    const blob = new Blob([buildReport(format)], { type: "text/plain;charset=utf-8" });
    const href = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const slug = (latestJob.metadata && latestJob.metadata.title
        ? latestJob.metadata.title
        : "video-analysis")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "")
        .slice(0, 60) || "video-analysis";

    link.href = href;
    link.download = `${slug}.${format}`;
    link.click();
    URL.revokeObjectURL(href);
    toast(`Downloaded .${format}`);
}

/* ── Theme ─────────────────────────────────────────────────────── */
function applyTheme(theme) {
    const toggle = $("themeToggle");
    document.documentElement.dataset.theme = theme;
    toggle.querySelector("use").setAttribute("href", theme === "light" ? "#i-moon" : "#i-sun");
    toggle.setAttribute("aria-label", `Switch to ${theme === "light" ? "dark" : "light"} theme`);
}

function toggleTheme() {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    applyTheme(next);
    writeStore(THEME_KEY, next);
}

/* ── Wiring ────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
    // The inline script in <head> already resolved saved-or-system theme.
    applyTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark");
    paintRecent();

    $("analyzeBtn").addEventListener("click", startAnalysis);
    $("cancelBtn").addEventListener("click", cancelAnalysis);
    $("copyBtn").addEventListener("click", copyActivePanel);
    $("downloadTxtBtn").addEventListener("click", () => downloadReport("txt"));
    $("downloadMdBtn").addEventListener("click", () => downloadReport("md"));
    $("themeToggle").addEventListener("click", toggleTheme);

    $("clearRecent").addEventListener("click", () => {
        writeStore(RECENT_KEY, []);
        paintRecent();
        toast("Recent list cleared");
    });

    $("videoUrl").addEventListener("keydown", (event) => {
        if (event.key === "Enter") startAnalysis();
    });

    $("videoUrl").addEventListener("input", (event) => {
        const value = event.target.value.trim();
        event.target.classList.toggle(
            "invalid",
            value.length > 0 && !looksLikeUrl(value) && !/[\\/.]/.test(value)
        );
    });

    $("transcriptSearch").addEventListener("input", searchTranscript);

    $("askForm").addEventListener("submit", (event) => {
        event.preventDefault();
        askQuestion($("askInput").value);
    });

    document.querySelectorAll(".suggestion").forEach((btn) => {
        btn.addEventListener("click", () => askQuestion(btn.textContent));
    });

    document.querySelectorAll(".tab").forEach((tab) => {
        tab.addEventListener("click", () => selectTab(tab.dataset.tab));
        tab.addEventListener("keydown", (event) => {
            if (event.key === "ArrowRight") { event.preventDefault(); moveTab(1); }
            if (event.key === "ArrowLeft") { event.preventDefault(); moveTab(-1); }
        });
    });

    // Ctrl/Cmd+K focuses the input from anywhere on the page.
    document.addEventListener("keydown", (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
            event.preventDefault();
            selectAppTab("analyze");
            $("videoUrl").focus();
            $("videoUrl").select();
        }
    });

    // Tools switcher.
    document.querySelectorAll(".app-tab").forEach((tab) => {
        tab.addEventListener("click", () => selectAppTab(tab.dataset.app));
        tab.addEventListener("keydown", (event) => {
            if (event.key === "ArrowRight") { event.preventDefault(); moveAppTab(1); }
            if (event.key === "ArrowLeft") { event.preventDefault(); moveAppTab(-1); }
        });
    });

    // Video / meeting mode.
    document.querySelectorAll('input[name="kind"]').forEach((radio) => {
        radio.addEventListener("change", paintMode);
    });
    paintMode();

    // Uploads: button, file picker, and drag-and-drop onto the input card.
    $("uploadBtn").addEventListener("click", () => $("fileInput").click());
    $("fileInput").addEventListener("change", (event) => uploadFile(event.target.files[0]));
    $("uploadClear").addEventListener("click", () => { clearUpload(); $("videoUrl").focus(); });

    const card = $("inputCard");
    let dragDepth = 0;
    card.addEventListener("dragenter", (event) => {
        if (![...(event.dataTransfer?.types || [])].includes("Files")) return;
        event.preventDefault();
        dragDepth += 1;
        card.classList.add("dragging");
    });
    card.addEventListener("dragover", (event) => event.preventDefault());
    card.addEventListener("dragleave", () => {
        dragDepth = Math.max(0, dragDepth - 1);
        if (!dragDepth) card.classList.remove("dragging");
    });
    card.addEventListener("drop", (event) => {
        event.preventDefault();
        dragDepth = 0;
        card.classList.remove("dragging");
        const file = event.dataTransfer?.files?.[0];
        if (file && !$("analyzeBtn").disabled) uploadFile(file);
    });

    // "Compare" on a result opens the workspace builder with it pre-selected.
    $("addToWorkspaceBtn").addEventListener("click", () => {
        if (currentJobId && typeof openCompareWith === "function") openCompareWith(currentJobId);
    });
});
