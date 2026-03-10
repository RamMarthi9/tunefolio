const API_BASE = window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost"
    ? "http://127.0.0.1:8000"
    : "";

/* ========================================
   Fetch helpers
======================================== */

const FETCH_OPTS = { credentials: "include" };

async function fetchRealisedPnl() {
  const res = await fetch(`${API_BASE}/portfolio/realised-pnl`, FETCH_OPTS);
  if (!res.ok) return null;  // Non-critical; degrade gracefully
  return res.json();
}

async function fetchMargins() {
  const res = await fetch(`${API_BASE}/portfolio/margins`, FETCH_OPTS);
  if (!res.ok) return null;  // Non-critical; degrade gracefully
  return res.json();
}

async function fetchHistoricalHoldings(fy = "") {
  const fyParam = fy ? `?fy=${fy}` : "";
  const res = await fetch(`${API_BASE}/portfolio/historical-holdings${fyParam}`, FETCH_OPTS);
  if (!res.ok) return null;
  return res.json();
}

async function fetchHoldings() {
  const res = await fetch(`${API_BASE}/portfolio/holdings`, FETCH_OPTS);
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      // Session expired or invalid — redirect to re-login
      window.location.href = `${API_BASE}/auth/zerodha/login`;
      throw new Error("Session expired — redirecting to login");
    }
    throw new Error(`Failed to load holdings (HTTP ${res.status})`);
  }
  return res.json();
}

// Sector allocation is now computed client-side from holdingsData
// (no separate API call needed — saves a full Zerodha round-trip)

