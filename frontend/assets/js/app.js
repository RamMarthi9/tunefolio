const API_BASE = window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost"
    ? "http://127.0.0.1:8000"
    : "";

/* ========================================
   Fetch helpers
======================================== */

async function fetchHoldings() {
  const res = await fetch(`${API_BASE}/portfolio/holdings`);
  if (!res.ok) throw new Error("Failed to load holdings");
  return res.json();
}

async function fetchSectorAllocation() {
  const res = await fetch(`${API_BASE}/portfolio/sector-allocation`);
  if (!res.ok) throw new Error("Failed to load sector allocation");
  return res.json();
}

async function logoutZerodha() {
  try {
    const res = await fetch(`${API_BASE}/auth/zerodha/logout`, { method: "POST" });
    if (!res.ok) throw new Error("Logout failed");
    // Redirect to login after logout
    window.location.href = `${API_BASE}/auth/zerodha/login`;
  } catch (err) {
    console.error("Logout error:", err);
  }
}

/* ========================================
   Utilities
======================================== */

function formatINR(value = 0) {
  return "\u20B9" + Number(value).toLocaleString("en-IN", {
    maximumFractionDigits: 2
  });
}

function generateColors(n) {
  return Array.from({ length: n }, (_, i) =>
    `hsl(${(i * 360) / n}, 65%, 55%)`
  );
}

function hslToHsla(hsl, alpha) {
  return hsl.replace("hsl(", "hsla(").replace(")", `, ${alpha})`);
}

/* ========================================
   Global State
======================================== */

const chartRegistry = {};

let holdingsData = [];
let sectorAllocData = null;
let currentSort = { key: null, dir: "asc" };
let dropdownsInitialized = false;

const globalFilter = {
  sectors: [],
  stocks: []
};

const pnlState = { dimension: "sector" };
const valueState = { dimension: "sector" };
const nestedPieState = { metric: "current" };

/* ========================================
   Global Filter Helpers
======================================== */

function isFilterActive() {
  return globalFilter.sectors.length > 0 || globalFilter.stocks.length > 0;
}

// OR logic: show holdings matching ANY selected sector OR ANY selected stock
function getGloballyFilteredHoldings() {
  if (!isFilterActive()) return [...holdingsData];

  return holdingsData.filter(h => {
    const matchesSector = globalFilter.sectors.length > 0 &&
      globalFilter.sectors.includes(h.sector);
    const matchesStock = globalFilter.stocks.length > 0 &&
      globalFilter.stocks.includes(h.symbol);

    // OR logic: if only sectors selected, filter by sector
    // if only stocks selected, filter by stock
    // if both selected, match either
    if (globalFilter.sectors.length > 0 && globalFilter.stocks.length > 0) {
      return matchesSector || matchesStock;
    }
    if (globalFilter.sectors.length > 0) return matchesSector;
    if (globalFilter.stocks.length > 0) return matchesStock;
    return true;
  });
}

function getGloballyFilteredSectorAlloc() {
  if (!sectorAllocData) return { by_current_value: [], by_invested_value: [] };
  if (!isFilterActive()) return sectorAllocData;

  // Rebuild sector aggregation from filtered holdings
  const filtered = getGloballyFilteredHoldings();
  const sectorMap = {};

  filtered.forEach(h => {
    const sec = h.sector || "Unknown";
    if (!sectorMap[sec]) sectorMap[sec] = { invested: 0, current: 0, pnl: 0 };
    sectorMap[sec].invested += Number(h.invested_value || 0);
    sectorMap[sec].current += Number(h.current_value || 0);
    sectorMap[sec].pnl += Number(h.pnl || 0);
  });

  const totalCurrent = Object.values(sectorMap).reduce((s, v) => s + v.current, 0) || 1;
  const totalInvested = Object.values(sectorMap).reduce((s, v) => s + v.invested, 0) || 1;

  return {
    by_current_value: Object.entries(sectorMap).map(([sector, v]) => ({
      sector,
      value: Math.round(v.current * 100) / 100,
      percentage: Math.round((v.current / totalCurrent) * 10000) / 100,
      profit: Math.round(v.pnl * 100) / 100
    })),
    by_invested_value: Object.entries(sectorMap).map(([sector, v]) => ({
      sector,
      value: Math.round(v.invested * 100) / 100,
      percentage: Math.round((v.invested / totalInvested) * 10000) / 100
    }))
  };
}

function toggleGlobalSectorFilter(sector) {
  if (!sector) return;
  const idx = globalFilter.sectors.indexOf(sector);
  if (idx === -1) {
    globalFilter.sectors.push(sector);
  } else {
    globalFilter.sectors.splice(idx, 1);
  }
  applyGlobalFilter();
}

function toggleGlobalStockFilter(stock) {
  if (!stock) return;
  const idx = globalFilter.stocks.indexOf(stock);
  if (idx === -1) {
    globalFilter.stocks.push(stock);
  } else {
    globalFilter.stocks.splice(idx, 1);
  }
  applyGlobalFilter();
}

function clearAllFilters() {
  globalFilter.sectors = [];
  globalFilter.stocks = [];
  applyGlobalFilter();
}

/* ========================================
   Nested Pie Chart (Sectors outer, Stocks inner)
======================================== */

