(function () {
  const COLORS = ["#38bdf8", "#34d399", "#fbbf24", "#fb7185", "#a78bfa", "#22d3ee"];

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
    }[c]));
  }

  function fmt(n) {
    return n == null || Number.isNaN(Number(n))
      ? "—"
      : Number(n).toLocaleString("en-US");
  }

  function shortNum(n) {
    const abs = Math.abs(n);
    if (abs >= 1e9) return (n / 1e9).toFixed(1) + "B";
    if (abs >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (abs >= 1e3) return (n / 1e3).toFixed(1) + "k";
    return String(Math.round(n));
  }

  function fmtDate(ts, withYear) {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "";
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return withYear ? `${d.getFullYear()}-${mm}-${dd}` : `${Number(mm)}/${Number(dd)}`;
  }

  function node(name, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v));
    return el;
  }

  function nearest(points, xTime) {
    let best = points[0];
    let bestDist = Math.abs(best.x - xTime);
    for (let i = 1; i < points.length; i++) {
      const dist = Math.abs(points[i].x - xTime);
      if (dist < bestDist) {
        best = points[i];
        bestDist = dist;
      }
    }
    return best;
  }

  function draw(svg, series, opts) {
    opts = opts || {};
    const wrap = svg.parentElement;
    const width = Math.max(320, wrap.clientWidth);
    const height = opts.height || 320;
    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.innerHTML = "";

    const tooltip = opts.tooltip || null;
    const pad = { l: 62, r: 16, t: 16, b: 30 };
    const iw = width - pad.l - pad.r;
    const ih = height - pad.t - pad.b;

    const all = [];
    series.forEach((s) => (s.points || []).forEach((p) => all.push(p)));
    if (!all.length) {
      const t = node("text", {
        x: width / 2, y: height / 2, "text-anchor": "middle",
        fill: "#64748b", "font-size": "13",
      });
      t.textContent = opts.empty || "暂无数据";
      svg.appendChild(t);
      return;
    }

    let x0 = Math.min(...all.map((p) => p.x));
    let x1 = Math.max(...all.map((p) => p.x));
    let y0 = Math.min(...all.map((p) => p.y));
    let y1 = Math.max(...all.map((p) => p.y));
    if (x1 === x0) x1 = x0 + 86400000;
    if (y1 === y0) {
      const d = Math.max(1, y1 * 0.04);
      y0 = Math.max(0, y0 - d);
      y1 = y1 + d;
    }
    const rawSpan = y1 - y0;
    y0 = Math.max(0, y0 - rawSpan * 0.05);
    y1 = y1 + rawSpan * 0.08;

    const sx = (x) => pad.l + ((x - x0) / (x1 - x0)) * iw;
    const sy = (y) => pad.t + ih - ((y - y0) / (y1 - y0)) * ih;

    const ticks = 4;
    for (let i = 0; i <= ticks; i++) {
      const yv = y0 + ((y1 - y0) * i) / ticks;
      const y = sy(yv);
      svg.appendChild(node("line", {
        x1: pad.l, y1: y, x2: width - pad.r, y2: y, stroke: "#1c2740", "stroke-width": 1,
      }));
      const label = node("text", {
        x: pad.l - 8, y: y + 4, "text-anchor": "end", fill: "#6b7f9e", "font-size": "11",
      });
      label.textContent = shortNum(yv);
      svg.appendChild(label);
    }

    const xTicks = 4;
    for (let i = 0; i <= xTicks; i++) {
      const xv = x0 + ((x1 - x0) * i) / xTicks;
      const label = node("text", {
        x: sx(xv), y: height - 8, "text-anchor": "middle", fill: "#6b7f9e", "font-size": "11",
      });
      label.textContent = fmtDate(xv, x1 - x0 > 180 * 86400000);
      svg.appendChild(label);
    }

    if (series.length === 1 && opts.area !== false) {
      const gid = "grad" + Math.random().toString(36).slice(2, 9);
      const defs = node("defs");
      const grad = node("linearGradient", { id: gid, x1: "0", y1: "0", x2: "0", y2: "1" });
      const color = series[0].color || COLORS[0];
      grad.appendChild(node("stop", { offset: "0%", "stop-color": color, "stop-opacity": "0.26" }));
      grad.appendChild(node("stop", { offset: "100%", "stop-color": color, "stop-opacity": "0" }));
      defs.appendChild(grad);
      svg.appendChild(defs);

      const pts = series[0].points;
      let d = `M ${sx(pts[0].x)} ${sy(pts[0].y)}`;
      pts.slice(1).forEach((p) => (d += ` L ${sx(p.x)} ${sy(p.y)}`));
      const area = d + ` L ${sx(pts[pts.length - 1].x)} ${pad.t + ih} L ${sx(pts[0].x)} ${pad.t + ih} Z`;
      svg.appendChild(node("path", { d: area, fill: `url(#${gid})`, stroke: "none" }));
    }

    series.forEach((s, idx) => {
      const color = s.color || COLORS[idx % COLORS.length];
      const pts = s.points || [];
      if (!pts.length) return;
      let d = `M ${sx(pts[0].x)} ${sy(pts[0].y)}`;
      pts.slice(1).forEach((p) => (d += ` L ${sx(p.x)} ${sy(p.y)}`));
      svg.appendChild(node("path", {
        d, fill: "none", stroke: color, "stroke-width": opts.lineWidth || 2,
        "stroke-linejoin": "round", "stroke-linecap": "round",
      }));
      if (pts.length <= (opts.maxPoints || 70)) {
        pts.forEach((p) => svg.appendChild(node("circle", {
          cx: sx(p.x), cy: sy(p.y), r: opts.dotRadius || 2, fill: color, opacity: 0.8,
        })));
      }
      const last = pts[pts.length - 1];
      svg.appendChild(node("circle", { cx: sx(last.x), cy: sy(last.y), r: 3.2, fill: color }));
    });

    if (!tooltip) return;

    const hoverLine = node("line", {
      x1: 0, y1: pad.t, x2: 0, y2: pad.t + ih,
      stroke: "#3b4a6b", "stroke-width": 1, "stroke-dasharray": "3 3", opacity: 0,
    });
    svg.appendChild(hoverLine);

    const hoverDots = series.map((s, idx) => {
      const dot = node("circle", {
        r: 4, fill: s.color || COLORS[idx % COLORS.length],
        stroke: "#0b1120", "stroke-width": 1.5, opacity: 0,
      });
      svg.appendChild(dot);
      return dot;
    });

    function hide() {
      hoverLine.setAttribute("opacity", 0);
      hoverDots.forEach((d) => d.setAttribute("opacity", 0));
      tooltip.classList.remove("show");
    }

    svg.onmousemove = (ev) => {
      const rect = svg.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      if (mx < pad.l - 4 || mx > width - pad.r + 4) {
        hide();
        return;
      }
      const tx = x0 + ((mx - pad.l) / iw) * (x1 - x0);
      const base = nearest(series[0].points, tx);
      const px = sx(base.x);
      hoverLine.setAttribute("x1", px);
      hoverLine.setAttribute("x2", px);
      hoverLine.setAttribute("opacity", 0.7);

      const valueFmt = opts.formatValue || fmt;
      const rows = [];
      series.forEach((s, idx) => {
        if (!s.points || !s.points.length) return;
        const p = nearest(s.points, base.x);
        hoverDots[idx].setAttribute("cx", sx(p.x));
        hoverDots[idx].setAttribute("cy", sy(p.y));
        hoverDots[idx].setAttribute("opacity", 1);
        rows.push({
          name: s.name || "",
          color: s.color || COLORS[idx % COLORS.length],
          value: p.y,
        });
      });

      tooltip.innerHTML =
        `<div class="t-date">${fmtDate(base.x, true)}</div>` +
        rows.map((r) => `
          <div class="t-row">
            <span class="dot" style="background:${r.color}"></span>
            ${r.name ? esc(r.name) + "：" : ""}${valueFmt(r.value)}
          </div>`).join("");
      const wrapRect = wrap.getBoundingClientRect();
      tooltip.style.left = (ev.clientX - wrapRect.left) + "px";
      tooltip.style.top = (ev.clientY - wrapRect.top) + "px";
      tooltip.classList.add("show");
    };
    svg.onmouseleave = hide;
  }

  window.GHChart = { draw, COLORS, fmt, shortNum, fmtDate, esc };
})();
