export interface MaterialIdentitySource {
  name: string
  mpn?: string | null
  manufacturer?: string | null
  attributes?: Record<string, unknown> | null
}

function attributeText(material: MaterialIdentitySource, key: string): string {
  const value = material.attributes?.[key]
  return typeof value === 'string' || typeof value === 'number' ? String(value).trim() : ''
}

export function isInternalPart(material: MaterialIdentitySource): boolean {
  return (
    attributeText(material, 'part_number_kind') === 'internal' ||
    material.attributes?.internal_part_number === true
  )
}

export function materialPrimaryIdentity(material: MaterialIdentitySource): string {
  const mpn = String(material.mpn || '').trim()
  const catalogMpn = attributeText(material, 'catalog_mpn_hint')
  const kind = attributeText(material, 'material_kind')
  if (kind === 'cable' && catalogMpn && (!mpn || isInternalPart(material))) {
    const rawLength = attributeText(material, 'length_cm')
    const parsedLength = Number(rawLength)
    const length = rawLength && Number.isFinite(parsedLength) ? `${parsedLength} cm` : ''
    return [catalogMpn, length].filter(Boolean).join(' · ')
  }
  if (isInternalPart(material)) return material.name.trim() || mpn
  return mpn || material.name.trim() || '未命名物料'
}

export function materialSecondaryIdentity(material: MaterialIdentitySource): string {
  const primary = materialPrimaryIdentity(material)
  const name = material.name.trim()
  const mpn = String(material.mpn || '').trim()
  if (isInternalPart(material)) return mpn ? `内部料号 ${mpn}` : ''
  if (name && name !== primary) return name
  return ''
}

export function materialPartNumberText(material: MaterialIdentitySource): string {
  const mpn = String(material.mpn || '').trim()
  if (!mpn) return '暂无 MPN'
  return isInternalPart(material) ? `内部料号 ${mpn}` : mpn
}
