import { create } from 'zustand';
import { FARMOS_API_URL, SHOP_API_URL } from '@/lib/serviceUrls';

const FARMOS_API = FARMOS_API_URL;
const SHOP_API = SHOP_API_URL;

interface AuthUser {
  login_id: string;
  name: string;
  shop_user_id: number | null;
}

interface UserState {
  user: AuthUser | null;
  isLoggedIn: boolean;
  isLoading: boolean;
  checkAuth: () => Promise<void>;
  logout: () => Promise<void>;
}

export const useUserStore = create<UserState>((set) => ({
  user: null,
  isLoggedIn: false,
  isLoading: true,

  checkAuth: async () => {
    try {
      // 쇼핑몰 백엔드가 FarmOS 백엔드에 서버사이드 검증 수행
      const res = await fetch(`${SHOP_API}/api/users/auth/status`, {
        credentials: 'include',
      });
      if (res.ok) {
        const data = await res.json();
        if (data.authenticated) {
          set({
            user: {
              login_id: data.login_id,
              name: data.name,
              shop_user_id: data.shop_user_id,
            },
            isLoggedIn: true,
            isLoading: false,
          });
          return;
        }
      }
    } catch {
      // 무시
    }
    set({ user: null, isLoggedIn: false, isLoading: false });
  },

  logout: async () => {
    // FarmOS 쿠키 삭제
    try {
      await fetch(`${FARMOS_API}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      });
    } catch {
      // 무시
    }
    set({ user: null, isLoggedIn: false });
  },
}));
