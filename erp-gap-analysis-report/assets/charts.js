(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();
  var danger = style.getPropertyValue('--danger').trim();
  var warn = style.getPropertyValue('--warn').trim();
  var success = style.getPropertyValue('--success').trim();
  var medium = style.getPropertyValue('--medium').trim();

  var baseTextStyle = { color: ink, fontFamily: 'InstrumentSans, sans-serif' };
  var baseGrid = { left: '3%', right: '4%', bottom: '8%', top: '15%', containLabel: true };

  // ================================================================
  // 图1：11维度生产就绪度雷达图
  // ================================================================
  var radarEl = document.getElementById('chart-radar');
  if (radarEl) {
    var radarChart = echarts.init(radarEl, null, { renderer: 'svg' });
    radarChart.setOption({
      animation: false,
      textStyle: baseTextStyle,
      tooltip: { trigger: 'item', appendToBody: true },
      legend: {
        data: ['当前状态', '生产标准'],
        bottom: 0,
        textStyle: { color: muted, fontSize: 12 }
      },
      radar: {
        center: ['50%', '48%'],
        radius: '65%',
        indicator: [
          { name: '架构完整性', max: 5 },
          { name: '安全与合规', max: 5 },
          { name: '数据层成熟度', max: 5 },
          { name: 'API与集成层', max: 5 },
          { name: '测试与质量保障', max: 5 },
          { name: '部署与DevOps', max: 5 },
          { name: 'LLM/AI集成质量', max: 5 },
          { name: '前端完成度', max: 5 },
          { name: '文档生成与导出', max: 5 },
          { name: '文档与知识管理', max: 5 },
          { name: '生产运维就绪度', max: 5 }
        ],
        splitArea: { areaStyle: { color: ['rgba(255,255,255,0.02)', 'rgba(255,255,255,0.04)'] } },
        axisLine: { lineStyle: { color: rule } },
        splitLine: { lineStyle: { color: rule } },
        name: { textStyle: { color: ink, fontSize: 11 } }
      },
      series: [{
        name: '就绪度对比',
        type: 'radar',
        data: [
          {
            value: [3, 2, 1, 3, 2, 2, 3, 2, 0, 1, 1],
            name: '当前状态',
            areaStyle: { color: accent, opacity: 0.25 },
            lineStyle: { color: accent, width: 2 },
            itemStyle: { color: accent }
          },
          {
            value: [4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4],
            name: '生产标准',
            areaStyle: { color: accent2, opacity: 0.15 },
            lineStyle: { color: accent2, width: 2, type: 'dashed' },
            itemStyle: { color: accent2 }
          }
        ]
      }]
    });
    window.addEventListener('resize', function() { radarChart.resize(); });
  }

  // ================================================================
  // 图2：风险等级分布饼图（环形）
  // ================================================================
  var riskPieEl = document.getElementById('chart-risk-pie');
  if (riskPieEl) {
    var riskPieChart = echarts.init(riskPieEl, null, { renderer: 'svg' });
    riskPieChart.setOption({
      animation: false,
      textStyle: baseTextStyle,
      tooltip: { trigger: 'item', appendToBody: true, formatter: '{b}: {c}项 ({d}%)' },
      legend: {
        bottom: 0,
        textStyle: { color: muted, fontSize: 12 }
      },
      series: [{
        name: '风险等级分布',
        type: 'pie',
        radius: ['40%', '70%'],
        center: ['50%', '45%'],
        avoidLabelOverlap: true,
        label: {
          show: true,
          formatter: '{b}\n{c}项',
          color: ink,
          fontSize: 13
        },
        labelLine: { lineStyle: { color: rule } },
        data: [
          { value: 6, name: 'Critical', itemStyle: { color: danger } },
          { value: 17, name: 'High', itemStyle: { color: warn } },
          { value: 10, name: 'Medium', itemStyle: { color: medium } },
          { value: 2, name: 'Low', itemStyle: { color: success } }
        ]
      }]
    });
    window.addEventListener('resize', function() { riskPieChart.resize(); });
  }

  // ================================================================
  // 图3：维度差距严重度柱状图
  // ================================================================
  var gapBarEl = document.getElementById('chart-gap-bar');
  if (gapBarEl) {
    var gapBarChart = echarts.init(gapBarEl, null, { renderer: 'svg' });
    var gapData = [
      { name: '架构完整性', value: 1 },
      { name: '安全与合规', value: 2 },
      { name: '数据层成熟度', value: 3 },
      { name: 'API与集成层', value: 1 },
      { name: '测试与质量保障', value: 2 },
      { name: '部署与DevOps', value: 2 },
      { name: 'LLM/AI集成质量', value: 1 },
      { name: '前端完成度', value: 2 },
      { name: '文档生成与导出', value: 4 },
      { name: '文档与知识管理', value: 3 },
      { name: '生产运维就绪度', value: 3 }
    ];
    gapBarChart.setOption({
      animation: false,
      textStyle: baseTextStyle,
      tooltip: { trigger: 'axis', appendToBody: true, formatter: '{b}: 差距 {c} 级' },
      grid: baseGrid,
      xAxis: {
        type: 'category',
        data: gapData.map(function(d) { return d.name; }),
        axisLabel: { color: muted, fontSize: 10, rotate: 35, interval: 0 },
        axisLine: { lineStyle: { color: rule } }
      },
      yAxis: {
        type: 'value',
        name: '差距级数',
        nameTextStyle: { color: muted },
        max: 5,
        axisLabel: { color: muted },
        splitLine: { lineStyle: { color: rule } }
      },
      series: [{
        name: '差距',
        type: 'bar',
        data: gapData.map(function(d) {
          var c = d.value >= 4 ? danger : (d.value >= 3 ? danger : (d.value >= 2 ? warn : success));
          return { value: d.value, itemStyle: { color: c, borderRadius: [4, 4, 0, 0] } };
        }),
        barWidth: '50%',
        label: { show: true, position: 'top', color: ink, fontSize: 12 }
      }]
    });
    window.addEventListener('resize', function() { gapBarChart.resize(); });
  }

  // ================================================================
  // 图4：后端Agent测试覆盖分布（堆积柱状图）
  // ================================================================
  var testCovEl = document.getElementById('chart-test-coverage');
  if (testCovEl) {
    var testCovChart = echarts.init(testCovEl, null, { renderer: 'svg' });
    testCovChart.setOption({
      animation: false,
      textStyle: baseTextStyle,
      tooltip: {
        trigger: 'axis',
        appendToBody: true,
        axisPointer: { type: 'shadow' },
        formatter: function(params) {
          var s = params[0].name + '<br/>';
          params.forEach(function(p) {
            s += p.marker + ' ' + p.seriesName + ': ' + p.value + '个<br/>';
          });
          return s;
        }
      },
      legend: {
        bottom: 0,
        textStyle: { color: muted, fontSize: 12 }
      },
      grid: { left: '3%', right: '4%', bottom: '12%', top: '10%', containLabel: true },
      xAxis: {
        type: 'value',
        axisLabel: { color: muted },
        splitLine: { lineStyle: { color: rule } }
      },
      yAxis: {
        type: 'category',
        data: ['Agent测试覆盖'],
        axisLabel: { color: ink, fontSize: 13 },
        axisLine: { lineStyle: { color: rule } }
      },
      series: [
        {
          name: '已覆盖测试',
          type: 'bar',
          stack: 'total',
          data: [13],
          itemStyle: { color: success, borderRadius: [0, 0, 0, 0] },
          barWidth: 40,
          label: { show: true, formatter: '13个 (39%)', color: '#fff', fontSize: 12, position: 'inside' }
        },
        {
          name: '未覆盖测试',
          type: 'bar',
          stack: 'total',
          data: [20],
          itemStyle: { color: danger, borderRadius: [0, 4, 4, 0] },
          label: { show: true, formatter: '20个 (61%)', color: '#fff', fontSize: 12, position: 'inside' }
        }
      ]
    });
    window.addEventListener('resize', function() { testCovChart.resize(); });
  }

  // ================================================================
  // 图5：前后端集成度对比（分组柱状图）
  // ================================================================
  var feIntegEl = document.getElementById('chart-frontend-integration');
  if (feIntegEl) {
    var feIntegChart = echarts.init(feIntegEl, null, { renderer: 'svg' });
    feIntegChart.setOption({
      animation: false,
      textStyle: baseTextStyle,
      tooltip: { trigger: 'axis', appendToBody: true, axisPointer: { type: 'shadow' } },
      legend: {
        bottom: 0,
        textStyle: { color: muted, fontSize: 12 }
      },
      grid: { left: '3%', right: '4%', bottom: '12%', top: '10%', containLabel: true },
      xAxis: {
        type: 'category',
        data: ['认证/权限', '会计/税务', '库存/采购', 'HR/薪酬', 'BI/报表', '其他页面'],
        axisLabel: { color: muted, fontSize: 11, rotate: 20 },
        axisLine: { lineStyle: { color: rule } }
      },
      yAxis: {
        type: 'value',
        name: '页面数',
        nameTextStyle: { color: muted },
        axisLabel: { color: muted },
        splitLine: { lineStyle: { color: rule } }
      },
      series: [
        {
          name: '已接API',
          type: 'bar',
          data: [3, 2, 2, 2, 1, 2],
          itemStyle: { color: success, borderRadius: [4, 4, 0, 0] },
          barGap: '10%',
          label: { show: true, position: 'top', color: ink, fontSize: 11 }
        },
        {
          name: '硬编码Mock',
          type: 'bar',
          data: [1, 3, 2, 3, 4, 7],
          itemStyle: { color: danger, borderRadius: [4, 4, 0, 0] },
          label: { show: true, position: 'top', color: ink, fontSize: 11 }
        }
      ]
    });
    window.addEventListener('resize', function() { feIntegChart.resize(); });
  }

  // ================================================================
  // 图6：11维度风险矩阵热力图
  // ================================================================
  var riskHeatEl = document.getElementById('chart-risk-heatmap');
  if (riskHeatEl) {
    var riskHeatChart = echarts.init(riskHeatEl, null, { renderer: 'svg' });
    var dims = [
      '架构完整性', '安全与合规', '数据层成熟度', 'API与集成层',
      '测试与质量保障', '部署与DevOps', 'LLM/AI集成质量', '前端完成度',
      '文档生成与导出', '文档与知识管理', '生产运维就绪度'
    ];
    var riskLevels = ['Critical', 'High', 'Medium', 'Low'];
    var heatData = [
      [0, 0, 0], [0, 1, 0], [0, 2, 0], [0, 3, 0],
      [1, 0, 2], [1, 1, 3], [1, 2, 1], [1, 3, 0],
      [2, 0, 2], [2, 1, 1], [2, 2, 1], [2, 3, 0],
      [3, 0, 0], [3, 1, 0], [3, 2, 1], [3, 3, 1],
      [4, 0, 0], [4, 1, 3], [4, 2, 1], [4, 3, 0],
      [5, 0, 0], [5, 1, 2], [5, 2, 2], [5, 3, 0],
      [6, 0, 0], [6, 1, 0], [6, 2, 2], [6, 3, 0],
      [7, 0, 0], [7, 1, 2], [7, 2, 0], [7, 3, 1],
      [8, 0, 1], [8, 1, 0], [8, 2, 0], [8, 3, 0],
      [9, 0, 0], [9, 1, 2], [9, 2, 2], [9, 3, 0],
      [10, 0, 1], [10, 1, 4], [10, 2, 0], [10, 3, 0]
    ];
    var heatColors = [
      [0, 'rgba(15,17,23,0.6)'],
      [0.5, 'rgba(79,124,255,0.3)'],
      [1, 'rgba(245,166,35,0.7)'],
      [2, 'rgba(239,68,68,0.85)'],
      [4, 'rgba(239,68,68,1)']
    ];
    riskHeatChart.setOption({
      animation: false,
      textStyle: baseTextStyle,
      tooltip: {
        appendToBody: true,
        formatter: function(p) {
          return dims[p.data[1]] + ' - ' + riskLevels[p.data[0]] + '<br/>风险数: ' + p.data[2] + ' 项';
        }
      },
      grid: { left: '15%', right: '8%', bottom: '10%', top: '8%' },
      xAxis: {
        type: 'category',
        data: riskLevels,
        name: '风险等级',
        nameLocation: 'middle',
        nameGap: 30,
        nameTextStyle: { color: muted },
        axisLabel: { color: ink, fontSize: 12 },
        splitArea: { show: true, areaStyle: { color: ['rgba(255,255,255,0.02)', 'rgba(255,255,255,0.04)'] } },
        axisLine: { lineStyle: { color: rule } }
      },
      yAxis: {
        type: 'category',
        data: dims,
        axisLabel: { color: ink, fontSize: 11 },
        splitArea: { show: true, areaStyle: { color: ['rgba(255,255,255,0.02)', 'rgba(255,255,255,0.04)'] } },
        axisLine: { lineStyle: { color: rule } }
      },
      visualMap: {
        min: 0,
        max: 4,
        calculable: true,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        textStyle: { color: muted },
        inRange: { color: ['rgba(15,17,23,0.8)', 'rgba(79,124,255,0.4)', 'rgba(245,166,35,0.7)', 'rgba(239,68,68,0.85)', 'rgba(239,68,68,1)'] }
      },
      series: [{
        name: '风险分布',
        type: 'heatmap',
        data: heatData,
        label: {
          show: true,
          color: '#fff',
          fontSize: 12,
          formatter: function(p) { return p.data[2] > 0 ? p.data[2] : ''; }
        },
        emphasis: {
          itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.5)' }
        }
      }]
    });
    window.addEventListener('resize', function() { riskHeatChart.resize(); });
  }

  // ================================================================
  // 图7：改进路线图（P0-P3阶段柱状图）
  // ================================================================
  var roadmapEl = document.getElementById('chart-roadmap');
  if (roadmapEl) {
    var roadmapChart = echarts.init(roadmapEl, null, { renderer: 'svg' });
    roadmapChart.setOption({
      animation: false,
      textStyle: baseTextStyle,
      tooltip: {
        trigger: 'axis',
        appendToBody: true,
        axisPointer: { type: 'shadow' },
        formatter: function(params) {
          var p = params[0];
          var desc = {
            'P0 生产阻断': '必须在部署前完成',
            'P1 就绪加固': '部署后立即推进',
            'P2 质量提升': '中期质量改进',
            'P3 持续优化': '长期持续优化'
          };
          return p.name + '<br/>任务数: ' + p.value + ' 项<br/>' + (desc[p.name] || '');
        }
      },
      grid: { left: '3%', right: '4%', bottom: '8%', top: '12%', containLabel: true },
      xAxis: {
        type: 'category',
        data: ['P0 生产阻断', 'P1 就绪加固', 'P2 质量提升', 'P3 持续优化'],
        axisLabel: { color: ink, fontSize: 12 },
        axisLine: { lineStyle: { color: rule } }
      },
      yAxis: {
        type: 'value',
        name: '任务数',
        nameTextStyle: { color: muted },
        axisLabel: { color: muted },
        splitLine: { lineStyle: { color: rule } }
      },
      series: [{
        name: '任务数',
        type: 'bar',
        data: [
          { value: 7, itemStyle: { color: danger, borderRadius: [4, 4, 0, 0] } },
          { value: 8, itemStyle: { color: warn, borderRadius: [4, 4, 0, 0] } },
          { value: 6, itemStyle: { color: medium, borderRadius: [4, 4, 0, 0] } },
          { value: 6, itemStyle: { color: accent2, borderRadius: [4, 4, 0, 0] } }
        ],
        barWidth: '40%',
        label: {
          show: true,
          position: 'top',
          color: ink,
          fontSize: 14,
          fontWeight: 'bold',
          formatter: '{c}项'
        }
      }]
    });
    window.addEventListener('resize', function() { roadmapChart.resize(); });
  }

  // ================================================================
  // 图8：Agent文档生成能力矩阵（热力图）
  // ================================================================
  var docMatEl = document.getElementById('chart-doc-matrix');
  if (docMatEl) {
    var docMatChart = echarts.init(docMatEl, null, { renderer: 'svg' });
    var agents = [
      '会计Agent', '税务Agent', '库存Agent', '采购Agent',
      'BI Agent', 'HR Agent', '薪酬Agent', '发票Agent',
      '报销Agent', '审计Agent', '风控Agent', 'BI Agent(PPT)'
    ];
    var formats = ['DOCX', 'PDF', 'PPTX', 'XLSX'];
    // 0 = 未规划, 1 = 已规划(未实现), 2 = 已实现
    // 当前状态：全部已规划但未实现
    var docData = [
      [0, 0, 0], [0, 1, 1], [0, 2, 0], [0, 3, 1],
      [1, 0, 0], [1, 1, 1], [1, 2, 0], [1, 3, 1],
      [2, 0, 0], [2, 1, 0], [2, 2, 0], [2, 3, 1],
      [3, 0, 1], [3, 1, 1], [3, 2, 0], [3, 3, 0],
      [4, 0, 0], [4, 1, 0], [4, 2, 1], [4, 3, 1],
      [5, 0, 1], [5, 1, 1], [5, 2, 0], [5, 3, 0],
      [6, 0, 0], [6, 1, 1], [6, 2, 0], [6, 3, 1],
      [7, 0, 0], [7, 1, 1], [7, 2, 0], [7, 3, 0],
      [8, 0, 1], [8, 1, 1], [8, 2, 0], [8, 3, 0],
      [9, 0, 1], [9, 1, 1], [9, 2, 0], [9, 3, 0],
      [10, 0, 0], [10, 1, 1], [10, 2, 0], [10, 3, 0],
      [11, 0, 0], [11, 1, 0], [11, 2, 1], [11, 3, 0]
    ];
    docMatChart.setOption({
      animation: false,
      textStyle: baseTextStyle,
      title: {
        text: '当前状态：全部为"已规划·未实现"',
        subtext: '红色=已规划未实现  |  深色=未规划',
        left: 'center',
        top: 0,
        textStyle: { color: danger, fontSize: 13 },
        subtextStyle: { color: muted, fontSize: 11 }
      },
      tooltip: {
        appendToBody: true,
        formatter: function(p) {
          var status = p.data[2] === 0 ? '未规划' : (p.data[2] === 1 ? '已规划·未实现' : '已实现');
          return agents[p.data[0]] + ' - ' + formats[p.data[1]] + '<br/>状态: ' + status;
        }
      },
      grid: { left: '15%', right: '8%', bottom: '12%', top: '15%' },
      xAxis: {
        type: 'category',
        data: formats,
        name: '文件格式',
        nameLocation: 'middle',
        nameGap: 30,
        nameTextStyle: { color: muted },
        axisLabel: { color: ink, fontSize: 12 },
        splitArea: { show: true, areaStyle: { color: ['rgba(255,255,255,0.02)', 'rgba(255,255,255,0.04)'] } },
        axisLine: { lineStyle: { color: rule } }
      },
      yAxis: {
        type: 'category',
        data: agents,
        axisLabel: { color: ink, fontSize: 11 },
        splitArea: { show: true, areaStyle: { color: ['rgba(255,255,255,0.02)', 'rgba(255,255,255,0.04)'] } },
        axisLine: { lineStyle: { color: rule } }
      },
      visualMap: {
        min: 0,
        max: 2,
        calculable: false,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        textStyle: { color: muted },
        inRange: { color: ['rgba(15,17,23,0.8)', 'rgba(239,68,68,0.7)', 'rgba(34,197,94,0.8)'] },
        text: ['已实现', '未规划'],
        show: true
      },
      series: [{
        name: '文档生成能力',
        type: 'heatmap',
        data: docData,
        label: {
          show: true,
          color: '#fff',
          fontSize: 11,
          formatter: function(p) {
            return p.data[2] === 1 ? '待实现' : (p.data[2] === 2 ? '已实现' : '');
          }
        },
        emphasis: {
          itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.5)' }
        }
      }]
    });
    window.addEventListener('resize', function() { docMatChart.resize(); });
  }

  // ================================================================
  // Mermaid 初始化
  // ================================================================
  if (typeof mermaid !== 'undefined') {
    mermaid.initialize({
      startOnLoad: true,
      theme: 'dark',
      themeVariables: {
        primaryColor: '#1e2330',
        primaryTextColor: '#e4e7ee',
        primaryBorderColor: '#4f7cff',
        lineColor: '#8892a6',
        secondaryColor: '#161922',
        tertiaryColor: '#0f1117',
        background: '#0f1117',
        mainBkg: '#1e2330',
        secondBkg: '#161922',
        textColor: '#e4e7ee',
        nodeBorder: '#4f7cff',
        clusterBkg: '#161922',
        clusterBorder: '#2a3142',
        edgeLabelBackground: '#0f1117'
      },
      flowchart: {
        curve: 'basis',
        useMaxWidth: true,
        htmlLabels: true
      },
      securityLevel: 'loose'
    });
  }
})();
