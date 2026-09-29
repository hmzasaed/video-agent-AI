/* Frontend unit tests for the pure helpers in static/js/main.js.
 *
 * main.js has no module system (it is a plain <script>), so this loads the
 * source, strips the DOM-wiring block, appends an export list, and imports the
 * result as a data: module. No browser or test framework needed.
 *
 * Run:  node tests/test_frontend.mjs
 */

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = fs.readFileSync(path.join(root, "static/js/main.js"), "utf8");

const wiringStart = source.indexOf('document.addEventListener("DOMContentLoaded"');
if (wiringStart === -1) {
    console.error("Could not find the DOMContentLoaded block — did main.js change shape?");
    process.exit(1);
}

globalThis.document = { getElementById: () => null, querySelectorAll: () => [] };
globalThis.localStorage = {
    store: {},
    getItem(key) { return this.store[key] ?? null; },
    setItem(key, value) { this.store[key] = value; },
};

const EXPORTS = [
    "renderMarkdown", "escapeHtml", "formatDuration", "formatClock",
    "countWords", "hintFor", "looksLikeUrl", "linkCitations",
    "mentionQuery", "filterContacts", "groupTasksByContact", "statusLabel",
];

const module_ = await import(
    "data:text/javascript;base64," +
    Buffer.from(
        source.slice(0, wiringStart) + `\nexport { ${EXPORTS.join(", ")} };`
    ).toString("base64")
);

let pass = 0;
let fail = 0;

function check(label, ok, detail = "") {
    console.log(`${ok ? "PASS" : "FAIL"}  ${label}${detail ? " — " + detail : ""}`);
    ok ? pass++ : fail++;
}

const {
    renderMarkdown, escapeHtml, formatDuration, formatClock,
    countWords, hintFor, looksLikeUrl, linkCitations,
    mentionQuery, filterContacts, groupTasksByContact, statusLabel,
} = module_;

// ── Escaping / XSS ──────────────────────────────────────────────────
// Model output and transcripts reach innerHTML, so this is the suite that
// matters most. Every one of these must stay passing.
const img = renderMarkdown('<img src=x onerror="alert(1)">');
check("img payload escaped", !img.includes("<img") && img.includes("&lt;img"));
check("quotes escaped", escapeHtml(`"'&<>`) === "&quot;&#39;&amp;&lt;&gt;");

const script = renderMarkdown("- <script>alert(1)</script>");
check("script in bullet escaped", !script.includes("<script>"));

const heading = renderMarkdown("## <b>bold</b>");
check("html in heading escaped", !heading.includes("<b>"));

const bold = renderMarkdown("**<i>x</i>**");
check("html inside bold escaped", !bold.includes("<i>") && bold.includes("<strong>"));

// ── Markdown ────────────────────────────────────────────────────────
check("## becomes h3", renderMarkdown("## Topic").includes("<h3>Topic</h3>"));
check("# becomes h2", renderMarkdown("# Topic").includes("<h2>Topic</h2>"));
check("bold", renderMarkdown("**hi**").includes("<strong>hi</strong>"));
check("italic", renderMarkdown("say *hi* now").includes("<em>hi</em>"));
check("inline code", renderMarkdown("use `pip`").includes("<code>pip</code>"));

const dashList = renderMarkdown("- one\n- two");
check("dash bullets", (dashList.match(/<li>/g) || []).length === 2);

const starList = renderMarkdown("* alpha\n* beta");
check("asterisk bullets", (starList.match(/<li>/g) || []).length === 2);

const numbered = renderMarkdown("1. first\n2. second");
check("numbered list", (numbered.match(/<li>/g) || []).length === 2);

const mixed = renderMarkdown("## H\n- a\n\npara\n\n- b");
check("lists close around paragraphs",
    (mixed.match(/<ul>/g) || []).length === 2 &&
    (mixed.match(/<\/ul>/g) || []).length === 2);

check("empty input", renderMarkdown("").includes('class="empty"'));
check("whitespace input", renderMarkdown("  \n  ").includes('class="empty"'));

// Shape the model actually produces.
const real = renderMarkdown(
    "📌 **Q3 Sync**\n\n🗂️ **Detailed Breakdown**\n\n## Checkout\n" +
    "* Moved 60% traffic 💳\n* Error rate 2.1% → 0.4%"
);
check("real model output renders",
    real.includes("<h3>Checkout</h3>") && (real.match(/<li>/g) || []).length === 2);

