/* ── State ── */
let selectedFiles = [];
const activePollers = {};  // jobId → intervalId
const jobStartTimes = {};  // jobId → ms timestamp when we started polling
let appConfig = { models: [] };

/* ── DOM refs ── */
const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const fileListEl = document.getElementById("file-list");
const submitBtn = document.getElementById("submit-btn");
const uploadError = document.getElementById("upload-error");
const activeJobsEl = document.getElementById("active-jobs");
const historyListEl = document.getElementById("history-list");
const formatSelect = document.getElementById("format");
const modelSelect = document.getElementById("model-select");
const cancelBtn = document.getElementById("cancel-btn");
const uploadProgress = document.getElementById("upload-progress");
const uploadProgressBar = document.getElementById("upload-progress-bar");
const uploadProgressText = document.getElementById("upload-progress-text");

/* ── Init: load config ── */
async function loadConfig() {
    try {
        const resp = await fetch("/api/config");
        if (resp.ok) appConfig = await resp.json();
    } catch (e) { /* defaults */ }

    // Determine the default selection: last-used wins, else the app default
    const APP_DEFAULT = "Qwen3.6-35B-FS";
    const lastModel = localStorage.getItem("ocr_last_model");
    const modelKeys = appConfig.models.map((m) => m.key);
    const defaultModel = (lastModel && modelKeys.includes(lastModel))
        ? lastModel
        : (modelKeys.includes(APP_DEFAULT)
            ? APP_DEFAULT
            : (modelKeys.find((k) => k !== "Tesseract") || modelKeys[0] || ""));

    modelSelect.innerHTML = "";
    for (const m of appConfig.models) {
        const opt = document.createElement("option");
        opt.value = m.key;
        opt.textContent = m.display + (m.key === APP_DEFAULT ? " (default)" : "");
        if (m.key === defaultModel) opt.selected = true;
        modelSelect.appendChild(opt);
    }
}

function getSelectedModels() {
    return modelSelect.value ? [modelSelect.value] : [];
}

/* ── Drop zone ── */
dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", (e) => { e.preventDefault(); dropZone.classList.remove("drag-over"); addFiles(e.dataTransfer.files); });
fileInput.addEventListener("change", () => { addFiles(fileInput.files); fileInput.value = ""; });

function addFiles(fileList) {
    for (const f of fileList) {
        if (!selectedFiles.some((s) => s.name === f.name && s.size === f.size)) selectedFiles.push(f);
    }
    renderFileList();
}

function removeFile(index) { selectedFiles.splice(index, 1); renderFileList(); }

function renderFileList() {
    fileListEl.innerHTML = "";
    selectedFiles.forEach((f, i) => {
        const li = document.createElement("li");
        li.innerHTML = `<span class="file-name">${escapeHtml(f.name)}</span><span class="file-size">${formatSize(f.size)}</span><button class="remove-btn" data-idx="${i}">&times;</button>`;
        fileListEl.appendChild(li);
    });
    submitBtn.disabled = selectedFiles.length === 0;
    fileListEl.querySelectorAll(".remove-btn").forEach((btn) => {
        btn.addEventListener("click", () => removeFile(parseInt(btn.dataset.idx)));
    });
}

/* ── Upload ── */

// Cloudflare's tunnel rejects request bodies over 100MB before they ever reach
// the app, so there is no server-side cap to report — we check here to give a
// precise message instead of letting the proxy return an opaque HTML error.
const MAX_SUBMISSION_BYTES = 100 * 1024 * 1024;

let uploadXhr = null;

submitBtn.addEventListener("click", submitUpload);
cancelBtn.addEventListener("click", () => { if (uploadXhr) uploadXhr.abort(); });