async function logoutZerodha() {
  try {
    const res = await fetch(`${API_BASE}/auth/zerodha/logout`, {
      method: "POST", credentials: "include"
    });
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
let historicalData = [];
let sectorAllocData = null;
let currentSort = { key: null, dir: "asc" };
let historicalSort = { key: null, dir: "asc" };
let dropdownsInitialized = false;

const globalFilter = {
  sectors: [],
  stocks: []
};

const pnlState = { dimension: "sector" };
const valueState = { dimension: "sector" };
const allocState = { dimension: "sector" };

// Shared color maps — populated once from holdingsData, reused across all charts
let sharedSectorColorMap = {};  // sector → hsl color
let sharedStockColorMap = {};   // symbol → hsl color

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
   Allocation Pie Charts (Current + Invested, side by side)
======================================== */

function initSharedColorMap() {
  // Sectors: sorted alphabetically → deterministic color assignment
  const allSectors = [...new Set(holdingsData.map(h => h.sector || "Unknown"))].sort();
  const sectorColors = generateColors(allSectors.length);
  sharedSectorColorMap = {};
  allSectors.forEach((sec, i) => {
    sharedSectorColorMap[sec] = sectorColors[i];
  });

  // Stocks: derive from sector hue, lightness varies per stock within sector
  sharedStockColorMap = {};
  const sectorStocks = {};
  holdingsData.forEach(h => {
    const sec = h.sector || "Unknown";
    if (!sectorStocks[sec]) sectorStocks[sec] = [];
    if (!sectorStocks[sec].includes(h.symbol)) sectorStocks[sec].push(h.symbol);
  });
  Object.entries(sectorStocks).forEach(([sec, symbols]) => {
    const baseColor = sharedSectorColorMap[sec] || "hsl(0, 65%, 55%)";
    const hueMatch = baseColor.match(/hsl\((\d+)/);
    const hue = hueMatch ? parseInt(hueMatch[1]) : 0;
    symbols.sort().forEach((sym, j) => {
      const lightness = 40 + (j * 10) % 45;
      sharedStockColorMap[sym] = `hsl(${hue}, 60%, ${lightness}%)`;
    });
  });
}

function buildPieData(metric) {
  const dimension = allocState.dimension;
  const filtered = getGloballyFilteredHoldings();
  if (filtered.length === 0) return null;

  const measure = metric === "current" ? "current_value" : "invested_value";

  // Auto drill-down: 1 sector selected in sector mode → show stocks within it
  if (globalFilter.sectors.length === 1 && dimension === "sector") {
    const selectedSector = globalFilter.sectors[0];
    const stocks = holdingsData.filter(h => h.sector === selectedSector);
    if (stocks.length > 0) {
      const sorted = stocks
        .map(h => ({ symbol: h.symbol, value: Number(h[measure] || 0) }))
        .sort((a, b) => b.value - a.value);
      return {
        labels: sorted.map(s => s.symbol),
        values: sorted.map(s => s.value),
        colors: sorted.map(s => sharedStockColorMap[s.symbol] || "#94a3b8"),
        isStock: true
      };
    }
  }

  // Stock mode: show all individual stocks
  if (dimension === "stock") {
    const sorted = filtered
      .map(h => ({ symbol: h.symbol, value: Number(h[measure] || 0) }))
      .sort((a, b) => b.value - a.value);
    return {
      labels: sorted.map(s => s.symbol),
      values: sorted.map(s => s.value),
      colors: sorted.map(s => {
        const base = sharedStockColorMap[s.symbol] || "#94a3b8";
        const dimmed = globalFilter.stocks.length > 0 && !globalFilter.stocks.includes(s.symbol);
        return dimmed ? hslToHsla(base, 0.2) : base;
      }),
      isStock: true
    };
  }

  // Sector mode (default): group by sector
  const sectorMap = {};
  filtered.forEach(h => {
    const sec = h.sector || "Unknown";
    if (!sectorMap[sec]) sectorMap[sec] = 0;
    sectorMap[sec] += Number(h[measure] || 0);
  });

  const sectors = Object.entries(sectorMap)
    .map(([sector, value]) => ({ sector, value }))
    .sort((a, b) => b.value - a.value);

  return {
    labels: sectors.map(s => s.sector),
    values: sectors.map(s => s.value),
    colors: sectors.map(s => {
      const baseColor = sharedSectorColorMap[s.sector] || "#94a3b8";
      const dimmed = globalFilter.sectors.length > 0 && !globalFilter.sectors.includes(s.sector);
      return dimmed ? hslToHsla(baseColor, 0.2) : baseColor;
    }),
    isStock: false
  };
}

function renderAllocationPie(canvasId, metric) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  if (chartRegistry[canvasId]) {
    chartRegistry[canvasId].destroy();
    delete chartRegistry[canvasId];
  }

  const pieData = buildPieData(metric);
  if (!pieData) return;

  const { labels, values, colors, isStock } = pieData;

  // Leader lines plugin — skip labels for stock-level (too many slices)
  const leaderLinesPlugin = {
    id: "leaderLines_" + canvasId,
    afterDraw(chart) {
      const ctx = chart.ctx;
      const meta = chart.getDatasetMeta(0);
      if (!meta || !meta.data) return;

      const centerX = chart.chartArea.left + (chart.chartArea.right - chart.chartArea.left) / 2;
      const centerY = chart.chartArea.top + (chart.chartArea.bottom - chart.chartArea.top) / 2;
      const minPct = isStock ? 6 : 4;

      meta.data.forEach((arc, i) => {
        const total = values.reduce((s, v) => s + v, 0);
        const pct = total ? (values[i] / total) * 100 : 0;
        if (pct < minPct) return;

        const midAngle = (arc.startAngle + arc.endAngle) / 2;
        const outerRadius = arc.outerRadius;

        const edgeX = centerX + Math.cos(midAngle) * outerRadius;
        const edgeY = centerY + Math.sin(midAngle) * outerRadius;

        const elbowLen = 14;
        const elbowX = centerX + Math.cos(midAngle) * (outerRadius + elbowLen);
        const elbowY = centerY + Math.sin(midAngle) * (outerRadius + elbowLen);

        const tailLen = 10;
        const isRight = Math.cos(midAngle) >= 0;
        const tailX = elbowX + (isRight ? tailLen : -tailLen);

        const label = `${labels[i]} ${pct.toFixed(1)}%`;

        ctx.save();
        ctx.beginPath();
        ctx.moveTo(edgeX, edgeY);
        ctx.lineTo(elbowX, elbowY);
        ctx.lineTo(tailX, elbowY);
        ctx.strokeStyle = "#64748b";
        ctx.lineWidth = 1;
        ctx.stroke();

        ctx.font = isStock ? "600 10px system-ui, sans-serif" : "600 11px system-ui, sans-serif";
        ctx.fillStyle = "#334155";
        ctx.textBaseline = "middle";
        ctx.textAlign = isRight ? "left" : "right";
        ctx.fillText(label, tailX + (isRight ? 4 : -4), elbowY);
        ctx.restore();
      });
    }
  };

  chartRegistry[canvasId] = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderWidth: isStock ? 1 : 2,
        borderColor: "#ffffff"
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      cutout: "35%",
      layout: { padding: { top: 25, bottom: 25, left: 60, right: 60 } },
      onClick: (event, elements) => {
        if (elements.length === 0) return;
        const idx = elements[0].index;
        if (isStock) {
          toggleGlobalStockFilter(labels[idx]);
        } else {
          toggleGlobalSectorFilter(labels[idx]);
        }
      },
      plugins: {
        legend: { display: false },
        datalabels: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const val = ctx.parsed;
              const total = ctx.dataset.data.reduce((s, v) => s + v, 0);
              const pct = total ? ((val / total) * 100).toFixed(1) : 0;
              return `${labels[ctx.dataIndex]}: \u20B9${val.toLocaleString("en-IN")} (${pct}%)`;
            }
          }
        }
      }
    },
    plugins: [ChartDataLabels, leaderLinesPlugin]
  });
}

