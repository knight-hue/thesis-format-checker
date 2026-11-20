import type { components } from './api.gen'

export type ApiIssue = components['schemas']['IssueResponse']
// extra="allow" 会生成字符串索引签名；先保留已声明字段，避免 Omit/Pick
// 将这些字段吞成 unknown。运行时额外字段仍随原对象完整保留。
type IssueFields = {
  [Key in keyof ApiIssue as string extends Key ? never : number extends Key ? never : Key]: ApiIssue[Key]
}
export type IssueSeverity = ApiIssue['severity']
export type IssueSource = ApiIssue['source']
// OpenAPI 将 actual/expected 声明为 unknown。保留原始取值，避免把对象/数组
// 强转为字符串后改变修复请求的内容；展示层按原有方式格式化。
export type IssueValue = ApiIssue['actual']
export type Issue = IssueFields & { _id: string }
export type IssueInput = IssueFields & { _id?: string }
export type IssuePreview = Partial<IssueFields>
export type IssueVariant = components['schemas']['IssueVariantResponse']
