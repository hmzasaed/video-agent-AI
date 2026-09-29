/* ══════════════════════════════════════════════════════════════
   AI Video Assistant — Compare & Ask: workspaces and the research agent.
   Depends on main.js (api, toast, renderMarkdown, linkCitations,
   formatDuration, selectAppTab, openAnalysis) and meetings.js (el, icon,
   openMeeting).
   ══════════════════════════════════════════════════════════════ */

let currentWorkspace = null;
let wsHistory = [];
let wsBusy = false;
const pendingSelection = new Set();

const SECTION_NAMES = {
    summary: "Summary", minutes: "Minutes", action_items: "Action items",
    decisions: "Decisions", questions: "Questions", transcript: "Transcript",
};

/* ── Workspace builder (history list) ──────────────────────────── */
async function paintHistory() {
    const list = $("historyList");
    let analyses = [];
    try {
        analyses = (await api("/api/analyses")).analyses;
    } catch (error) {
        list.replaceChildren(el("p", { class: "hint", text: error.message }));
        return;
    }
    if (!analyses.length) {
        list.replaceChildren(el("p", { class: "hint", text: "Nothing analyzed yet. Analyze a video first." }));
        updateCreateButton();
        return;
    }
    list.replaceChildren(...analyses.map((analysis) => {
        const id = `pick-${analysis.id}`;
        const box = el("input", { type: "checkbox", id, checked: pendingSelection.has(analysis.id) });
        box.addEventListener("change", () => {
            if (box.checked) pendingSelection.add(analysis.id);
            else pendingSelection.delete(analysis.id);
            updateCreateButton();
        });
        return el("label", { class: "pick-item pick-check", for: id }, [
            box,
            icon(analysis.kind === "meeting" ? "users" : "play"),
            el("span", { class: "option-text" }, [
                el("strong", { text: analysis.title || analysis.source }),
                el("small", { text: `${analysis.kind === "meeting" ? "Meeting" : "Video"} · ` +
                    (analysis.finished_at || "").replace("T", " ").slice(0, 16) }),
            ]),
        ]);
    }));
    wsHistory = analyses;
    updateCreateButton();
}

function updateCreateButton() {
    const count = pendingSelection.size;
    $("wsCreate").disabled = count < 1;
    $("wsCreate").lastChild.textContent = count > 1
        ? `Create workspace (${count})`
        : count === 1 ? "Create workspace (add one more to compare)" : "Create workspace";
}

async function createWorkspace() {
    try {
        const workspace = await api("/api/workspaces", {
            method: "POST",
            body: { analysis_ids: [...pendingSelection], name: $("wsName").value.trim() },
        });
        pendingSelection.clear();
        $("wsName").value = "";
        toast("Workspace created");
        await paintWorkspaces();
        openWorkspace(workspace.id);
        paintHistory();
    } catch (error) {
        toast(error.message);
    }
}

/* ── Workspace list ────────────────────────────────────────────── */
async function paintWorkspaces() {
    const list = $("wsList");
    const { workspaces } = await api("/api/workspaces");
    if (!workspaces.length) {
        list.replaceChildren(el("p", { class: "hint", text: "No workspaces yet." }));
        return;
    }
    list.replaceChildren(...workspaces.map((ws) => el("button", {
        class: "pick-item", type: "button",
        "aria-pressed": String(currentWorkspace && currentWorkspace.id === ws.id),
        onclick: () => openWorkspace(ws.id),
    }, [
        icon("layers"),
        el("span", { class: "option-text" }, [
            el("strong", { text: ws.name }),
            el("small", { text: `${ws.video_count} video${ws.video_count === 1 ? "" : "s"}` }),
        ]),
    ])));
}