function refreshAllocationPies() {
  renderAllocationPie("allocChartCurrent", "current");
  renderAllocationPie("allocChartInvested", "invested");
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
          pnl: Number(h.pnl || 0),
          invested: Number(h.invested_value || 0)
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
        pnl: Number(h.pnl || 0),
        invested: Number(h.invested_value || 0)
      })).sort((a, b) => b.pnl - a.pnl)
    };
  }

  // Sector level from sector alloc data
  const filteredAlloc = getGloballyFilteredSectorAlloc();
  if (!filteredAlloc.by_current_value) return { isDrilldown: false, data: [] };

  // Build invested lookup from by_invested_value
  const investedMap = {};
  (filteredAlloc.by_invested_value || []).forEach(d => { investedMap[d.sector] = d.value; });

  return {
    isDrilldown: false,
    data: filteredAlloc.by_current_value.map(d => ({
      label: d.sector,
      pnl: d.profit,
      invested: investedMap[d.sector] || 0
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
  const investedValues = dataset.map(d => d.invested);
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

  // Dynamic height — slightly more room for datalabels
  const boxEl = canvas.parentElement;
  if (boxEl) {
    const dynamicH = Math.max(200, dataset.length * 24);
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
      layout: { padding: { right: 110 } },
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
            font: { size: 11 },
            callback: (v) => "\u20B9" + Number(v).toLocaleString("en-IN")
          }
        },
        y: {
          grid: { display: false },
          ticks: {
            color: "#334155",
            font: { size: dimension === "stock" || isDrilldown ? 10 : 11 }
          }
        }
      },
      plugins: {
        legend: { display: false },
        datalabels: {
          anchor: function(ctx) {
            return ctx.dataset.data[ctx.dataIndex] >= 0 ? "end" : "start";
          },
          align: function(ctx) {
            return ctx.dataset.data[ctx.dataIndex] >= 0 ? "right" : "left";
          },
          font: { size: 10, weight: "600" },
          color: function(ctx) {
            return ctx.dataset.data[ctx.dataIndex] >= 0 ? "#16a34a" : "#dc2626";
          },
          formatter: function(value, ctx) {
            const inv = investedValues[ctx.dataIndex];
            const pct = inv ? ((value / inv) * 100).toFixed(1) : "0.0";
            const sign = value >= 0 ? "+" : "";
            const rupee = "\u20B9" + Math.abs(value).toLocaleString("en-IN", { maximumFractionDigits: 0 });
            return (value >= 0 ? "+" : "-") + rupee + "  (" + sign + pct + "%)";
          }
        },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const inv = investedValues[ctx.dataIndex];
              const pct = inv ? ((ctx.parsed.x / inv) * 100).toFixed(2) : "0.00";
              const sign = ctx.parsed.x >= 0 ? "+" : "";
              return `P&L: \u20B9${ctx.parsed.x.toLocaleString("en-IN")} (${sign}${pct}%)`;
            }
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
            font: { size: 11 },
            callback: (v) => "\u20B9" + Number(v).toLocaleString("en-IN")
          }
        },
        y: {
          grid: { display: false },
          ticks: {
            color: "#334155",
            font: { size: isStock ? 10 : 11 }
          }
        }
      },
      plugins: {
        legend: {
          display: true,
          position: "top",
          labels: { color: "#334155", font: { size: 12 }, boxWidth: 12 }
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

    const tr = document.createElement("tr");
    tr.classList.add("holdings-row");
    const sectorLabel = h.sector || "\u2014";
    const sectorClass = h.sector
      ? "sector-pill sector-pill--clickable"
      : "sector-pill sector-unknown";

    tr.innerHTML = `
      <td class="expand-cell">
        <button class="expand-btn" data-symbol="${h.symbol}" title="Show delivery volume">&#9654;</button>
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
      <td>${h.num_trades || 0}</td>
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

    // Delivery detail row (hidden by default)
    {
      const detailTr = document.createElement("tr");
      detailTr.classList.add("delivery-detail-row", "hidden");
      detailTr.id = `delivery-row-${h.symbol}`;
      detailTr.innerHTML = `
        <td colspan="10" class="delivery-chart-cell">
          <div class="delivery-chart-wrapper">
            <div class="delivery-chart-header">
              <h4>${h.symbol}</h4>
              <div class="toggle-group" id="period-toggle-${h.symbol}">
                <button class="toggle-btn" data-period="3m">3M</button>
                <button class="toggle-btn" data-period="6m">6M</button>
                <button class="toggle-btn active" data-period="1y">1Y</button>
                <button class="toggle-btn" data-period="2y">2Y</button>
                <button class="toggle-btn" data-period="3y">3Y</button>
                <button class="toggle-btn" data-period="all">All</button>
              </div>
            </div>
            <div class="price-chart-box">
              <canvas id="priceChart-${h.symbol}"></canvas>
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
   Historical Holdings
======================================== */

function renderHistoricalTable(data) {
  const tbody = document.querySelector("#historical-table tbody");
  if (!tbody) return;

  tbody.innerHTML = "";

  data.forEach(h => {
    const invested = Number(h.total_invested || 0);
    const proceeds = Number(h.total_proceeds || 0);
    const pnl = Number(h.total_pnl || 0);

    const tr = document.createElement("tr");
    tr.classList.add("holdings-row");
    const sectorLabel = h.sector || "\u2014";
    const sectorClass = h.sector ? "sector-pill" : "sector-pill sector-unknown";

    tr.innerHTML = `
      <td class="expand-cell">
        <button class="expand-btn" data-symbol="${h.symbol}" title="Show delivery volume">&#9654;</button>
      </td>
      <td class="symbol">${h.symbol}</td>
      <td><span class="${sectorClass}">${sectorLabel}</span></td>
      <td>${h.total_qty_traded}</td>
      <td>${formatINR(h.avg_buy_price)}</td>
      <td>${formatINR(h.avg_sell_price)}</td>
      <td>${formatINR(invested)}</td>
      <td>${formatINR(proceeds)}</td>
      <td class="${pnl >= 0 ? "positive" : "negative"}">
        ${formatINR(pnl)}
      </td>
      <td>${h.num_trades || 0}</td>
    `;

    tbody.appendChild(tr);

    // Delivery detail row (hidden by default)
    {
      const detailTr = document.createElement("tr");
      detailTr.classList.add("delivery-detail-row", "hidden");
      detailTr.id = `delivery-row-hist-${h.symbol}`;
      detailTr.innerHTML = `
        <td colspan="10" class="delivery-chart-cell">
          <div class="delivery-chart-wrapper">
            <div class="delivery-chart-header">
              <h4>${h.symbol} (Exited)</h4>
              <div class="toggle-group" id="period-toggle-hist-${h.symbol}">
                <button class="toggle-btn" data-period="3m">3M</button>
                <button class="toggle-btn" data-period="6m">6M</button>
                <button class="toggle-btn active" data-period="1y">1Y</button>
                <button class="toggle-btn" data-period="2y">2Y</button>
                <button class="toggle-btn" data-period="3y">3Y</button>
                <button class="toggle-btn" data-period="all">All</button>
              </div>
            </div>
            <div class="price-chart-box">
              <canvas id="priceChart-hist-${h.symbol}"></canvas>
            </div>
            <div class="delivery-chart-box">
              <canvas id="deliveryChart-hist-${h.symbol}"></canvas>
            </div>
            <div class="delivery-loading" id="delivery-loading-hist-${h.symbol}">Loading delivery data...</div>
          </div>
        </td>
      `;
      tbody.appendChild(detailTr);

      // Wire expand button
      const expandBtn = tr.querySelector(".expand-btn");
      expandBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleHistoricalDeliveryRow(h.symbol, expandBtn);
      });

      // Wire period toggles
      detailTr.querySelectorAll(".toggle-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const period = btn.dataset.period;
          detailTr.querySelectorAll(".toggle-btn").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
          loadHistoricalDeliveryChart(h.symbol, period);
        });
      });
    }
  });
}

