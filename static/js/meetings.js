/* ══════════════════════════════════════════════════════════════
   AI Video Assistant — meetings: task board, @mentions, contacts, email.
   Depends on helpers from main.js ($, api, toast, escapeHtml,
   filterContacts, mentionQuery, statusLabel, openAnalysis, selectAppTab).
   Every email is sent only after the user confirms it in #sendDialog.
   ══════════════════════════════════════════════════════════════ */

/* Tiny DOM builder: el("div", {class: "x", text: "hi"}, [children]). */
function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (value === undefined || value === null || value === false) continue;
        if (key === "text") node.textContent = value;
        else if (key === "class") node.className = value;
        else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of [].concat(children)) {
        if (child) node.append(child);
    }
    return node;
}

function icon(name, extra = "") {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", `icon ${extra}`.trim());
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
}

let contactsCache = [];
let healthCache = null;

async function loadContacts() {
    contactsCache = (await api("/api/contacts")).contacts;
    return contactsCache;
}

async function loadHealth() {
    if (!healthCache) healthCache = await api("/api/health");
    return healthCache;
}

/* ── Send confirmation dialog ──────────────────────────────────── */
function confirmSend(emails, dryRun) {
    const dialog = $("sendDialog");
    const count = emails.length;
    $("sendDialogText").textContent = count === 1
        ? "This email will be sent to:"
        : `These ${count} emails will be sent to:`;
    const list = $("sendDialogList");
    list.innerHTML = "";
    for (const email of emails) {
        list.append(el("li", {}, [
            el("strong", { text: email.contact_name || email.to_addr }),
            el("span", { text: ` <${email.to_addr}> — ${email.subject}` }),
        ]));
    }
    $("sendDialogMode").textContent = dryRun
        ? "Dry-run mode is on: the emails will be logged on the server, not delivered."
        : "They will be delivered from your configured SMTP account.";
    $("sendDialogConfirm").lastChild.textContent = count === 1 ? "Send" : `Send ${count}`;

    return new Promise((resolve) => {
        dialog.returnValue = "cancel";
        dialog.addEventListener("close", () => resolve(dialog.returnValue === "send"), { once: true });
        dialog.showModal();
    });
}

async function sendEmails(emails, board) {
    const health = await loadHealth();
    if (!emails.length || !(await confirmSend(emails, health.email_dry_run))) return;

    let sent = 0;
    let dry = 0;
    const failures = [];
    for (const email of emails) {
        try {
            const result = await api(`/api/emails/${email.id}/send`, { method: "POST", body: { confirm: true } });
            if (result.status === "sent") sent += 1;
            else dry += 1;
        } catch (error) {
            failures.push(`${email.contact_name || email.to_addr}: ${error.message}`);
        }
    }
    if (sent) toast(`Sent ${sent} email${sent === 1 ? "" : "s"}`);
    else if (dry) toast(`Dry run: ${dry} email${dry === 1 ? "" : "s"} logged, not sent`);
    if (failures.length) toast(`Could not send: ${failures[0]}`);
    board.reload();
}

