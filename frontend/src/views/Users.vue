<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Delete, EditPen, Key, Plus, UserFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'
import type { Role, User } from '../types'

type PermissionOption = {
  value: string
  label: string
  description: string
}

type PermissionGroup = {
  name: string
  description: string
  options: PermissionOption[]
}

const permissionGroups: PermissionGroup[] = [
  {
    name: '工作台',
    description: '系统入口和库存总览',
    options: [
      { value: 'dashboard:view', label: '查看仪表盘', description: '查看库存汇总和近期动态' },
    ],
  },
  {
    name: '物料资料',
    description: '器件档案、分类和附件',
    options: [
      { value: 'material:view', label: '查看物料', description: '查询物料和线缆资料' },
      { value: 'material:manage', label: '管理物料', description: '新增、编辑和删除物料' },
      { value: 'category:manage', label: '管理分类', description: '维护分类树并调整器件分类' },
      { value: 'attachment:manage', label: '管理附件', description: '上传和删除物料附件' },
    ],
  },
  {
    name: '库存与库位',
    description: '库存数量、流水和物料盒',
    options: [
      { value: 'inventory:view', label: '查看库存流水', description: '查看库存数量和历史变动' },
      { value: 'inventory:operate', label: '执行库存操作', description: '入库、出库、盘点和调整' },
      { value: 'location:manage', label: '管理库位', description: '维护仓库、元件盒和格口' },
    ],
  },
  {
    name: '项目与采购',
    description: 'BOM、供应商和采购业务',
    options: [
      { value: 'project:view', label: '查看项目 / 生产任务', description: '只读查看项目执行需求、产品定义和 BOM' },
      { value: 'project:manage', label: '管理项目 / 生产任务', description: '创建项目并维护项目执行需求' },
      { value: 'supplier:manage', label: '管理供应商', description: '维护供应商资料' },
      { value: 'purchase:manage', label: '管理采购', description: '创建和维护采购业务' },
    ],
  },
  {
    name: '生产拣料',
    description: '查看拣料任务或执行库位确认',
    options: [
      { value: 'picking:view', label: '查看拣料任务', description: '查看拣料计划、路线和执行进度' },
      { value: 'picking:operate', label: '执行拣料', description: '确认实际库位取料并产生受控库存流水' },
    ],
  },
  {
    name: '数据交换',
    description: '批量导入与数据导出',
    options: [
      { value: 'import:manage', label: '导入数据', description: '导入物料和线缆订单' },
      { value: 'export:view', label: '导出数据', description: '导出物料和库存流水' },
    ],
  },
  {
    name: '系统管理',
    description: '账号、角色和审计',
    options: [
      { value: 'user:manage', label: '管理用户', description: '创建、停用和删除用户' },
      { value: 'role:manage', label: '管理角色', description: '创建角色并修改访问权限' },
      { value: 'audit:view', label: '查看审计日志', description: '查看系统关键操作记录' },
    ],
  },
]

const allPermissionValues = permissionGroups.flatMap((group) =>
  group.options.map((option) => option.value),
)
const auth = useAuthStore()
const router = useRouter()
const users = ref<User[]>([])
const roles = ref<Role[]>([])
const loading = ref(false)
const userDialog = ref(false)
const creatingUser = ref(false)
const deletingUserIds = ref<number[]>([])
const updatingUserIds = ref<number[]>([])
const activeTab = ref(auth.can('user:manage') ? 'users' : 'roles')
const roleSaving = ref(false)
const allAccess = ref(false)
const roleDraft = reactive({
  id: null as number | null,
  name: '',
  description: '',
  permissions: [] as string[],
})
const userForm = reactive({
  username: '',
  full_name: '',
  department: '',
  role_id: null as number | null,
  password: '',
})

const activeUsers = computed(() => users.value.filter((item) => item.is_active).length)
const selectedRole = computed(() => roles.value.find((item) => item.id === roleDraft.id) || null)
const selectedPermissionCount = computed(() =>
  allAccess.value ? allPermissionValues.length : roleDraft.permissions.length,
)
const canManageUsers = computed(() => auth.can('user:manage'))
const canManageRoles = computed(() => auth.can('role:manage'))

