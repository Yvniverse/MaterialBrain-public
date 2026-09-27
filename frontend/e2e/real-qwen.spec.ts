import { expect, test } from '@playwright/test'

const realQwenEnabled = process.env.RUN_REAL_QWEN_E2E === 'true'
test.skip(!realQwenEnabled, 'Set RUN_REAL_QWEN_E2E=true for the manual real-model pilot')

test('real Qwen floating-agent location pilot', async ({ page }) => {
  const username = process.env.E2E_USERNAME
  const password = process.env.E2E_PASSWORD
  test.skip(!username || !password, 'E2E_USERNAME and E2E_PASSWORD are required')

  await page.goto('/login')
  await page.getByPlaceholder('请输入账号').fill(username!)
  await page.getByPlaceholder('请输入密码').fill(password!)
  await page.getByRole('button', { name: '安全登录' }).click()
  await expect(page).toHaveURL(/\/dashboard$/)

  await page.getByTestId('floating-agent-launcher').click()
  await page.getByTestId('agent-query-input').fill('STM32F405RGT6 在哪里？')
  await page.getByTestId('agent-submit').click()

  await expect(page.getByTestId('agent-result')).toBeVisible({ timeout: 100_000 })
  await expect(page.getByTestId('agent-timeline')).toContainText('定位物理库位')
  await expect(page.getByTestId('agent-grounded-facts')).toBeVisible()
  await expect(page.getByTestId('agent-location-result')).toBeVisible()
})