/* ── Owner picker (@mention combobox) ──────────────────────────── */
function ownerPicker(task, board) {
    const listId = `owner-list-${task.id}`;
    const inputId = `owner-${task.id}`;
    const display = task.contact_name ? `@${task.contact_name}` : "";
    const input = el("input", {
        id: inputId, class: "owner-input", type: "text", role: "combobox",
        "aria-autocomplete": "list", "aria-expanded": "false", "aria-controls": listId,
        placeholder: task.owner_name ? `@${task.owner_name}?` : "@assign", autocomplete: "off",
        value: display,
    });
    const list = el("ul", { id: listId, class: "listbox", role: "listbox", hidden: true,
                            "aria-label": "Contacts" });
    let options = [];
    let active = -1;

    function paint() {
        list.innerHTML = "";
        options = filterContacts(contactsCache, input.value);
        options.forEach((contact, index) => {
            list.append(el("li", {
                id: `${listId}-${index}`, role: "option", class: "listbox-option",
                "aria-selected": String(index === active),
                onmousedown: (event) => { event.preventDefault(); choose(contact); },
            }, [
                el("span", { class: "avatar", text: contact.name.slice(0, 1).toUpperCase(), "aria-hidden": "true" }),
                el("span", { class: "option-text" }, [
                    el("strong", { text: contact.name }),
                    el("small", { text: contact.email }),
                ]),
            ]));
        });
        if (task.contact_id) {
            list.append(el("li", {
                role: "option", class: "listbox-option listbox-muted", id: `${listId}-none`,
                "aria-selected": String(active === options.length),
                onmousedown: (event) => { event.preventDefault(); choose(null); },
                text: "Unassign",
            }));
        }
        if (!options.length && !task.contact_id) {
            list.append(el("li", { class: "listbox-empty", text: contactsCache.length
                ? "No matching contacts" : "No contacts yet — add one in Meetings & tasks" }));
        }
        const activeNode = list.querySelector('[aria-selected="true"]');
        input.setAttribute("aria-activedescendant", activeNode ? activeNode.id : "");
        if (activeNode) activeNode.scrollIntoView({ block: "nearest" });
    }

    function open() {
        active = -1;
        list.hidden = false;
        input.setAttribute("aria-expanded", "true");
        paint();
    }

    function close(restore = true) {
        list.hidden = true;
        input.setAttribute("aria-expanded", "false");
        input.removeAttribute("aria-activedescendant");
        if (restore) input.value = display;
    }

    async function choose(contact) {
        close(false);
        try {
            await api(`/api/tasks/${task.id}`, {
                method: "PATCH",
                body: contact ? { contact_id: contact.id, owner_name: contact.name } : { contact_id: null },
            });
            toast(contact ? `Assigned to ${contact.name}` : "Owner removed");
            board.reload({ focus: `owner-${task.id}` });
        } catch (error) {
            toast(error.message);
            input.value = display;
        }
    }

    input.addEventListener("focus", () => { input.select(); open(); });
    input.addEventListener("input", () => { active = 0; list.hidden = false; paint(); });
    input.addEventListener("blur", () => close());
    input.addEventListener("keydown", (event) => {
        const count = options.length + (task.contact_id ? 1 : 0);
        if (event.key === "ArrowDown") {
            event.preventDefault();
            if (list.hidden) open();
            active = Math.min(active + 1, count - 1);
            paint();
        } else if (event.key === "ArrowUp") {
            event.preventDefault();
            active = Math.max(active - 1, 0);
            paint();
        } else if (event.key === "Enter") {
            event.preventDefault();
            if (active >= 0 && active < options.length) choose(options[active]);
            else if (active === options.length && task.contact_id) choose(null);
            else if (options.length === 1) choose(options[0]);
        } else if (event.key === "Escape") {
            if (!list.hidden) { event.preventDefault(); close(); }
        }
    });

    return el("div", { class: "owner-picker" }, [
        el("label", { class: "visually-hidden", for: inputId, text: `Owner of: ${task.text}` }),
        icon("at", "owner-at"),
        input,
        list,
    ]);
}

