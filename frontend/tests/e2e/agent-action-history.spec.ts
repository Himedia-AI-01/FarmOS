/**
 * agent-action-history E2E Tests
 *
 * Design Ref: §8.4 (L2 UI Actions), §8.5 (L3 E2E Scenarios)
 * Plan Ref: SC-2/SC-3/SC-4 최종 검증
 *
 * 전제:
 *   - FarmOS BE (http://localhost:8000)
 *   - FarmOS FE (http://localhost:5173)
 *   - global-setup.ts 가 farmer01 세션을 미리 준비
 *   - seed: ai_agent_decisions 20건 이상 (backend/scripts/seed_ai_agent.py)
 */

import { test, expect, type Page, type BrowserContext } from '@playwright/test';

const FARMOS_API = 'http://localhost:8000/api/v1';
const DASHBOARD_PATH = '/iot';

// ── U1: 대시보드 로드 → Summary Cards 4개 가시 ─────────────────────────────────

test('U1: loads dashboard and shows AI activity summary cards', async ({ page }: { page: Page }) => {
  await page.goto(DASHBOARD_PATH);
  await expect(page.getByRole('tab', { name: '오늘' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '7일' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '30일' })).toBeVisible();
  await expect(page.getByText('총 판단', { exact: false })).toBeVisible();
  await expect(page.getByText('최다 제어', { exact: false })).toBeVisible();
  await expect(page.getByText('최다 소스', { exact: false })).toBeVisible();
  await expect(page.getByText('평균 판단시간', { exact: false })).toBeVisible();
});

// ── U2: 탭 7일 클릭 → /activity/summary?range=7d 호출 ──────────────────────

test('U2: clicking 7d tab triggers summary?range=7d', async ({ page }: { page: Page }) => {
  await page.goto(DASHBOARD_PATH);
  const summaryRequest = page.waitForRequest((req) =>
    req.url().includes(`${FARMOS_API}/ai-agent/activity/summary`) &&
    req.url().includes('range=7d') &&
    req.method() === 'GET'
  );
  await page.getByRole('tab', { name: '7일' }).click();
  await summaryRequest;
  await expect(page.getByRole('tab', { name: '7일' })).toHaveAttribute('aria-selected', 'true');
});

// ── 헬퍼: 대시보드 진입 + decisions 응답 대기 + row 1건 가시 보장 ─────────

async function gotoDashboardWithRows(page: Page): Promise<void> {
  await page.goto(DASHBOARD_PATH);
  await page.waitForResponse(
    (res) => res.url().includes('/ai-agent/decisions') && res.status() === 200,
    { timeout: 10_000 }
  );
  // decision row 가 렌더될 때까지 최대 5초 대기
  await page.getByTestId('ai-decision-row').first().waitFor({ state: 'visible', timeout: 5_000 });
}

// ── U3: 더보기 클릭 → /decisions?cursor= 호출 (seed 20+건 필요) ────────────

test('U3: more button fetches next page with cursor', async ({ page }: { page: Page }) => {
  await gotoDashboardWithRows(page);
  const moreBtn = page.getByTestId('ai-more-btn');
  if ((await moreBtn.count()) === 0) {
    test.skip(true, 'seed 데이터 부족 — more 버튼이 표시되지 않음');
    return;
  }
  const morePromise = page.waitForRequest(
    (req) => req.url().includes('/ai-agent/decisions') && req.url().includes('cursor=')
  );
  await moreBtn.click();
  await morePromise;
});

// ── U4: row 클릭 → DetailModal 오픈, 섹션 5개 표시 ─────────────────────────

test('U4: clicking decision row opens detail modal', async ({ page }: { page: Page }) => {
  await gotoDashboardWithRows(page);
  await page.getByTestId('ai-decision-row').first().click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute('aria-modal', 'true');
  await expect(dialog.getByText(/AI 판단 결과|판단 근거|규칙 기반 판단/)).toBeVisible();
  await expect(dialog.getByText('적용된 제어', { exact: false })).toBeVisible();
});

// ── U5: Copy 버튼 → clipboard 에 action JSON ──────────────────────────────

test('U5: copy button writes decision id to clipboard', async ({
  page,
  context,
}: {
  page: Page;
  context: BrowserContext;
}) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await gotoDashboardWithRows(page);
  await page.getByTestId('ai-decision-row').first().click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: /Copy decision id/ }).click();
  const clipText = await page.evaluate(() => navigator.clipboard.readText());
  expect(clipText.length).toBeGreaterThan(0);
});

// ── U6: Esc → Modal close + focus 원복 ────────────────────────────────────

test('U6: Esc closes modal', async ({ page }: { page: Page }) => {
  await gotoDashboardWithRows(page);
  await page.getByTestId('ai-decision-row').first().click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
});

// ── E1: 전체 플로우 ────────────────────────────────────────────────────────

test('E1: full flow — load → summary → list → more → detail → copy → close', async ({
  page,
  context,
}: {
  page: Page;
  context: BrowserContext;
}) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);

  await gotoDashboardWithRows(page);
  await expect(page.getByText('총 판단', { exact: false })).toBeVisible();

  const sumReq = page.waitForRequest((r) => r.url().includes('range=7d'));
  await page.getByRole('tab', { name: '7일' }).click();
  await sumReq;

  const rows = page.getByTestId('ai-decision-row');
  const initialCount = await rows.count();

  const moreBtn = page.getByTestId('ai-more-btn');
  if ((await moreBtn.count()) > 0) {
    // waitForResponse 는 click 전에 promise 선언 필요 (race 방지)
    const morePromise = page.waitForResponse(
      (r) => r.url().includes('/ai-agent/decisions') && r.url().includes('cursor=')
    );
    await moreBtn.click();
    await morePromise;
    const after = await rows.count();
    expect(after).toBeGreaterThanOrEqual(initialCount);
  }

  await rows.first().click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: /Copy decision id/ }).click();
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
});

// ── E3: 빈 응답 렌더 (mock route) — seed 무관 ──────────────────────────────

test('E3: empty decisions list renders without errors', async ({ page }: { page: Page }) => {
  await page.route('**/api/v1/ai-agent/decisions**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
    });
  });
  await page.route('**/api/v1/ai-agent/activity/summary**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        range: 'today',
        total: 0,
        by_control_type: {},
        by_source: {},
        by_priority: {},
        avg_duration_ms: null,
        latest_at: null,
        generated_at: new Date().toISOString(),
      }),
    });
  });

  await page.goto(DASHBOARD_PATH);
  await expect(page.getByText('총 판단', { exact: false })).toBeVisible();
  await expect(page.getByText('0건', { exact: false }).first()).toBeVisible();
  // AIAgentPanel 의 더보기는 has_more=false 이므로 없어야 함 (IoTDashboard 의 다른 더보기 버튼은 무관)
  await expect(page.getByTestId('ai-more-btn')).toHaveCount(0);
});
