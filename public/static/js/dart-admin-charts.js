/* D.A.R.T Admin analytics charts (Chart.js 4).
   Data: <script id="adxData"> (from super_admin_analytics()).
   Colors come from CSS roles on .adx (light / dark stepped, validated), so
   a theme switch redraws with the right steps. Identity is never color
   alone: legends for 2+ series, a table view under every chart.
   Series colors follow the entity everywhere: Athletes = slot 1,
   Coaches = 2, Scouts = 3, Organizations = 4. */
(function () {
  'use strict';

  var root = document.getElementById('adminAnalytics');
  var dataEl = document.getElementById('adxData');
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
  function compact(n) {
    n = +n || 0;
    if (Math.abs(n) < 1000) return String(n);
    return (n / 1000).toFixed(n < 10000 ? 1 : 0).replace(/\.0$/, '') + 'K';
  }

  // Vertical guide at the hovered month (line / bar trends).
  var crosshair = {
    id: 'adxCrosshair',
    afterDraw: function (chart) {
      var act = chart.tooltip && chart.tooltip.getActiveElements();
      if (!act || !act.length || chart.config.type === 'doughnut' || chart.options.indexAxis === 'y') return;
      var x = act[0].element.x, area = chart.chartArea, ctx = chart.ctx;
      ctx.save();
      ctx.beginPath(); ctx.moveTo(x, area.top); ctx.lineTo(x, area.bottom);
      ctx.lineWidth = 1; ctx.strokeStyle = token('--adx-axis'); ctx.setLineDash([3, 3]); ctx.stroke();
      ctx.restore();
    }
  };

  function base(t) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? false : { duration: 450 },
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { top: 4, right: 4 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: t.surface, titleColor: t.ink, bodyColor: t.ink2, borderColor: t.ring, borderWidth: 1,
          padding: 10, cornerRadius: 10, boxPadding: 4, usePointStyle: true,
          titleFont: { weight: '700' }, bodyFont: { size: 12 },
          callbacks: { label: function (c) { return ' ' + c.dataset.label + ': ' + (c.formattedValue); } }
        }
      },
      scales: {
        x: { grid: { display: false }, border: { color: t.axis }, ticks: { color: t.muted, font: { size: 11 }, maxRotation: 0, autoSkipPadding: 10 } },
        y: { beginAtZero: true, grid: { color: t.grid, drawTicks: false }, border: { display: false },
             ticks: { color: t.muted, font: { size: 11 }, precision: 0, padding: 8, callback: compact, maxTicksLimit: 5 } }
      }
    };
  }

  function legend(t) {
    return { display: true, position: 'top', align: 'end',
             labels: { color: t.ink2, usePointStyle: true, pointStyle: 'rectRounded', boxWidth: 10, boxHeight: 10, padding: 14, font: { size: 12, weight: '600' } } };
  }

  function bars(color, t) {
    return { backgroundColor: color, hoverBackgroundColor: color, borderRadius: { topLeft: 4, topRight: 4 },
             borderSkipped: 'start', maxBarThickness: 26, categoryPercentage: 0.7, barPercentage: 0.85 };
  }

  function make(id, config) {
    var el = document.getElementById(id);
    if (!el) return;
    config.plugins = (config.plugins || []).concat([crosshair]);
    charts.push(new window.Chart(el, config));
  }

  function draw() {
    charts.forEach(function (c) { c.destroy(); });
    charts = [];
    var Chart = window.Chart;
    Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

    var t = {
      surface: token('--adx-surface') || '#fff', ink: token('--adx-ink') || '#111', ink2: token('--adx-ink-2'),
      muted: token('--adx-muted'), grid: token('--adx-grid'), axis: token('--adx-axis'), ring: token('--adx-ring'),
      s1: token('--adx-s1'), s2: token('--adx-s2'), s3: token('--adx-s3'), s4: token('--adx-s4'),
      good: token('--adx-good'), warning: token('--adx-warning'), critical: token('--adx-critical')
    };
    var labels = D.labels;

    // 1. Records uploaded vs approved (same unit -> one axis)
    var o1 = base(t);
    o1.plugins.legend = legend(t);
    make('adxRecordsTrend', {
      type: 'line',
      data: { labels: labels, datasets: [
        { label: 'Uploaded', data: D.records.total, borderColor: t.s1, backgroundColor: alpha(t.s1, 0.12), fill: 'origin',
          borderWidth: 2, tension: 0.35, pointRadius: 0, pointHoverRadius: 5, pointHoverBorderWidth: 2, pointHoverBorderColor: t.surface, pointBackgroundColor: t.s1 },
        { label: 'Approved', data: D.records.approved, borderColor: t.s3, backgroundColor: t.s3, fill: false,
          borderWidth: 2, tension: 0.35, pointRadius: 0, pointHoverRadius: 5, pointHoverBorderWidth: 2, pointHoverBorderColor: t.surface }
      ] },
      options: o1
    });

    // 2. Review status (status colors; the list beside it labels them)
    var status = [
      +(document.querySelector('[data-stat="records_approved"]') || {}).textContent || 0,
      +(document.querySelector('[data-stat="records_pending"]') || {}).textContent || 0,
      +(document.querySelector('[data-stat="records_rejected"]') || {}).textContent || 0
    ];
    var donutEmpty = !status[0] && !status[1] && !status[2];
    make('adxRecordsStatus', {
      type: 'doughnut',
      data: { labels: ['Approved', 'Pending', 'Rejected'], datasets: [{
        data: donutEmpty ? [1] : status,
        backgroundColor: donutEmpty ? [t.grid] : [t.good, t.warning, t.critical],
        borderColor: t.surface, borderWidth: 2, hoverOffset: 4, borderRadius: 4
      }] },
      options: { responsive: true, maintainAspectRatio: false, cutout: '74%',
                 animation: base(t).animation,
                 plugins: { legend: { display: false }, tooltip: donutEmpty ? { enabled: false } : base(t).plugins.tooltip } }
    });

    // 3. Records by sport (one series, ranked: horizontal bars, most first)
    var sports = D.records.by_sport.slice().sort(function (x, y) { return y[1] - x[1]; });
    var o3 = base(t);
    o3.indexAxis = 'y';
    o3.interaction = { mode: 'nearest', intersect: true, axis: 'y' };
    o3.scales = {
      x: { beginAtZero: true, grid: { color: t.grid, drawTicks: false }, border: { display: false },
           ticks: { color: t.muted, font: { size: 11 }, precision: 0, maxTicksLimit: 6, callback: compact } },
      y: { grid: { display: false }, border: { color: t.axis }, ticks: { color: t.ink2, font: { size: 12, weight: '600' } } }
    };
    make('adxRecordsSport', {
      type: 'bar',
      data: { labels: sports.map(function (r) { return r[0]; }),
              datasets: [Object.assign({ label: 'Records', data: sports.map(function (r) { return r[1]; }) }, bars(t.s1, t),
                                       { borderRadius: { topRight: 4, bottomRight: 4 }, maxBarThickness: 22 })] },
      options: o3
    });

    // 4. New accounts per month by role (stacked; 2px surface gap between segments)
    var roles = [['Athletes', 'Athlete', t.s1], ['Coaches', 'Coach', t.s2], ['Scouts', 'Scout', t.s3], ['Organizations', 'Organization', t.s4]];
    var o4 = base(t);
    o4.plugins.legend = legend(t);
    o4.scales.x.stacked = true; o4.scales.y.stacked = true;
    o4.plugins.tooltip.callbacks.footer = function (items) {
      var sum = items.reduce(function (a, i) { return a + i.parsed.y; }, 0);
      return 'Total: ' + sum;
    };
    make('adxSignups', {
      type: 'bar',
      data: { labels: labels, datasets: roles.map(function (r, i) {
        return Object.assign({ label: r[0], data: D.users.signups[r[1]], borderColor: t.surface,
                               borderWidth: i === roles.length - 1 ? 0 : { top: 2 } }, bars(r[2], t),
                             { borderRadius: i === roles.length - 1 ? { topLeft: 4, topRight: 4 } : 0 });
      }) },
      options: o4
    });

    // 5-7. Coaches / scouts / organizations per month (one series each, entity color)
    [['adxCoachesTrend', 'New coaches', D.coaches.signups, t.s2],
     ['adxScoutsTrend', 'New scouts', D.scouts.signups, t.s3],
     ['adxOrgTrend', 'New organizations', D.organizations.signups, t.s4]].forEach(function (c) {
      make(c[0], { type: 'bar', data: { labels: labels, datasets: [Object.assign({ label: c[1], data: c[2] }, bars(c[3], t))] }, options: base(t) });
    });

    // 8. Verified organizations by type (horizontal; one series)
    var o8 = base(t);
    o8.indexAxis = 'y';
    o8.interaction = { mode: 'nearest', intersect: true, axis: 'y' };
    o8.scales = {
      x: { beginAtZero: true, grid: { color: t.grid, drawTicks: false }, border: { display: false },
           ticks: { color: t.muted, font: { size: 11 }, precision: 0, maxTicksLimit: 5 } },
      y: { grid: { display: false }, border: { color: t.axis }, ticks: { color: t.ink2, font: { size: 12 } } }
    };
    make('adxOrgTypes', {
      type: 'bar',
      data: { labels: D.organizations.types.map(function (r) { return r[0]; }),
              datasets: [Object.assign({ label: 'Organizations', data: D.organizations.types.map(function (r) { return r[1]; }) }, bars(t.s4, t),
                                       { borderRadius: { topRight: 4, bottomRight: 4 }, maxBarThickness: 18 })] },
      options: o8
    });
  }

  function start() {
    if (!window.Chart) return;          // CDN unreachable (offline): numbers + tables still work
    draw();
    // Theme toggle / OS theme -> redraw with the right color steps.
    new MutationObserver(function () { draw(); }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    if (window.matchMedia) window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
  }

  if (window.Chart) start();
  else window.addEventListener('load', start);
})();