function buildNestedPieData(metric) {
  const filtered = getGloballyFilteredHoldings();
  if (filtered.length === 0) return null;

  const measure = metric === "current" ? "current_value" : "invested_value";

  // Group stocks by sector
  const sectorMap = {};
  filtered.forEach(h => {
    const sec = h.sector || "Unknown";
    if (!sectorMap[sec]) sectorMap[sec] = { total: 0, stocks: [] };
    const val = Number(h[measure] || 0);
    sectorMap[sec].total += val;
    sectorMap[sec].stocks.push({ symbol: h.symbol, value: val });
  });

  // Sort sectors by value descending
  const sectors = Object.entries(sectorMap)
    .map(([sector, v]) => ({ sector, total: v.total, stocks: v.stocks.sort((a, b) => b.value - a.value) }))
    .sort((a, b) => b.total - a.total);

  const grandTotal = sectors.reduce((s, sec) => s + sec.total, 0) || 1;

  // Build outer ring (sectors) and inner ring (stocks)
  const outerLabels = [];
  const outerValues = [];
  const outerColors = [];
  const innerLabels = [];
  const innerValues = [];
  const innerColors = [];
  const sectorColorMap = {};

  const sectorBaseColors = generateColors(sectors.length);

  sectors.forEach((sec, si) => {
    const baseColor = sectorBaseColors[si];
    sectorColorMap[sec.sector] = baseColor;

    // Dim if filtered and not selected
    const dimmed = globalFilter.sectors.length > 0 && !globalFilter.sectors.includes(sec.sector);

    outerLabels.push(sec.sector);
    outerValues.push(sec.total);
    outerColors.push(dimmed ? hslToHsla(baseColor, 0.2) : baseColor);

    // Generate stock colors as lighter variants of sector color
    sec.stocks.forEach((stock, j) => {
      const lightness = 45 + (j * 12) % 40; // Vary lightness for stocks
      const hueMatch = baseColor.match(/hsl\((\d+)/);
      const hue = hueMatch ? parseInt(hueMatch[1]) : (si * 40);
      const stockColor = `hsl(${hue}, 55%, ${lightness}%)`;

      innerLabels.push(stock.symbol);
      innerValues.push(stock.value);
      innerColors.push(dimmed ? hslToHsla(stockColor, 0.2) : stockColor);
    });
  });

  return {
    grandTotal,
    sectors,
    sectorColorMap,
    outer: { labels: outerLabels, values: outerValues, colors: outerColors },
    inner: { labels: innerLabels, values: innerValues, colors: innerColors }
  };
}

function renderNestedPie(metric) {
  const canvasId = "nestedPieChart";
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  if (chartRegistry[canvasId]) {
    chartRegistry[canvasId].destroy();
    delete chartRegistry[canvasId];
  }

  const pieData = buildNestedPieData(metric);
  if (!pieData) return;

  const { grandTotal, sectors, sectorColorMap, outer, inner } = pieData;

  // Build legend
  renderNestedPieLegend(sectors, sectorColorMap, grandTotal);

  chartRegistry[canvasId] = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: outer.labels,
      datasets: [
        {
          // Outer ring — Sectors
          label: "Sectors",
          data: outer.values,
          backgroundColor: outer.colors,
          borderWidth: 2,
          borderColor: "#ffffff",
          weight: 1
        },
        {
          // Inner ring — Stocks
          label: "Stocks",
          data: inner.values,
          backgroundColor: inner.colors,
          borderWidth: 1,
          borderColor: "#ffffff",
          weight: 1
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      cutout: "25%",
      layout: { padding: 4 },
      onClick: (event, elements) => {
        if (elements.length === 0) return;
        const dsIndex = elements[0].datasetIndex;
        const idx = elements[0].index;

        if (dsIndex === 0) {
          // Clicked outer ring (sector)
          toggleGlobalSectorFilter(outer.labels[idx]);
        } else {
          // Clicked inner ring (stock)
          toggleGlobalStockFilter(inner.labels[idx]);
        }
      },
      plugins: {
        legend: { display: false },
        datalabels: {
          color: "#ffffff",
          font: { weight: "600", size: 9 },
          textShadowBlur: 3,
          textShadowColor: "rgba(0,0,0,0.3)",
          formatter: (value, ctx) => {
            const total = ctx.dataset.data.reduce((s, v) => s + v, 0);
            const pct = total ? ((value / total) * 100).toFixed(1) : 0;
            if (ctx.datasetIndex === 0) {
              // Outer ring: show sector name + pct for large slices
              return pct >= 8 ? `${outer.labels[ctx.dataIndex]}\n${pct}%` : (pct >= 4 ? `${pct}%` : "");
            } else {
              // Inner ring: show stock name for large slices
              return pct >= 6 ? `${inner.labels[ctx.dataIndex]}` : "";
            }
          }
        },
        tooltip: {
          callbacks: {
            title: (tooltipItems) => {
              const item = tooltipItems[0];
              return item.datasetIndex === 0 ? "Sector" : "Stock";
            },
            label: (ctx) => {
              const label = ctx.datasetIndex === 0
                ? outer.labels[ctx.dataIndex]
                : inner.labels[ctx.dataIndex];
              const val = ctx.parsed;
              const total = ctx.dataset.data.reduce((s, v) => s + v, 0);
              const pct = total ? ((val / total) * 100).toFixed(1) : 0;
              return `${label}: \u20B9${val.toLocaleString("en-IN")} (${pct}%)`;
            }
          }
        }
      }
    },
    plugins: [ChartDataLabels]
  });
}