/* Inline "add this person as a contact" for names that didn't match. */
function quickAddContact(task, board) {
    const wrap = el("div", { class: "quick-add", hidden: true });
    const emailId = `quick-email-${task.id}`;
    const email = el("input", { id: emailId, class: "text-input text-input-sm", type: "email",
                                placeholder: `${task.owner_name.split(" ")[0].toLowerCase()}@company.com` });
    const save = el("button", { class: "btn-primary btn-sm", type: "button", text: "Save" });
    const toggle = el("button", {
        class: "btn-text", type: "button", "aria-expanded": "false",
        onclick: () => {
            wrap.hidden = !wrap.hidden;
            toggle.setAttribute("aria-expanded", String(!wrap.hidden));
            if (!wrap.hidden) email.focus();
        },
    }, [icon("user-plus"), `Add ${task.owner_name} as a contact`]);

    async function submit() {
        try {
            const contact = await api("/api/contacts", {
                method: "POST", body: { name: task.owner_name, email: email.value.trim() },
            });
            await api(`/api/tasks/${task.id}`, { method: "PATCH", body: { contact_id: contact.id } });
            toast(`${contact.name} added and assigned`);
            await loadContacts();
            document.dispatchEvent(new CustomEvent("contacts:change"));
            board.reload();
        } catch (error) {
            toast(error.message);
        }
    }
    save.addEventListener("click", submit);
    email.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); submit(); } });
    wrap.append(el("label", { class: "visually-hidden", for: emailId, text: `Email for ${task.owner_name}` }), email, save);
    return el("div", { class: "quick-add-wrap" }, [toggle, wrap]);
}

/* ── Task board ────────────────────────────────────────────────── */
function taskRow(task, board) {
    const dismissed = task.status === "dismissed";
    const text = el("textarea", {
        class: "task-text", rows: "1", "aria-label": "Task", disabled: dismissed,
    });
    text.value = task.text;
    // Grow with the content so long tasks wrap instead of being cut off.
    const fit = () => { text.style.height = "auto"; text.style.height = `${text.scrollHeight}px`; };
    text.addEventListener("input", fit);
    requestAnimationFrame(fit);
    text.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); text.blur(); }
    });
    text.addEventListener("change", async () => {
        try {
            await api(`/api/tasks/${task.id}`, { method: "PATCH", body: { text: text.value } });
            toast("Task updated");
            board.reload();
        } catch (error) { toast(error.message); text.value = task.text; }
    });

    const due = el("input", {
        class: "task-due", type: "text", value: task.due || "", placeholder: "No deadline",
        "aria-label": `Due date for: ${task.text}`, disabled: dismissed,
    });
    due.addEventListener("change", async () => {
        try {
            await api(`/api/tasks/${task.id}`, { method: "PATCH", body: { due: due.value } });
            board.reload();
        } catch (error) { toast(error.message); }
    });

    let ownerState;
    if (task.contact_id) {
        ownerState = el("span", { class: "owner-state matched" }, [icon("check"), task.contact_email]);
    } else if (task.owner_name) {
        ownerState = el("span", { class: "owner-state unmatched" },
            [icon("alert"), `"${task.owner_name}" isn't in your contacts`]);
    } else {
        ownerState = el("span", { class: "owner-state unmatched" }, [icon("alert"), "Nobody was assigned"]);
    }

    const toggleDismiss = el("button", {
        class: "btn-icon btn-icon-sm", type: "button",
        "aria-label": dismissed ? `Restore task: ${task.text}` : `Dismiss task: ${task.text}`,
        title: dismissed ? "Restore" : "Dismiss",
        onclick: async () => {
            try {
                await api(`/api/tasks/${task.id}`, {
                    method: "PATCH", body: { status: dismissed ? "proposed" : "dismissed" },
                });
                board.reload();
            } catch (error) { toast(error.message); }
        },
    }, [icon(dismissed ? "refresh" : "x")]);

    return el("li", { class: `task-row status-${task.status}` }, [
        el("div", { class: "task-main" }, [
            text,
            task.evidence ? el("blockquote", { class: "task-evidence", text: task.evidence }) : null,
        ]),
        el("div", { class: "task-owner" }, [
            dismissed ? el("span", { class: "muted", text: task.contact_name || task.owner_name || "—" })
                      : ownerPicker(task, board),
            ownerState,
            !task.contact_id && task.owner_name && !dismissed ? quickAddContact(task, board) : null,
        ]),
        el("div", { class: "task-meta" }, [
            due,
            el("span", { class: `badge badge-${task.status}`, text: statusLabel(task.status) }),
            toggleDismiss,
        ]),
    ]);
}

