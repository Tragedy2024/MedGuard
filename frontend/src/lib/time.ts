/**
 * 时间显示。
 *
 * 后端返回的是 ISO-8601 UTC（带 Z）。界面上按**北京时间**显示。
 *
 * 本产品部署在中国医院，用户看到的必须是本地时间；而库里存 UTC 是为了
 * 这个字段离开前端之后仍然无歧义——转换发生在展示层，不是存储层。
 * 反过来的做法（库里存北京时间）会让该字段失去时区标记，将来谁读都得先考古。
 */

const BEIJING = 'Asia/Shanghai'

export function beijingTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso   // 认不出就原样显示，别丢信息
  // sv-SE 的本地化输出恰好是 `YYYY-MM-DD HH:MM:SS`，与原来的观感一致，
  // 省去手工拼 Intl.formatToParts
  return d.toLocaleString('sv-SE', { timeZone: BEIJING })
}