function submitUpload() {
    uploadError.classList.add("hidden");
    const models = getSelectedModels();

    if (selectedFiles.length === 0) return showError("Please select at least one file.");
    if (models.length === 0) return showError("Please select at least one model.");

    const totalBytes = selectedFiles.reduce((n, f) => n + f.size, 0);
    if (totalBytes > MAX_SUBMISSION_BYTES) {
        return showError(
            `This submission is ${formatSize(totalBytes)}, which is over the 100MB limit. ` +
            `Submit fewer files at a time — the limit applies to the whole submission, not per file.`
        );
    }

    const form = new FormData();
    selectedFiles.forEach((f) => form.append("files", f));
    form.append("output_format", formatSelect.value);
    form.append("models", models.join(","));

    setUploading(true);
    setUploadProgress(0, totalBytes, totalBytes);

    const xhr = new XMLHttpRequest();
    uploadXhr = xhr;
    xhr.open("POST", "/api/upload");
    xhr.timeout = 0;  // a 100MB body over a slow link can legitimately take a while

    xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) setUploadProgress(e.loaded, e.total, totalBytes);
    });

    // Body fully sent, but the server is still saving and validating it. For large
    // PDFs this gap is seconds-to-minutes, so say so rather than sitting at 100%.
    xhr.upload.addEventListener("load", () => {
        uploadProgressBar.style.width = "100%";
        uploadProgressBar.classList.add("indeterminate");
        uploadProgressText.textContent = "Upload complete — validating and queueing...";
    });

    xhr.addEventListener("load", () => {
        if (xhr.status >= 200 && xhr.status < 300) {
            let data;
            try {
                data = JSON.parse(xhr.responseText);
            } catch (e) {
                finishUpload();
                return showError("Upload succeeded but the server sent an unreadable response.");
            }
            finishUpload();
            onUploadAccepted(data, models);
        } else {
            finishUpload();
            showError(describeUploadFailure(xhr));
        }
    });

    xhr.addEventListener("error", () => {
        finishUpload();
        showError(
            "The connection dropped during upload. This usually means the submission was " +
            "too large for the proxy or the network interrupted it — try one file at a time."
        );
    });

    xhr.addEventListener("abort", () => {
        finishUpload();
        showError("Upload cancelled.");
    });

    xhr.send(form);
}

function onUploadAccepted(data, models) {
    selectedFiles = [];
    renderFileList();

    // Remember last model selection
    if (models.length === 1) {
        localStorage.setItem("ocr_last_model", models[0]);
    }

    // Save tokens and start polling for each job
    for (const jid of data.job_ids) {
        saveJobToken(jid, data.access_tokens[jid]);
    }
    addActiveGroup(data.group_id, data.job_ids, data.access_tokens, data.models);
}

/* Turn a failed upload into something actionable. The app sends JSON {detail},
   but proxies in front of it (Cloudflare) return HTML, so never assume JSON. */
function describeUploadFailure(xhr) {
    let detail = "";
    try {
        const parsed = JSON.parse(xhr.responseText);
        if (parsed && parsed.detail) {
            detail = Array.isArray(parsed.detail)
                ? parsed.detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
                : String(parsed.detail);
        }
    } catch (e) { /* not JSON (proxy error page) — fall back to the status code */ }

    if (detail) return detail;

    if (xhr.status === 413) {
        return "Submission rejected as too large. The 100MB limit is enforced by Cloudflare " +
               "before the files reach the server — submit fewer files at a time.";
    }
    if (xhr.status === 502 || xhr.status === 503 || xhr.status === 504) {
        return `The server is unreachable or restarting (HTTP ${xhr.status}). Try again shortly.`;
    }
    if (xhr.status >= 500) return `Server error (HTTP ${xhr.status}). Nothing was queued.`;
    if (xhr.status === 0) return "No response from the server — the request never completed.";
    return `Upload failed (HTTP ${xhr.status}). Nothing was queued.`;
}

function setUploading(active) {
    submitBtn.disabled = active;
    submitBtn.textContent = active ? "Uploading..." : "Submit for OCR";
    cancelBtn.classList.toggle("hidden", !active);
    uploadProgress.classList.toggle("hidden", !active);
}

function setUploadProgress(loaded, total, totalBytes) {
    const pct = total > 0 ? Math.round((loaded / total) * 100) : 0;
    uploadProgressBar.classList.remove("indeterminate");
    uploadProgressBar.style.width = pct + "%";
    uploadProgressText.textContent =
        `Uploading ${formatSize(loaded)} of ${formatSize(totalBytes)} (${pct}%)`;
}

function finishUpload() {
    uploadXhr = null;
    setUploading(false);
    uploadProgressBar.classList.remove("indeterminate");
    uploadProgressBar.style.width = "0%";
    uploadProgressText.textContent = "";
}

function showError(msg) { uploadError.textContent = msg; uploadError.classList.remove("hidden"); }

/* ── Job group tracking ── */
function addActiveGroup(groupId, jobIds, accessTokens, models) {
    const card = document.createElement("div");
    card.className = "job-card";
    card.id = `group-${groupId}`;

    let inner = `<div class="job-header"><h3>Job Group ${groupId.slice(0, 8)}...</h3></div>`;
    for (let i = 0; i < jobIds.length; i++) {
        const jid = jobIds[i];
        const model = models[i];
        inner += `
            <div class="model-subcard" id="job-${jid}">
                <h4>${escapeHtml(model)} <span class="status-badge status-queued">queued</span></h4>
                <div class="progress-bar-wrap"><div class="progress-bar" style="width: 0%"></div></div>
                <p class="progress-text">Waiting to start...</p>
                <div class="stats-row hidden"></div>
                <ul class="download-list hidden"></ul>
            </div>
        `;
    }
    card.innerHTML = inner;
    activeJobsEl.prepend(card);

    // Poll each job
    for (const jid of jobIds) {
        jobStartTimes[jid] = Date.now();
        const token = accessTokens[jid];
        const poll = setInterval(() => pollJob(jid, token, poll, groupId, jobIds), 2000);
        activePollers[jid] = poll;
        pollJob(jid, token, poll, groupId, jobIds);
    }
}