/* ── Workspace view ────────────────────────────────────────────── */
function videoCard(video, number) {
    const meta = video.metadata || {};
    // Skip the summary's own title line (it repeats the card title), then flatten markdown.
    const body = (video.summary || "").replace(/^\s*📌[^\n]*\n/u, "");
    const summary = body.replace(/\p{Extended_Pictographic}️?/gu, "")
        .replace(/[#*`>]/g, "").replace(/\s+/g, " ").trim();
    return el("article", { class: "video-card" }, [
        el("div", { class: "video-thumb" }, [
            meta.thumbnail
                ? el("img", { src: meta.thumbnail, alt: "", loading: "lazy", width: "160", height: "90" })
                : icon(video.kind === "meeting" ? "users" : "play", "thumb-icon"),
            el("span", { class: "video-ref", text: `V${number}` }),
        ]),
        el("div", { class: "video-body" }, [
            el("h4", { text: video.title || video.source }),
            el("p", { class: "video-meta", text: [
                video.kind === "meeting" ? "Meeting" : "Video",
                meta.uploader, formatDuration(meta.duration),
                video.qa_ready ? "" : "not searchable",
            ].filter(Boolean).join(" · ") }),
            el("p", { class: "video-summary", text: summary.slice(0, 180) + (summary.length > 180 ? "…" : "") }),
            el("div", { class: "video-actions" }, [
                el("button", { class: "btn-text", type: "button", onclick: () => openAnalysis(video.id),
                               text: "Open" }),
                video.kind === "meeting"
                    ? el("button", { class: "btn-text", type: "button", onclick: () => openMeeting(video.id),
                                     text: "Tasks" })
                    : null,
                el("button", { class: "btn-text danger-text", type: "button",
                    "aria-label": `Remove ${video.title || "video"} from workspace`,
                    onclick: () => removeFromWorkspace(video.id), text: "Remove" }),
            ]),
        ]),
    ]);
}

async function openWorkspace(workspaceId) {
    try {
        currentWorkspace = await api(`/api/workspaces/${workspaceId}`);
    } catch (error) {
        toast(error.message);
        return;
    }
    const ws = currentWorkspace;
    $("wsEmpty").hidden = true;
    $("wsView").hidden = false;
    $("wsTitle").textContent = ws.name;
    $("wsVideos").replaceChildren(...ws.videos.map((video, i) => videoCard(video, i + 1)));
    paintComparison(ws.comparison);
    $("wsCompareBtn").disabled = ws.videos.length < 2;
    $("wsChatLog").innerHTML = "";
    $("wsSuggestions").style.display = "";
    wsChat = [];
    paintWorkspaces();
}

function paintComparison(markdown) {
    const box = $("wsComparison");
    if (markdown) {
        box.innerHTML = renderMarkdown(markdown);
    } else {
        box.replaceChildren(el("p", { class: "hint", text: currentWorkspace.videos.length < 2
            ? "Add a second video to compare."
            : "Generate a side-by-side comparison: common ground, differences, and what's unique to each." }));
    }
}

async function runComparison() {
    const button = $("wsCompareBtn");
    button.disabled = true;
    button.lastChild.textContent = "Comparing...";
    $("wsComparison").replaceChildren(el("p", { class: "hint shimmer", text: "Reading the summaries..." }));
    try {
        const { comparison } = await api(`/api/workspaces/${currentWorkspace.id}/compare`, { method: "POST" });
        currentWorkspace.comparison = comparison;
        paintComparison(comparison);
    } catch (error) {
        paintComparison(currentWorkspace.comparison);
        toast(error.message);
    } finally {
        button.disabled = false;
        button.lastChild.textContent = "Compare again";
    }
}

async function removeFromWorkspace(analysisId) {
    try {
        await api(`/api/workspaces/${currentWorkspace.id}/videos/${analysisId}`, { method: "DELETE" });
        openWorkspace(currentWorkspace.id);
    } catch (error) { toast(error.message); }
}

async function deleteWorkspace() {
    const button = $("wsDelete");
    if (button.dataset.confirm !== "1") {
        button.dataset.confirm = "1";
        button.textContent = "Click again to delete";
        setTimeout(() => { button.dataset.confirm = ""; button.textContent = "Delete workspace"; }, 3000);
        return;
    }
    try {
        await api(`/api/workspaces/${currentWorkspace.id}`, { method: "DELETE" });
        currentWorkspace = null;
        $("wsView").hidden = true;
        $("wsEmpty").hidden = false;
        button.dataset.confirm = "";
        button.textContent = "Delete workspace";
        toast("Workspace deleted");
        paintWorkspaces();
    } catch (error) { toast(error.message); }
}

/* Called from the Analyze tab's "Compare" button. */
function openCompareWith(analysisId) {
    pendingSelection.add(analysisId);
    selectAppTab("compare");
    toast("Pick another video to compare with");
    $("wsName").focus();
}

/* ── Agent chat ────────────────────────────────────────────────── */
let wsChat = [];

function renderAgentSources(sources) {
    if (!sources.length) return null;
    const videos = sources.filter((s) => s.type === "video");
    const web = sources.filter((s) => s.type === "web");
    const list = el("ol", { class: "agent-sources" });
    for (const source of [...videos, ...web]) {
        const item = el("li", { id: `src-${source.id}-${wsChat.length}`, "data-ref": source.id }, [
            el("span", { class: `source-tag ${source.type === "web" ? "source-web" : ""}`, text: source.id }),
        ]);
        if (source.type === "web") {
            item.append(icon("globe"), " ", el("a", {
                href: source.url, target: "_blank", rel: "noopener noreferrer", text: source.title || source.url,
            }));
        } else {
            item.append(el("small", { class: "source-section",
                text: `${source.title ? source.title + " · " : ""}${SECTION_NAMES[source.section] || source.section}` }),
            el("span", { class: "source-text", text: source.text }));
        }
        list.append(item);
    }
    const label = [
        videos.length ? `${videos.length} video excerpt${videos.length === 1 ? "" : "s"}` : "",
        web.length ? `${web.length} web source${web.length === 1 ? "" : "s"}` : "",
    ].filter(Boolean).join(" · ");
    return el("details", { class: "sources" }, [el("summary", { text: label }), list]);
}

function renderSteps(steps) {
    if (!steps.length) return null;
    const names = {
        search_videos: "Searched videos", web_search: "Searched the web", get_summary: "Read a summary",
        list_videos: "Listed videos", list_tasks: "Checked tasks", draft_task_emails: "Drafted emails",
    };
    return el("details", { class: "sources steps-trace" }, [
        el("summary", {}, [icon("route"), `How I answered · ${steps.length} step${steps.length === 1 ? "" : "s"}`]),
        el("ol", { class: "trace-list" }, steps.map((step) => el("li", {}, [
            el("strong", { text: names[step.tool] || step.tool }),
            el("span", { text: ` — ${step.summary}` }),
        ]))),
    ]);
}

function renderDraftCard(drafts) {
    if (!drafts.length) return null;
    const meetingId = drafts[0].analysis_id;
    return el("div", { class: "draft-card" }, [
        icon("mail"),
        el("div", {}, [
            el("strong", { text: `${drafts.length} email draft${drafts.length === 1 ? "" : "s"} ready for review` }),
            el("p", { text: `To ${drafts.map((d) => d.contact_name).join(", ")}. Nothing has been sent.` }),
        ]),
        el("button", { class: "btn-primary btn-sm", type: "button",
                       onclick: () => openMeeting(meetingId) }, ["Review & send"]),
    ]);
}

/* Clicking a citation opens the sources list and highlights the source. */
function wireCitations(bubble) {
    bubble.querySelectorAll(".cite").forEach((cite) => {
        cite.tabIndex = 0;
        cite.setAttribute("role", "button");
        cite.setAttribute("aria-label", `Show source ${cite.dataset.refs}`);
        const reveal = () => {
            const details = bubble.querySelector(".sources:not(.steps-trace)");
            if (!details) return;
            details.open = true;
            const first = (cite.dataset.refs || "").split(",")[0];
            const target = details.querySelector(`[data-ref="${CSS.escape(first)}"]`);
            if (target) {
                target.classList.remove("flash");
                void target.offsetWidth;
                target.classList.add("flash");
                target.scrollIntoView({ block: "nearest", behavior: "smooth" });
            }
        };
        cite.addEventListener("click", reveal);
        cite.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") { event.preventDefault(); reveal(); }
        });
    });
}

