/* IT Opportunity Finder — dashboard logic.
   Reads opportunities.json (written daily by the GitHub Action) and renders a
   filterable, sortable table. No framework, no backend, no login. */

(function () {
  "use strict";

  var STATE = { all: [], view: [] };

  // --- DOM helpers ---------------------------------------------------------
  var $ = function (id) { return document.getElementById(id); };
  function esc(s) {
    return (s == null ? "" : String(s)).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function daysUntil(iso) {
    if (!iso) return null;
    var d = new Date(iso + "T00:00:00");
    if (isNaN(d)) return null;
    return Math.ceil((d - new Date()) / 86400000);
  }

  // --- Load ----------------------------------------------------------------
  // Cache-bust so the browser always shows the freshest daily file.
  fetch("opportunities.json?t=" + Date.now())
    .then(function (r) { return r.json(); })
    .then(init)
    .catch(function (e) {
      $("count").textContent = "Could not load opportunities.json — has the daily run committed it yet?";
      console.error(e);
    });

  function init(data) {
    STATE.all = data.opportunities || [];

    // Header meta + health line.
    var gen = data.generated_at ? new Date(data.generated_at) : null;
    $("meta").textContent = gen ? "Updated " + gen.toLocaleString() : "";
    var h = data.health || {};
    var health = "Sources fetched: " + (h.ok || 0) + "/" + (h.total || 0) + " OK";
    if (h.sources) {
      var failed = h.sources.filter(function (s) { return !s.ok; })
        .map(function (s) { return s.source; });
      if (failed.length) health += " · issues: " + failed.join(", ");
    }
    $("health").textContent = health;

    populateSelect("state", uniq(STATE.all.map(function (o) { return o.state; })));
    populateSelect("source", uniq(STATE.all.map(function (o) { return o.source_name; })));
    populateSelect("agency", uniq(STATE.all.map(function (o) { return o.agency; })));

    // Wire up controls — any change re-renders.
    ["q", "state", "source", "agency", "sort", "soon", "newonly", "minscore"]
      .forEach(function (id) {
        $(id).addEventListener("input", render);
        $(id).addEventListener("change", render);
      });
    $("minscore").addEventListener("input", function () {
      $("minscoreval").textContent = $("minscore").value;
    });

    render();
  }

  function uniq(arr) {
    return Array.from(new Set(arr.filter(Boolean))).sort();
  }
  function populateSelect(id, values) {
    var sel = $(id);
    values.forEach(function (v) {
      var o = document.createElement("option");
      o.value = v; o.textContent = v;
      sel.appendChild(o);
    });
  }

  // --- Filter + sort + render ---------------------------------------------
  function render() {
    var q = $("q").value.trim().toLowerCase();
    var state = $("state").value;
    var source = $("source").value;
    var agency = $("agency").value;
    var soon = $("soon").checked;
    var newonly = $("newonly").checked;
    var minscore = parseInt($("minscore").value, 10) || 0;
    var sort = $("sort").value;

    var rows = STATE.all.filter(function (o) {
      if (state && o.state !== state) return false;
      if (source && o.source_name !== source) return false;
      if (agency && o.agency !== agency) return false;
      if (newonly && !o.is_new) return false;
      if ((o.it_score || 0) < minscore) return false;
      if (soon) {
        var d = daysUntil(o.due_date);
        if (d === null || d < 0 || d > 14) return false;
      }
      if (q) {
        var hay = (o.title + " " + o.description + " " + o.agency + " " +
          o.source_name + " " + o.naics + " " + o.psc).toLowerCase();
        if (hay.indexOf(q) === -1) return false;
      }
      return true;
    });

    rows.sort(function (a, b) {
      if (sort === "deadline") {
        return (a.due_date || "9999") .localeCompare(b.due_date || "9999");
      }
      if (sort === "posted") {
        return (b.posted_date || "").localeCompare(a.posted_date || "");
      }
      return (b.it_score || 0) - (a.it_score || 0); // score
    });

    STATE.view = rows;
    draw(rows);
  }

  function draw(rows) {
    var tbody = $("rows");
    tbody.innerHTML = "";
    $("empty").hidden = rows.length > 0;
    $("table").style.display = rows.length ? "" : "none";
    $("count").textContent = rows.length + " of " + STATE.all.length + " opportunities";

    var frag = document.createDocumentFragment();
    rows.forEach(function (o) {
      var tr = document.createElement("tr");
      var d = daysUntil(o.due_date);
      var soonBadge = (d !== null && d >= 0 && d <= 14)
        ? '<span class="badge soon">' + d + 'd</span>' : "";
      var newBadge = o.is_new ? '<span class="badge new">NEW</span>' : "";
      var code = esc(o.naics || o.psc || "—");
      var link = o.url ? esc(o.url) : "#";

      tr.innerHTML =
        '<td class="title-cell" data-label="Title">' +
          '<a href="' + link + '" target="_blank" rel="noopener">' + esc(o.title) + '</a>' +
          newBadge + soonBadge +
          '<div class="desc">' + esc(o.description || "") + '</div>' +
        '</td>' +
        '<td data-label="State">' + esc(o.state || "—") + '</td>' +
        '<td data-label="Source">' + esc(o.source_name) + '</td>' +
        '<td data-label="Code">' + code + '</td>' +
        '<td data-label="Set-aside">' + esc(o.set_aside || "—") + '</td>' +
        '<td data-label="Posted">' + esc(o.posted_date || "—") + '</td>' +
        '<td data-label="Due">' + esc(o.due_date || "—") + '</td>' +
        '<td data-label="Score" class="score">' + (o.it_score || 0) + '</td>';
      frag.appendChild(tr);
    });
    tbody.appendChild(frag);
  }
})();