function toggleHistoricalDeliveryRow(symbol, expandBtn) {
  const detailRow = document.getElementById(`delivery-row-hist-${symbol}`);
  if (!detailRow) return;

  const isHidden = detailRow.classList.contains("hidden");
  detailRow.classList.toggle("hidden");
  expandBtn.classList.toggle("expanded", isHidden);

  if (isHidden) {
    loadHistoricalDeliveryChart(symbol, "1y");
  }
}

async function loadHistoricalDeliveryChart(symbol, period) {
  const loadingEl = document.getElementById(`delivery-loading-hist-${symbol}`);
  const deliveryCanvasId = `deliveryChart-hist-${symbol}`;
  const priceCanvasId = `priceChart-hist-${symbol}`;
  const periodLabels = {"3m":"3 months","6m":"6 months","1y":"1 year","2y":"2 years","3y":"3 years","all":"all time"};
  const periodLabel = periodLabels[period] || period;

  if (loadingEl) {
    loadingEl.textContent = "Loading delivery data...";
    loadingEl.style.display = "block";
  }

  try {
    const [data, tradeData] = await Promise.all([
      fetchDeliveryData(symbol, period),
      fetchTradesForSymbol(symbol)
    ]);
    if (loadingEl) loadingEl.style.display = "none";

    if (!data || data.length === 0) {
      [deliveryCanvasId, priceCanvasId].forEach(id => {
        if (chartRegistry[id]) { chartRegistry[id].destroy(); delete chartRegistry[id]; }
      });
      if (loadingEl) {
        loadingEl.textContent = `Data not available for last ${periodLabel}`;
        loadingEl.style.display = "block";
      }
      return;
    }

    renderPriceLineChart(priceCanvasId, data, symbol, tradeData);
    renderDeliveryChart(deliveryCanvasId, data, symbol);
  } catch (err) {
    console.error(`Delivery data error for ${symbol}:`, err);
    if (loadingEl) {
      loadingEl.textContent = `Data not available for last ${periodLabel}`;
      loadingEl.style.display = "block";
    }
  }
}

