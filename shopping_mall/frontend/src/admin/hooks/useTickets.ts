import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '@/lib/api';
import type { Ticket, TicketStats, TicketStatus, TicketActionType } from '@/admin/types/ticket';

interface TicketFilters {
  status?: TicketStatus | 'all';
  action_type?: TicketActionType | 'all';
  user_id?: number | null;
  limit?: number;
}

export function useTickets(filters: TicketFilters = {}) {
  const params: Record<string, string> = {};
  if (filters.status && filters.status !== 'all') params.status = filters.status;
  if (filters.action_type && filters.action_type !== 'all') params.action_type = filters.action_type;
  if (filters.user_id != null) params.user_id = String(filters.user_id);
  if (filters.limit != null) params.limit = String(filters.limit);

  return useQuery<Ticket[]>({
    queryKey: ['admin-tickets', filters],
    queryFn: async () => {
      const { data } = await api.get('/api/admin/tickets', { params });
      return data;
    },
  });
}

/** 특정 사용자의 교환 티켓만 가져오는 편의 훅 (챗봇 대화 상세 연동용) */
export function useUserExchangeTickets(userId: number | null) {
  return useQuery<Ticket[]>({
    queryKey: ['admin-tickets', { action_type: 'exchange', user_id: userId }],
    queryFn: async () => {
      const { data } = await api.get('/api/admin/tickets', {
        params: { action_type: 'exchange', user_id: userId },
      });
      return data;
    },
    enabled: userId != null,
  });
}

export function useTicketStats() {
  return useQuery<TicketStats>({
    queryKey: ['admin-ticket-stats'],
    queryFn: async () => {
      const { data } = await api.get('/api/admin/tickets/stats');
      return data;
    },
  });
}

export function useUpdateTicketStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ ticketId, status }: { ticketId: number; status: TicketStatus }) => {
      const { data } = await api.patch(`/api/admin/tickets/${ticketId}/status`, { status });
      return data as Ticket;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-tickets'] });
      queryClient.invalidateQueries({ queryKey: ['admin-ticket-stats'] });
    },
  });
}
