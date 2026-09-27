const replacements: Array<[RegExp, string]> = [
  [/(?:[;；]\s*)?demo stock is synthetic\.?/gi, ''],
  [/(?:[;；]\s*)?stock is synthetic\.?/gi, ''],
  [/(?:[;；]\s*)?demo metadata\b/gi, ''],
  [/\bdemo\s+(?=(?:module|electromechanical sensor|dc\/dc module)\b)/gi, ''],
  [/Synthetic Portfolio cable catalog item\.?/gi, ''],
  [/Current quantity comes only from the governed synthetic inbound movement\.?/gi, ''],
  [/Portfolio demo synthetic data;?\s*for resume\/project demonstration only\.?/gi, ''],
  [/for resume\/project demonstration only\.?/gi, ''],
  [/\[\s*Portfolio Demo(?: v2)?\s*\]/gi, ''],
  [/业务数据\s*[:：]\s*/g, ''],
  [/项目数据\s*[:：]\s*/g, ''],
  [/演示数据\s*[:：]\s*/g, ''],
  [/线缆目录物料。当前数量仅来自受控的库存入库流程。?/g, ''],
  [/cable catalog item\. Current quantity comes only from the governed movement\.?/gi, ''],
  [/(?:内部工程)+(?:项目)?\s*[-:：·|]?\s*/g, ''],
  [/秋招作品展示[:：]?/g, ''],
  [/作品集演示|作品集产品|秋招|作品展示|简历项目|面试展示|求职|作品集/g, ''],
  [/Portfolio review\s*[:：]?/gi, ''],
  [/Anonymous Portfolio\s*[:：]?/gi, ''],
  [/Portfolio Demo(?: v2)?|Portfolio Showcase|Demo Only|Synthetic Test Data/gi, ''],
  [/Resume Project|Job Hunting/gi, ''],
  [/Synthetic Portfolio/gi, ''],
  [/Anonymous Portfolio/gi, ''],
  [/Portfolio v2\.5(?:\.1)?/gi, ''],
  [/\bsynthetic\s+(?:demo|data|inbound)\b/gi, ''],
]

/** Remove legacy development provenance without inventing replacement business copy. */
export function sanitizeBusinessCopy(value: unknown): string {
  let text = String(value ?? '')
  for (const [pattern, replacement] of replacements) text = text.replace(pattern, replacement)
  return text
    .replace(/\[\s*\]/g, '')
    .replace(/^[\s;；,.。]+/, '')
    .replace(/\n\s*\n+/g, '\n')
    .replace(/^[-·|：:]\s*/, '')
    .replace(/\s{2,}/g, ' ')
    .trim()
}