function renderNestedPieLegend(sectors, sectorColorMap, grandTotal) {
  const container = document.getElementById("nestedPieLegend");
  if (!container) return;

  let html = "";
  sectors.forEach(sec => {
    const pct = grandTotal ? ((sec.total / grandTotal) * 100).toFixed(1) : 0;
    const color = sectorColorMap[sec.sector] || "#ccc";
    const dimmed = globalFilter.sectors.length > 0 && !globalFilter.sectors.includes(sec.sector);
    const opacity = dimmed ? "0.35" : "1";

    html += `<div class="nested-legend-item" style="opacity:${opacity}" data-sector="${sec.sector}">`;
    html += `<span class="nested-legend-swatch" style="background:${color}"></span>`;
    html += `<span class="nested-legend-label">${sec.sector}</span>`;
    html += `<span class="nested-legend-pct">${pct}%</span>`;
    html += `</div>`;
  });

  container.innerHTML = html;

  // Make legend items clickable for sector filtering
  container.querySelectorAll(".nested-legend-item").forEach(item => {
    item.addEventListener("click", () => {
      const sec = item.dataset.sector;
      if (sec) toggleGlobalSectorFilter(sec);
    });
  });
}

function refreshNestedPie() {
  renderNestedPie(nestedPieState.metric);
}

/* ========================================
   P&L Bar Chart Rendering (Sector/Stock + auto drill-down)
======================================== */

function buildPnlDataset(dimension) {
  // Auto drill-down: if exactly 1 sector selected, show stocks within it
  if (globalFilter.sectors.length === 1 && dimension === "sector") {
    const selectedSector = globalFilter.sectors[0];
    const stocksInSector = holdingsData.filter(h => h.sector === selectedSector);
    if (stocksInSector.length > 0) {
      return {
        isDrilldown: true,
        data: stocksInSector.map(h => ({
          label: h.symbol,
          pnl: Number(h.pnl || 0)
        })).sort((a, b) => b.pnl - a.pnl)
      };
    }
  }

  if (dimension === "stock") {
    const filtered = getGloballyFilteredHoldings();
    return {
      isDrilldown: false,
      data: filtered.map(h => ({
        label: h.symbol,
        pnl: Number(h.pnl || 0)
      })).sort((a, b) => b.pnl - a.pnl)
    };
  }

  // Sector level from sector alloc data
  const filteredAlloc = getGloballyFilteredSectorAlloc();
  if (!filteredAlloc.by_current_value) return { isDrilldown: false, data: [] };

  return {
    isDrilldown: false,
    data: filteredAlloc.by_current_value.map(d => ({
      label: d.sector,
      pnl: d.profit
    })).sort((a, b) => b.pnl - a.pnl)
  };
}

function renderPnlBar(canvasId, dimension) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const { isDrilldown, data: dataset } = buildPnlDataset(dimension);
  if (dataset.length === 0) {
    if (chartRegistry[canvasId]) { chartRegistry[canvasId].destroy(); delete chartRegistry[canvasId]; }
    return;
  }

  if (chartRegistry[canvasId]) { chartRegistry[canvasId].destroy(); }

  const labels = dataset.map(d => d.label);
  const values = dataset.map(d => d.pnl);
  const colors = values.map((v, i) => {
    const base = v >= 0 ? "#16a34a" : "#dc2626";
    if (!isDrilldown && dimension === "sector" && globalFilter.sectors.length > 0 &&
        !globalFilter.sectors.includes(dataset[i].label)) {
      return base + "40";
    }
    if (dimension === "stock" && globalFilter.stocks.length > 0 &&
        !globalFilter.stocks.includes(dataset[i].label)) {
      return base + "40";
    }
    return base;
  });

  // Dynamic height
  const boxEl = canvas.parentElement;
  if (boxEl) {
    const dynamicH = Math.max(200, dataset.length * 22);
    boxEl.style.height = dynamicH + "px";
  }

  chartRegistry[canvasId] = new Chart(canvas, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderRadius: 3,
        barThickness: dimension === "stock" || isDrilldown ? 10 : 14
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      layout: { padding: 0 },
      indexAxis: "y",
      onClick: (event, elements) => {
        if (elements.length === 0) return;
        const idx = elements[0].index;
        const clickedLabel = labels[idx];
        if (isDrilldown || dimension === "stock") {
          toggleGlobalStockFilter(clickedLabel);
        } else {
          toggleGlobalSectorFilter(clickedLabel);
        }
      },
      scales: {
        x: {
          grid: { color: "rgba(0,0,0,0.06)" },
          ticks: {
            color: "#64748b",
            font: { size: 9 },
            callback: (v) => "\u20B9" + Number(v).toLocaleString("en-IN")
          }
        },
        y: {
          grid: { display: false },
          ticks: {
            color: "#334155",
            font: { size: dimension === "stock" || isDrilldown ? 8 : 9 }
          }
        }
      },
      plugins: {
        legend: { display: false },
        datalabels: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => `P&L: \u20B9${ctx.parsed.x.toLocaleString("en-IN")}`
          }
        }
      }
    },
    plugins: [ChartDataLabels]
  });
}

function refreshPnlChart() {
  renderPnlBar("sectorPnlChart", pnlState.dimension);
}

