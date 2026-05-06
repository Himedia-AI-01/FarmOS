import { describe, expect, it } from 'vitest';
import { FARMOS_API_URL, FARMOS_LOGIN_URL, SHOP_API_URL } from './serviceUrls';

describe('service URL fallbacks', () => {
  it('uses deployment fallback URLs when env vars are not configured', () => {
    expect(SHOP_API_URL).toBe('https://shop.farmos.biz');
    expect(FARMOS_API_URL).toBe('https://app.farmos.biz/api/v1');
    expect(FARMOS_LOGIN_URL).toBe('https://app.farmos.biz/login');
  });
});
