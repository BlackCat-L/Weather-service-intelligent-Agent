<script setup lang="ts">
/**
 * 出力曲线图。
 *
 * **数据从哪来**：不从回答文本里抠数字（那要靠正则解析自然语言，非常脆弱），
 * 而是直接从工具调用的返回结果里取结构化数据——轨迹里本来就有 `hourly` 数组，
 * 天然适合画图。这是「结构化数据留在工具层」这个设计的一个额外收益。
 *
 * 支持的图表由工具名决定，未识别的工具结果自动跳过（不报错）。
 */
import * as echarts from 'echarts'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { TraceEntry } from '@/api/types'

const props = defineProps<{ trace: TraceEntry[] }>()

const el = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

interface ChartSpec {
  title: string
  times: string[]
  primary: { name: string; unit: string; data: number[] }
  secondary?: { name: string; unit: string; data: number[] }
}

/** 从轨迹里找最后一个可绘制的工具结果 */
const spec = computed<ChartSpec | null>(() => {
  for (let i = props.trace.length - 1; i >= 0; i--) {
    const t = props.trace[i]
    const r = t.result as any
    if (!r || !Array.isArray(r.hourly) || !r.hourly.length) continue

    const times = r.hourly.map((x: any) => String(x.time ?? ''))
    if (t.name === 'calc_pv_output') {
      return {
        title: '光伏出力曲线',
        times,
        primary: {
          name: '出力',
          unit: 'kW',
          data: r.hourly.map((x: any) => Number(x.power_kw ?? 0)),
        },
        secondary: {
          name: '辐照',
          unit: 'W/m²',
          data: r.hourly.map((x: any) => Number(x.radiation_wm2 ?? 0)),
        },
      }
    }
    if (t.name === 'calc_wind_output') {
      return {
        title: '风电出力曲线',
        times,
        primary: {
          name: '出力',
          unit: 'kW',
          data: r.hourly.map((x: any) => Number(x.power_kw ?? 0)),
        },
        secondary: {
          name: '风速',
          unit: 'm/s',
          data: r.hourly.map((x: any) => Number(x.wind_ms ?? 0)),
        },
      }
    }
    if (t.name === 'get_forecast_hourly') {
      return {
        title: '逐小时气象要素',
        times,
        primary: {
          name: '气温',
          unit: '°C',
          data: r.hourly ? r.hourly.map((x: any) => Number(x.temp_c ?? 0))
                         : r.rows.map((x: any) => Number(x.temp_c ?? 0)),
        },
        secondary: {
          name: '风速',
          unit: 'km/h',
          data: r.hourly ? r.hourly.map((x: any) => Number(x.wind10_kmh ?? 0))
                         : r.rows.map((x: any) => Number(x.wind10_kmh ?? 0)),
        },
      }
    }
  }
  return null
})

function draw() {
  if (!el.value || !spec.value) return
  if (!chart || chart.isDisposed()) chart = echarts.init(el.value)

  const s = spec.value
  chart.setOption(
    {
      title: {
        text: s.title,
        left: 0,
        top: 0,
        textStyle: { fontSize: 13, fontWeight: 600 },
      },
      tooltip: { trigger: 'axis' },
      legend: { top: 0, right: 0, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11 } },
      grid: { left: 8, right: 8, top: 38, bottom: 4, containLabel: true },
      xAxis: {
        type: 'category',
        data: s.times,
        axisLabel: { fontSize: 10, color: '#94a3b8', interval: Math.max(0, Math.floor(s.times.length / 8) - 1) },
        axisLine: { lineStyle: { color: '#e3e8ef' } },
      },
      yAxis: [
        {
          type: 'value',
          name: s.primary.unit,
          nameTextStyle: { fontSize: 10, color: '#94a3b8' },
          axisLabel: { fontSize: 10, color: '#94a3b8' },
          splitLine: { lineStyle: { color: '#f0f3f7' } },
        },
        ...(s.secondary
          ? [
              {
                type: 'value',
                name: s.secondary.unit,
                nameTextStyle: { fontSize: 10, color: '#94a3b8' },
                axisLabel: { fontSize: 10, color: '#94a3b8' },
                splitLine: { show: false },
              },
            ]
          : []),
      ],
      series: [
        {
          name: s.primary.name,
          type: 'line',
          smooth: true,
          symbol: 'none',
          data: s.primary.data,
          areaStyle: { opacity: 0.12 },
          lineStyle: { width: 2, color: '#0d8f86' },
          itemStyle: { color: '#0d8f86' },
        },
        ...(s.secondary
          ? [
              {
                name: s.secondary.name,
                type: 'line',
                yAxisIndex: 1,
                smooth: true,
                symbol: 'none',
                data: s.secondary.data,
                lineStyle: { width: 1.5, type: 'dashed', color: '#f59e0b' },
                itemStyle: { color: '#f59e0b' },
              },
            ]
          : []),
      ],
    },
    true,
  )
}

function onResize() {
  chart?.resize()
}

onMounted(() => {
  draw()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})

// ⚠️ 必须用 flush: 'post'
//
// 踩过的坑：图表容器是 `v-if="spec"` 控制的，而 `watch` 默认是 **pre-flush**——
// 回调在组件重新渲染**之前**执行。此时容器还没被创建，`el.value` 仍是 null，
// draw() 直接 return，**图表永远画不出来**（而页面其他部分完全正常，
// 所以很容易被忽略，直到录演示视频时才发现右侧是空的）。
// post-flush 保证回调在 DOM 更新之后运行，容器已就位。
watch(spec, () => draw(), { deep: false, flush: 'post' })
</script>

<template>
  <div v-if="spec" class="chart card">
    <div ref="el" class="canvas" />
  </div>
</template>

<style scoped>
.chart {
  padding: 12px 14px 8px;
}
.canvas {
  width: 100%;
  height: 210px;
}
</style>