function sortHistorical(key) {
  if (historicalSort.key === key) {
    historicalSort.dir = historicalSort.dir === "asc" ? "desc" : "asc";
  } else {
    historicalSort.key = key;
    historicalSort.dir = (key === "sector" || key === "symbol") ? "asc" : "desc";
  }

  let data = [...historicalData];
  if (historicalSort.key) {
    const k = historicalSort.key;
    data.sort((a, b) => {
      let valA = a[k];
      let valB = b[k];

      if (k === "sector" || k === "symbol") {
        valA = (valA || "zzz").toLowerCase();
        valB = (valB || "zzz").toLowerCase();
        return historicalSort.dir === "asc"
          ? valA.localeCompare(valB)
          : valB.localeCompare(valA);
      }

      valA = Number(valA || 0);
      valB = Number(valB || 0);
      return historicalSort.dir === "asc" ? valA - valB : valB - valA;
    });
  }

  renderHistoricalTable(data);

  // Update header icons
  document.querySelectorAll("#historical-table .sortable").forEach(th => {
    th.classList.remove("sort-asc", "sort-desc");
    if (th.dataset.sort === historicalSort.key) {
      th.classList.add(historicalSort.dir === "asc" ? "sort-asc" : "sort-desc");
    }
  });
}

async function renderHistoricalHoldings(fy = "") {
  const tbody = document.getElementById("historical-body");
  try {
    const res = await fetchHistoricalHoldings(fy);
    if (!res || !Array.isArray(res.data)) {
      console.warn("Historical holdings: empty or invalid response", res);
      if (tbody) {
        tbody.innerHTML = `<tr><td colspan="10" class="loading" style="color:var(--muted);">
          No historical trade data available.
        </td></tr>`;
      }
      document.getElementById("historical-count").innerText = "0 stocks";
      return;
    }

    // Populate FY dropdown from available_fys
    const select = document.getElementById("fy-filter");
    if (select && res.available_fys) {
      const currentVal = select.value;
      select.innerHTML = '<option value="">All Time</option>';
      res.available_fys.slice().reverse().forEach(fyLabel => {
        const opt = document.createElement("option");
        opt.value = fyLabel;
        opt.textContent = fyLabel;
        select.appendChild(opt);
      });
      select.value = currentVal || "";
    }

    historicalData = res.data;
    renderHistoricalTable(historicalData);

    document.getElementById("historical-count").innerText =
      `${res.count} stocks`;

  } catch (err) {
    console.error("Historical holdings error:", err);
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="10" class="loading" style="color:var(--muted);">
        No historical trade data available.
      </td></tr>`;
    }
  }
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
  if (holdingsData.length > 0) {
    refreshAllocationPies();
    refreshPnlChart();
    refreshValueCompare();
  }
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

    /* -------- BUILD SECTOR ALLOC FROM HOLDINGS (no extra API call) -------- */
    const sectorMap = {};
    holdingsData.forEach(h => {
      const sec = h.sector || "Unknown";
      if (!sectorMap[sec]) sectorMap[sec] = { invested: 0, current: 0, pnl: 0 };
      sectorMap[sec].invested += Number(h.invested_value || 0);
      sectorMap[sec].current += Number(h.current_value || 0);
      sectorMap[sec].pnl += Number(h.pnl || 0);
    });

    const totalC = Object.values(sectorMap).reduce((s, v) => s + v.current, 0) || 1;
    const totalI = Object.values(sectorMap).reduce((s, v) => s + v.invested, 0) || 1;

    sectorAllocData = {
      by_current_value: Object.entries(sectorMap).map(([sector, v]) => ({
        sector,
        value: Math.round(v.current * 100) / 100,
        percentage: Math.round((v.current / totalC) * 10000) / 100,
        profit: Math.round(v.pnl * 100) / 100
      })),
      by_invested_value: Object.entries(sectorMap).map(([sector, v]) => ({
        sector,
        value: Math.round(v.invested * 100) / 100,
        percentage: Math.round((v.invested / totalI) * 10000) / 100
      }))
    };

    /* -------- RENDER ALL CHARTS -------- */
    initSharedColorMap();
    refreshAllocationPies();
    refreshPnlChart();
    refreshValueCompare();

    console.log("KPIs + charts updated successfully");

    /* -------- REALISED P&L KPIs -------- */
    try {
      const rpnl = await fetchRealisedPnl();
      if (rpnl) {
        const ytdEl = document.getElementById("kpi-realised-ytd");
        ytdEl.innerText = formatINR(rpnl.ytd.realised_pnl);
        ytdEl.className = "value " + (rpnl.ytd.realised_pnl >= 0 ? "positive" : "negative");

        const prevEl = document.getElementById("kpi-realised-prev");
        prevEl.innerText = formatINR(rpnl.previous_fy.realised_pnl);
        prevEl.className = "value " + (rpnl.previous_fy.realised_pnl >= 0 ? "positive" : "negative");

        // Dynamic label for previous FY
        const prevLabel = document.getElementById("kpi-realised-prev-label");
        if (prevLabel && rpnl.previous_fy.label) {
          prevLabel.innerText = "Realised P&L (" + rpnl.previous_fy.label + ")";
        }
      }
    } catch (e) {
      console.warn("Realised P&L fetch failed:", e);
    }

    /* -------- CASH AVAILABLE KPI -------- */
    try {
      const margins = await fetchMargins();
      if (margins) {
        const cashEl = document.getElementById("kpi-cash");
        cashEl.innerText = formatINR(margins.net);
      }
    } catch (e) {
      console.warn("Margins fetch failed:", e);
    }

  } catch (err) {
    console.error("Holdings error:", err);
    // Show error in holdings table so user sees something
    const tbody = document.getElementById("holdings-body");
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="10" class="loading" style="color:#dc2626;">
        Failed to load holdings. Please <a href="${API_BASE}/auth/zerodha/login" style="color:#2563eb;text-decoration:underline;">re-login to Zerodha</a>.
      </td></tr>`;
    }
  }
}

