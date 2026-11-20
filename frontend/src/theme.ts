// 界面主题：侧栏固定深蓝，主题色只做强调色（选中项、指示条、点缀）。
// 通过 CSS 变量注入，组件用 var(--accent) / var(--accent-soft) 取值。

export const DEFAULT_THEME = '#4F8EF7'

// 低饱和强调色：不再用高饱和色整块铺满侧栏
export const THEME_COLORS = [
  { name: '学院蓝', value: '#4F8EF7' },
  { name: '松绿', value: '#0F766E' },
  { name: '砖红', value: '#B42318' },
  { name: '琥珀', value: '#B45309' },
  { name: '紫罗兰', value: '#6D28D9' },
  { name: '石墨', value: '#475569' },
] as const

export type ThemeColor = typeof THEME_COLORS[number]['value']
export type ThemeOption = typeof THEME_COLORS[number]

function hexToRgb(hex: string) {
  let h = (hex || '').replace('#', '')
  if (h.length === 3) h = h.split('').map((c) => c + c).join('')
  const n = parseInt(h, 16)
  if (Number.isNaN(n)) return { r: 255, g: 255, b: 255 }
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 }
}

function mix(hex: string, target: string, amount: number) {
  const a = hexToRgb(hex)
  const b = hexToRgb(target)
  const r = Math.round(a.r + (b.r - a.r) * amount)
  const g = Math.round(a.g + (b.g - a.g) * amount)
  const bl = Math.round(a.b + (b.b - a.b) * amount)
  return `rgb(${r}, ${g}, ${bl})`
}

const tint = (hex: string, amount: number) => mix(hex, '#ffffff', amount)
const shade = (hex: string, amount: number) => mix(hex, '#000000', amount)

export function isLight(hex: string) {
  const { r, g, b } = hexToRgb(hex)
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6
}

export function applyTheme(color: ThemeColor) {
  const c = color || DEFAULT_THEME
  const root = document.documentElement
  const soft = tint(c, 0.9)
  const strong = shade(c, 0.18)
  // 深色侧栏/顶栏用的亮色，以及第二个渐变端点（向紫偏移）
  const bright = tint(c, 0.38)
  const second = mix(c, '#7C3AED', 0.4)
  root.style.setProperty('--accent', c)
  root.style.setProperty('--accent-bright', bright)
  root.style.setProperty('--accent-2', second)
  root.style.setProperty('--accent-3', '#D9B96A')
  root.style.setProperty('--accent-soft', soft)
  root.style.setProperty('--accent-strong', strong)
  root.style.setProperty('--accent-contrast', isLight(c) ? '#172033' : '#ffffff')
  // 让 Element Plus 的按钮/开关/复选框/分页等也跟随强调色
  root.style.setProperty('--el-color-primary', c)
  root.style.setProperty('--el-color-primary-light-3', tint(c, 0.3))
  root.style.setProperty('--el-color-primary-light-5', tint(c, 0.5))
  root.style.setProperty('--el-color-primary-light-7', tint(c, 0.7))
  root.style.setProperty('--el-color-primary-light-8', tint(c, 0.8))
  root.style.setProperty('--el-color-primary-light-9', tint(c, 0.9))
  root.style.setProperty('--el-color-primary-dark-2', shade(c, 0.2))
  // 兼容旧变量
  root.style.setProperty('--theme-color', c)
  root.style.setProperty('--theme-tint', soft)
  root.style.setProperty('--theme-shade', strong)
  root.style.setProperty('--theme-text', '#ffffff')
  root.style.setProperty('--theme-text-soft', 'rgba(255, 255, 255, 0.72)')
  root.style.setProperty('--theme-hover', 'rgba(255, 255, 255, 0.08)')
}

export function loadTheme(): ThemeColor {
  try {
    const color = localStorage.getItem('themeColor')
    return THEME_COLORS.find((item) => item.value === color)?.value ?? DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}

export function saveTheme(color: ThemeColor) {
  try {
    localStorage.setItem('themeColor', color)
  } catch {
    /* ignore */
  }
}