function setWsBusy(busy) {
    wsBusy = busy;
    $("wsInput").disabled = busy;
    $("wsSend").disabled = busy;
    $("wsSend").textContent = busy ? "Thinking..." : "Ask";
    document.querySelectorAll("#wsSuggestions .chip-btn").forEach((b) => { b.disabled = busy; });
}

async function askAgent(message) {
    message = (message || "").trim();
    if (!message || wsBusy || !currentWorkspace) return;

    const log = $("wsChatLog");
    $("wsInput").value = "";
    $("wsSuggestions").style.display = "none";
    log.append(el("div", { class: "bubble user", text: message }));
    const allowWeb = $("wsWeb").checked;
    const pending = el("div", { class: "bubble assistant pending" }, [
        el("span", { class: "typing", "aria-hidden": "true" }, [el("i"), el("i"), el("i")]),
        allowWeb ? " Searching the videos and the web..." : " Searching the videos...",
    ]);
    log.append(pending);
    pending.scrollIntoView({ block: "nearest", behavior: "smooth" });
    setWsBusy(true);

    try {
        const result = await api(`/api/workspaces/${currentWorkspace.id}/chat`, {
            method: "POST", body: { message, history: wsChat, allow_web: allowWeb },
        });
        pending.className = "bubble assistant";
        pending.innerHTML = linkCitations(renderMarkdown(result.answer));
        for (const extra of [renderDraftCard(result.drafts || []),
                             renderAgentSources(result.sources || []),
                             renderSteps(result.steps || [])]) {
            if (extra) pending.append(extra);
        }
        wireCitations(pending);
        wsChat.push({ role: "user", content: message }, { role: "assistant", content: result.answer });
    } catch (error) {
        const hint = hintFor(error.message);
        pending.className = "bubble assistant error";
        pending.textContent = error.message + (hint ? ` — ${hint}` : "");
    } finally {
        setWsBusy(false);
        $("wsInput").focus();
    }
}

document.addEventListener("DOMContentLoaded", () => {
    $("wsCreate").addEventListener("click", createWorkspace);
    $("wsCompareBtn").addEventListener("click", runComparison);
    $("wsDelete").addEventListener("click", deleteWorkspace);
    $("wsChatForm").addEventListener("submit", (event) => {
        event.preventDefault();
        askAgent($("wsInput").value);
    });
    document.querySelectorAll("#wsSuggestions .chip-btn").forEach((button) => {
        button.addEventListener("click", () => askAgent(button.textContent));
    });

    document.addEventListener("apptab:change", (event) => {
        if (event.detail !== "compare") return;
        paintHistory();
        paintWorkspaces().catch((error) => toast(error.message));
    });
});
