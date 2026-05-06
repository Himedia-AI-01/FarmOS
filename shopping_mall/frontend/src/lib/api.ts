import axios from 'axios';
import { SHOP_API_URL } from '@/lib/serviceUrls';

const api = axios.create({
  baseURL: SHOP_API_URL,
  withCredentials: true, // farmos_token 쿠키 자동 전송
  headers: {
    'Content-Type': 'application/json',
  },
});

export default api;