/* ========================================
   Value Compare Grouped Bar Chart (Invested + Current)
======================================== */

function buildValueCompareDataset(dimension) {
  // Auto drill-down: if exactly 1 sector selected, show stocks within it
  if (globalFilter.sectors.length === 1 && dimension === "sector") {
    const selectedSector = globalFilter.sectors[0];
    const stocksInSector = holdingsData.filter(h => h.sector === selectedSector);
    if (stocksInSector.length > 0) {
      return {
        isDrilldown: true,
        data: stocksInSector.map(h => ({
          label: h.symbol,
          invested: Number(h.invested_value || 0),
          current: Number(h.current_value || 0)
        })).sort((a, b) => b.current - a.current)
      };
    }
  }

  if (dimension === "stock") {
    const filtered = getGloballyFilteredHoldings();
    return {
      isDrilldown: false,
      data: filtered.map(h => ({
        label: h.symbol,
        invested: Number(h.invested_value || 0),
        current: Number(h.current_value || 0)
      })).sort((a, b) => b.current - a.current)
    };
  }

  // Sector-level
  const filteredAlloc = getGloballyFilteredSectorAlloc();
  if (!filteredAlloc.by_current_value) return { isDrilldown: false, data: [] };

  const investedMap = {};
  (filteredAlloc.by_invested_value || []).forEach(d => { investedMap[d.sector] = d.value; });

  return {
    isDrilldown: false,
    data: filteredAlloc.by_current_value.map(d => ({
      label: d.sector,
      invested: investedMap[d.sector] || 0,
      current: d.value
    })).sort((a, b) => b.current - a.current)
  };
}

function renderValueCompareBar(canvasId, dimension) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const { isDrilldown, data: dataset } = buildValueCompareDataset(dimension);
  if (dataset.length === 0) {
    if (chartRegistry[canvasId]) { chartRegistry[canvasId].destroy(); delete chartRegistry[canvasId]; }
    return;
  }

  if (chartRegistry[canvasId]) { chartRegistry[canvasId].destroy(); }

  const labels = dataset.map(d => d.label);
  const investedValues = dataset.map(d => d.invested);
  const currentValues = dataset.map(d => d.current);
  const isStock = dimension === "stock" || isDrilldown;

  // Dimming helpers
  function dimColor(base, label) {
    if (!isDrilldown && dimension === "sector" && globalFilter.sectors.length > 0 &&
        !globalFilter.sectors.includes(label)) return base + "40";
    if (dimension === "stock" && globalFilter.stocks.length > 0 &&
        !globalFilter.stocks.includes(label)) return base + "40";
    return base;
  }

  const investedColors = dataset.map(d => dimColor("#6366f1", d.label));
  const currentColors = dataset.map(d => dimColor("#22d3ee", d.label));

  // Dynamic height
  const boxEl = canvas.parentElement;
  if (boxEl) {
    const dynamicH = Math.max(260, dataset.length * 28);
    boxEl.style.height = dynamicH + "px";
  }

  chartRegistry[canvasId] = new Chart(canvas, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Invested",
          data: investedValues,
          backgroundColor: investedColors,
          borderRadius: 3,
          barThickness: isStock ? 8 : 12
        },
        {
          label: "Current",
          data: currentValues,
          backgroundColor: currentColors,
          borderRadius: 3,
          barThickness: isStock ? 8 : 12
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      layout: { padding: 0 },
      indexAxis: "y",
      onClick: (event, elements) => {
        if (elements.length === 0) return;
        const idx = elements[0].index;
        const clickedLabel = labels[idx];
        if (isDrilldown || dimension === "stock") {
          toggleGlobalStockFilter(clickedLabel);
        } else {
          toggleGlobalSectorFilter(clickedLabel);
        }
      },
      scales: {
        x: {
          grid: { color: "rgba(0,0,0,0.06)" },
          ticks: {
            color: "#64748b",
            font: { size: 9 },
            callback: (v) => "\u20B9" + Number(v).toLocaleString("en-IN")
          }
        },
        y: {
          grid: { display: false },
          ticks: {
            color: "#334155",
            font: { size: isStock ? 8 : 9 }
          }
        }
      },
      plugins: {
        legend: {
          display: true,
          position: "top",
          labels: { color: "#334155", font: { size: 10 }, boxWidth: 12 }
        },
        datalabels: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              return `${ctx.dataset.label}: \u20B9${ctx.parsed.x.toLocaleString("en-IN")}`;
            }
          }
        }
      }
    },
    plugins: [ChartDataLabels]
  });
}

function refreshValueCompare() {
  renderValueCompareBar("valueCompareChart", valueState.dimension);
}

/* ========================================
   Multi-Select Dropdown Component
======================================== */

