import { useQuery } from '@tanstack/react-query';
import api from '@/lib/api';
import type { DashboardData } from '@/admin/types/dashboard';
import type { ChatLog } from '@/admin/types/chatlog';
import type { TicketStats } from '@/admin/types/ticket';

export function useDashboard() {
  return useQuery<DashboardData>({
    queryKey: ['dashboard'],
    queryFn: async () => {
      const { data } = await api.get('/api/analytics/dashboard');
      return data;
    },
  });
}

export function useEscalatedLogs() {
  return useQuery<ChatLog[]>({
    queryKey: ['admin-chat-logs-escalated'],
    queryFn: async () => {
      const { data } = await api.get('/api/admin/chatbot/logs/escalated');
      return data;
    },
  });
}

export function useDashboardTicketStats() {
  return useQuery<TicketStats>({
    queryKey: ['admin-ticket-stats'],
    queryFn: async () => {
      const { data } = await api.get('/api/admin/tickets/stats');
      return data;
    },
  });
}