async function pollJob(jobId, token, intervalId, groupId, allJobIds) {
    try {
        const resp = await fetch(`/api/jobs/${jobId}?token=${token}`);
        if (!resp.ok) return;
        const job = await resp.json();
        updateSubcard(jobId, job, token);

        if (job.status === "completed" || job.status === "failed") {
            clearInterval(intervalId);
            delete activePollers[jobId];

            // If all jobs in group are done, refresh history
            const allDone = allJobIds.every((jid) => !activePollers[jid]);
            if (allDone) loadHistory();
        }
    } catch (e) { /* ignore */ }
}

function updateSubcard(jobId, job, token) {
    const sub = document.getElementById(`job-${jobId}`);
    if (!sub) return;

    const badge = sub.querySelector(".status-badge");
    badge.className = `status-badge status-${job.status}`;
    badge.textContent = job.status;

    const bar = sub.querySelector(".progress-bar");
    const text = sub.querySelector(".progress-text");

    if (job.status === "processing") {
        const pages = job.total_pages || 0;
        const page = job.current_page || 0;
        const filePrefix = job.total_files > 1 ? `File ${job.current_file}/${job.total_files}: ` : "";
        const name = job.filename || "document";

        if (pages > 0 && page > 0) {
            const pct = Math.min(100, Math.round((page / pages) * 100));
            bar.classList.remove("indeterminate");
            bar.style.width = pct + "%";
            text.textContent =
                `${filePrefix}${name} — page ${page} of ${pages} (${pct}%)${elapsedSuffix(jobId)}`;
        } else {
            // total_pages is known but no page has been emitted yet: the document is
            // still being parsed/laid out. On an 800-page manual that stage alone runs
            // for minutes, so show motion rather than a bar frozen at 0%.
            bar.classList.add("indeterminate");
            bar.style.width = "100%";
            text.textContent = pages > 0
                ? `${filePrefix}Preparing ${name} — ${pages} pages detected, starting OCR...${elapsedSuffix(jobId)}`
                : `${filePrefix}Preparing ${name}...${elapsedSuffix(jobId)}`;
        }
    } else if (job.status === "completed") {
        bar.classList.remove("indeterminate");
        bar.style.width = "100%";
        bar.style.background = "var(--success)";
        text.textContent = "Complete!";

        // Show stats
        if (job.stats) renderStats(sub, job.stats);

        // Show downloads
        const dl = sub.querySelector(".download-list");
        dl.classList.remove("hidden");
        dl.innerHTML = job.result_files.map((f) =>
            `<li><a href="/api/jobs/${jobId}/files/${encodeURIComponent(f)}?token=${token}" download>&#11015; ${escapeHtml(f)}</a></li>`
        ).join("");

        // Delete button
        if (!sub.querySelector(".btn-danger")) {
            const delBtn = document.createElement("button");
            delBtn.className = "btn btn-danger";
            delBtn.style.marginTop = "0.5rem";
            delBtn.textContent = "Delete";
            delBtn.onclick = () => deleteJob(jobId, token);
            sub.appendChild(delBtn);
        }
    } else if (job.status === "failed") {
        bar.classList.remove("indeterminate");
        bar.style.width = "100%";
        bar.style.background = "var(--error)";
        text.textContent = job.error || "Failed.";
        text.style.color = "var(--error)";
        if (job.stats) renderStats(sub, job.stats);
    }
}

function renderStats(container, stats) {
    const row = container.querySelector(".stats-row");
    row.classList.remove("hidden");
    const secs = stats.processing_seconds;
    const timeStr = secs >= 60 ? `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s` : `${secs}s`;
    row.innerHTML = `
        <span class="stat">Time: <strong>${timeStr}</strong></span>
        <span class="stat">Input tokens: <strong>${stats.prompt_tokens.toLocaleString()}</strong></span>
        <span class="stat">Output tokens: <strong>${stats.completion_tokens.toLocaleString()}</strong></span>
        <span class="stat">Total tokens: <strong>${(stats.prompt_tokens + stats.completion_tokens).toLocaleString()}</strong></span>
    `;
}

/* ── Delete job ── */
async function deleteJob(jobId, token) {
    if (!confirm("Delete this job and its files?")) return;
    try {
        await fetch(`/api/jobs/${jobId}?token=${token}`, { method: "DELETE" });
        const sub = document.getElementById(`job-${jobId}`);
        if (sub) sub.remove();
        removeJobToken(jobId);
        loadHistory();
    } catch (e) { /* ignore */ }
}