function createMultiSelect(containerId, items, onChangeCallback) {
  const container = document.getElementById(containerId);
  if (!container) return;

  const display = container.querySelector(".multi-select-display");
  const dropdown = container.querySelector(".multi-select-dropdown");

  // Build dropdown: search + checkbox options
  const sorted = [...items].sort();
  let html = '<input type="text" class="multi-select-search" placeholder="Search\u2026" />';
  sorted.forEach(item => {
    html += `
      <label class="multi-select-option" data-value="${item}">
        <input type="checkbox" value="${item}" />
        <span>${item}</span>
      </label>`;
  });
  dropdown.innerHTML = html;

  const searchInput = dropdown.querySelector(".multi-select-search");

  // Toggle open/close
  display.addEventListener("click", (e) => {
    e.stopPropagation();
    // Close other open dropdowns
    document.querySelectorAll(".multi-select.open").forEach(ms => {
      if (ms !== container) ms.classList.remove("open");
    });
    container.classList.toggle("open");
    if (container.classList.contains("open")) {
      searchInput.value = "";
      searchInput.focus();
      // Reset search visibility
      dropdown.querySelectorAll(".multi-select-option").forEach(opt => {
        opt.style.display = "flex";
      });
    }
  });

  // Search within dropdown
  searchInput.addEventListener("input", () => {
    const q = searchInput.value.toLowerCase();
    dropdown.querySelectorAll(".multi-select-option").forEach(opt => {
      opt.style.display = opt.dataset.value.toLowerCase().includes(q) ? "flex" : "none";
    });
  });

  searchInput.addEventListener("click", (e) => e.stopPropagation());

  // Checkbox change
  dropdown.querySelectorAll("input[type='checkbox']").forEach(cb => {
    cb.addEventListener("change", (e) => {
      e.stopPropagation();
      const selected = getMultiSelectValues(containerId);
      onChangeCallback(selected);
    });
  });

  // Prevent dropdown clicks from bubbling
  dropdown.addEventListener("click", (e) => e.stopPropagation());
}

function getMultiSelectValues(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return [];
  const checked = container.querySelectorAll(".multi-select-dropdown input[type='checkbox']:checked");
  return Array.from(checked).map(cb => cb.value);
}

function setMultiSelectValues(containerId, values) {
  const container = document.getElementById(containerId);
  if (!container) return;

  container.querySelectorAll(".multi-select-dropdown input[type='checkbox']").forEach(cb => {
    cb.checked = values.includes(cb.value);
    const opt = cb.closest(".multi-select-option");
    if (opt) opt.classList.toggle("selected", cb.checked);
  });

  updateMultiSelectDisplay(containerId, values);
}

function updateMultiSelectDisplay(containerId, selected) {
  const container = document.getElementById(containerId);
  if (!container) return;

  const display = container.querySelector(".multi-select-display");
  const placeholder = containerId === "stock-multiselect" ? "All Stocks" : "All Sectors";
  const arrowSvg = '<svg class="multi-select-arrow" width="12" height="12" viewBox="0 0 12 12"><path d="M3 5l3 3 3-3" stroke="currentColor" stroke-width="1.5" fill="none"/></svg>';

  if (selected.length === 0) {
    display.innerHTML = `<span class="multi-select-placeholder">${placeholder}</span>${arrowSvg}`;
  } else {
    const tagsHtml = selected.map(v =>
      `<span class="multi-select-tag">${v}<span class="multi-select-tag-remove" data-value="${v}">\u00D7</span></span>`
    ).join("");
    display.innerHTML = `<span class="multi-select-tags">${tagsHtml}</span>${arrowSvg}`;

    // Wire up tag removal
    display.querySelectorAll(".multi-select-tag-remove").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const val = btn.dataset.value;

        if (containerId === "stock-multiselect") {
          globalFilter.stocks = globalFilter.stocks.filter(s => s !== val);
        } else {
          globalFilter.sectors = globalFilter.sectors.filter(s => s !== val);
        }
        applyGlobalFilter();
      });
    });
  }
}

function syncDropdownsToGlobalFilter() {
  setMultiSelectValues("sector-multiselect", globalFilter.sectors);
  setMultiSelectValues("stock-multiselect", globalFilter.stocks);
}

/* ========================================
   Holdings Table Rendering
======================================== */

function renderHoldingsTable(data) {
  const tbody = document.querySelector("#holdings-table tbody");
  if (!tbody) return;

  tbody.innerHTML = "";

  data.forEach(h => {
    const invested = Number(h.invested_value || 0);
    const current = Number(h.current_value || 0);
    const pnl = Number(h.pnl || 0);
    const isNSE = h.exchange === "NSE";

    const tr = document.createElement("tr");
    tr.classList.add("holdings-row");
    const sectorLabel = h.sector || "\u2014";
    const sectorClass = h.sector
      ? "sector-pill sector-pill--clickable"
      : "sector-pill sector-unknown";

    tr.innerHTML = `
      <td class="expand-cell">
        ${isNSE ? '<button class="expand-btn" data-symbol="' + h.symbol + '" title="Show delivery volume">&#9654;</button>' : ''}
      </td>
      <td class="symbol">${h.symbol}</td>
      <td><span class="${sectorClass}" data-sector="${h.sector || ""}">${sectorLabel}</span></td>
      <td>${h.quantity}</td>
      <td>${formatINR(h.avg_buy_price)}</td>
      <td>${formatINR(h.current_price)}</td>
      <td>${formatINR(invested)}</td>
      <td>${formatINR(current)}</td>
      <td class="${pnl >= 0 ? "positive" : "negative"}">
        ${formatINR(pnl)}
      </td>
    `;

    // Make sector pill clickable for filtering
    const pill = tr.querySelector(".sector-pill--clickable");
    if (pill) {
      pill.addEventListener("click", (e) => {
        e.stopPropagation();
        const sec = pill.dataset.sector;
        if (sec) toggleGlobalSectorFilter(sec);
      });
    }

    tbody.appendChild(tr);

    // Delivery detail row (hidden by default) — only for NSE stocks
    if (isNSE) {
      const detailTr = document.createElement("tr");
      detailTr.classList.add("delivery-detail-row", "hidden");
      detailTr.id = `delivery-row-${h.symbol}`;
      detailTr.innerHTML = `
        <td colspan="9" class="delivery-chart-cell">
          <div class="delivery-chart-wrapper">
            <div class="delivery-chart-header">
              <h4>Delivery Volume: ${h.symbol}</h4>
              <div class="toggle-group" id="period-toggle-${h.symbol}">
                <button class="toggle-btn" data-period="3m">3M</button>
                <button class="toggle-btn" data-period="6m">6M</button>
                <button class="toggle-btn active" data-period="1y">1Y</button>
              </div>
            </div>
            <div class="delivery-chart-box">
              <canvas id="deliveryChart-${h.symbol}"></canvas>
            </div>
            <div class="delivery-loading" id="delivery-loading-${h.symbol}">Loading delivery data...</div>
          </div>
        </td>
      `;
      tbody.appendChild(detailTr);

      // Wire expand button
      const expandBtn = tr.querySelector(".expand-btn");
      expandBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleDeliveryRow(h.symbol, expandBtn);
      });

      // Wire period toggles
      detailTr.querySelectorAll(".toggle-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const period = btn.dataset.period;
          detailTr.querySelectorAll(".toggle-btn").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
          loadDeliveryChart(h.symbol, period);
        });
      });
    }
  });
}

