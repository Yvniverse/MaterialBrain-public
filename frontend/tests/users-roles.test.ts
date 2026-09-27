import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'src/views/Users.vue'), 'utf8')

describe('user and role administration', () => {
  it('provides guarded user deletion and explains retained history', () => {
    expect(source).toContain('data-testid="delete-user"')
    expect(source).toContain('await api.delete(`/users/${row.id}`)')
    expect(source).toContain('不能删除当前登录账号')
    expect(source).toContain('历史库存流水和审计记录会继续保留')
  })

  it('edits permissions on existing roles using Chinese grouped controls', () => {
    expect(source).toContain('角色权限')
    expect(source).toContain('功能权限')
    expect(source).toContain('系统管理')
    expect(source).toContain('data-testid="save-role-permissions"')
    expect(source).toContain('await api.put<Role>(`/roles/${roleDraft.id}`')
  })

  it('separates picking visibility from picking execution permissions', () => {
    expect(source).toContain("value: 'picking:view'")
    expect(source).toContain("value: 'picking:operate'")
    expect(source).toContain("name: '生产拣料'")
  })

  it('gives every two-line permission label an explicit non-zero line height', () => {
    expect(source).toContain('class="group-title"')
    expect(source).toContain('class="group-description"')
    expect(source).toContain('class="permission-name"')
    expect(source).toContain('class="permission-description"')
    expect(source).toMatch(/\.group-title\{[^}]*line-height:21px/)
    expect(source).toMatch(/\.group-description\{[^}]*line-height:17px/)
    expect(source).toMatch(/\.permission-name\{[^}]*line-height:20px/)
    expect(source).toMatch(/\.permission-description\{[^}]*line-height:17px/)
  })
})
