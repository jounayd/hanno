/* Mind-map viewer: ported from hanno/viz/trace_mindmap.html, unchanged in
 * substance (layout, rendering, pan/zoom, detail panel, filters). The only
 * difference from the standalone file is how data arrives: here it can come
 * from a dropped/loaded trace.json (unchanged) OR from Mindmap.loadData()
 * called by app.js's poll loop while a mining job is running -- same
 * function either way, so a live seed and a post-hoc trace file render
 * identically.
 */
window.Mindmap = (function () {
  "use strict";

  var state = {
    seed: null,
    candidates: [],
    byId: {},
    childrenOf: {},
    positions: {},
    selectedId: null,
    filters: { minScore: 0, dimDupes: false, dimDead: false, dimFailed: true, search: "" },
    view: { scale: 1, tx: 0, ty: 0 }
  };

  var RING_GAP = 130;
  var CENTER_MARGIN = 70;

  // Mirrors COUNTEREXAMPLE_MARKER in hanno/verification/counterexample.py --
  // keep in sync if that constant ever changes.
  var COUNTEREXAMPLE_MARKER = "COUNTEREXAMPLE FOUND:";

  // Mirrors classify_result() in hanno/lean/checker.py: null means a
  // genuine triviality-filter survivor (compiles, not trivially closed --
  // never VERIFIED at this stage, that requires an actual prover).
  function leanResultLabel(result) {
    if (result === null || result === undefined) return { text: "survived triviality filter (compiles, non-trivial)", cls: "pass" };
    if (result === "VERIFIED") return { text: "PROVEN — complete Lean proof", cls: "pass" };
    if (result === "MALFORMED") return { text: "compile error", cls: "fail" };
    if (result === "UNTRANSLATABLE") return { text: "model declined to formalize", cls: "fail" };
    if (result === "TIMEOUT") return { text: "Lean check timed out", cls: "fail" };
    if (result === "KNOWN") return { text: "trivially closed by exact?/simp", cls: "" };
    return { text: result, cls: "" };
  }

  // Mirrors _status() in hanno/cli/commands/discoveries.py -- the UI and
  // CLI must never disagree about what "deep verification" concluded.
  function discoveryStatus(c) {
    if (!c.discovery) return null;
    if (c.discovery.status) {
      var status = c.discovery.status;
      return { text: status, cls: status.indexOf("REFUTED") === 0 ? "fail" : status.indexOf("PROVEN") === 0 ? "pass" : "gold" };
    }
    var invocations = c.tool_invocations || [];
    var refuted = invocations.some(function (inv) { return (inv.stdout || "").indexOf(COUNTEREXAMPLE_MARKER) !== -1; });
    if (refuted) return { text: "REFUTED (counterexample found)", cls: "fail" };
    var verifications = c.lean_verifications || [];
    var proven = verifications.some(function (lv) { return lv.result === "VERIFIED"; });
    if (proven) return { text: "PROVEN (complete Lean proof found)", cls: "pass" };
    if (invocations.length === 0) return { text: "cleared triviality filter — deep verification not yet run", cls: "gold" };
    return { text: "unresolved (no counterexample, but no automated proof either)", cls: "gold" };
  }

  // Drives both the ⊢ glyph and the node's own border color/width (see
  // render()): gold/"pending" while still unresolved, green/"proven" once
  // genuinely PROVEN, red/"failed" on any failure (triviality check OR a
  // real counterexample). One function so the glyph, the border, and the
  // detail panel can never disagree about a node's verification status.
  function leanNodeStatus(c) {
    var verifications = c.lean_verifications || [];
    if (!verifications.length) return null;
    if (c.discovery) {
      var ds = discoveryStatus(c);
      var status = ds.cls === "fail" ? "failed" : ds.cls === "pass" ? "proven" : "pending";
      var color = status === "failed" ? "var(--danger)" : status === "proven" ? "var(--success)" : "var(--gold)";
      return { color: color, label: ds.text, status: status };
    }
    var last = verifications[verifications.length - 1];
    return { color: "var(--danger)", label: leanResultLabel(last.result).text, status: "failed" };
  }
  var svg, viewport, emptyState, bodyGrid, detailPanel;
  var onSeedLoaded = null; // optional callback(seed) for the host page
  var onReseed = null; // optional callback(candidateId, text, domain) -> Promise, for the host page

  function init(opts) {
    svg = document.getElementById(opts.canvasId || "canvas");
    viewport = document.getElementById(opts.viewportId || "viewport");
    emptyState = document.getElementById(opts.emptyStateId || "empty-state");
    bodyGrid = document.getElementById(opts.bodyGridId || "body-grid");
    detailPanel = document.getElementById(opts.detailPanelId || "detail-panel");
    onSeedLoaded = opts.onSeedLoaded || null;
    onReseed = opts.onReseed || null;
    initReseedModal();

    var themeBtn = document.getElementById(opts.themeBtnId || "btn-theme");
    if (themeBtn) {
      themeBtn.addEventListener("click", function () {
        var root = document.documentElement;
        var current = root.getAttribute("data-theme");
        var prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
        var effectiveIsDark = current ? current === "dark" : prefersDark;
        root.setAttribute("data-theme", effectiveIsDark ? "light" : "dark");
      });
    }

    var fileInput = document.getElementById(opts.fileInputId || "file-input");
    var loadBtn = document.getElementById(opts.loadBtnId || "btn-load");
    if (loadBtn && fileInput) {
      loadBtn.addEventListener("click", function () { fileInput.click(); });
      fileInput.addEventListener("change", function (e) {
        if (e.target.files && e.target.files[0]) readFile(e.target.files[0]);
      });
    }
    var dropzone = document.getElementById(opts.dropzoneId || "dropzone");
    if (dropzone) {
      ["dragenter", "dragover"].forEach(function (evt) {
        document.body.addEventListener(evt, function (e) { e.preventDefault(); dropzone.classList.add("over"); });
      });
      ["dragleave", "drop"].forEach(function (evt) {
        document.body.addEventListener(evt, function (e) { e.preventDefault(); dropzone.classList.remove("over"); });
      });
      document.body.addEventListener("drop", function (e) {
        var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        if (f) readFile(f);
      });
    }

    document.getElementById(opts.fitBtnId || "btn-fit").addEventListener("click", fitToScreen);
    window.addEventListener("resize", function () { if (state.seed) fitToScreen(); });

    document.getElementById(opts.scoreFilterId || "score-filter").addEventListener("input", function (e) {
      state.filters.minScore = parseFloat(e.target.value);
      document.getElementById(opts.scoreValId || "score-val").textContent = state.filters.minScore.toFixed(2);
      render();
    });
    document.getElementById(opts.hideDupesId || "hide-dupes").addEventListener("change", function (e) { state.filters.dimDupes = e.target.checked; render(); });
    document.getElementById(opts.hideDeadId || "hide-dead").addEventListener("change", function (e) { state.filters.dimDead = e.target.checked; render(); });
    document.getElementById(opts.hideFailedId || "hide-failed").addEventListener("change", function (e) { state.filters.dimFailed = e.target.checked; render(); });
    document.getElementById(opts.searchId || "search").addEventListener("input", function (e) { state.filters.search = e.target.value; render(); });

    var dragging = false, lastX = 0, lastY = 0;
    svg.addEventListener("mousedown", function (e) { dragging = true; lastX = e.clientX; lastY = e.clientY; svg.classList.add("grabbing"); });
    window.addEventListener("mouseup", function () { dragging = false; svg.classList.remove("grabbing"); });
    window.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      state.view.tx += (e.clientX - lastX); state.view.ty += (e.clientY - lastY);
      lastX = e.clientX; lastY = e.clientY;
      applyView();
    });
    svg.addEventListener("wheel", function (e) {
      e.preventDefault();
      var rect = svg.getBoundingClientRect();
      var mx = e.clientX - rect.left, my = e.clientY - rect.top;
      var factor = e.deltaY < 0 ? 1.12 : 0.89;
      var newScale = Math.max(0.08, Math.min(4, state.view.scale * factor));
      state.view.tx = mx - (mx - state.view.tx) * (newScale / state.view.scale);
      state.view.ty = my - (my - state.view.ty) * (newScale / state.view.scale);
      state.view.scale = newScale;
      applyView();
    }, { passive: false });

    applyView();
  }

  function readFile(file) {
    var reader = new FileReader();
    reader.onload = function () {
      try { loadData(JSON.parse(reader.result)); }
      catch (err) { alert("Could not parse that file as JSON: " + err.message); }
    };
    reader.readAsText(file);
  }

  function clear() {
    state.seed = null;
    state.candidates = [];
    state.byId = {};
    state.childrenOf = {};
    state.positions = {};
    state.selectedId = null;
    closeDetail();
    while (viewport && viewport.firstChild) viewport.removeChild(viewport.firstChild);
    if (emptyState) emptyState.classList.remove("hide");
    var pill = document.getElementById("seed-pill");
    if (pill) { pill.classList.remove("show"); pill.classList.remove("running"); }
  }

  // ---------- Load + layout ----------
  function loadData(data) {
    var previousIds = Object.keys(state.byId);
    state.seed = data.seed || {};
    state.candidates = data.candidates || [];
    state.byId = {};
    state.childrenOf = { root: [] };

    state.candidates.forEach(function (c) { state.byId[c.id] = c; });
    state.candidates.forEach(function (c) {
      var parent = c.refined_from_candidate_id;
      var key = (parent && state.byId[parent]) ? parent : "root";
      if (!state.childrenOf[key]) state.childrenOf[key] = [];
      state.childrenOf[key].push(c);
    });
    Object.keys(state.childrenOf).forEach(function (k) {
      state.childrenOf[k].sort(function (a, b) { return (a.generated_at || "").localeCompare(b.generated_at || ""); });
    });

    var newIds = state.candidates.map(function (c) { return c.id; }).filter(function (id) { return previousIds.indexOf(id) === -1; });

    computeLayout();
    renderSeedSummary();
    renderStats();
    render(newIds);
    if (emptyState) emptyState.classList.add("hide");
    var pill = document.getElementById("seed-pill");
    if (pill) {
      pill.classList.add("show");
      document.getElementById("seed-pill-text").textContent = (state.seed.domain || "seed") + " — " + (state.seed.id || "");
    }
    if (previousIds.length === 0) fitToScreen();
    if (onSeedLoaded) onSeedLoaded(state.seed);
  }

  function computeLayout() {
    var order = 0;
    var totalLeaves = 0;

    function countLeaves(id) {
      var kids = state.childrenOf[id] || [];
      if (kids.length === 0) { totalLeaves += 1; return 1; }
      var sum = 0;
      kids.forEach(function (k) { sum += countLeaves(k.id); });
      return sum;
    }
    countLeaves("root");
    if (totalLeaves === 0) totalLeaves = 1;

    function assign(id) {
      var kids = state.childrenOf[id] || [];
      if (kids.length === 0) {
        var angle = (order + 0.5) / totalLeaves * Math.PI * 2;
        order += 1;
        return angle;
      }
      var angles = kids.map(function (k) { return assign(k.id); });
      var avg = angles.reduce(function (a, b) { return a + b; }, 0) / angles.length;
      kids.forEach(function (k, i) {
        var c = k;
        var radius = CENTER_MARGIN + (c.iteration || 1) * RING_GAP;
        var a = angles[i] - Math.PI / 2;
        state.positions[c.id] = { x: Math.cos(a) * radius, y: Math.sin(a) * radius, angle: angles[i], radius: radius };
      });
      return avg;
    }
    assign("root");
  }

  function maxIteration() {
    var m = 1;
    state.candidates.forEach(function (c) { if ((c.iteration || 1) > m) m = c.iteration; });
    return m;
  }

  // ---------- Rendering ----------
  var NS = "http://www.w3.org/2000/svg";
  function el(tag, attrs) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function scoreColor(score) {
    var s = Math.max(0, Math.min(1, score || 0));
    var from = [139, 133, 120], to = [227, 185, 77];
    var mix = from.map(function (v, i) { return Math.round(v + (to[i] - v) * s); });
    return "rgb(" + mix.join(",") + ")";
  }

  function nodeRadius(score) { return 5 + Math.max(0, Math.min(1, score || 0)) * 9; }

  function borderStyle(c) {
    if (c.duplicate_status === "EXACT_DUPLICATE") return { dash: "3,3", color: "var(--danger)" };
    if (c.duplicate_status === "NEAR_DUPLICATE") return { dash: "3,3", color: "var(--gold)" };
    if (c.parse_status === "failed") return { dash: "1,3", color: "var(--muted)" };
    return { dash: "none", color: "var(--ink-soft)" };
  }

  function isFiltered(c) {
    var f = state.filters;
    if ((c.path_score || 0) < f.minScore) return true;
    if (f.dimDupes && c.duplicate_status === "EXACT_DUPLICATE") return true;
    if (f.dimDead && c.exploration_signal === "dead_end") return true;
    if (f.dimFailed && c.parse_status === "failed") return true;
    return false;
  }

  function matchesSearch(c) {
    if (!state.filters.search) return true;
    var q = state.filters.search.toLowerCase();
    return (c.core_claim || "").toLowerCase().indexOf(q) !== -1;
  }

  function lineageIds(id) {
    var chain = [];
    var cur = state.byId[id];
    var seen = {};
    while (cur && !seen[cur.id]) {
      seen[cur.id] = true;
      chain.push(cur.id);
      cur = cur.refined_from_candidate_id ? state.byId[cur.refined_from_candidate_id] : null;
    }
    return chain;
  }

  function render(newIds) {
    newIds = newIds || [];
    while (viewport.firstChild) viewport.removeChild(viewport.firstChild);
    if (!state.seed) return;

    var ringsGroup = el("g", { id: "rings" });
    var edgesGroup = el("g", { id: "edges" });
    var nodesGroup = el("g", { id: "nodes" });
    viewport.appendChild(ringsGroup);
    viewport.appendChild(edgesGroup);
    viewport.appendChild(nodesGroup);

    var maxIt = maxIteration();
    for (var i = 1; i <= maxIt; i++) {
      var r = CENTER_MARGIN + i * RING_GAP;
      ringsGroup.appendChild(el("circle", { class: "ring", cx: 0, cy: 0, r: r }));
      var label = el("text", { class: "ring-label", x: 4, y: -r - 4 });
      label.textContent = "iteration " + i;
      ringsGroup.appendChild(label);
    }

    var highlightChain = state.selectedId ? lineageIds(state.selectedId) : [];
    var highlightSet = {};
    highlightChain.forEach(function (id) { highlightSet[id] = true; });

    state.candidates.forEach(function (c) {
      var pos = state.positions[c.id];
      if (!pos) return;
      var parentPos = c.refined_from_candidate_id ? state.positions[c.refined_from_candidate_id] : { x: 0, y: 0 };
      var lit = highlightSet[c.id] && (c.refined_from_candidate_id ? highlightSet[c.refined_from_candidate_id] : true);
      var dim = (highlightChain.length > 0 && !lit);
      var path = el("path", { class: "edge" + (lit ? " lit" : "") + (dim ? " dim" : ""), d: curvePath(parentPos.x, parentPos.y, pos.x, pos.y) });
      edgesGroup.appendChild(path);
    });

    var root = el("g", { class: "root-node" });
    root.appendChild(el("circle", { cx: 0, cy: 0, r: 20 }));
    var rt = el("text", { x: 0, y: 4, "text-anchor": "middle" });
    rt.textContent = "SEED";
    root.appendChild(rt);
    nodesGroup.appendChild(root);

    state.candidates.forEach(function (c) {
      var pos = state.positions[c.id];
      if (!pos) return;
      var g = el("g", { class: "node", "data-id": c.id, transform: "translate(" + pos.x + "," + pos.y + ")" });
      if (isFiltered(c)) g.classList.add("dim");
      if (!matchesSearch(c)) g.classList.add("dim");
      if (state.selectedId === c.id) g.classList.add("selected");
      if (state.filters.search && matchesSearch(c) && state.filters.search.length > 1) g.classList.add("match");
      if (newIds.indexOf(c.id) !== -1) g.classList.add("pulsing");

      var border = borderStyle(c);
      var r = nodeRadius(c.path_score);
      var leanStatus = leanNodeStatus(c);
      // Fill still encodes path_score (existing signal, still useful); the
      // border encodes Lean verification status when present, since that's
      // ground truth from an actual proof assistant -- a stronger, more
      // trustworthy signal than the miner's own self-rated score, and it
      // should read as visually distinct at a glance, not buried in a
      // corner glyph. See the "lean-pending/-proven/-failed" CSS rules for
      // the actual color/width -- kept there rather than inline so they
      // interact correctly with .node.selected's own border override.
      if (leanStatus) g.classList.add("lean-" + leanStatus.status);
      g.appendChild(el("circle", { class: "halo", cx: 0, cy: 0, r: r + 8, fill: scoreColor(c.path_score) }));
      var core = el("circle", { class: "core", cx: 0, cy: 0, r: r, fill: scoreColor(c.path_score), stroke: border.color, "stroke-dasharray": border.dash });
      g.appendChild(core);

      if (c.exploration_signal === "branch_point") {
        var star = el("text", { class: "glyph", x: r + 2, y: -r, fill: "var(--gold)" });
        star.textContent = "✦";
        g.appendChild(star);
      }
      if (c.numeric_check_result === true) {
        var ok = el("text", { class: "glyph", x: -r - 8, y: -r, fill: "var(--success)" });
        ok.textContent = "✓";
        g.appendChild(ok);
      } else if (c.numeric_check_result === false) {
        var bad = el("text", { class: "glyph", x: -r - 8, y: -r, fill: "var(--danger)" });
        bad.textContent = "✕";
        g.appendChild(bad);
      }
      if (c.tool_invocations && c.tool_invocations.length > 0) {
        var toolGlyph = el("text", {
          class: "glyph tool-glyph", x: 0, y: r + 11, "text-anchor": "middle", fill: "var(--focus)",
        });
        toolGlyph.textContent = "⚙" + (c.tool_invocations.length > 1 ? " " + c.tool_invocations.length : "");
        g.appendChild(toolGlyph);
      }
      if (leanStatus) {
        var leanGlyph = el("text", {
          class: "glyph lean-glyph", x: 0, y: -r - 10, "text-anchor": "middle", fill: leanStatus.color,
        });
        leanGlyph.textContent = "⊢";
        var leanTitle = el("title", {});
        leanTitle.textContent = "Lean check: " + leanStatus.label;
        leanGlyph.appendChild(leanTitle);
        g.appendChild(leanGlyph);
      }

      if (onReseed) {
        var badgeOffset = r + 16;
        var reseedIcon = el("g", {
          class: "reseed-icon", transform: "translate(" + badgeOffset + "," + badgeOffset + ")",
        });
        var reseedTitle = el("title", {});
        reseedTitle.textContent = "Reseed from this candidate";
        reseedIcon.appendChild(reseedTitle);
        // Generous invisible hit area, separate from the small visible dot --
        // precise clicks on an 8px SVG shape are unreliable, especially when
        // zoomed out on a dense map, and this must never overlap the node's
        // own hit area (see the halo pointer-events fix below this node loop).
        reseedIcon.appendChild(el("circle", { class: "reseed-icon-hit", r: 12 }));
        reseedIcon.appendChild(el("circle", { class: "reseed-icon-bg", r: 8 }));
        var reseedPlus = el("text", { class: "reseed-icon-glyph", "text-anchor": "middle", y: 3 });
        reseedPlus.textContent = "+";
        reseedIcon.appendChild(reseedPlus);
        reseedIcon.addEventListener("click", function (e) {
          e.stopPropagation();
          openReseedModal(c);
        });
        g.appendChild(reseedIcon);
      }

      g.addEventListener("click", function () { selectNode(c.id); });
      nodesGroup.appendChild(g);
    });
  }

  function curvePath(x1, y1, x2, y2) {
    var mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    return "M " + x1 + " " + y1 + " Q " + mx + " " + my + " " + x2 + " " + y2;
  }

  // ---------- Selection + detail panel ----------
  function selectNode(id) { state.selectedId = id; render(); openDetail(state.byId[id]); }

  function claimTypeLabel(t) { return { construction: "Construction", asymptotic: "Asymptotic", structural: "Structural" }[t] || (t || "unclassified"); }

  function renderSkillsUsed(invocations) {
    if (!invocations || !invocations.length) return "";
    var items = invocations.map(function (inv) {
      var statusChip = inv.timed_out
        ? '<span class="chip fail">timed out</span>'
        : (inv.exit_code === 0 ? '<span class="chip pass">exit 0</span>' : '<span class="chip fail">exit ' + inv.exit_code + '</span>');
      return (
        '<div class="skill-invocation">' +
          '<div class="skill-invocation-head">' +
            '<span class="chip gold">' + esc(inv.skill_name) + '</span>' +
            statusChip +
            '<span class="chip">' + (inv.duration_ms || 0) + ' ms</span>' +
          '</div>' +
          '<div class="mono-block">' + esc(truncate(inv.input_text || "", 400)) + '</div>' +
          (inv.stdout ? '<div class="mono-block">' + esc(truncate(inv.stdout, 400)) + '</div>' : "") +
          (inv.stderr ? '<div class="mono-block" style="color:var(--danger)">' + esc(truncate(inv.stderr, 300)) + '</div>' : "") +
        '</div>'
      );
    }).join("");
    return '<section><h4>Skills used (' + invocations.length + ')</h4>' + items + '</section>';
  }

  function renderLeanChecks(verifications) {
    if (!verifications || !verifications.length) return "";
    var items = verifications.map(function (lv) {
      var label = leanResultLabel(lv.result);
      return (
        '<div class="skill-invocation">' +
          '<div class="skill-invocation-head">' +
            '<span class="chip ' + label.cls + '">' + esc(label.text) + '</span>' +
            '<span class="chip">translation attempt ' + (lv.translation_attempt_number || 1) + '</span>' +
            (lv.execution_time_ms ? '<span class="chip">' + lv.execution_time_ms + ' ms</span>' : '') +
          '</div>' +
          (lv.lean_code ? '<div class="mono-block">' + esc(lv.lean_code) + '</div>' : '') +
          (lv.lean_stdout ? '<div class="mono-block">' + esc(truncate(lv.lean_stdout, 800)) + '</div>' : '') +
          (lv.lean_stderr ? '<div class="mono-block" style="color:var(--danger)">' + esc(truncate(lv.lean_stderr, 400)) + '</div>' : '') +
        '</div>'
      );
    }).join("");
    var heading = "Lean triviality check" + (verifications.length > 1 ? "s (" + verifications.length + ")" : "");
    return '<section><h4>' + heading + '</h4>' + items + '</section>';
  }

  function renderDiscoveryBadge(c) {
    if (!c.discovery) return "";
    var ds = discoveryStatus(c);
    return (
      '<section class="discovery-banner"><h4>Discovery — deep verification</h4>' +
      '<div class="chips">' +
        '<span class="chip ' + ds.cls + '">' + esc(ds.text) + '</span>' +
        (c.discovery.human_reviewed
          ? '<span class="chip pass">human reviewed' + (c.discovery.human_verdict ? ": " + esc(c.discovery.human_verdict) : "") + '</span>'
          : '<span class="chip">awaiting human review</span>') +
        '<span class="chip">flagged ' + esc((c.discovery.created_at || "").slice(0, 16).replace("T", " ")) + '</span>' +
      '</div>' +
      (c.discovery.human_notes ? '<p>' + esc(c.discovery.human_notes) + '</p>' : '') +
      '</section>'
    );
  }

  function openDetail(c) {
    bodyGrid.classList.add("panel-open");
    var score = c.path_score || 0;
    var numericBlock = "";
    if (c.numeric_check) {
      numericBlock =
        '<section><h4>Numeric check &mdash; ' +
        (c.numeric_check_result === true ? '<span class="chip pass">passed</span>' :
         c.numeric_check_result === false ? '<span class="chip fail">failed</span>' : '<span class="chip">n/a</span>') +
        '</h4><div class="mono-block">' + esc(JSON.stringify(c.numeric_check, null, 2)) + '</div></section>';
    }
    var connections = (c.connections || []).map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("");
    var lineageChain = lineageIds(c.id);
    var skillsBlock = renderSkillsUsed(c.tool_invocations);
    var leanBlock = renderLeanChecks(c.lean_verifications);
    var discoveryBlock = renderDiscoveryBadge(c);
    var leanStatus = leanNodeStatus(c);

    detailPanel.innerHTML =
      '<button class="detail-close" id="detail-close" aria-label="Close">&times;</button>' +
      '<h3>' + esc(truncate(c.core_claim || "(no claim &mdash; parse failed)", 220)) + '</h3>' +
      '<div class="chips">' +
        '<span class="chip gold">' + claimTypeLabel(c.claim_type) + '</span>' +
        '<span class="chip">iteration ' + (c.iteration || 1) + '</span>' +
        '<span class="chip">' + esc(c.prompt_variant || "") + '</span>' +
        '<span class="chip">temp ' + c.temperature + '</span>' +
        '<span class="chip">' + esc(c.reasoning_effort || "") + '</span>' +
        '<span class="chip">' + esc(c.duplicate_status || "unscored") + '</span>' +
        '<span class="chip">' + esc(c.exploration_signal || "") + '</span>' +
        (c.tool_invocations && c.tool_invocations.length
          ? '<span class="chip gold">⚙ ' + c.tool_invocations.length + ' skill call' + (c.tool_invocations.length > 1 ? "s" : "") + '</span>'
          : "") +
        (leanStatus ? '<span class="chip" style="border-color:' + leanStatus.color + ';color:' + leanStatus.color + '">⊢ ' + esc(leanStatus.label) + '</span>' : "") +
      '</div>' +
      '<div class="score-bar-wrap">' +
        '<div class="num">' + score.toFixed(2) + ' <span style="font-size:0.6rem;color:var(--muted);font-family:var(--font-body)">path score</span></div>' +
        '<div class="score-bar-track"><div class="score-bar-fill" style="width:' + (score * 100) + '%"></div></div>' +
      '</div>' +
      discoveryBlock +
      (c.steering_hint ? '<section><h4>Steering hint</h4><p>' + esc(c.steering_hint) + '</p></section>' : '') +
      '<section><h4>Formal statement</h4><div class="mono-block">' + esc(c.formal_statement || "—") + '</div></section>' +
      '<section><h4>Intuition</h4><p>' + esc(c.intuition || "—") + '</p></section>' +
      (connections ? '<section><h4>Connections</h4><ul class="connection-list">' + connections + '</ul></section>' : '') +
      '<section><h4>Identified weaknesses</h4><p>' + esc(c.identified_weaknesses || "—") + '</p></section>' +
      numericBlock +
      leanBlock +
      skillsBlock +
      '<section><h4>Self-rated</h4><div class="chips"><span class="chip">novelty ' + c.self_rated_novelty + '</span><span class="chip">rigor ' + c.self_rated_rigor + '</span><span class="chip">tokens ' + (c.input_tokens||0) + '→' + (c.output_tokens||0) + '</span></div></section>' +
      (lineageChain.length > 1 ? '<button class="btn ghost lineage-btn" id="show-lineage">Highlight full lineage (' + lineageChain.length + ')</button>' : '') +
      (onReseed ? '<button class="btn primary reseed-open-btn" id="open-reseed-modal">Reseed from this candidate</button>' : '') +
      '<section><h4>Prompt sent</h4><div class="mono-block">' + esc(truncate(c.prompt_text || "", 1600)) + '</div></section>' +
      '<section><h4>Raw response</h4><div class="mono-block">' + esc(truncate(c.raw_text || "", 1600)) + '</div></section>';

    document.getElementById("detail-close").addEventListener("click", closeDetail);
    var lineageBtn = document.getElementById("show-lineage");
    if (lineageBtn) lineageBtn.addEventListener("click", function () { render(); });
    var openReseedBtn = document.getElementById("open-reseed-modal");
    if (openReseedBtn) openReseedBtn.addEventListener("click", function () { openReseedModal(c); });
  }

  // ---------- Reseed modal ----------
  var reseedModal, reseedModalChips, reseedModalFormal, reseedModalClaim, reseedModalSuggestions;

  function initReseedModal() {
    reseedModal = document.getElementById("reseed-modal");
    if (!reseedModal) return;
    reseedModalChips = document.getElementById("reseed-modal-chips");
    reseedModalFormal = document.getElementById("reseed-modal-formal");
    reseedModalClaim = document.getElementById("reseed-modal-claim");
    reseedModalSuggestions = document.getElementById("reseed-modal-suggestions");

    document.getElementById("reseed-modal-close").addEventListener("click", closeReseedModal);
    reseedModal.addEventListener("click", function (e) { if (e.target === reseedModal) closeReseedModal(); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !reseedModal.hidden) closeReseedModal();
    });
  }

  function openReseedModal(c) {
    if (!onReseed || !reseedModal) return;
    var domain = (state.seed && state.seed.domain) || c.domain || "mathematics";
    var suggested = c.suggested_seeds || [];

    reseedModalChips.innerHTML =
      '<span class="chip gold">' + claimTypeLabel(c.claim_type) + '</span>' +
      '<span class="chip">iteration ' + (c.iteration || 1) + '</span>' +
      '<span class="chip">' + esc(domain) + '</span>' +
      '<span class="chip">' + esc(c.duplicate_status || "unscored") + '</span>' +
      '<span class="chip">path score ' + (c.path_score || 0).toFixed(2) + '</span>';
    reseedModalFormal.textContent = c.formal_statement || "—";
    reseedModalClaim.textContent = c.core_claim || "(no claim — parse failed)";

    reseedModalSuggestions.innerHTML = suggested.map(function (txt, i) {
      return '<button type="button" class="btn ghost reseed-suggestion" data-idx="' + i + '">' + esc(truncate(txt, 90)) + '</button>';
    }).join("");
    var textEl = document.getElementById("reseed-text");
    var suggestionBtns = reseedModalSuggestions.querySelectorAll(".reseed-suggestion");
    for (var i = 0; i < suggestionBtns.length; i++) {
      (function (btn) {
        btn.addEventListener("click", function () {
          textEl.value = suggested[parseInt(btn.getAttribute("data-idx"), 10)];
        });
      })(suggestionBtns[i]);
    }

    textEl.value = c.formal_statement || c.core_claim || "";
    var domainEl = document.getElementById("reseed-domain");
    domainEl.value = domain;
    var statusEl = document.getElementById("reseed-status");
    statusEl.textContent = "";
    statusEl.className = "reseed-status";

    document.getElementById("reseed-submit").onclick = function () {
      var text = textEl.value.trim();
      var chosenDomain = domainEl.value;
      if (!text) {
        statusEl.textContent = "Seed text is required.";
        statusEl.className = "reseed-status error";
        return;
      }
      statusEl.textContent = "Submitting…";
      statusEl.className = "reseed-status";
      onReseed(c.id, text, chosenDomain).then(function (result) {
        statusEl.textContent = "Reseeded → new seed " + result.seed_id + " (chain depth " + result.chain_depth + ")" +
          (result.duplicate_classification && result.duplicate_classification !== "NOVEL"
            ? " — " + result.duplicate_classification : "");
        statusEl.className = "reseed-status success";
      }).catch(function (err) {
        statusEl.textContent = "Failed: " + (err && err.message ? err.message : err);
        statusEl.className = "reseed-status error";
      });
    };

    reseedModal.hidden = false;
    requestAnimationFrame(function () { textEl.focus(); });
  }

  function closeReseedModal() {
    if (reseedModal) reseedModal.hidden = true;
  }

  function closeDetail() {
    if (bodyGrid) bodyGrid.classList.remove("panel-open");
    state.selectedId = null;
    if (state.seed) render();
  }

  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function truncate(s, n) { return s.length > n ? s.slice(0, n) + "…" : s; }

  // ---------- Seed summary + stats ----------
  function renderSeedSummary() {
    var s = state.seed;
    var elS = document.getElementById("seed-summary");
    if (!elS) return;
    elS.innerHTML = '<div class="seed-card"><div class="domain">' + esc(s.domain || "") + ' &middot; ' + esc(s.type || "") + '</div><p>' + esc(s.text || "") + '</p></div>';
  }

  function renderStats() {
    var cs = state.candidates;
    var n = cs.length;
    var avg = n ? (cs.reduce(function (a, c) { return a + (c.path_score || 0); }, 0) / n) : 0;
    var novel = cs.filter(function (c) { return c.duplicate_status === "NOVEL"; }).length;
    var branch = cs.filter(function (c) { return c.exploration_signal === "branch_point"; }).length;
    var passed = cs.filter(function (c) { return c.numeric_check_result === true; }).length;
    var maxIt = maxIteration();
    var stats = [[n, "Candidates"], [avg.toFixed(2), "Avg. path score"], [maxIt, "Iterations"], [novel, "Novel"], [branch, "Branch points"], [passed, "Checks passed"]];
    var elStats = document.getElementById("stats");
    if (elStats) elStats.innerHTML = stats.map(function (s) { return '<div class="stat"><div class="n">' + s[0] + '</div><div class="l">' + s[1] + '</div></div>'; }).join("");
  }

  // ---------- Pan / zoom ----------
  function applyView() {
    if (!viewport) return;
    viewport.setAttribute("transform", "translate(" + state.view.tx + "," + state.view.ty + ") scale(" + state.view.scale + ")");
  }

  function fitToScreen() {
    var rect = svg.getBoundingClientRect();
    var maxR = CENTER_MARGIN + maxIteration() * RING_GAP + 30;
    var scale = Math.min(rect.width, rect.height) / (maxR * 2.2);
    state.view.scale = Math.max(0.15, Math.min(1.4, scale || 1));
    state.view.tx = rect.width / 2;
    state.view.ty = rect.height / 2;
    applyView();
  }

  return { init: init, loadData: loadData, clear: clear, fitToScreen: fitToScreen };
})();