/* ========================================
   Sorting & Filtering Pipeline
======================================== */

function getFilteredAndSorted() {
  let data = getGloballyFilteredHoldings();

  if (currentSort.key) {
    const key = currentSort.key;
    data.sort((a, b) => {
      let valA = a[key];
      let valB = b[key];

      if (key === "sector" || key === "symbol") {
        valA = (valA || "zzz").toLowerCase();
        valB = (valB || "zzz").toLowerCase();
        return currentSort.dir === "asc"
          ? valA.localeCompare(valB)
          : valB.localeCompare(valA);
      }

      valA = Number(valA || 0);
      valB = Number(valB || 0);
      return currentSort.dir === "asc" ? valA - valB : valB - valA;
    });
  }

  return data;
}

function sortHoldings(key) {
  if (currentSort.key === key) {
    currentSort.dir = currentSort.dir === "asc" ? "desc" : "asc";
  } else {
    currentSort.key = key;
    currentSort.dir = (key === "sector" || key === "symbol") ? "asc" : "desc";
  }

  renderHoldingsTable(getFilteredAndSorted());

  // Update header icons
  document.querySelectorAll("#holdings-table .sortable").forEach(th => {
    th.classList.remove("sort-asc", "sort-desc");
    if (th.dataset.sort === key) {
      th.classList.add(currentSort.dir === "asc" ? "sort-asc" : "sort-desc");
    }
  });
}

/* ========================================
   Central Filter Pipeline
======================================== */

function applyGlobalFilter() {
  // 1. Sync dropdowns
  syncDropdownsToGlobalFilter();

  // 2. Recalculate KPIs from filtered holdings
  const filtered = getGloballyFilteredHoldings();
  let totalInvested = 0;
  let totalCurrent = 0;

  filtered.forEach(h => {
    totalInvested += Number(h.invested_value || 0);
    totalCurrent += Number(h.current_value || 0);
  });

  const totalPnl = totalCurrent - totalInvested;

  document.getElementById("kpi-invested").innerText = formatINR(totalInvested);
  document.getElementById("kpi-current").innerText = formatINR(totalCurrent);

  const pnlEl = document.getElementById("kpi-pnl");
  pnlEl.innerText = formatINR(totalPnl);
  pnlEl.className = "value " + (totalPnl >= 0 ? "positive" : "negative");

  const active = isFilterActive();
  document.getElementById("holdings-count").innerText = active
    ? `${filtered.length} of ${holdingsData.length} stocks`
    : `${holdingsData.length} stocks`;

  // 3. Clear filters bar
  const clearBar = document.getElementById("clearFiltersBar");
  const clearLabel = document.getElementById("clearFiltersLabel");
  if (clearBar) {
    clearBar.classList.toggle("visible", active);
    if (active) {
      const parts = [];
      if (globalFilter.sectors.length > 0)
        parts.push(`Sectors: ${globalFilter.sectors.join(", ")}`);
      if (globalFilter.stocks.length > 0)
        parts.push(`Stocks: ${globalFilter.stocks.join(", ")}`);
      clearLabel.textContent = parts.join("  |  ");
    }
  }

  // 4. Re-render holdings table
  renderHoldingsTable(getFilteredAndSorted());

  // 5. Re-render all charts
  if (sectorAllocData || holdingsData.length > 0) {
    // Nested pie: sectors outer, stocks inner
    refreshNestedPie();

    // P&L bar: supports sector/stock + auto drill-down
    refreshPnlChart();
  }

  // 6. Value compare grouped bar
  refreshValueCompare();
}

/* ========================================
   Initial Data Loaders
======================================== */