// ── Formatting ──────────────────────────────────────────────────────
check("duration < 1h", formatDuration(1845) === "30m 45s", formatDuration(1845));
check("duration > 1h", formatDuration(7325) === "2h 02m", formatDuration(7325));
check("duration zero", formatDuration(0) === "");
check("clock", formatClock(125000) === "02:05", formatClock(125000));
check("word count", countWords("  one two   three ") === 3);
check("word count empty", countWords("") === 0);

// ── Error hints ─────────────────────────────────────────────────────
check("hint: api key", hintFor("GOOGLE_API_KEY is missing.").includes(".env"));
check("hint: rate limit", hintFor("Gemini API rate limit reached").length > 0);
check("hint: whisper", hintFor("Whisper is not installed.").includes("pip install"));
check("hint: silent", hintFor("No speech was detected").length > 0);
check("no hint for unknown", hintFor("something odd happened") === "");

// ── URL detection ───────────────────────────────────────────────────
check("http url", looksLikeUrl("https://youtu.be/x"));
check("windows path is not a url", !looksLikeUrl("C:\\video.mp4"));

// ── Q&A citations ───────────────────────────────────────────────────
check("citation marked",
    linkCitations("<p>Friday [2].</p>") === '<p>Friday <sup class="cite" data-refs="2">[2]</sup>.</p>');
check("grouped citation", linkCitations("x [1, 3]").includes('data-refs="1,3">[1,3]</sup>'));
check("non-numeric brackets untouched", linkCitations("[todo]") === "[todo]");
const cited = linkCitations(renderMarkdown("<script>x</script> [1]"));
check("citations keep escaping", !cited.includes("<script>") && cited.includes("cite"));
check("hint: qa indexing", hintFor("Q&A indexing failed: boom").length > 0);

// ── Agent citations ─────────────────────────────────────────────────
check("video excerpt citation", linkCitations("A [V1-3].").includes('data-refs="V1-3">[V1-3]</sup>'));
check("web citation", linkCitations("B [W2]").includes('data-refs="W2">[W2]</sup>'));
check("mixed citation group", linkCitations("C [V2-1, W1]").includes('data-refs="V2-1,W1"'));
check("whole-video citation", linkCitations("D [V3]").includes('data-refs="V3">[V3]</sup>'));
check("lookalike brackets untouched", linkCitations("[Wx] [V1-] [V] [W]") === "[Wx] [V1-] [V] [W]");
check("agent citations keep escaping",
    !linkCitations(renderMarkdown('<img src=x onerror=alert(1)> [W1]')).includes("<img"));

// ── @mentions and task helpers ──────────────────────────────────────
const people = [
    { id: 1, name: "Sarah Khan", email: "sarah@example.com", aliases: "" },
    { id: 2, name: "Omar Ali", email: "omar@example.com", aliases: "O, Oz" },
    { id: 3, name: "Samir Patel", email: "sp@example.com", aliases: "" },
];
check("mention strips @ and case", mentionQuery("  @SaRa ") === "sara");
check("empty query lists everyone", filterContacts(people, "@").length === 3);
check("name prefix match", filterContacts(people, "@sa").map((c) => c.id).join() === "3,1");
check("last-name match", filterContacts(people, "khan")[0].id === 1);
check("alias match", filterContacts(people, "oz")[0].id === 2);
check("email match", filterContacts(people, "sp@")[0].id === 3);
check("no match", filterContacts(people, "zzz").length === 0);
check("result limit", filterContacts(people, "", 2).length === 2);

const grouped = groupTasksByContact([
    { id: 1, contact_id: 1, status: "proposed" },
    { id: 2, contact_id: 1, status: "drafted" },
    { id: 3, contact_id: null, status: "proposed" },
    { id: 4, contact_id: 2, status: "dismissed" },
]);
check("tasks grouped per contact", grouped.get(1).length === 2);
check("unassigned grouped under null", grouped.get(null).length === 1);
check("dismissed tasks skipped", !grouped.has(2));
check("status labels", statusLabel("drafted") === "Draft ready" && statusLabel("dry-run") === "Dry run");
check("unknown status passes through", statusLabel("mystery") === "mystery");

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