function roleUserCount(roleId: number) {
  return users.value.filter((item) => item.role.id === roleId).length
}

function groupSelectionCount(group: PermissionGroup) {
  return group.options.filter((option) => roleDraft.permissions.includes(option.value)).length
}

function selectRole(item: Role) {
  roleDraft.id = item.id
  roleDraft.name = item.name
  roleDraft.description = item.description
  allAccess.value = item.permissions.includes('*')
  roleDraft.permissions = allAccess.value ? [] : [...item.permissions]
}

function openNewRole() {
  activeTab.value = 'roles'
  roleDraft.id = null
  roleDraft.name = ''
  roleDraft.description = ''
  roleDraft.permissions = []
  allAccess.value = false
}

function changeAllAccess(enabled: string | number | boolean) {
  if (!enabled && roleDraft.permissions.length === 0) {
    roleDraft.permissions = [...allPermissionValues]
  }
}

function togglePermissionGroup(group: PermissionGroup) {
  const groupValues = group.options.map((option) => option.value)
  const allSelected = groupValues.every((permission) =>
    roleDraft.permissions.includes(permission),
  )
  if (allSelected) {
    roleDraft.permissions = roleDraft.permissions.filter(
      (permission) => !groupValues.includes(permission),
    )
    return
  }
  roleDraft.permissions = Array.from(new Set([...roleDraft.permissions, ...groupValues]))
}

function resetUserForm() {
  userForm.username = ''
  userForm.full_name = ''
  userForm.department = ''
  userForm.role_id = roles.value[0]?.id ?? null
  userForm.password = ''
}

function openUserDialog() {
  resetUserForm()
  userDialog.value = true
}

async function load(preferredRoleId?: number | null) {
  loading.value = true
  try {
    const [userResponse, roleResponse] = await Promise.all([
      canManageUsers.value
        ? api.get<User[]>('/users')
        : Promise.resolve({ data: [] as User[] }),
      api.get<Role[]>('/roles'),
    ])
    users.value = userResponse.data
    roles.value = roleResponse.data
    const roleToSelect =
      roles.value.find((item) => item.id === preferredRoleId) ||
      roles.value.find((item) => item.id === roleDraft.id) ||
      roles.value[0]
    if (roleToSelect) selectRole(roleToSelect)
  } finally {
    loading.value = false
  }
}

async function saveUser() {
  if (!userForm.username.trim() || !userForm.full_name.trim() || !userForm.role_id) {
    ElMessage.warning('请填写登录账号、员工姓名并选择角色')
    return
  }
  if (userForm.password.length < 6) {
    ElMessage.error('初始密码至少需要 6 位')
    return
  }
  creatingUser.value = true
  try {
    await api.post('/users', {
      ...userForm,
      username: userForm.username.trim(),
      full_name: userForm.full_name.trim(),
      department: userForm.department.trim(),
    })
    ElMessage.success('用户已创建')
    userDialog.value = false
    await load(roleDraft.id)
  } finally {
    creatingUser.value = false
  }
}

async function toggleUser(row: User) {
  if (row.id === auth.user?.id && row.is_active) {
    ElMessage.warning('不能停用当前登录账号')
    return
  }
  updatingUserIds.value.push(row.id)
  try {
    await api.put(`/users/${row.id}`, { is_active: !row.is_active })
    ElMessage.success(row.is_active ? '用户已停用' : '用户已启用')
    await load(roleDraft.id)
  } finally {
    updatingUserIds.value = updatingUserIds.value.filter((id) => id !== row.id)
  }
}