async function renderHoldings() {
  console.log("renderHoldings called");

  try {
    const res = await fetchHoldings();

    if (!res || !Array.isArray(res.data)) {
      throw new Error("Invalid holdings response shape");
    }

    holdingsData = res.data;
    renderHoldingsTable(holdingsData);

    /* -------- KPI UPDATE -------- */
    let totalInvested = 0;
    let totalCurrent = 0;

    holdingsData.forEach(h => {
      totalInvested += Number(h.invested_value || 0);
      totalCurrent += Number(h.current_value || 0);
    });

    const totalPnl = totalCurrent - totalInvested;

    document.getElementById("kpi-invested").innerText = formatINR(totalInvested);
    document.getElementById("kpi-current").innerText = formatINR(totalCurrent);

    const pnlEl = document.getElementById("kpi-pnl");
    pnlEl.innerText = formatINR(totalPnl);
    pnlEl.className = "value " + (totalPnl >= 0 ? "positive" : "negative");

    document.getElementById("last-sync").innerText =
      "Last sync: " + new Date().toLocaleTimeString();

    document.getElementById("holdings-count").innerText =
      `${res.count} stocks`;

    // Populate multi-select dropdowns (once)
    if (!dropdownsInitialized) {
      const uniqueStocks = [...new Set(holdingsData.map(h => h.symbol))].filter(Boolean);
      const uniqueSectors = [...new Set(holdingsData.map(h => h.sector))].filter(Boolean);

      createMultiSelect("stock-multiselect", uniqueStocks, (selected) => {
        globalFilter.stocks = selected;
        applyGlobalFilter();
      });

      createMultiSelect("sector-multiselect", uniqueSectors, (selected) => {
        globalFilter.sectors = selected;
        applyGlobalFilter();
      });

      dropdownsInitialized = true;
    }

    // Refresh charts if sector data already loaded
    if (sectorAllocData) {
      refreshPnlChart();
      refreshValueCompare();
    }

    console.log("KPIs updated successfully");

  } catch (err) {
    console.error("Holdings error:", err);
  }
}

async function renderSectorAllocation() {
  try {
    const data = await fetchSectorAllocation();
    sectorAllocData = data;

    // Render nested pie (uses holdingsData for stock-level breakdown)
    refreshNestedPie();

    refreshPnlChart();
    refreshValueCompare();

  } catch (err) {
    console.error("Sector allocation error:", err);
  }
}

/* ========================================
   Delivery Volume Chart
======================================== */

const deliveryCache = {};

async function fetchDeliveryData(symbol, period = "1y") {
  const cacheKey = `${symbol}_${period}`;
  if (deliveryCache[cacheKey]) return deliveryCache[cacheKey];

  const res = await fetch(`${API_BASE}/portfolio/delivery-data?symbol=${symbol}&period=${period}`);
  if (!res.ok) throw new Error(`Failed to fetch delivery data for ${symbol}`);
  const json = await res.json();
  deliveryCache[cacheKey] = json.data;
  return json.data;
}

function toggleDeliveryRow(symbol, expandBtn) {
  const detailRow = document.getElementById(`delivery-row-${symbol}`);
  if (!detailRow) return;

  const isHidden = detailRow.classList.contains("hidden");
  detailRow.classList.toggle("hidden");
  expandBtn.classList.toggle("expanded", isHidden);

  if (isHidden) {
    loadDeliveryChart(symbol, "1y");
  }
}

async function loadDeliveryChart(symbol, period) {
  const loadingEl = document.getElementById(`delivery-loading-${symbol}`);
  const canvasId = `deliveryChart-${symbol}`;
  const periodLabel = period === "1y" ? "1 year" : period === "6m" ? "6 months" : "3 months";

  if (loadingEl) {
    loadingEl.textContent = "Loading delivery data...";
    loadingEl.style.display = "block";
  }

  try {
    const data = await fetchDeliveryData(symbol, period);
    if (loadingEl) loadingEl.style.display = "none";

    if (!data || data.length === 0) {
      // Destroy any existing chart
      if (chartRegistry[canvasId]) {
        chartRegistry[canvasId].destroy();
        delete chartRegistry[canvasId];
      }
      if (loadingEl) {
        loadingEl.textContent = `Data not available for last ${periodLabel}`;
        loadingEl.style.display = "block";
      }
      return;
    }

    renderDeliveryChart(canvasId, data, symbol);
  } catch (err) {
    console.error(`Delivery data error for ${symbol}:`, err);
    if (loadingEl) {
      loadingEl.textContent = `Data not available for last ${periodLabel}`;
      loadingEl.style.display = "block";
    }
  }
}