/* ── History ── */
async function loadHistory() {
    try {
        const resp = await fetch("/api/my-jobs");
        if (!resp.ok) return;
        const jobs = await resp.json();
        renderHistory(jobs);
    } catch (e) { /* ignore */ }
}

function renderHistory(jobs) {
    if (jobs.length === 0) {
        historyListEl.innerHTML = '<p style="color: var(--text-dim); font-size: 0.85rem;">No previous jobs.</p>';
        return;
    }

    // Group by group_id
    const groups = {};
    const ungrouped = [];
    for (const job of jobs) {
        if (job.group_id) {
            if (!groups[job.group_id]) groups[job.group_id] = [];
            groups[job.group_id].push(job);
        } else {
            ungrouped.push(job);
        }
    }

    let html = "";

    for (const [gid, gjobs] of Object.entries(groups)) {
        html += `<div class="job-card"><div class="job-header"><h3>Job Group ${gid.slice(0, 8)}...</h3></div>`;
        for (const job of gjobs) {
            html += renderHistorySubcard(job);
        }
        html += `</div>`;
    }

    for (const job of ungrouped) {
        html += `<div class="job-card">${renderHistorySubcard(job)}</div>`;
    }

    historyListEl.innerHTML = html;
}

function renderHistorySubcard(job) {
    const token = getJobToken(job.job_id);
    const tokenParam = token ? `?token=${token}` : "";
    const dateStr = job.created_at ? new Date(parseFloat(job.created_at) * 1000).toLocaleString() : "";
    const modelLabel = job.model ? `<strong>${escapeHtml(job.model)}</strong> &middot; ` : "";
    const deleteBtn = (job.status === "completed" || job.status === "failed")
        ? ` <button class="btn btn-danger" onclick="deleteJob('${job.job_id}', '${token}')">Delete</button>`
        : "";

    let statsHtml = "";
    if (job.stats && job.stats.processing_seconds > 0) {
        const s = job.stats;
        const secs = s.processing_seconds;
        const timeStr = secs >= 60 ? `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s` : `${secs}s`;
        statsHtml = `<div class="stats-row">
            <span class="stat">Time: <strong>${timeStr}</strong></span>
            <span class="stat">Input: <strong>${s.prompt_tokens.toLocaleString()}</strong></span>
            <span class="stat">Output: <strong>${s.completion_tokens.toLocaleString()}</strong></span>
            <span class="stat">Total: <strong>${(s.prompt_tokens + s.completion_tokens).toLocaleString()}</strong></span>
        </div>`;
    }

    const downloads = (job.status === "completed" && job.result_files.length > 0)
        ? `<ul class="download-list">${job.result_files.map((f) =>
            `<li><a href="/api/jobs/${job.job_id}/files/${encodeURIComponent(f)}${tokenParam}" download>&#11015; ${escapeHtml(f)}</a></li>`
          ).join("")}</ul>`
        : "";

    return `
        <div class="model-subcard" id="job-${job.job_id}">
            <h4>${modelLabel}<span class="status-badge status-${job.status}">${job.status}</span>${deleteBtn}</h4>
            <p class="progress-text">${job.file_count} file(s) &middot; ${job.output_format} &middot; ${dateStr}</p>
            ${statsHtml}
            ${downloads}
        </div>
    `;
}

/* ── Local token storage ── */
function saveJobToken(jobId, token) {
    try { const t = JSON.parse(localStorage.getItem("ocr_tokens") || "{}"); t[jobId] = token; localStorage.setItem("ocr_tokens", JSON.stringify(t)); } catch (e) {}
}
function getJobToken(jobId) {
    try { return JSON.parse(localStorage.getItem("ocr_tokens") || "{}")[jobId] || ""; } catch (e) { return ""; }
}
function removeJobToken(jobId) {
    try { const t = JSON.parse(localStorage.getItem("ocr_tokens") || "{}"); delete t[jobId]; localStorage.setItem("ocr_tokens", JSON.stringify(t)); } catch (e) {}
}

/* ── Utils ── */
// Multi-hour jobs are normal here, so show elapsed time to distinguish "slow"
// from "stuck". Only available for jobs polled in this tab.
function elapsedSuffix(jobId) {
    const started = jobStartTimes[jobId];
    if (!started) return "";
    const secs = Math.floor((Date.now() - started) / 1000);
    if (secs < 60) return ` · ${secs}s elapsed`;
    const mins = Math.floor(secs / 60);
    if (mins < 60) return ` · ${mins}m ${secs % 60}s elapsed`;
    return ` · ${Math.floor(mins / 60)}h ${mins % 60}m elapsed`;
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1048576).toFixed(1) + " MB";
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

/* ── Init ── */
loadConfig().then(() => loadHistory());
