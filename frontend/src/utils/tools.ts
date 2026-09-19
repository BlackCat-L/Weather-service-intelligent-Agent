/**
 * 工具的展示元数据。
 *
 * 后端返回的是英文函数名（geocode_place），直接显示给用户看不懂。
 * 这里做一层「机器名 → 人话」的映射，并且可以给每个工具配一个图标与说明，
 * 让轨迹面板一眼能读懂 Agent 在干什么。
 *
 * 新增工具时：后端注册完，这里补一条即可。没登记的会走默认样式，
 * 不会报错——这样后端先上新工具也不会把前端搞坏。
 */

export interface ToolMeta {
  label: string
  icon: string
  desc: string
  /** 结果里哪个字段适合做摘要展示 */
  summaryKey?: string
}

const REGISTRY: Record<string, ToolMeta> = {
  geocode_place: {
    label: '地名解析',
    icon: '📍',
    desc: '把地名转成经纬度',
    summaryKey: 'name',
  },
  get_forecast_daily: {
    label: '逐日预报',
    icon: '📅',
    desc: '查询未来若干天的天气',
  },
  get_forecast_hourly: {
    label: '逐小时数据',
    icon: '⏱️',
    desc: '查询某天的逐小时气象要素',
  },
  get_history_daily: {
    label: '历史再分析',
    icon: '📚',
    desc: 'ERA5 历史数据与统计',
  },
  calc_pv_output: {
    label: '光伏出力计算',
    icon: '☀️',
    desc: '按物理模型估算发电量与出力曲线',
  },
  calc_wind_output: {
    label: '风电出力计算',
    icon: '🌀',
    desc: '按功率曲线估算风机出力',
  },
  assess_weather_risk: {
    label: '风险与适宜性评估',
    icon: '⚠️',
    desc: '逐小时扫描设备风险与作业适宜性',
  },
  search_weather_knowledge: {
    label: '知识库检索',
    icon: '🔍',
    desc: '检索灾害标准与行业影响规则',
  },
}

export function toolMeta(name: string): ToolMeta {
  return (
    REGISTRY[name] || {
      label: name,
      icon: '🔧',
      desc: '自定义工具',
    }
  )
}