function renderDeliveryChart(canvasId, data, symbol) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  if (chartRegistry[canvasId]) {
    chartRegistry[canvasId].destroy();
  }

  const labels = data.map(d => d.date);
  const deliveredQty = data.map(d => d.delivered_qty);
  const notDeliveredQty = data.map(d => d.not_delivered_qty);

  // Color per bar based on price direction:
  // Green day (price up): dark green delivered, light green settled
  // Red day (price down): red delivered, light red settled
  const deliveredColors = data.map(d =>
    d.price_up ? "#15803d" : "#dc2626"       // dark green / red
  );
  const settledColors = data.map(d =>
    d.price_up ? "#86efac" : "#fca5a5"       // light green / light red
  );

  chartRegistry[canvasId] = new Chart(canvas, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Delivered",
          data: deliveredQty,
          backgroundColor: deliveredColors,
          borderWidth: 0
        },
        {
          label: "Settled (Not Delivered)",
          data: notDeliveredQty,
          backgroundColor: settledColors,
          borderWidth: 0
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      scales: {
        x: {
          stacked: true,
          grid: { color: "rgba(0,0,0,0.06)" },
          ticks: {
            color: "#64748b",
            font: { size: 8 },
            maxRotation: 45,
            maxTicksLimit: 30
          }
        },
        y: {
          stacked: true,
          grid: { color: "rgba(0,0,0,0.06)" },
          ticks: {
            color: "#64748b",
            font: { size: 9 },
            callback: (v) => v >= 1000000
              ? (v / 1000000).toFixed(1) + "M"
              : v >= 1000
                ? (v / 1000).toFixed(0) + "K"
                : v
          }
        }
      },
      plugins: {
        legend: {
          display: true,
          labels: {
            color: "#334155",
            font: { size: 10 },
            generateLabels: () => [
              { text: "Delivered (price up)", fillStyle: "#15803d", strokeStyle: "transparent", lineWidth: 0 },
              { text: "Settled (price up)", fillStyle: "#86efac", strokeStyle: "transparent", lineWidth: 0 },
              { text: "Delivered (price down)", fillStyle: "#dc2626", strokeStyle: "transparent", lineWidth: 0 },
              { text: "Settled (price down)", fillStyle: "#fca5a5", strokeStyle: "transparent", lineWidth: 0 }
            ]
          }
        },
        datalabels: { display: false },
        tooltip: {
          callbacks: {
            title: (tooltipItems) => {
              const idx = tooltipItems[0].dataIndex;
              const dir = data[idx].price_up ? "\u25B2 Up" : "\u25BC Down";
              return `${labels[idx]}  (${dir})`;
            },
            label: (ctx) => {
              const idx = ctx.dataIndex;
              const total = (deliveredQty[idx] || 0) + (notDeliveredQty[idx] || 0);
              const pct = total > 0 ? ((ctx.parsed.y / total) * 100).toFixed(1) : 0;
              return `${ctx.dataset.label}: ${ctx.parsed.y.toLocaleString("en-IN")} (${pct}%)`;
            },
            afterBody: (tooltipItems) => {
              const idx = tooltipItems[0].dataIndex;
              const total = (deliveredQty[idx] || 0) + (notDeliveredQty[idx] || 0);
              return `Total traded: ${total.toLocaleString("en-IN")}`;
            }
          }
        }
      }
    },
    plugins: [ChartDataLabels]
  });
}

/* ========================================
   Bootstrap (ORDER MATTERS)
======================================== */

document.addEventListener("DOMContentLoaded", async () => {
  // Detect auth success redirect (?status=connected)
  const params = new URLSearchParams(window.location.search);
  if (params.get("status") === "connected") {
    const statusEl = document.getElementById("connection-status");
    if (statusEl) {
      statusEl.textContent = "\u25cf Just connected to Zerodha";
      statusEl.style.color = "#16a34a";
    }
    // Clean up URL (remove query param without page reload)
    window.history.replaceState({}, "", window.location.pathname);
  } else {
    // Check for active session — redirect to Zerodha login if none
    try {
      const sessionRes = await fetch(`${API_BASE}/session/active`);
      if (!sessionRes.ok) {
        window.location.href = `${API_BASE}/auth/zerodha/login`;
        return; // Stop bootstrap — page is redirecting
      }
    } catch (err) {
      console.error("Session check failed:", err);
      window.location.href = `${API_BASE}/auth/zerodha/login`;
      return;
    }
  }

  renderHoldings();
  renderSectorAllocation();

  // Collapsible toggles
  document.querySelectorAll(".card-header--toggle").forEach(toggle => {
    const panel = toggle.nextElementSibling;
    if (panel && panel.classList.contains("collapsible")) {
      toggle.addEventListener("click", () => {
        toggle.classList.toggle("collapsed");
        panel.classList.toggle("collapsed");
      });
    }
  });

  // Nested pie: Current/Invested metric toggle
  document.querySelectorAll("#nestedPieToggle .toggle-btn").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      nestedPieState.metric = btn.dataset.metric;
      document.querySelectorAll("#nestedPieToggle .toggle-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      refreshNestedPie();
    });
  });

  // P&L chart: Sector/Stock dimension toggle
  document.querySelectorAll("#pnlDimToggle .toggle-btn").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      pnlState.dimension = btn.dataset.dim;
      document.querySelectorAll("#pnlDimToggle .toggle-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      refreshPnlChart();
    });
  });

  // Value compare chart: Sector/Stock dimension toggle
  document.querySelectorAll("#valueDimToggle .toggle-btn").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      valueState.dimension = btn.dataset.dim;
      document.querySelectorAll("#valueDimToggle .toggle-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      refreshValueCompare();
    });
  });

  // Sortable column headers
  document.querySelectorAll("#holdings-table .sortable").forEach(th => {
    th.addEventListener("click", (e) => {
      e.stopPropagation();
      sortHoldings(th.dataset.sort);
    });
  });

  // Clear all filters button
  const clearBtn = document.getElementById("clearFiltersBtn");
  if (clearBtn) {
    clearBtn.addEventListener("click", clearAllFilters);
  }

  // Logout button
  const logoutBtn = document.getElementById("logoutBtn");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", logoutZerodha);
  }

  // Close dropdowns on outside click
  document.addEventListener("click", () => {
    document.querySelectorAll(".multi-select.open").forEach(ms => {
      ms.classList.remove("open");
    });
  });
});