/* ========================================
   Delivery Volume Chart
======================================== */

const deliveryCache = {};
const tradeCache = {};

async function fetchDeliveryData(symbol, period = "1y") {
  const cacheKey = `${symbol}_${period}`;
  if (deliveryCache[cacheKey]) return deliveryCache[cacheKey];

  const res = await fetch(`${API_BASE}/portfolio/delivery-data?symbol=${symbol}&period=${period}`, FETCH_OPTS);
  if (!res.ok) throw new Error(`Failed to fetch delivery data for ${symbol}`);
  const json = await res.json();
  deliveryCache[cacheKey] = json.data;
  return json.data;
}

async function fetchTradesForSymbol(symbol) {
  if (tradeCache[symbol]) return tradeCache[symbol];
  try {
    const res = await fetch(`${API_BASE}/portfolio/trades?symbol=${symbol}`, FETCH_OPTS);
    if (!res.ok) return null;
    const json = await res.json();
    tradeCache[symbol] = json;
    return json;
  } catch (e) {
    console.warn(`Failed to fetch trades for ${symbol}:`, e);
    return null;
  }
}

function isoToDisplayDate(isoDate) {
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const [y, m, d] = isoDate.split("-");
  return `${d}-${months[parseInt(m, 10) - 1]}-${y}`;
}

function displayDateToISO(displayDate) {
  // "01-Mar-2026" → "2026-03-01"
  const monthMap = {Jan:"01",Feb:"02",Mar:"03",Apr:"04",May:"05",Jun:"06",
                    Jul:"07",Aug:"08",Sep:"09",Oct:"10",Nov:"11",Dec:"12"};
  const parts = displayDate.split("-");
  if (parts.length !== 3) return null;
  return `${parts[2]}-${monthMap[parts[1]] || "01"}-${parts[0]}`;
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
  const deliveryCanvasId = `deliveryChart-${symbol}`;
  const priceCanvasId = `priceChart-${symbol}`;
  const periodLabels = {"3m":"3 months","6m":"6 months","1y":"1 year","2y":"2 years","3y":"3 years","all":"all time"};
  const periodLabel = periodLabels[period] || period;

  if (loadingEl) {
    loadingEl.textContent = "Loading delivery data...";
    loadingEl.style.display = "block";
  }

  try {
    const [data, tradeData] = await Promise.all([
      fetchDeliveryData(symbol, period),
      fetchTradesForSymbol(symbol)
    ]);
    if (loadingEl) loadingEl.style.display = "none";

    if (!data || data.length === 0) {
      // Destroy existing charts
      [deliveryCanvasId, priceCanvasId].forEach(id => {
        if (chartRegistry[id]) { chartRegistry[id].destroy(); delete chartRegistry[id]; }
      });
      if (loadingEl) {
        loadingEl.textContent = `Data not available for last ${periodLabel}`;
        loadingEl.style.display = "block";
      }
      return;
    }

    renderPriceLineChart(priceCanvasId, data, symbol, tradeData);
    renderDeliveryChart(deliveryCanvasId, data, symbol);
  } catch (err) {
    console.error(`Delivery data error for ${symbol}:`, err);
    if (loadingEl) {
      loadingEl.textContent = `Data not available for last ${periodLabel}`;
      loadingEl.style.display = "block";
    }
  }
}

/* ── Variable-density fill plugin ──
   Draws the area under the price line with opacity proportional to
   the number of shares held at each point (from cumulative trade data). */
const variableFillPlugin = {
  id: "variableFill",

  beforeDatasetDraw(chart, args) {
    if (args.index !== 0) return;
    const ds = chart.data.datasets[0];
    if (!ds._fillSegments) return;
    // Suppress the default fill — we draw our own in afterDatasetDraw
    ds._origFill = ds.fill;
    ds.fill = false;
  },

  afterDatasetDraw(chart, args) {
    if (args.index !== 0) return;
    const ds = chart.data.datasets[0];
    if (!ds._fillSegments) return;

    // Restore fill flag so tooltips etc. still reference it
    ds.fill = ds._origFill !== undefined ? ds._origFill : true;

    const meta = chart.getDatasetMeta(0);
    const points = meta.data;
    if (!points || points.length === 0) return;

    const yScale = chart.scales.y;
    const bottom = yScale.bottom;
    const { left, top, right, bottom: areaBottom } = chart.chartArea;
    const ctx = chart.ctx;
    const segments = ds._fillSegments;
    const base = ds._fillBaseColor;

    ctx.save();
    ctx.beginPath();
    ctx.rect(left, top, right - left, areaBottom - top);
    ctx.clip();

    for (const seg of segments) {
      const { startIdx, endIdx, opacity } = seg;
      if (opacity <= 0) continue;
      const si = Math.max(0, Math.min(startIdx, points.length - 1));
      const ei = Math.max(0, Math.min(endIdx, points.length - 1));

      ctx.beginPath();
      ctx.moveTo(points[si].x, bottom);
      for (let i = si; i <= ei; i++) {
        ctx.lineTo(points[i].x, points[i].y);
      }
      ctx.lineTo(points[ei].x, bottom);
      ctx.closePath();
      ctx.fillStyle = `rgba(${base.r}, ${base.g}, ${base.b}, ${opacity})`;
      ctx.fill();
    }
    ctx.restore();
  }
};

