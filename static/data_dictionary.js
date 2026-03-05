(function () {
  "use strict";

  const esc = (s) =>
    String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  let DATA = { excel_fields: [], derived_fields: [] };
  let activeFilter = "all";

  // ── Renderizado ─────────────────────────────────────────────────────

  function sheetTag(sheet) {
    const label = { Sheet1: "Hoja 1", Sheet2: "Hoja 2", Both: "Ambas" }[sheet] || sheet;
    const cls = sheet === "Sheet1" ? "tag-sheet1" : sheet === "Sheet2" ? "tag-sheet2" : "tag-both";
    return `<span class="tag ${cls}">${esc(label)}</span>`;
  }

  function storedTag(stored) {
    return stored
      ? '<span class="tag tag-yes">Si</span>'
      : '<span class="tag tag-no">No</span>';
  }

  function storageTag(type) {
    const labels = {
      "as-is": "tal cual",
      transformed: "transformado",
      renamed: "renombrado",
      derived: "derivado",
    };
    const cls = {
      "as-is": "tag-as-is",
      transformed: "tag-transformed",
      renamed: "tag-renamed",
      derived: "tag-derived",
    }[type] || "";
    return `<span class="tag ${cls}">${esc(labels[type] || type)}</span>`;
  }

  function commentCell(f) {
    const meta = f.comment_updated_by
      ? `<span class="comment-meta">Ultimo: ${esc(f.comment_updated_by)} ${esc(f.comment_updated_at || "")}</span>`
      : "";
    return `<div class="comment-cell">
      <textarea class="comment-textarea" data-key="${esc(f.field_key)}" placeholder="Agregar comentario de validacion...">${esc(f.comment || "")}</textarea>
      <span class="comment-saved" id="saved-${esc(f.field_key)}">Guardado</span>
      ${meta}
    </div>`;
  }

  function renderExcelRow(f) {
    return `<tr data-sheet="${esc(f.sheet)}" data-key="${esc(f.field_key)}" data-has-comment="${f.comment ? "1" : "0"}">
      <td class="col-excel">${esc(f.excel_column)}</td>
      <td class="col-sheet">${sheetTag(f.sheet)}</td>
      <td class="col-stored">${storedTag(f.stored)}</td>
      <td class="col-internal">${f.stored ? esc(f.internal_name) : "\u2014"}</td>
      <td class="col-type">${f.stored ? storageTag(f.storage_type) : "\u2014"}</td>
      <td class="col-transform">${esc(f.transform_notes || "")}</td>
      <td class="col-desc">${esc(f.description || "")}</td>
      <td class="col-comment">${commentCell(f)}</td>
    </tr>`;
  }

  function renderDerivedRow(f) {
    return `<tr data-sheet="derived" data-key="${esc(f.field_key)}" data-has-comment="${f.comment ? "1" : "0"}">
      <td class="col-internal">${esc(f.internal_name)}</td>
      <td>${esc(f.source)}</td>
      <td class="col-transform">${esc(f.transform_notes || "")}</td>
      <td class="col-desc">${esc(f.description || "")}</td>
      <td class="col-comment">${commentCell(f)}</td>
    </tr>`;
  }

  function applyFilters() {
    const q = document.getElementById("searchBox").value.toLowerCase().trim();

    let visibleExcel = 0;
    document.querySelectorAll("#excelBody tr").forEach((tr) => {
      const sheet = tr.dataset.sheet;
      const hasComment = tr.dataset.hasComment === "1";
      const text = tr.textContent.toLowerCase();

      let show = true;
      if (activeFilter === "sheet1") show = sheet === "Sheet1";
      else if (activeFilter === "sheet2") show = sheet === "Sheet2";
      else if (activeFilter === "both") show = sheet === "Both";
      else if (activeFilter === "derived") show = false;
      else if (activeFilter === "commented") show = hasComment;

      if (show && q) show = text.includes(q);
      tr.style.display = show ? "" : "none";
      if (show) visibleExcel++;
    });

    let visibleDerived = 0;
    document.querySelectorAll("#derivedBody tr").forEach((tr) => {
      const hasComment = tr.dataset.hasComment === "1";
      const text = tr.textContent.toLowerCase();

      let show = true;
      if (["sheet1", "sheet2", "both"].includes(activeFilter)) show = false;
      else if (activeFilter === "commented") show = hasComment;

      if (show && q) show = text.includes(q);
      tr.style.display = show ? "" : "none";
      if (show) visibleDerived++;
    });

    document.getElementById("excelCount").textContent = visibleExcel;
    document.getElementById("derivedCount").textContent = visibleDerived;

    document.getElementById("excelTitle").style.display =
      activeFilter === "derived" ? "none" : "";
    document.getElementById("excelTitle").nextElementSibling.style.display =
      activeFilter === "derived" ? "none" : "";
    document.getElementById("derivedTitle").style.display =
      ["sheet1", "sheet2", "both"].includes(activeFilter) ? "none" : "";
    document.getElementById("derivedTitle").nextElementSibling.style.display =
      ["sheet1", "sheet2", "both"].includes(activeFilter) ? "none" : "";
  }

  function render() {
    document.getElementById("excelBody").innerHTML = DATA.excel_fields
      .map(renderExcelRow)
      .join("");
    document.getElementById("derivedBody").innerHTML = DATA.derived_fields
      .map(renderDerivedRow)
      .join("");
    applyFilters();
    bindCommentHandlers();
  }

  // ── Guardado de comentarios ────────────────────────────────────────

  let saveTimers = {};

  function saveComment(fieldKey, comment) {
    fetch(`/api/data-dictionary/${encodeURIComponent(fieldKey)}/comment`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ comment }),
    }).then((r) => {
      if (r.ok) {
        const el = document.getElementById(`saved-${fieldKey}`);
        if (el) {
          el.classList.add("show");
          setTimeout(() => el.classList.remove("show"), 2000);
        }
        const tr = document.querySelector(`tr[data-key="${fieldKey}"]`);
        if (tr) tr.dataset.hasComment = comment.trim() ? "1" : "0";
      }
    });
  }

  function bindCommentHandlers() {
    document.querySelectorAll(".comment-textarea").forEach((ta) => {
      ta.addEventListener("input", () => {
        const key = ta.dataset.key;
        clearTimeout(saveTimers[key]);
        saveTimers[key] = setTimeout(() => saveComment(key, ta.value), 800);
      });
      ta.addEventListener("blur", () => {
        const key = ta.dataset.key;
        clearTimeout(saveTimers[key]);
        saveComment(key, ta.value);
      });
    });
  }

  // ── Filtros ────────────────────────────────────────────────────────

  document.getElementById("filterPills").addEventListener("click", (e) => {
    const pill = e.target.closest(".pill");
    if (!pill) return;
    document.querySelectorAll("#filterPills .pill").forEach((p) => p.classList.remove("active"));
    pill.classList.add("active");
    activeFilter = pill.dataset.filter;
    applyFilters();
  });

  document.getElementById("searchBox").addEventListener("input", applyFilters);

  // ── Exportar CSV ──────────────────────────────────────────────────

  document.getElementById("exportCsv").addEventListener("click", () => {
    const rows = [
      ["Seccion", "Columna Excel", "Hoja", "Guardado", "Campo Interno", "Tipo Almacenamiento", "Notas de Transformacion", "Descripcion del Sistema", "Comentario"],
    ];
    DATA.excel_fields.forEach((f) => {
      rows.push([
        "Campo Excel",
        f.excel_column,
        f.sheet,
        f.stored ? "Si" : "No",
        f.internal_name || "",
        f.storage_type || "",
        f.transform_notes || "",
        f.description || "",
        f.comment || "",
      ]);
    });
    DATA.derived_fields.forEach((f) => {
      rows.push([
        "Derivado",
        "",
        "",
        "",
        f.internal_name,
        "derivado",
        f.transform_notes || "",
        f.description || "",
        f.comment || "",
      ]);
    });

    const csv = rows
      .map((r) => r.map((c) => '"' + String(c).replace(/"/g, '""') + '"').join(","))
      .join("\n");
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "diccionario_datos_plt.csv";
    a.click();
    URL.revokeObjectURL(url);
  });

  // ── Cargar datos ──────────────────────────────────────────────────

  fetch("/api/data-dictionary")
    .then((r) => r.json())
    .then((d) => {
      DATA = d;
      render();
    });
})();