function emailCard(email, board) {
    const editable = ["drafted", "failed", "dry-run"].includes(email.status);
    const subjectId = `email-subject-${email.id}`;
    const bodyId = `email-body-${email.id}`;
    const subject = el("input", { id: subjectId, class: "text-input", type: "text",
                                  value: email.subject, disabled: !editable });
    const body = el("textarea", { id: bodyId, class: "text-input email-body", rows: "8", disabled: !editable });
    body.value = email.body;

    const save = el("button", { class: "btn-ghost", type: "button", hidden: true }, [icon("check"), "Save changes"]);
    const markDirty = () => {
        save.hidden = subject.value === email.subject && body.value === email.body;
    };
    subject.addEventListener("input", markDirty);
    body.addEventListener("input", markDirty);
    save.addEventListener("click", async () => {
        try {
            await api(`/api/emails/${email.id}`, { method: "PATCH", body: { subject: subject.value, body: body.value } });
            toast("Draft saved");
            board.reload();
        } catch (error) { toast(error.message); }
    });

    const send = el("button", { class: "btn-primary", type: "button", hidden: !editable,
        onclick: async () => {
            if (!save.hidden) {
                await api(`/api/emails/${email.id}`, { method: "PATCH", body: { subject: subject.value, body: body.value } });
            }
            sendEmails([{ ...email, subject: subject.value }], board);
        } }, [icon("send"), email.status === "failed" ? "Retry" : "Send"]);

    const details = el("details", { class: "email-card", open: editable || undefined }, [
        el("summary", {}, [
            icon("mail"),
            el("span", { class: "email-to" }, [
                el("strong", { text: email.contact_name || email.to_addr }),
                el("small", { text: email.to_addr }),
            ]),
            el("span", { class: `badge badge-${email.status}`, text: statusLabel(email.status) }),
        ]),
        el("div", { class: "email-fields" }, [
            el("label", { for: subjectId, class: "field-label", text: "Subject" }), subject,
            el("label", { for: bodyId, class: "field-label", text: "Message" }), body,
            email.error ? el("p", { class: "email-error", text: email.error }) : null,
            el("div", { class: "email-actions" }, [save, send]),
        ]),
    ]);
    return details;
}

