import type { PresetQuery } from '../api/types'

/** 与后端 demo/queries.json 的键一一对应。 */
export const PRESET_QUERIES: PresetQuery[] = [
  { id: 'doctor_dept_visits',     question: '统计各科室接诊量',       tokenTypes: ['staff'] },
  { id: 'doctor_diabetes_cost',   question: '糖尿病患者产生了多少费用', tokenTypes: ['staff'] },
  { id: 'doctor_diagnosis_stats', question: '按诊断结果分类统计患者数', tokenTypes: ['staff'] },
  { id: 'doctor_export_roster',   question: '导出患者基本信息核对表',   tokenTypes: ['staff'] },
  { id: 'patient_my_lab',         question: '我上次的血糖是多少',     tokenTypes: ['patient'] },
  { id: 'patient_my_medication',  question: '医生给我开的药怎么吃',   tokenTypes: ['patient'] },
  { id: 'patient_my_imaging',     question: '我的影像报告怎么说',     tokenTypes: ['patient'] },
  { id: 'patient_doctors',        question: '心内科有哪些医生',       tokenTypes: ['patient'] },
  { id: 'patient_others_count',   question: '得这个病的有多少人',     tokenTypes: ['patient'] },
]

export const presetsFor = (type: 'staff' | 'patient') =>
  PRESET_QUERIES.filter(q => q.tokenTypes.includes(type))
