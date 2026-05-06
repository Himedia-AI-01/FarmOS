/**
 * Global setup — farmer01 으로 로그인 후 storageState 저장.
 * playwright.config.ts 의 `use.storageState` 가 이 파일을 참조해 모든 test 에 자동 주입.
 */
import { request as pwRequest } from '@playwright/test';
import { existsSync, mkdirSync } from 'fs';
import { dirname } from 'path';

const STATE_PATH = 'tests/e2e/.auth/farmer01.json';
const BE_URL = 'http://localhost:8000/api/v1';

export default async function globalSetup(): Promise<void> {
  const ctx = await pwRequest.newContext();
  const res = await ctx.post(`${BE_URL}/auth/login`, {
    data: { user_id: 'farmer01', password: 'farm1234' },
  });
  if (!res.ok()) {
    throw new Error(
      `[globalSetup] Login failed: ${res.status()} — BE 가 기동되어 있고 seed_users 에서 farmer01 이 생성됐는지 확인하세요.`
    );
  }

  const dir = dirname(STATE_PATH);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  await ctx.storageState({ path: STATE_PATH });
  await ctx.dispose();

  console.log(`[globalSetup] Login OK — session saved to ${STATE_PATH}`);
}