async function changeUserRole(row: User, roleId: number) {
  if (roleId === row.role.id) return
  updatingUserIds.value.push(row.id)
  try {
    await api.put(`/users/${row.id}`, { role_id: roleId })
    ElMessage.success('用户角色已更新')
    await load(roleDraft.id)
    if (row.id === auth.user?.id) {
      await auth.fetchMe()
      if (!auth.can(['user:manage', 'role:manage'])) await router.replace('/dashboard')
    }
  } finally {
    updatingUserIds.value = updatingUserIds.value.filter((id) => id !== row.id)
  }
}

async function deleteUser(row: User) {
  if (row.id === auth.user?.id) {
    ElMessage.warning('不能删除当前登录账号')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定删除用户“${row.full_name}（${row.username}）”吗？删除后该账号会立即退出并无法再次登录，历史库存流水和审计记录会继续保留。`,
      '删除用户',
      {
        type: 'warning',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }
  deletingUserIds.value.push(row.id)
  try {
    await api.delete(`/users/${row.id}`)
    ElMessage.success('用户已删除')
    await load(roleDraft.id)
  } finally {
    deletingUserIds.value = deletingUserIds.value.filter((id) => id !== row.id)
  }
}

async function saveRole() {
  const name = roleDraft.name.trim()
  if (name.length < 2) {
    ElMessage.warning('角色名称至少需要 2 个字符')
    return
  }
  if (!allAccess.value && roleDraft.permissions.length === 0) {
    ElMessage.warning('请至少选择一项权限')
    return
  }
  roleSaving.value = true
  try {
    const payload = {
      name,
      description: roleDraft.description.trim(),
      permissions: allAccess.value ? ['*'] : roleDraft.permissions,
    }
    const response = roleDraft.id
      ? await api.put<Role>(`/roles/${roleDraft.id}`, payload)
      : await api.post<Role>('/roles', payload)
    ElMessage.success(roleDraft.id ? '角色权限已更新' : '角色已创建')
    await load(response.data.id)
    await auth.fetchMe()
    if (!auth.can('user:manage') && !auth.can('role:manage')) {
      await router.replace('/dashboard')
    }
  } finally {
    roleSaving.value = false
  }
}

onMounted(() => load())
</script>

<template>
  <div class="page user-role-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">用户与角色</h1>
        <div class="page-subtitle">删除离职账号，并按角色精确控制每个功能的访问范围</div>
      </div>
      <div class="header-actions">
        <el-button v-if="canManageRoles" :icon="Key" @click="openNewRole">新建角色</el-button>
        <el-button v-if="canManageUsers" type="primary" :icon="Plus" @click="openUserDialog">
          创建用户
        </el-button>
      </div>
    </div>

    <section class="summary-grid">
      <div class="summary-card">
        <span class="summary-icon users"><el-icon><UserFilled /></el-icon></span>
        <div><b>{{ users.length }}</b><span>用户总数</span></div>
      </div>
      <div class="summary-card">
        <span class="summary-icon active"><span class="status-dot"></span></span>
        <div><b>{{ activeUsers }}</b><span>正常使用</span></div>
      </div>
      <div class="summary-card">
        <span class="summary-icon roles"><el-icon><Key /></el-icon></span>
        <div><b>{{ roles.length }}</b><span>角色数量</span></div>
      </div>
    </section>

    <el-card class="card management-card" shadow="never">
      <el-tabs v-model="activeTab" class="management-tabs">
        <el-tab-pane v-if="canManageUsers" name="users">
          <template #label>
            <span class="tab-label"><UserFilled />用户管理</span>
          </template>
          <div class="section-heading">
            <div>
              <h2>用户账号</h2>
              <p>可调整所属角色、停用账号或安全删除用户</p>
            </div>
            <el-button v-if="canManageUsers" type="primary" plain :icon="Plus" @click="openUserDialog">
              创建用户
            </el-button>
          </div>
          <el-table
            v-loading="loading"
            :data="users"
            row-key="id"
            stripe
            class="user-table"
          >
            <el-table-column label="用户" min-width="210">
              <template #default="{ row }">
                <div class="identity">
                  <el-avatar :size="36">{{ row.full_name.slice(0, 1) }}</el-avatar>
                  <div><b>{{ row.full_name }}</b><span>@{{ row.username }}</span></div>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="department" label="部门" min-width="130">
              <template #default="{ row }">{{ row.department || '未设置' }}</template>
            </el-table-column>
            <el-table-column label="所属角色" min-width="180">
              <template #default="{ row }">
                <el-select
                  :model-value="row.role.id"
                  :disabled="!canManageUsers || updatingUserIds.includes(row.id)"
                  :loading="updatingUserIds.includes(row.id)"
                  aria-label="所属角色"
                  @change="changeUserRole(row, Number($event))"
                >
                  <el-option
                    v-for="item in roles"
                    :key="item.id"
                    :label="item.name"
                    :value="item.id"
                  />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <span class="user-status" :class="{ disabled: !row.is_active }">
                  <i></i>{{ row.is_active ? '正常' : '已停用' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="首次改密" width="100" align="center">
              <template #default="{ row }">{{ row.must_change_password ? '需要' : '完成' }}</template>
            </el-table-column>
            <el-table-column v-if="canManageUsers" label="操作" width="190" fixed="right">
              <template #default="{ row }">
                <el-button
                  link
                  :type="row.is_active ? 'warning' : 'success'"
                  :loading="updatingUserIds.includes(row.id)"
                  :disabled="row.id === auth.user?.id && row.is_active"
                  @click="toggleUser(row)"
                >
                  {{ row.is_active ? '停用' : '启用' }}
                </el-button>
                <el-button
                  link
                  type="danger"
                  :icon="Delete"
                  :loading="deletingUserIds.includes(row.id)"
                  :disabled="row.id === auth.user?.id"
                  data-testid="delete-user"
                  @click="deleteUser(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!loading && !users.length" description="暂无用户" />
        </el-tab-pane>

        <el-tab-pane name="roles">
          <template #label>
            <span class="tab-label"><Key />角色权限</span>
          </template>
          <div class="role-layout">
            <aside class="role-list-panel">
              <div class="role-list-heading">
                <div><b>现有角色</b><span>{{ roles.length }} 个</span></div>
                <el-button
                  v-if="canManageRoles"
                  circle
                  plain
                  :icon="Plus"
                  aria-label="新建角色"
                  @click="openNewRole"
                />
              </div>
              <button
                v-for="item in roles"
                :key="item.id"
                type="button"
                class="role-list-item"
                :class="{ selected: roleDraft.id === item.id }"
                @click="selectRole(item)"
              >
                <span class="role-mark">{{ item.name.slice(0, 1) }}</span>
                <span class="role-copy">
                  <span class="role-name">
                    <b>{{ item.name }}</b>
                    <el-tag v-if="item.is_system" size="small" type="info">内置</el-tag>
                  </span>
                  <span class="role-meta">{{ roleUserCount(item.id) }} 名用户 · {{ item.permissions.includes('*') ? '全部权限' : `${item.permissions.length} 项权限` }}</span>
                </span>
              </button>
            </aside>

            <section class="permission-editor">
              <div class="editor-heading">
                <div class="editor-copy">
                  <span class="eyebrow">{{ roleDraft.id ? '编辑现有角色' : '创建自定义角色' }}</span>
                  <h2>{{ roleDraft.id ? roleDraft.name : '新角色' }}</h2>
                  <p>保存后，属于该角色的用户将在下一次请求时立即使用新权限。</p>
                </div>
                <el-tag v-if="selectedRole?.is_system" type="info" effect="plain">系统内置角色</el-tag>
              </div>

              <div class="role-fields">
                <el-form-item label="角色名称" required>
                  <el-input
                    v-model="roleDraft.name"
                    :prefix-icon="EditPen"
                    :disabled="!canManageRoles"
                    placeholder="例如：仓库操作员"
                  />
                </el-form-item>
                <el-form-item label="角色说明">
                  <el-input
                    v-model="roleDraft.description"
                    :disabled="!canManageRoles"
                    placeholder="说明该角色适用的人群"
                  />
                </el-form-item>
              </div>

              <div class="all-access">
                <div class="all-access-copy">
                  <b class="all-access-title">全部权限</b>
                  <span class="all-access-description">开启后自动拥有当前及以后新增的所有系统权限</span>
                </div>
                <div class="access-control">
                  <span class="access-state" :class="{ enabled: allAccess }">{{ allAccess ? '已开启' : '未开启' }}</span>
                  <el-switch
                    v-model="allAccess"
                    :disabled="!canManageRoles"
                    aria-label="全部权限"
                    @change="changeAllAccess"
                  />
                </div>
              </div>

              <div class="permission-title">
                <div class="permission-title-copy">
                  <b>功能权限</b>
                  <span>已选择 {{ selectedPermissionCount }} / {{ allPermissionValues.length }} 项</span>
                </div>
                <el-button
                  v-if="canManageRoles && !allAccess"
                  link
                  type="primary"
                  @click="roleDraft.permissions = [...allPermissionValues]"
                >
                  选择全部
                </el-button>
              </div>

              <el-checkbox-group
                v-model="roleDraft.permissions"
                class="permission-grid"
                :class="{ locked: allAccess }"
              >
                <article v-for="group in permissionGroups" :key="group.name" class="permission-group">
                  <header>
                    <div class="group-copy">
                      <b class="group-title">{{ group.name }}</b>
                      <span class="group-description">{{ group.description }}</span>
                    </div>
                    <el-button
                      v-if="canManageRoles && !allAccess"
                      link
                      type="primary"
                      @click="togglePermissionGroup(group)"
                    >
                      {{ groupSelectionCount(group) === group.options.length ? '清除' : '全选' }}
                    </el-button>
                  </header>
                  <el-checkbox
                    v-for="option in group.options"
                    :key="option.value"
                    :value="option.value"
                    :disabled="allAccess || !canManageRoles"
                    class="permission-item"
                  >
                    <span class="permission-copy">
                      <b class="permission-name">{{ option.label }}</b>
                      <span class="permission-description">{{ option.description }}</span>
                    </span>
                  </el-checkbox>
                </article>
              </el-checkbox-group>

              <div v-if="canManageRoles" class="editor-actions">
                <span>
                  {{ allAccess ? '该角色拥有完整系统访问权' : `将授予 ${roleDraft.permissions.length} 项权限` }}
                </span>
                <el-button
                  type="primary"
                  :loading="roleSaving"
                  :disabled="!roleDraft.name.trim()"
                  data-testid="save-role-permissions"
                  @click="saveRole"
                >
                  {{ roleDraft.id ? '保存权限更改' : '创建角色' }}
                </el-button>
              </div>
            </section>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="userDialog" title="创建员工账号" width="560px" destroy-on-close>
      <el-form label-position="top">
        <div class="two-column-form">
          <el-form-item label="登录账号" required>
            <el-input v-model="userForm.username" placeholder="字母、数字、点或下划线" />
          </el-form-item>
          <el-form-item label="员工姓名" required>
            <el-input v-model="userForm.full_name" />
          </el-form-item>
          <el-form-item label="部门">
            <el-input v-model="userForm.department" placeholder="例如：研发部" />
          </el-form-item>
          <el-form-item label="角色" required>
            <el-select v-model="userForm.role_id">
              <el-option v-for="item in roles" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="初始密码（至少 6 位，可仅使用数字）" required>
          <el-input
            v-model="userForm.password"
            type="password"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>
        <el-alert
          title="新用户首次登录后需要修改初始密码。"
          type="info"
          :closable="false"
          show-icon
        />
      </el-form>
      <template #footer>
        <el-button @click="userDialog = false">取消</el-button>
        <el-button type="primary" :loading="creatingUser" @click="saveUser">创建用户</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.user-role-page{min-width:0}
.header-actions{display:flex;gap:10px}
.summary-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:18px}
.summary-card{display:flex;align-items:center;gap:14px;padding:17px 20px;border:1px solid #e2e9f2;border-radius:14px;background:#fff;box-shadow:0 8px 24px rgba(25,48,80,.045)}
.summary-card>div{display:flex;flex-direction:column}.summary-card b{font-size:24px;line-height:1;color:#172b4d}.summary-card div span{margin-top:6px;color:#7b8ca5;font-size:12px}
.summary-icon{width:42px;height:42px;display:grid;place-items:center;border-radius:12px;font-size:20px}.summary-icon.users{color:#2878e8;background:#eaf3ff}.summary-icon.active{background:#eaf8f1}.summary-icon.roles{color:#7b5dde;background:#f1edff}.status-dot{width:12px;height:12px;border-radius:50%;background:#22a06b;box-shadow:0 0 0 6px rgba(34,160,107,.12)}
.management-card{overflow:hidden}.management-card:deep(.el-card__body){padding:0}.management-tabs:deep(.el-tabs__header){margin:0;padding:0 24px;border-bottom:1px solid #e7edf5}.management-tabs:deep(.el-tabs__nav-wrap::after){display:none}.management-tabs:deep(.el-tabs__item){height:58px;font-size:15px}.management-tabs:deep(.el-tabs__content){overflow:visible}.tab-label{display:inline-flex;align-items:center;gap:7px}.tab-label svg{width:17px;height:17px}
.section-heading{display:flex;justify-content:space-between;align-items:center;padding:22px 24px 14px}.section-heading h2,.editor-heading h2{margin:0;color:#172b4d}.section-heading h2{font-size:18px}.section-heading p,.editor-heading p{margin:6px 0 0;color:#7c8da5;font-size:13px}
.user-table{width:100%}.identity{display:flex;align-items:center;gap:11px}.identity .el-avatar{flex:0 0 auto;background:#dceaff;color:#2d68b3;font-weight:700}.identity>div{min-width:0;display:flex;flex-direction:column}.identity b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#263b5b}.identity span{margin-top:3px;color:#8a99ac;font-size:12px}.user-status{display:inline-flex;align-items:center;gap:7px;color:#16845b;font-size:13px}.user-status i{width:7px;height:7px;border-radius:50%;background:#27ae76}.user-status.disabled{color:#8b98aa}.user-status.disabled i{background:#aab4c2}.user-table:deep(.el-select){width:155px}
.role-layout{display:grid;grid-template-columns:276px minmax(0,1fr);min-height:620px}.role-list-panel{padding:20px 14px;border-right:1px solid #e7edf5;background:#f8fafd}.role-list-heading{display:flex;align-items:center;justify-content:space-between;padding:0 8px 12px}.role-list-heading>div{display:flex;align-items:baseline;gap:8px}.role-list-heading b{line-height:22px}.role-list-heading span{color:#8493a8;font-size:12px;line-height:18px}
.role-list-item{width:100%;min-height:68px;display:flex;align-items:center;gap:11px;margin:3px 0;padding:11px 12px;border:1px solid transparent;border-radius:11px;background:transparent;color:inherit;text-align:left;cursor:pointer;transition:.16s ease}.role-list-item:hover{background:#fff;border-color:#e0e8f3}.role-list-item.selected{background:#fff;border-color:#bcd6fb;box-shadow:0 5px 16px rgba(45,104,179,.08)}.role-mark{width:36px;height:36px;flex:0 0 auto;display:grid;place-items:center;border-radius:10px;background:#e8f1ff;color:#2f6bb8;font-weight:800;line-height:1}.role-copy{min-width:0;display:flex;flex:1;flex-direction:column;gap:3px}.role-name{min-width:0;min-height:22px;display:flex;align-items:center;gap:6px;line-height:22px}.role-name b{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px;line-height:22px}.role-meta{display:block;color:#8291a6;font-size:11px;line-height:18px;white-space:normal}
.permission-editor{min-width:0;padding:25px 28px 0}.editor-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding-bottom:20px;border-bottom:1px solid #e7edf5}.editor-copy{min-width:0}.editor-heading h2{margin-top:4px;font-size:21px;line-height:29px}.editor-heading p{line-height:20px}.eyebrow{display:block;color:#2f75d6;font-size:11px;font-weight:700;line-height:18px;letter-spacing:.08em}.role-fields{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.4fr);gap:16px;padding-top:20px}.role-fields .el-form-item{margin-bottom:16px}.role-fields:deep(.el-form-item__label){height:auto;padding-bottom:8px;line-height:20px}.role-fields:deep(.el-form-item__content){min-width:0}.all-access{display:flex;align-items:center;justify-content:space-between;gap:20px;min-height:76px;padding:15px 18px;border:1px solid #d9e7fb;border-radius:12px;background:#f5f9ff}.all-access-copy{min-width:0;display:flex;flex-direction:column;gap:3px}.all-access-title{color:#244b80;font-size:14px;line-height:21px}.all-access-description{color:#71849d;font-size:12px;line-height:18px}
.access-control{flex:0 0 auto;display:flex;align-items:center;gap:10px}.access-state{min-width:39px;color:#7a899c;font-size:12px;font-weight:600;line-height:20px;text-align:right}.access-state.enabled{color:#2f75d6}
.permission-title{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:23px 0 12px}.permission-title-copy{min-width:0;display:flex;align-items:baseline;gap:10px}.permission-title-copy b{font-size:16px;line-height:23px}.permission-title-copy span{color:#8291a5;font-size:12px;line-height:18px}.permission-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding-bottom:20px;font-size:14px;line-height:normal}.permission-grid.locked{opacity:.62}.permission-group{min-width:0;padding:15px;border:1px solid #e2e9f2;border-radius:12px;background:#fff;font-size:14px;line-height:normal}.permission-group header{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;min-height:51px;margin-bottom:8px;padding-bottom:10px;border-bottom:1px solid #edf1f6;line-height:normal}.group-copy{min-width:0;display:flex;flex-direction:column;gap:3px}.group-title{display:block;color:#263b5b;font-size:14px;line-height:21px}.group-description{display:block;color:#8997aa;font-size:11px;line-height:17px}.permission-item{width:100%;min-height:54px;height:auto;margin:0;padding:8px 0;align-items:flex-start;line-height:normal}.permission-item:deep(.el-checkbox__input){flex:0 0 auto;margin-top:3px}.permission-item:deep(.el-checkbox__label){min-width:0;display:block;flex:1;padding-left:10px;white-space:normal;line-height:normal}.permission-copy{min-width:0;display:flex;flex-direction:column;gap:3px;line-height:normal}.permission-name{display:block;color:#344763;font-size:13px;font-weight:600;line-height:20px}.permission-description{display:block;color:#8a98aa;font-size:11px;line-height:17px}
.editor-actions{position:sticky;bottom:0;display:flex;align-items:center;justify-content:space-between;gap:18px;margin:0 -28px;padding:16px 28px;border-top:1px solid #e2e9f2;background:rgba(255,255,255,.96);backdrop-filter:blur(8px);z-index:2}.editor-actions span{color:#7b8aa0;font-size:12px;line-height:19px}
.two-column-form{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}.two-column-form:deep(.el-select){width:100%}
@media(max-width:1050px){.role-layout{grid-template-columns:230px minmax(0,1fr)}.permission-grid{grid-template-columns:1fr}.role-fields{grid-template-columns:1fr}}
@media(max-width:760px){.summary-grid{grid-template-columns:1fr}.header-actions{width:100%}.header-actions .el-button{flex:1}.role-layout{display:block}.role-list-panel{border-right:0;border-bottom:1px solid #e7edf5}.role-list-item{display:inline-flex;width:calc(50% - 4px);vertical-align:top}.permission-editor{padding:20px 16px 0}.editor-actions{margin:0 -16px;padding:14px 16px}.two-column-form{grid-template-columns:1fr}.section-heading{align-items:flex-start}.user-table:deep(.el-select){width:135px}}
</style>
