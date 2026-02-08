const API_BASE = "http://127.0.0.1:8000";

async function fetchOverview() {
  const res = await fetch(`${API_BASE}/portfolio/overview`);
  if (!res.ok) throw new Error("Failed to load overview");
  return res.json();
}

async function fetchHoldings() {
  const res = await fetch(`${API_BASE}/portfolio/holdings`);
  if (!res.ok) throw new Error("Failed to load holdings");
  return res.json();
}

function formatINR(value) {
  return "₹" + value.toLocaleString("en-IN", {
    maximumFractionDigits: 2
  });
}

async function renderOverview() {
  try {
    const o = await fetchOverview();

    document.getElementById("total-invested").innerText =
      formatINR(o.total_invested_value);

    document.getElementById("current-value").innerText =
      formatINR(o.current_value);

    const pnlEl = document.getElementById("total-pnl");
    pnlEl.innerText = formatINR(o.total_pnl);
    pnlEl.className = o.total_pnl >= 0 ? "positive" : "negative";

  } catch (err) {
    console.error(err);
  }
}

async function renderHoldings() {
  try {
    const res = await fetchHoldings();
    const tbody = document.getElementById("holdings-body");
    tbody.innerHTML = "";

    res.data.forEach(h => {
      const tr = document.createElement("tr");

      tr.innerHTML = `
        <td>${h.symbol}</td>
        <td>${h.quantity}</td>
        <td>${formatINR(h.avg_buy_price)}</td>
        <td>${formatINR(h.current_price)}</td>
        <td class="${h.pnl >= 0 ? "positive" : "negative"}">
          ${formatINR(h.pnl)}
        </td>
      `;

      tbody.appendChild(tr);
    });

  } catch (err) {
    console.error(err);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  renderOverview();
  renderHoldings();
});