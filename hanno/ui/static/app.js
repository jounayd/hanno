(function () {
  "use strict";

  var POLL_MS = 1500;
  var pollTimer = null;
  var activeSeedId = null;

  // ---------- Tabs ----------
  var tabButtons = document.querySelectorAll(".tab-btn");
  var tabPanels = {
    mine: document.getElementById("tab-mine"),
    seeds: document.getElementById("tab-seeds"),
    settings: document.getElementById("tab-settings"),
    skills: document.getElementById("tab-skills"),
  };

  function applyTab(name) {
    if (!tabPanels.hasOwnProperty(name)) name = "mine";
    tabButtons.forEach(function (b) { b.classList.toggle("active", b.dataset.tab === name); });
    Object.keys(tabPanels).forEach(function (k) { tabPanels[k].classList.toggle("active", k === name); });
    if (name === "seeds") { loadSeedQueue(); startSeedsPolling(); } else { stopSeedsPolling(); }
    if (name === "settings") loadSettings();
    if (name === "skills") loadSkills();
    if (name === "mine") Mindmap.fitToScreen();
  }

  // ---------- Browser back/forward: #tab or #tab/seedId drives all navigation ----------
  function currentHash() { return location.hash.replace(/^#\/?/, ""); }
  function buildHash(tab, seedId) { return tab + (seedId ? "/" + seedId : ""); }

  function parseHash() {
    var parts = currentHash().split("/").filter(Boolean);
    var tab = tabPanels.hasOwnProperty(parts[0]) ? parts[0] : "mine";
    return { tab: tab, seedId: parts[1] || null };
  }

  function applyRoute(tab, seedId) {
    applyTab(tab);
    if (tab === "mine" && seedId) {
      seedIdInput.value = seedId;
      if (seedSelect.querySelector('option[value="' + seedId + '"]')) seedSelect.value = seedId;
      loadSeedTrace(seedId);
    }
  }

  function navigateTo(tab, seedId) {
    var hash = buildHash(tab, seedId);
    if (currentHash() === hash) applyRoute(tab, seedId); // same URL -- still (re)apply, e.g. re-picking the same seed
    else location.hash = hash; // triggers hashchange below, which applies the route
  }

  window.addEventListener("hashchange", function () {
    var r = parseHash();
    applyRoute(r.tab, r.seedId);
  });

  tabButtons.forEach(function (b) { b.addEventListener("click", function () { navigateTo(b.dataset.tab); }); });

  // ---------- Mindmap wiring ----------
  Mindmap.init({
    onSeedLoaded: function (seed) {
      // Keeps the URL truthful to whatever's displayed (e.g. after "Load
      // trace JSON") WITHOUT creating a history entry -- routine polling
      // reloads the same seed repeatedly and must not spam back/forward.
      if (seed && seed.id) {
        var hash = buildHash("mine", seed.id);
        if (currentHash() !== hash) history.replaceState(null, "", "#" + hash);
      }
    },
    onReseed: function (candidateId, text, domain) {
      return api("POST", "/api/reseed", { candidate_id: candidateId, text: text, domain: domain })
        .then(function (result) {
          refreshSeedList();
          return result;
        });
    },
  });

  // ---------- API helper ----------
  function api(method, path, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) throw new Error(data.detail || (res.status + " " + res.statusText));
        return data;
      });
    });
  }

  // ---------- Mine tab: seed picker ----------
  var seedSelect = document.getElementById("seed-select");
  var seedIdInput = document.getElementById("seed-id-input");

  function refreshSeedList() {
    api("GET", "/api/seeds").then(function (seeds) {
      var current = seedSelect.value;
      seedSelect.innerHTML = '<option value="">— choose —</option>' + seeds.map(function (s) {
        var label = (s.text || "").slice(0, 70) + (s.text && s.text.length > 70 ? "…" : "");
        return '<option value="' + s.id + '">[' + s.status + '] ' + escHtml(label) + '</option>';
      }).join("");
      if (current) seedSelect.value = current;
    }).catch(function () { /* seed list is best-effort */ });
  }
  refreshSeedList();

  function escHtml(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  seedSelect.addEventListener("change", function () {
    if (seedSelect.value) navigateTo("mine", seedSelect.value);
  });

  function currentSeedId() { return (seedIdInput.value || seedSelect.value || "").trim(); }

  function loadSeedTrace(seedId) {
    api("GET", "/api/seeds/" + seedId + "/trace").then(function (data) {
      Mindmap.loadData(data);
    }).catch(function (err) {
      setMineStatus("Could not load that seed's trace: " + err.message, true);
    });
  }

  // ---------- Mine tab: run controls ----------
  var candidatesInput = document.getElementById("candidates-input");
  var depthInput = document.getElementById("depth-input");
  var topkInput = document.getElementById("topk-input");
  var freshInput = document.getElementById("fresh-input");

  function bindRangeLabel(input, labelId) {
    var label = document.getElementById(labelId);
    input.addEventListener("input", function () { label.textContent = input.value; });
  }
  bindRangeLabel(candidatesInput, "candidates-val");
  bindRangeLabel(depthInput, "depth-val");
  bindRangeLabel(topkInput, "topk-val");

  var mineStatusEl = document.getElementById("mine-status");
  function setMineStatus(text, isError) {
    mineStatusEl.textContent = text;
    mineStatusEl.style.color = isError ? "var(--danger)" : "var(--muted)";
  }

  document.getElementById("btn-start-mine").addEventListener("click", function () {
    var seedId = currentSeedId();
    if (!seedId) { setMineStatus("Pick a seed or paste a seed ID first.", true); return; }

    var body = {
      seed_id: seedId,
      candidates: parseInt(candidatesInput.value, 10),
      depth: parseInt(depthInput.value, 10),
      top_k: parseInt(topkInput.value, 10),
      fresh: freshInput.checked
    };
    setMineStatus("Starting…");
    api("POST", "/api/mine", body).then(function () {
      setMineStatus("Mining started — updating live every " + (POLL_MS / 1000) + "s.");
      startPolling(seedId);
    }).catch(function (err) {
      setMineStatus("Could not start mining: " + err.message, true);
    });
  });

  function startPolling(seedId) {
    activeSeedId = seedId;
    var pill = document.getElementById("seed-pill");
    if (pill) pill.classList.add("running");
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(function () { pollOnce(seedId); }, POLL_MS);
    pollOnce(seedId); // immediate first tick
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    var pill = document.getElementById("seed-pill");
    if (pill) pill.classList.remove("running");
  }

  function pollOnce(seedId) {
    Promise.all([
      api("GET", "/api/mine/" + seedId + "/status"),
      api("GET", "/api/seeds/" + seedId + "/trace")
    ]).then(function (results) {
      var status = results[0], trace = results[1];
      Mindmap.loadData(trace);
      if (status.running) {
        setMineStatus("Mining in progress — " + trace.candidates.length + " candidate(s) so far.");
      } else {
        stopPolling();
        if (status.error) setMineStatus("Mining stopped with an error: " + status.error, true);
        else setMineStatus("Mining finished — " + trace.candidates.length + " candidate(s) total.");
        refreshSeedList();
      }
    }).catch(function (err) {
      setMineStatus("Lost contact with the server: " + err.message, true);
    });
  }

  // ---------- Seeds tab ----------
  var STATUS_LABELS = { queued: "queued", active: "active", exhausted: "exhausted", retired: "retired" };
  var SEEDS_POLL_MS = 3000;
  var seedsPollTimer = null;

  function startSeedsPolling() {
    if (seedsPollTimer) return;
    seedsPollTimer = setInterval(loadSeedQueue, SEEDS_POLL_MS);
  }
  function stopSeedsPolling() {
    if (seedsPollTimer) { clearInterval(seedsPollTimer); seedsPollTimer = null; }
  }

  function loadSeedQueue() {
    var domain = document.getElementById("seed-queue-domain-filter").value;
    var list = document.getElementById("seed-queue-list");
    if (!list.children.length || list.querySelector(".empty-inline")) {
      list.innerHTML = '<div class="empty-inline">Loading&hellip;</div>';
    }
    var path = "/api/seeds" + (domain ? ("?domain=" + encodeURIComponent(domain)) : "");
    api("GET", path).then(function (seeds) {
      if (!seeds.length) {
        list.innerHTML = '<div class="empty-inline">No seeds yet — add one above.</div>';
        return;
      }
      list.innerHTML = seeds.map(renderSeedRow).join("");
      seeds.forEach(function (s) {
        var mineBtn = document.getElementById("mine-this-" + cssEscape(s.id));
        if (mineBtn && !mineBtn.disabled) mineBtn.addEventListener("click", function () { mineThisSeed(s.id); });
        var traceBtn = document.getElementById("view-trace-" + cssEscape(s.id));
        if (traceBtn) traceBtn.addEventListener("click", function () { viewSeedTrace(s.id, s.mining); });
      });
    }).catch(function (err) {
      list.innerHTML = '<div class="empty-inline">Could not load seeds: ' + escHtml(err.message) + '</div>';
    });
  }

  function renderSeedRow(s) {
    var statusClass = "status-" + (s.status || "queued");
    var statusLabel = STATUS_LABELS[s.status] || s.status;
    var hasTrace = (s.cycles_run || 0) > 0 || (s.candidates_produced || 0) > 0;
    var miningChip = s.mining
      ? '<span class="chip mining"><span class="mining-dot"></span> MINING NOW</span>'
      : "";
    var mineBtn = s.mining
      ? '<button class="btn mining-active" disabled>Mining in progress&hellip;</button>'
      : '<button class="btn ghost" id="mine-this-' + cssEscape(s.id) + '">Mine this seed &rarr;</button>';
    var traceBtn = (hasTrace || s.mining)
      ? '<button class="btn ' + (s.mining ? "primary" : "ghost") + '" id="view-trace-' + cssEscape(s.id) + '">'
        + (s.mining ? "View live trace &rarr;" : "View trace &rarr;") + '</button>'
      : "";
    return (
      '<div class="seed-row' + (s.mining ? " mining" : "") + '">' +
        '<div class="seed-row-head">' +
          miningChip +
          '<span class="chip ' + statusClass + '">' + escHtml(statusLabel) + '</span>' +
          '<span class="chip">' + escHtml(s.domain) + '</span>' +
          '<span class="chip">' + escHtml(s.type) + '</span>' +
          '<span class="seed-row-id">' + s.id + '</span>' +
        '</div>' +
        '<p class="seed-row-text">' + escHtml(truncate(s.text || "", 220)) + '</p>' +
        '<div class="seed-row-foot">' +
          '<div class="seed-row-stats">' +
            '<span class="chip">cycles ' + (s.cycles_run || 0) + '</span>' +
            '<span class="chip">candidates ' + (s.candidates_produced || 0) + '</span>' +
            '<span class="chip">discoveries ' + (s.discoveries_attributed || 0) + '</span>' +
          '</div>' +
          '<div style="display:flex; gap:8px;">' + traceBtn + mineBtn + '</div>' +
        '</div>' +
      '</div>'
    );
  }

  function truncate(s, n) { return s.length > n ? s.slice(0, n) + "…" : s; }

  function mineThisSeed(seedId) {
    navigateTo("mine", seedId);
  }

  function viewSeedTrace(seedId, isMining) {
    navigateTo("mine", seedId);
    if (isMining) {
      setMineStatus("Watching a mining run already in progress…");
      startPolling(seedId);
    }
  }

  document.getElementById("seed-queue-domain-filter").addEventListener("change", loadSeedQueue);

  var seedAddStatusEl = document.getElementById("seed-add-status");
  function setSeedAddStatus(text, isError) {
    seedAddStatusEl.textContent = text;
    seedAddStatusEl.className = "save-status " + (isError ? "err" : "ok");
  }

  document.getElementById("seed-add-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var text = document.getElementById("seed-text-input").value.trim();
    var domain = document.getElementById("seed-domain-input").value;
    var type = document.getElementById("seed-type-input").value;
    var submitter = document.getElementById("seed-submitter-input").value.trim();

    if (!text) { setSeedAddStatus("Enter seed text first.", true); return; }

    setSeedAddStatus("Submitting…");
    api("POST", "/api/seeds", {
      text: text, domain: domain, type: type,
      submitter_id: submitter || null,
    }).then(function (result) {
      document.getElementById("seed-text-input").value = "";
      if (result.duplicate_classification === "NEAR_DUPLICATE" || result.duplicate_classification === "EXACT_DUPLICATE") {
        setSeedAddStatus("Queued as " + result.seed_id + " — " + result.feedback);
      } else {
        setSeedAddStatus("Queued as " + result.seed_id + ".");
      }
      loadSeedQueue();
      refreshSeedList();
    }).catch(function (err) {
      setSeedAddStatus("Rejected: " + err.message, true);
    });
  });

  // ---------- Settings tab ----------
  var CONFIG_FIELDS = [
    "provider", "base_url", "model", "request_timeout_seconds", "daily_spend_limit_usd", "candidates_per_cycle_per_seed",
    "max_tokens_per_candidate", "arm_selection_strategy", "ucb1_exploration_constant", "iteration_top_k",
    "strong_path_score_threshold", "min_iterations_for_reseed", "beam_diversity_max_similarity",
    "iteration_lineage_retention_threshold", "iteration_plateau_patience", "max_lean_checks_per_iteration",
  ];
  var LIST_FIELDS = { temperatures: true, reasoning_efforts: true };
  var BOOL_FIELDS = { iteration_exploration_thread: true };

  function loadSettings() {
    api("GET", "/api/config").then(function (cfg) {
      CONFIG_FIELDS.forEach(function (f) {
        var el = document.getElementById("cfg-" + f);
        if (el && cfg[f] !== undefined && cfg[f] !== null) el.value = cfg[f];
      });
      Object.keys(LIST_FIELDS).forEach(function (f) {
        var el = document.getElementById("cfg-" + f);
        if (el && cfg[f]) el.value = cfg[f].join(", ");
      });
      Object.keys(BOOL_FIELDS).forEach(function (f) {
        var el = document.getElementById("cfg-" + f);
        if (el && cfg[f] !== undefined) el.checked = !!cfg[f];
      });
      refreshKeyStatus(cfg.provider || "anthropic");
    }).catch(function (err) {
      setConfigStatus("Could not load current settings: " + err.message, true);
      refreshKeyStatus("anthropic");
    });
  }

  function refreshKeyStatus(provider) {
    var label = document.getElementById("api-key-label");
    if (label) label.textContent = "API key (" + provider + ")";
    api("GET", "/api/secrets/api-key-status?provider=" + encodeURIComponent(provider))
      .then(renderKeyStatus)
      .catch(function () { renderKeyStatus({ set: false, last4: null }); });
  }

  var providerSelect = document.getElementById("cfg-provider");
  if (providerSelect) {
    providerSelect.addEventListener("change", function () { refreshKeyStatus(providerSelect.value); });
  }

  function renderKeyStatus(status) {
    var el = document.getElementById("key-status");
    var text = document.getElementById("key-status-text");
    el.classList.toggle("set", !!status.set);
    text.textContent = status.set ? ("API key set (ends in " + status.last4 + ")") : "No API key set yet.";
  }

  function setConfigStatus(text, isError) {
    var el = document.getElementById("config-save-status");
    el.textContent = text;
    el.className = "save-status " + (isError ? "err" : "ok");
  }
  function setKeyStatus(text, isError) {
    var el = document.getElementById("key-save-status");
    el.textContent = text;
    el.className = "save-status " + (isError ? "err" : "ok");
  }

  document.getElementById("settings-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var updates = {};
    CONFIG_FIELDS.forEach(function (f) {
      var el = document.getElementById("cfg-" + f);
      if (!el || el.value === "") return;
      updates[f] = el.type === "number" ? parseFloat(el.value) : el.value;
    });
    Object.keys(LIST_FIELDS).forEach(function (f) {
      var el = document.getElementById("cfg-" + f);
      if (!el || !el.value.trim()) return;
      var parts = el.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
      updates[f] = f === "temperatures" ? parts.map(parseFloat) : parts;
    });
    Object.keys(BOOL_FIELDS).forEach(function (f) {
      var el = document.getElementById("cfg-" + f);
      if (el) updates[f] = el.checked;
    });

    setConfigStatus("Saving…");
    api("POST", "/api/config", updates).then(function () {
      setConfigStatus("Saved to config/local.yaml.");
    }).catch(function (err) {
      setConfigStatus("Save failed: " + err.message, true);
    });
  });

  document.getElementById("btn-save-key").addEventListener("click", function () {
    var input = document.getElementById("api-key-input");
    var provider = (document.getElementById("cfg-provider") || {}).value || "anthropic";
    if (!input.value.trim()) { setKeyStatus("Enter a key first.", true); return; }
    setKeyStatus("Saving…");
    api("POST", "/api/secrets/api-key", { api_key: input.value.trim(), provider: provider }).then(function (status) {
      input.value = "";
      renderKeyStatus(status);
      setKeyStatus("Saved.");
    }).catch(function (err) {
      setKeyStatus("Save failed: " + err.message, true);
    });
  });

  // ---------- Skills tab ----------
  function loadSkills() {
    var list = document.getElementById("skills-list");
    list.innerHTML = '<div class="empty-inline">Loading&hellip;</div>';
    api("GET", "/api/skills").then(function (skills) {
      if (!skills.length) {
        list.innerHTML = '<div class="empty-inline">No skills found in the skills/ directory.</div>';
        return;
      }
      list.innerHTML = skills.map(renderSkillCard).join("");
      skills.forEach(function (s) {
        var toggle = document.getElementById("skill-toggle-" + cssEscape(s.name));
        if (toggle) toggle.addEventListener("change", function () { toggleSkill(s.name, toggle.checked); });
      });
    }).catch(function (err) {
      list.innerHTML = '<div class="empty-inline">Could not load skills: ' + escHtml(err.message) + '</div>';
    });
  }

  function cssEscape(s) { return String(s).replace(/[^a-zA-Z0-9_-]/g, "_"); }

  function renderSkillCard(s) {
    var id = "skill-toggle-" + cssEscape(s.name);
    return (
      '<div class="skill-card' + (s.enabled ? "" : " disabled") + '" id="skill-card-' + cssEscape(s.name) + '">' +
        '<div class="skill-head">' +
          '<span class="skill-name">' + escHtml(s.name) + '</span>' +
          '<label class="switch"><input type="checkbox" id="' + id + '" ' + (s.enabled ? "checked" : "") + ' /><span class="track"></span></label>' +
        '</div>' +
        '<p class="skill-desc">' + escHtml(s.description) + '</p>' +
        '<div class="mono-block">' + escHtml(s.command.join(" ")) + '</div>' +
        '<div class="skill-meta">' +
          '<span class="chip">input: ' + escHtml(s.input_mode) + '</span>' +
          '<span class="chip">timeout: ' + s.timeout_seconds + 's</span>' +
          '<span class="chip">output cap: ' + s.max_output_chars + ' chars</span>' +
        '</div>' +
      '</div>'
    );
  }

  function toggleSkill(name, enabled) {
    api("POST", "/api/skills/" + encodeURIComponent(name) + "/toggle", { enabled: enabled }).then(function () {
      var card = document.getElementById("skill-card-" + cssEscape(name));
      if (card) card.classList.toggle("disabled", !enabled);
    }).catch(function (err) {
      alert("Could not toggle " + name + ": " + err.message);
      loadSkills(); // resync UI with server truth on failure
    });
  }

  // ---------- Initial route: deep-link / refresh / restore on load ----------
  if (!location.hash) history.replaceState(null, "", "#mine");
  (function () {
    var r = parseHash();
    applyRoute(r.tab, r.seedId);
  })();
})();