function renderPriceLineChart(canvasId, data, symbol, tradeData) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  if (chartRegistry[canvasId]) {
    chartRegistry[canvasId].destroy();
  }

  const labels = data.map(d => d.date);
  const closePrices = data.map(d => d.close_price || 0);

  // Skip if no price data
  if (closePrices.every(p => p === 0)) {
    canvas.parentElement.style.display = "none";
    return;
  }
  canvas.parentElement.style.display = "block";

  // Color: green if overall up, red if down
  const firstPrice = closePrices.find(p => p > 0) || 0;
  const lastPrice = closePrices[closePrices.length - 1] || 0;
  const isUp = lastPrice >= firstPrice;
  const lineColor = isUp ? "#16a34a" : "#dc2626";
  const defaultFillColor = isUp ? "rgba(22, 163, 106, 0.08)" : "rgba(220, 38, 38, 0.08)";

  // ── Build trade marker datasets ──
  const dateIndexMap = {};
  labels.forEach((label, idx) => { dateIndexMap[label] = idx; });

  const buyPoints = [];
  const sellPoints = [];
  let hasTradeSegments = false;

  if (tradeData && tradeData.trades) {
    for (const t of tradeData.trades) {
      const displayDate = isoToDisplayDate(t.date);
      const idx = dateIndexMap[displayDate];

      // Trade is within visible range → place at exact date
      // Trade is before visible range → place at far-left (index 0)
      const useIdx = idx !== undefined ? idx : null;
      const useLabel = idx !== undefined ? displayDate : labels[0];
      const usePrice = idx !== undefined ? closePrices[idx] : closePrices[0];
      if (!usePrice) continue;
      // Skip trades after the visible range entirely
      if (idx === undefined) {
        // Check if trade date is AFTER the visible range (skip it)
        const tradeISO = t.date; // YYYY-MM-DD
        const lastLabel = labels[labels.length - 1];
        // Convert last label back to ISO for comparison
        const lastISO = displayDateToISO(lastLabel);
        if (lastISO && tradeISO > lastISO) continue;
      }

      if (t.buys && t.buys.length > 0) {
        const totalQty = t.buys.reduce((s, b) => s + b.quantity, 0);
        const avgPrice = t.buys.reduce((s, b) => s + b.quantity * b.price, 0) / totalQty;
        buyPoints.push({
          x: useLabel, y: usePrice,
          tradeQty: totalQty, tradePrice: avgPrice,
          tradeDate: displayDate, tradeType: "BUY"
        });
      }
      if (t.sells && t.sells.length > 0) {
        const totalQty = t.sells.reduce((s, b) => s + b.quantity, 0);
        const avgPrice = t.sells.reduce((s, b) => s + b.quantity * b.price, 0) / totalQty;
        sellPoints.push({
          x: useLabel, y: usePrice,
          tradeQty: totalQty, tradePrice: avgPrice,
          tradeDate: displayDate, tradeType: "SELL"
        });
      }
    }
  }

  // ── Build variable-density fill segments ──
  const MIN_OPACITY = 0.04;
  const MAX_OPACITY = 0.28;

  const priceDataset = {
    label: "Close Price",
    data: closePrices,
    borderColor: lineColor,
    backgroundColor: defaultFillColor,
    borderWidth: 1.5,
    pointRadius: 0,
    pointHitRadius: 6,
    tension: 0.3,
    fill: true,
    order: 3
  };

  if (tradeData && tradeData.cumulative_qty && tradeData.cumulative_qty.length > 0) {
    // Map trade dates → cumulative qty
    const cumQtyByDate = {};
    for (const c of tradeData.cumulative_qty) {
      cumQtyByDate[isoToDisplayDate(c.date)] = c.qty_after;
    }
    const maxQty = Math.max(...tradeData.cumulative_qty.map(c => Math.abs(c.qty_after)), 1);

    // Determine initial position from trades before visible range
    let currentQty = 0;
    for (const c of tradeData.cumulative_qty) {
      const dd = isoToDisplayDate(c.date);
      if (dateIndexMap[dd] === undefined) {
        currentQty = c.qty_after; // trade before visible range
      } else {
        break;
      }
    }

    // Build segments: one per gap between trade events
    const segments = [];
    let segStartIdx = 0;

    for (let i = 0; i < labels.length; i++) {
      if (cumQtyByDate[labels[i]] !== undefined) {
        // Close previous segment
        if (i > segStartIdx) {
          const opacity = currentQty > 0
            ? MIN_OPACITY + (Math.abs(currentQty) / maxQty) * (MAX_OPACITY - MIN_OPACITY)
            : 0;
          segments.push({ startIdx: segStartIdx, endIdx: i, opacity });
        }
        currentQty = cumQtyByDate[labels[i]];
        segStartIdx = i;
      }
    }
    // Final segment to end of chart
    if (segStartIdx < labels.length - 1) {
      const opacity = currentQty > 0
        ? MIN_OPACITY + (Math.abs(currentQty) / maxQty) * (MAX_OPACITY - MIN_OPACITY)
        : 0;
      segments.push({ startIdx: segStartIdx, endIdx: labels.length - 1, opacity });
    }

    const rgb = isUp ? { r: 22, g: 163, b: 106 } : { r: 220, g: 38, b: 38 };
    priceDataset._fillSegments = segments;
    priceDataset._fillBaseColor = rgb;
    priceDataset.backgroundColor = "transparent"; // plugin handles fill
    hasTradeSegments = true;
  }

  // ── Assemble datasets ──
  const datasets = [priceDataset];

  if (buyPoints.length > 0) {
    datasets.push({
      label: "Buy",
      type: "scatter",
      data: buyPoints,
      pointStyle: "triangle",
      pointRadius: 7,
      pointHoverRadius: 10,
      backgroundColor: "#16a34a",
      borderColor: "#fff",
      borderWidth: 1.5,
      order: 1
    });
  }
  if (sellPoints.length > 0) {
    datasets.push({
      label: "Sell",
      type: "scatter",
      data: sellPoints,
      pointStyle: "triangle",
      rotation: 180,
      pointRadius: 7,
      pointHoverRadius: 10,
      backgroundColor: "#dc2626",
      borderColor: "#fff",
      borderWidth: 1.5,
      order: 1
    });
  }

  chartRegistry[canvasId] = new Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      scales: {
        x: {
          display: false // delivery chart below shares the same dates
        },
        y: {
          position: "right",
          grid: { color: "rgba(0,0,0,0.04)" },
          ticks: {
            color: "#64748b",
            font: { size: 11 },
            callback: (v) => "\u20B9" + Number(v).toLocaleString("en-IN")
          }
        }
      },
      plugins: {
        legend: { display: false },
        datalabels: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => {
              const item = items[0];
              if (item.dataset.label === "Buy" || item.dataset.label === "Sell") {
                return item.raw.tradeDate;
              }
              return item.label;
            },
            label: (ctx) => {
              if (ctx.dataset.label === "Buy" || ctx.dataset.label === "Sell") {
                const r = ctx.raw;
                return [
                  `${r.tradeType}: ${r.tradeQty} shares`,
                  `Avg Price: \u20B9${r.tradePrice.toLocaleString("en-IN", {minimumFractionDigits: 2})}`,
                  `Close: \u20B9${r.y.toLocaleString("en-IN")}`
                ];
              }
              return `Close: \u20B9${ctx.parsed.y.toLocaleString("en-IN")}`;
            }
          }
        }
      }
    },
    plugins: [ChartDataLabels, variableFillPlugin]
  });
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
            font: { size: 10 },
            maxRotation: 45,
            maxTicksLimit: 30
          }
        },
        y: {
          stacked: true,
          grid: { color: "rgba(0,0,0,0.06)" },
          ticks: {
            color: "#64748b",
            font: { size: 11 },
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
            font: { size: 12 },
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
      const sessionRes = await fetch(`${API_BASE}/session/active`, FETCH_OPTS);
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
  renderHistoricalHoldings();

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

  // Allocation pie: Sector/Stock dimension toggle
  document.querySelectorAll("#allocDimToggle .toggle-btn").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      allocState.dimension = btn.dataset.dim;
      document.querySelectorAll("#allocDimToggle .toggle-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      refreshAllocationPies();
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

  // Historical holdings sortable headers
  document.querySelectorAll("#historical-table .sortable").forEach(th => {
    th.addEventListener("click", (e) => {
      e.stopPropagation();
      sortHistorical(th.dataset.sort);
    });
  });

  // Historical holdings FY filter
  const fyFilter = document.getElementById("fy-filter");
  if (fyFilter) {
    fyFilter.addEventListener("change", (e) => {
      renderHistoricalHoldings(e.target.value);
    });
  }

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

  // Session security: The tf_session cookie has no Max-Age/Expires,
  // making it a browser-session cookie. When the browser is fully closed:
  //   1. Cookie is automatically deleted by the browser
  //   2. Next visit has no cookie -> /session/active returns 404 -> redirect to login
  // This provides auto-logout on browser close without any JS needed.
});
