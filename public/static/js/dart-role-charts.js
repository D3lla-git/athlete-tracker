/* Charts for the coach, scout and organization analytics dashboards
   (same look and rules as dart-admin-charts.js): data in <script id="rxData">,
   colors from the validated roles on .adx (light / dark), hover tooltips,
   a legend when there are 2+ series, redrawn when the theme changes. */
(function () {
  'use strict';

  var root = document.getElementById('roleAnalytics');
  var dataEl = document.getElementById('rxData');
  if (!root || !dataEl) return;
  var D;
  try { D = JSON.parse(dataEl.textContent); } catch (e) { return; }
  var charts = [];

  function token(name) { return getComputedStyle(root).getPropertyValue(name).trim(); }
  function alpha(hex, a) {
    var h = hex.replace('#', '');
    if (h.length === 3) h = h.split('').map(function (c) { return c + c; }).join('');
    var n = parseInt(h, 16);
    return 'rgba(' + (n >> 16 & 255) + ',' + (n >> 8 & 255) + ',' + (n & 255) + ',' + a + ')';
  }
  function compact(n) { n = +n || 0; return Math.abs(n) < 1000 ? String(n) : (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K'; }

  var crosshair = {
    id: 'rxCrosshair',
    afterDraw: function (chart) {
      var act = chart.tooltip && chart.tooltip.getActiveElements();
      if (!act || !act.length || chart.config.type === 'doughnut' || chart.options.indexAxis === 'y') return;
      var x = act[0].element.x, area = chart.chartArea, ctx = chart.ctx;
      ctx.save(); ctx.beginPath(); ctx.moveTo(x, area.top); ctx.lineTo(x, area.bottom);
      ctx.lineWidth = 1; ctx.strokeStyle = token('--adx-axis'); ctx.setLineDash([3, 3]); ctx.stroke(); ctx.restore();
    }
  };

  function base(t) {
    return {
      responsive: true, maintainAspectRatio: false,
      animation: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? false : { duration: 450 },
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { top: 4, right: 4 } },
      plugins: {
        legend: { display: false },
        tooltip: { backgroundColor: t.surface, titleColor: t.ink, bodyColor: t.ink2, borderColor: t.ring, borderWidth: 1,
                   padding: 10, cornerRadius: 10, boxPadding: 4, usePointStyle: true, titleFont: { weight: '700' } }
      },
      scales: {
        x: { grid: { display: false }, border: { color: t.axis }, ticks: { color: t.muted, font: { size: 11 }, maxRotation: 0, autoSkipPadding: 10 } },
        y: { beginAtZero: true, grid: { color: t.grid, drawTicks: false }, border: { display: false },
             ticks: { color: t.muted, font: { size: 11 }, precision: 0, padding: 8, callback: compact, maxTicksLimit: 5 } }
      }
    };
  }

  function make(id, config) {
    var el = document.getElementById(id);
    if (!el) return;
    config.plugins = [crosshair];
    charts.push(new window.Chart(el, config));
  }

  function draw() {
    charts.forEach(function (c) { c.destroy(); });
    charts = [];
    window.Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
    var t = {
      surface: token('--adx-surface') || '#fff', ink: token('--adx-ink'), ink2: token('--adx-ink-2'), muted: token('--adx-muted'),
      grid: token('--adx-grid'), axis: token('--adx-axis'), ring: token('--adx-ring'),
      good: token('--adx-good'), warning: token('--adx-warning'), critical: token('--adx-critical')
    };
    function slot(n) { return token('--adx-s' + n); }

    // Trend (1 or 2 series, same unit -> one axis)
    var ot = base(t);
    if (D.trend.series.length > 1) {
      ot.plugins.legend = { display: true, position: 'top', align: 'end',
        labels: { color: t.ink2, usePointStyle: true, pointStyle: 'rectRounded', boxWidth: 10, boxHeight: 10, padding: 14, font: { size: 12, weight: '600' } } };
    }
    make('rxTrend', {
      type: 'line',
      data: { labels: D.labels, datasets: D.trend.series.map(function (s, i) {
        var c = slot(s.slot);
        return { label: s.label, data: s.data, borderColor: c, backgroundColor: i === 0 ? alpha(c, 0.12) : c, fill: i === 0 ? 'origin' : false,
                 borderWidth: 2, tension: 0.35, pointRadius: 0, pointHoverRadius: 5, pointHoverBorderWidth: 2,
                 pointHoverBorderColor: t.surface, pointBackgroundColor: c };
      }) },
      options: ot
    });

    // Review status (reserved status colors, labeled in the list beside it)
    if (D.status) {
      var vals = [D.status.approved, D.status.pending, D.status.rejected];
      var empty = !vals[0] && !vals[1] && !vals[2];
      make('rxStatus', {
        type: 'doughnut',
        data: { labels: ['Approved', 'Pending', 'Rejected'], datasets: [{
          data: empty ? [1] : vals, backgroundColor: empty ? [t.grid] : [t.good, t.warning, t.critical],
          borderColor: t.surface, borderWidth: 2, borderRadius: 4, hoverOffset: 4 }] },
        options: { responsive: true, maintainAspectRatio: false, cutout: '74%', animation: base(t).animation,
                   plugins: { legend: { display: false }, tooltip: empty ? { enabled: false } : base(t).plugins.tooltip } }
      });
    }

    // Ranking by sport (horizontal, most first)
    var or = base(t);
    or.indexAxis = 'y';
    or.interaction = { mode: 'nearest', intersect: true, axis: 'y' };
    or.scales = {
      x: { beginAtZero: true, grid: { color: t.grid, drawTicks: false }, border: { display: false },
           ticks: { color: t.muted, font: { size: 11 }, precision: 0, maxTicksLimit: 6, callback: compact } },
      y: { grid: { display: false }, border: { color: t.axis }, ticks: { color: t.ink2, font: { size: 12, weight: '600' } } }
    };
    make('rxRank', {
      type: 'bar',
      data: { labels: D.rank.rows.map(function (r) { return r[0]; }),
              datasets: [{ label: 'Records', data: D.rank.rows.map(function (r) { return r[1]; }), backgroundColor: slot(1),
                           borderRadius: { topRight: 4, bottomRight: 4 }, borderSkipped: 'start', maxBarThickness: 22 }] },
      options: or
    });

    // Monthly bars (one series)
    make('rxBars', {
      type: 'bar',
      data: { labels: D.labels, datasets: [{ label: D.bars.label, data: D.bars.data, backgroundColor: slot(D.bars.slot),
                                             borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: 'start',
                                             maxBarThickness: 26, categoryPercentage: 0.7, barPercentage: 0.85 }] },
      options: base(t)
    });
  }

  function start() {
    if (!window.Chart) return;          // CDN unreachable: numbers + tables still work
    draw();
    new MutationObserver(draw).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    if (window.matchMedia) window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
  }
  if (window.Chart) start(); else window.addEventListener('load', start);
})();
