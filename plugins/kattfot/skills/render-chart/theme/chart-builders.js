/* chart-builders.js — the single source of truth for governed ECharts option
 * building, shared by render-chart (template.html) and render-dashboard
 * (dashboard.html).
 *
 * Exposes window.KattfotCharts.buildOption(SPEC, T, TOKENS) -> ECharts option (or null
 * for an unknown chart_type). SPEC is a chart_spec.py spec (single_line,
 * multi_series_line, ranked_bar, funnel); T is the resolved light/dark token set;
 * TOKENS is the full theme tokens object.
 *
 * Governance lives here, in ONE place: rate axes anchor at 0 (SPEC.y_axis_min),
 * the optional baseline is drawn as a reference line, and trailing still-maturing
 * points (SPEC.flag_last_n) render as hollow markers. Faithful only — it plots
 * exactly the points in the spec, never re-queries, smooths, or extrapolates.
 */
(function () {
  "use strict";

  function buildOption(SPEC, T, TOKENS) {
    var vf = SPEC.value_format || "count";
    function fmt(v) {
      if (v === null || v === undefined || (typeof v === "number" && isNaN(v))) return "—";
      if (vf === "percent") return (v * 100).toFixed(1) + "%";
      if (vf === "currency") return "$" + Math.round(v).toLocaleString();
      // "number": raw fractional values (an unformatted ad-hoc ratio) —
      // significant digits, never rounded to an integer.
      if (vf === "number") return Number(v.toPrecision(4)).toString();
      return Math.round(v).toLocaleString();
    }
    var PALETTE = TOKENS.series_palette;

    function areaFill(hex) {
      var a = TOKENS.series_gradient_alpha;
      function rgba(al) {
        var n = parseInt(hex.slice(1), 16);
        return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + al + ")";
      }
      return { type: "linear", x: 0, y: 0, x2: 0, y2: 1,
        colorStops: [{ offset: 0, color: rgba(a * 2) }, { offset: 1, color: rgba(0) }] };
    }

    var axisCommon = {
      axisLine: { lineStyle: { color: T.axis_line } },
      axisTick: { show: false },
      axisLabel: { color: T.ink_muted, fontSize: TOKENS.font.axis_size },
      splitLine: { lineStyle: { color: T.grid } }
    };
    var tooltipCommon = {
      backgroundColor: T.tooltip_bg, borderWidth: 0,
      textStyle: { color: T.tooltip_ink, fontSize: 12.5 },
      padding: [9, 12],
      extraCssText: "border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.18)"
    };

    function baselineMark(axisPercentZero) {
      if (SPEC.baseline === null || SPEC.baseline === undefined) return undefined;
      return {
        silent: true, symbol: "none",
        lineStyle: { color: T.baseline, type: "dashed", width: 1 },
        label: { formatter: "baseline " + fmt(SPEC.baseline), color: T.baseline,
                 position: "insideEndTop", fontSize: 11, rotate: 0 },
        data: [axisPercentZero ? { xAxis: SPEC.baseline } : { yAxis: SPEC.baseline }]
      };
    }

    function lineOption() {
      var multi = SPEC.series.length > 1;
      var cats = [];
      SPEC.series.forEach(function (s) {
        s.points.forEach(function (p) { if (cats.indexOf(p.x) < 0) cats.push(p.x); });
      });
      cats.sort();
      var flagN = SPEC.flag_last_n || 0;

      var series = SPEC.series.map(function (s, i) {
        var color = PALETTE[i % PALETTE.length];
        var byX = {}; s.points.forEach(function (p) { byX[p.x] = p; });
        var data = cats.map(function (x, idx) {
          var p = byX[x];
          var y = p ? p.y : null;
          var maturing = flagN && idx >= cats.length - flagN;
          var item = { value: y };
          if (maturing && y !== null) {
            item.symbol = "circle"; item.symbolSize = 9;
            item.itemStyle = { color: T.card_bg, borderColor: color, borderWidth: 2 };
          }
          return item;
        });
        return {
          name: s.name, type: "line", data: data, smooth: 0.35, symbol: "circle",
          symbolSize: 7, showSymbol: true, connectNulls: false,
          lineStyle: { width: 2.5, color: color },
          itemStyle: { color: color },
          areaStyle: multi ? undefined : { color: areaFill(color) },
          emphasis: { focus: "series" },
          markLine: i === 0 ? baselineMark(false) : undefined,
          endLabel: { show: true, formatter: function (pp) { return fmt(pp.value); },
                      color: color, fontWeight: 700, fontSize: 12 }
        };
      });

      return {
        color: PALETTE,
        grid: { left: 8, right: 56, top: 18, bottom: 6, containLabel: true },
        tooltip: Object.assign({ trigger: "axis",
          valueFormatter: function (v) { return fmt(v); } }, tooltipCommon),
        legend: multi ? { show: true, top: 0, right: 0, icon: "roundRect",
          textStyle: { color: T.ink_muted } } : { show: false },
        xAxis: Object.assign({ type: "category", data: cats, boundaryGap: false,
          name: SPEC.x_label || "", nameLocation: "middle", nameGap: 34,
          nameTextStyle: { color: T.ink_faint } }, axisCommon),
        yAxis: Object.assign({ type: "value",
          min: SPEC.y_axis_min !== undefined ? SPEC.y_axis_min : null,
          axisLabel: { color: T.ink_muted, fontSize: TOKENS.font.axis_size,
            formatter: function (v) { return fmt(v); } },
          axisLine: { show: false }, axisTick: { show: false },
          splitLine: { lineStyle: { color: T.grid } } }, {}),
        series: series,
        animationDuration: 700, animationEasing: "cubicOut"
      };
    }

    function rankedBarOption() {
      var bars = SPEC.bars.slice();          // chart_spec.py already sorts desc
      var labels = bars.map(function (b) { return b.label; }).reverse();  // largest on top
      var values = bars.map(function (b) { return b.y === null ? 0 : b.y; }).reverse();
      return {
        color: PALETTE,
        grid: { left: 8, right: 64, top: 14, bottom: 6, containLabel: true },
        tooltip: Object.assign({ trigger: "axis", axisPointer: { type: "shadow" },
          valueFormatter: function (v) { return fmt(v); } }, tooltipCommon),
        xAxis: Object.assign({}, axisCommon, { type: "value", min: 0,
          name: SPEC.as_of ? "as of " + SPEC.as_of : "",
          nameLocation: "middle", nameGap: 32, nameTextStyle: { color: T.ink_faint },
          axisLabel: { color: T.ink_muted, fontSize: TOKENS.font.axis_size,
            formatter: function (v) { return fmt(v); } } }),
        yAxis: { type: "category", data: labels,
          axisLine: { show: false }, splitLine: { show: false },
          axisTick: { show: false },
          axisLabel: { color: T.ink, fontSize: TOKENS.font.label_size } },
        series: [{
          type: "bar", data: values, barMaxWidth: 30,
          itemStyle: { borderRadius: [0, TOKENS.radius.bar, TOKENS.radius.bar, 0],
            color: function (p) { return PALETTE[(values.length - 1 - p.dataIndex) % PALETTE.length]; } },
          label: { show: true, position: "right", color: T.ink, fontWeight: 700,
            fontSize: 12, formatter: function (p) { return fmt(p.value); } },
          markLine: baselineMark(true),
          emphasis: { itemStyle: { opacity: 0.85 } }
        }],
        animationDuration: 700, animationEasing: "cubicOut"
      };
    }

    function funnelOption() {
      var stages = SPEC.stages;
      var data = stages.map(function (s, i) {
        var detail = (s.num != null && s.den) ?
          " (" + s.num.toLocaleString() + "/" + s.den.toLocaleString() + ")" : "";
        return {
          name: s.label,
          value: s.rate === null ? 0 : s.rate * 100,
          itemStyle: { color: PALETTE[i % PALETTE.length], borderColor: T.card_bg, borderWidth: 2 },
          _label: s.label, _rate: s.rate, _detail: detail
        };
      });
      var cum = SPEC.cumulative_passthrough;
      return {
        tooltip: Object.assign({ trigger: "item",
          formatter: function (p) {
            return p.data._label + "<br/><b>" + fmt(p.data._rate) + "</b>" + p.data._detail; }
        }, tooltipCommon),
        title: cum != null ? {
          text: "end-to-end pass-through ≈ " + fmt(cum),
          right: 0, top: 0, textStyle: { color: T.ink_muted, fontSize: 13, fontWeight: 600 }
        } : undefined,
        series: [{
          type: "funnel", sort: "none", top: 40, bottom: 10, left: "8%", right: "8%",
          minSize: "26%", maxSize: "100%", gap: 3,
          label: { show: true, position: "inside", color: "#fff", fontWeight: 600,
            formatter: function (p) { return p.data._label + "  " + fmt(p.data._rate); } },
          labelLine: { show: false },
          emphasis: { label: { fontSize: 14 } },
          data: data
        }],
        animationDuration: 700, animationEasing: "cubicOut"
      };
    }

    var BUILDERS = {
      single_line: lineOption, multi_series_line: lineOption,
      ranked_bar: rankedBarOption, funnel: funnelOption
    };
    var build = BUILDERS[SPEC.chart_type];
    return build ? build() : null;
  }

  if (typeof window !== "undefined") window.KattfotCharts = { buildOption: buildOption };
})();