/* Render the task board for one meeting into `container`. */
function renderTaskBoard(container, analysisId, { focus } = {}) {
    const board = {
        reload: (options = {}) => renderTaskBoard(container, analysisId, options),
    };
    container.setAttribute("aria-busy", "true");

    Promise.all([
        api(`/api/analyses/${analysisId}/tasks`),
        loadContacts(),
        api(`/api/emails?analysis=${encodeURIComponent(analysisId)}`),
        loadHealth(),
    ]).then(([taskData, , emailData, health]) => {
        const tasks = taskData.tasks;
        const emails = emailData.emails;
        const contactById = new Map(contactsCache.map((c) => [c.id, c]));
        emails.forEach((e) => { e.contact_name = contactById.get(e.contact_id)?.name || ""; });
        if (typeof latestJob !== "undefined" && latestJob && latestJob.id === analysisId) {
            latestJob.tasks = tasks;
        }

        const live = tasks.filter((t) => t.status !== "dismissed");
        const assigned = live.filter((t) => t.contact_id).length;
        const drafts = emails.filter((e) => ["drafted", "failed", "dry-run"].includes(e.status));

        const notices = [];
        if (health.email_dry_run) {
            notices.push(el("p", { class: "notice" }, [icon("alert"),
                el("span", {}, ["Dry-run mode: emails are logged on the server, not delivered. Set ",
                    el("code", { text: "EMAIL_DRY_RUN=false" }), " and your SMTP settings in ",
                    el("code", { text: ".env" }), " to send for real."])]));
        } else if (!health.smtp_configured) {
            notices.push(el("p", { class: "notice notice-warn" }, [icon("alert"),
                "Email isn't configured. Add SMTP settings to .env before sending."]));
        }

        const rematch = el("button", { class: "btn-ghost", type: "button",
            onclick: async () => {
                await api(`/api/analyses/${analysisId}/tasks/rematch`, { method: "POST" });
                toast("Owners re-matched to your contacts");
                board.reload();
            } }, [icon("refresh"), "Re-match owners"]);
        const prepare = el("button", { class: "btn-primary", type: "button", disabled: !assigned,
            onclick: async () => {
                try {
                    const result = await api(`/api/analyses/${analysisId}/emails/draft`, { method: "POST", body: {} });
                    const n = result.drafts.length;
                    toast(n ? `Drafted ${n} email${n === 1 ? "" : "s"} — review, then send`
                            : "Nothing to draft — assign owners first");
                    board.reload({ focus: "emails" });
                } catch (error) { toast(error.message); }
            } }, [icon("mail"), "Prepare emails"]);

        const content = [
            ...notices,
            el("div", { class: "board-bar" }, [
                el("p", { class: "board-stats", text:
                    `${live.length} task${live.length === 1 ? "" : "s"} · ${assigned} assigned` +
                    (live.length - assigned ? ` · ${live.length - assigned} need an owner` : "") }),
                el("div", { class: "board-actions" }, [rematch, prepare]),
            ]),
        ];

        if (!tasks.length) {
            content.push(el("p", { class: "empty", text: "No tasks were found in this meeting." }));
        } else {
            content.push(el("ul", { class: "task-list", "aria-label": "Tasks" },
                tasks.map((task) => taskRow(task, board))));
        }

        const emailSection = el("section", { class: "email-section", id: `emails-${analysisId}`,
                                             "aria-label": "Emails", tabindex: "-1" }, [
            el("div", { class: "board-bar" }, [
                el("h4", { class: "card-subtitle" }, [icon("mail"), "Emails"]),
                drafts.length > 1 ? el("button", { class: "btn-primary", type: "button",
                    onclick: () => sendEmails(drafts, board) }, [icon("send"), `Send all ${drafts.length}`]) : null,
            ]),
            emails.length
                ? el("div", { class: "email-list" }, emails.map((email) => emailCard(email, board)))
                : el("p", { class: "hint", text: "Prepare emails to draft one message per person with their tasks. You review every email before it's sent." }),
        ]);
        content.push(emailSection);

        container.replaceChildren(...content);
        container.removeAttribute("aria-busy");
        if (focus === "emails") emailSection.focus();
        else if (focus) document.getElementById(focus)?.focus();
    }).catch((error) => {
        container.replaceChildren(el("p", { class: "alert visible", text: error.message }));
        container.removeAttribute("aria-busy");
    });
}

/* ── Contacts manager (Meetings & tasks sidebar) ───────────────── */
function paintContacts() {
    const list = $("contactList");
    list.innerHTML = "";
    if (!contactsCache.length) {
        list.append(el("li", { class: "hint", text: "No contacts yet." }));
        return;
    }
    for (const contact of contactsCache) {
        let confirming = false;
        const remove = el("button", { class: "btn-icon btn-icon-sm", type: "button",
            "aria-label": `Delete ${contact.name}`, title: "Delete" }, [icon("trash")]);
        remove.addEventListener("click", async () => {
            if (!confirming) {
                confirming = true;
                remove.classList.add("confirming");
                remove.setAttribute("aria-label", `Click again to delete ${contact.name}`);
                setTimeout(() => {
                    confirming = false;
                    remove.classList.remove("confirming");
                    remove.setAttribute("aria-label", `Delete ${contact.name}`);
                }, 3000);
                return;
            }
            try {
                await api(`/api/contacts/${contact.id}`, { method: "DELETE" });
                await refreshContacts();
                toast(`${contact.name} removed`);
            } catch (error) { toast(error.message); }
        });
        const edit = el("button", { class: "btn-icon btn-icon-sm", type: "button",
            "aria-label": `Edit ${contact.name}`, title: "Edit",
            onclick: () => startEditContact(contact) }, [icon("summary")]);

        list.append(el("li", { class: "contact-item" }, [
            el("span", { class: "avatar", text: contact.name.slice(0, 1).toUpperCase(), "aria-hidden": "true" }),
            el("span", { class: "option-text" }, [
                el("strong", { text: contact.name }),
                el("small", { text: contact.email + (contact.aliases ? ` · also "${contact.aliases}"` : "") }),
            ]),
            edit, remove,
        ]));
    }
}

let editingContactId = null;

function startEditContact(contact) {
    editingContactId = contact.id;
    $("contactName").value = contact.name;
    $("contactEmail").value = contact.email;
    $("contactAliases").value = contact.aliases || "";
    $("contactSave").lastChild.textContent = "Save contact";
    $("contactName").focus();
}

function resetContactForm() {
    editingContactId = null;
    $("contactForm").reset();
    $("contactSave").lastChild.textContent = "Add contact";
}

async function refreshContacts() {
    await loadContacts();
    paintContacts();
    document.dispatchEvent(new CustomEvent("contacts:change"));
}

/* ── Meeting list ──────────────────────────────────────────────── */
let selectedMeeting = null;

async function paintMeetingList() {
    const list = $("meetingList");
    let analyses = [];
    try {
        analyses = (await api("/api/analyses")).analyses.filter((a) => a.kind === "meeting");
    } catch (error) {
        list.replaceChildren(el("p", { class: "hint", text: error.message }));
        return;
    }
    if (!analyses.length) {
        list.replaceChildren(el("p", { class: "hint", text: "No meetings analyzed yet." }));
        return;
    }
    list.replaceChildren(...analyses.map((meeting) => el("button", {
        class: "pick-item", type: "button", "aria-pressed": String(meeting.id === selectedMeeting),
        onclick: () => openMeeting(meeting.id),
    }, [
        icon("users"),
        el("span", { class: "option-text" }, [
            el("strong", { text: meeting.title || "Untitled meeting" }),
            el("small", { text: (meeting.finished_at || "").replace("T", " ").slice(0, 16) }),
        ]),
    ])));
}

/* Show one meeting's task board in the Meetings & tasks tab. */
async function openMeeting(analysisId) {
    selectedMeeting = analysisId;
    selectAppTab("meetings");
    try {
        const analysis = await api(`/api/analyses/${analysisId}`);
        $("meetTitle").textContent = analysis.title || "Untitled meeting";
    } catch { /* title is cosmetic */ }
    $("meetEmpty").hidden = true;
    $("meetView").hidden = false;
    renderTaskBoard($("meetTasks"), analysisId);
    paintMeetingList();
}

document.addEventListener("DOMContentLoaded", () => {
    $("contactForm").addEventListener("submit", async (event) => {
        event.preventDefault();
        const body = {
            name: $("contactName").value.trim(),
            email: $("contactEmail").value.trim(),
            aliases: $("contactAliases").value.trim(),
        };
        try {
            if (editingContactId) {
                await api(`/api/contacts/${editingContactId}`, { method: "PUT", body });
                toast(`${body.name} updated`);
            } else {
                await api("/api/contacts", { method: "POST", body });
                toast(`${body.name} added`);
            }
            resetContactForm();
            await refreshContacts();
        } catch (error) {
            toast(error.message);
        }
    });
    $("contactForm").addEventListener("keydown", (event) => {
        if (event.key === "Escape" && editingContactId) resetContactForm();
    });

    $("meetOpen").addEventListener("click", () => { if (selectedMeeting) openAnalysis(selectedMeeting); });

    document.addEventListener("apptab:change", (event) => {
        if (event.detail !== "meetings") return;
        paintMeetingList();
        refreshContacts().catch(() => {});
        if (selectedMeeting) renderTaskBoard($("meetTasks"), selectedMeeting);
    });
    document.addEventListener("analysis:done", (event) => {
        if (event.detail.kind === "meeting") paintMeetingList();
    });
});
